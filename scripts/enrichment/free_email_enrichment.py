#!/usr/bin/env python3
"""
free_email_enrichment.py
========================
Multi-source FREE email enrichment for unmatched liens across TX / AZ / GA.
Runs daily (Task Scheduler, 6:00 AM) before the 8:00 AM email sends so fresh
leads are ready.

Patterns reused from scripts/enrichment/multi_state_email_enrichment.py
(Google CSE call, junk-email filtering, registrable-domain matching) and
app/workers/enrich_liens_from_web.py (requests session, BeautifulSoup contact
page scraping).

Sources, in priority order (cheapest/most-permanent first):
  1. Google Custom Search API   — 100 free/day, permanent. Primary.
  2. SerpAPI Google Maps        — only when CSE yields no email (conserve credits).
  3. BBB scraper                — free, company-name leads only.
  4. Website contact scraper    — triggered whenever 1-3 return a website URL.

On a found email, inserts into lien_dbpr_contacts (dbpr_score=55 — below the
DBPR/TDLR match band, reflecting lower confidence). confidence is 'medium' when
the email domain matches the business name, else 'low'. A `source` column records
which source produced it.

Usage:
  python scripts/enrichment/free_email_enrichment.py
  python scripts/enrichment/free_email_enrichment.py --dry-run
  python scripts/enrichment/free_email_enrichment.py --state AZ
  python scripts/enrichment/free_email_enrichment.py --limit 30
  python scripts/enrichment/free_email_enrichment.py --source google
  python scripts/enrichment/free_email_enrichment.py --reset-quota
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

# Windows consoles are often cp1252 — never let an emoji crash the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

from app.core.db import get_connection  # noqa: E402

# ── Config ──────────────────────────────────────────────────────────────────────
GOOGLE_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY", "")
GOOGLE_CSE_ID  = os.getenv("GOOGLE_CSE_ID", "")
SERPAPI_KEY    = os.getenv("SERPAPI_KEY", "")

GCS_DAILY_LIMIT = 100
DEFAULT_STATES  = ["TX", "AZ", "GA"]
DBPR_SCORE      = 55          # below DBPR/TDLR matches — lower-confidence source

OPS_DIR        = LEADFLOW_DIR / "data" / "ops"
OPS_DIR.mkdir(parents=True, exist_ok=True)
GCS_COUNT_FILE     = OPS_DIR / "gcs_daily_count.json"
SERPAPI_COUNT_FILE = OPS_DIR / "serpapi_daily_count.json"

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]+")
PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b")

# Three standard browser UAs to rotate (BBB is sensitive to a static UA).
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

# Local parts / domains we never accept.
BAD_LOCALPARTS = ("noreply", "no-reply", "donotreply", "privacy", "wordpress",
                  "postmaster", "mailer-daemon", "abuse", "webmaster@example")
BAD_EMAIL_DOMAINS = {
    "example.com", "domain.com", "email.com", "youremail.com", "company.com",
    "test.com", "sentry.io", "wix.com", "wixpress.com", "godaddy.com",
    "squarespace.com", "wordpress.com", "sentry-next.wixpress.com",
}
GENERIC_LOCALPARTS = {"info", "contact", "hello", "office", "sales", "admin",
                      "support", "team", "mail", "service", "billing"}
ENTITY_WORDS = {"llc", "inc", "incorporated", "corp", "corporation", "co",
                "company", "ltd", "pllc", "plc", "lp", "llp", "pc"}
_BIZ_STOP = {"the", "and", "of", "for", "services", "service", "solutions",
             "group", "construction", "contractors", "contractor", "systems",
             "management", "enterprises", "holdings", "associates"}

STATE_NAMES = {"TX": "Texas", "AZ": "Arizona", "GA": "Georgia", "FL": "Florida"}

SESSION = requests.Session()
SESSION.headers.update({"Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                        "Accept-Language": "en-US,en;q=0.9"})


# ── Daily quota counters (reset at midnight) ────────────────────────────────────

def _load_count(path: Path) -> dict:
    today = date.today().isoformat()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if data.get("date") == today:
                return data
        except Exception:
            pass
    return {"date": today, "used": 0}


def _save_count(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2))


def gcs_used() -> int:
    return _load_count(GCS_COUNT_FILE)["used"]


def gcs_remaining() -> int:
    return max(0, GCS_DAILY_LIMIT - gcs_used())


def gcs_increment():
    d = _load_count(GCS_COUNT_FILE)
    d["used"] += 1
    _save_count(GCS_COUNT_FILE, d)


def serpapi_used() -> int:
    return _load_count(SERPAPI_COUNT_FILE)["used"]


def serpapi_increment():
    d = _load_count(SERPAPI_COUNT_FILE)
    d["used"] += 1
    _save_count(SERPAPI_COUNT_FILE, d)


def reset_quota():
    today = date.today().isoformat()
    _save_count(GCS_COUNT_FILE, {"date": today, "used": 0})
    _save_count(SERPAPI_COUNT_FILE, {"date": today, "used": 0})
    print("  Quota counters reset for", today)


# ── Email helpers ───────────────────────────────────────────────────────────────

def registrable_domain(host: str) -> str:
    host = (host or "").lower().split(":")[0].strip(".")
    host = re.sub(r"^www\.", "", host)
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _biz_tokens(business_name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (business_name or "").lower())
            if len(t) > 2 and t not in _BIZ_STOP and t not in ENTITY_WORDS]


def email_matches_business(email: str, business_name: str) -> bool:
    """True when the email's registrable domain contains a business-name token."""
    if not email or "@" not in email:
        return False
    dom = registrable_domain(email.rsplit("@", 1)[1]).replace(".", "")
    toks = _biz_tokens(business_name)
    return any(t in dom for t in toks)


def is_bad_email(email: str) -> bool:
    email = (email or "").lower().strip()
    if "@" not in email or len(email) > 100:
        return True
    local, _, domain = email.partition("@")
    if len(local) <= 1:
        return True
    if any(b in email for b in BAD_LOCALPARTS):
        return True
    if domain in BAD_EMAIL_DOMAINS:
        return True
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    if len(tld) < 2 or not tld.isalpha():
        return True
    if email.rsplit(".", 1)[-1] in ("png", "jpg", "jpeg", "gif", "css", "js", "svg", "webp"):
        return True
    return False


def pick_best_email(emails: list[str], business_name: str) -> str | None:
    """Filter junk, prefer a domain that matches the business name, then a
    non-generic local part, else the first survivor."""
    clean, seen = [], set()
    for e in emails:
        e = (e or "").lower().strip()
        if e and e not in seen and not is_bad_email(e):
            seen.add(e)
            clean.append(e)
    if not clean:
        return None
    matching = [e for e in clean if email_matches_business(e, business_name)]
    pool = matching or clean
    non_generic = [e for e in pool if e.split("@", 1)[0] not in GENERIC_LOCALPARTS]
    return sorted(non_generic or pool)[0]


def is_company_name(name: str) -> bool:
    """True when the name looks like a business (has an entity word), not a person."""
    toks = re.split(r"[^a-z0-9]+", (name or "").lower())
    return any(t in ENTITY_WORDS for t in toks)


def _ua_headers(i: int) -> dict:
    return {"User-Agent": USER_AGENTS[i % len(USER_AGENTS)]}


# ── Source 4: website contact-page scraper ───────────────────────────────────────

def scrape_website(url: str, business_name: str) -> tuple[str | None, str | None]:
    """Fetch a site's home/contact/about pages and return (best_email, phone)."""
    if not url:
        return None, None
    base = urlparse(url)
    if not base.scheme:
        url = "https://" + url
        base = urlparse(url)
    root = f"{base.scheme}://{base.netloc}"
    pages = [url, urljoin(root, "/contact"), urljoin(root, "/about"),
             urljoin(root, "/contact-us")]

    emails, phone = [], None
    for i, page in enumerate(pages):
        try:
            r = SESSION.get(page, headers=_ua_headers(i), timeout=10, allow_redirects=True)
            if r.status_code != 200:
                continue
            emails.extend(EMAIL_RE.findall(r.text))
            if not phone:
                m = PHONE_RE.search(r.text)
                if m:
                    phone = m.group(0).strip()
            if pick_best_email(emails, business_name):
                break
        except Exception:
            continue
        time.sleep(0.3)
    return pick_best_email(emails, business_name), phone


# ── Source 1: Google Custom Search API ───────────────────────────────────────────

def source_google_cse(name: str, city: str, state: str) -> dict:
    """Returns {email, phone, website}. Spends one CSE call (caller checks quota)."""
    out = {"email": None, "phone": None, "website": None}
    if not (GOOGLE_API_KEY and GOOGLE_CSE_ID):
        return out
    query = f'"{name}" "{city}" {state} contractor email'
    try:
        r = SESSION.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID, "q": query, "num": 5},
            timeout=10,
        )
        gcs_increment()
        if r.status_code != 200:
            return out
        items = r.json().get("items", []) or []
    except Exception:
        return out

    snippet_emails = []
    for it in items:
        blob = f"{it.get('title','')} {it.get('snippet','')}"
        snippet_emails.extend(EMAIL_RE.findall(blob))
        if not out["phone"]:
            m = PHONE_RE.search(blob)
            if m:
                out["phone"] = m.group(0).strip()
    out["email"] = pick_best_email(snippet_emails, name)

    # First non-directory result becomes the website to scrape.
    for it in items:
        link = it.get("link", "")
        dom = urlparse(link).netloc.lower()
        if link and not any(s in dom for s in (
                "yelp.", "facebook.", "linkedin.", "bbb.org", "youtube.",
                "google.", "indeed.", "mapquest.", "yellowpages.")):
            out["website"] = link
            break
    return out


# ── Source 2: SerpAPI Google Maps ────────────────────────────────────────────────

def source_serpapi_maps(business_name: str, city: str, state: str) -> dict:
    out = {"email": None, "phone": None, "website": None}
    if not SERPAPI_KEY:
        return out
    try:
        r = SESSION.get(
            "https://serpapi.com/search",
            params={"engine": "google_maps", "type": "search",
                    "q": f"{business_name} {city} {state}",
                    "api_key": SERPAPI_KEY},
            timeout=15,
        )
        serpapi_increment()
        if r.status_code != 200:
            return out
        data = r.json()
    except Exception:
        return out

    locals_ = data.get("local_results") or []
    if isinstance(locals_, dict):
        locals_ = locals_.get("places", []) or []
    if not locals_:
        place = data.get("place_results") or {}
        locals_ = [place] if place else []
    if locals_:
        top = locals_[0]
        out["website"] = top.get("website")
        out["phone"] = top.get("phone")
        for key in ("email", "emails"):
            val = top.get(key)
            if isinstance(val, list) and val:
                out["email"] = val[0]
            elif isinstance(val, str) and val:
                out["email"] = val
    return out


# ── Source 3: BBB scraper ─────────────────────────────────────────────────────────

def source_bbb(business_name: str, city: str, state: str, ua_idx: int = 0) -> dict:
    out = {"email": None, "phone": None, "website": None}
    try:
        r = SESSION.get(
            "https://www.bbb.org/search",
            params={"find_text": business_name, "find_loc": f"{city} {state}"},
            headers=_ua_headers(ua_idx), timeout=10,
        )
        if r.status_code != 200:
            return out
        soup = BeautifulSoup(r.text, "lxml")
        mailtos = [a.get("href", "")[7:] for a in soup.find_all("a", href=True)
                   if a["href"].lower().startswith("mailto:")]
        emails = [m.split("?")[0] for m in mailtos if m]
        emails.extend(EMAIL_RE.findall(soup.get_text(" ")))
        out["email"] = pick_best_email(emails, business_name)
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and "bbb.org" not in href:
                out["website"] = href
                break
    except Exception:
        return out
    return out


# ── DB ────────────────────────────────────────────────────────────────────────────

def ensure_source_column(conn):
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE lien_dbpr_contacts ADD COLUMN IF NOT EXISTS source TEXT")
    conn.commit()


def get_leads(conn, states: list[str], limit: int) -> list[dict]:
    """Unmatched liens (no lien_dbpr_contacts row, OR existing row with no email)
    in the given states, highest amount first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                nl.id                                          AS lien_id,
                nl.county_id                                   AS county_id,
                c.county_name                                  AS county_name,
                c.state                                        AS state,
                nl.business_name                               AS business_name,
                nl.debtor_name                                 AS debtor_name,
                nl.amount                                      AS amount,
                COALESCE(NULLIF(nl.business_name, ''), nl.debtor_name) AS search_name
            FROM normalized_liens nl
            JOIN counties c ON c.id = nl.county_id
            WHERE c.state = ANY(%s)
              AND COALESCE(NULLIF(nl.business_name, ''), nl.debtor_name) IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM lien_dbpr_contacts d
                  WHERE d.lien_id = nl.id
                    AND d.email IS NOT NULL AND d.email <> ''
              )
            ORDER BY nl.amount DESC NULLS LAST, nl.id
            LIMIT %s
            """,
            (states, limit),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def insert_contact(conn, lien: dict, email: str, phone: str | None,
                   source: str) -> None:
    """Insert/update enriched contact. ON CONFLICT only fills email/phone when the
    new value is non-null AND the existing value is null (fill-if-empty)."""
    debtor = (lien.get("debtor_name") or lien.get("business_name") or "")[:250]
    biz    = lien.get("business_name") or lien.get("debtor_name") or ""
    confidence = "medium" if email_matches_business(email, biz) else "low"
    phone = (phone or None)
    if phone:
        phone = phone[:50]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO lien_dbpr_contacts
                (lien_id, county_id, debtor_name, full_name,
                 email, phone, state, confidence, dbpr_score, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (lien_id) DO UPDATE SET
                email      = COALESCE(lien_dbpr_contacts.email, EXCLUDED.email),
                phone      = COALESCE(lien_dbpr_contacts.phone, EXCLUDED.phone),
                full_name  = COALESCE(lien_dbpr_contacts.full_name, EXCLUDED.full_name),
                confidence = EXCLUDED.confidence,
                dbpr_score = EXCLUDED.dbpr_score,
                source     = COALESCE(lien_dbpr_contacts.source, EXCLUDED.source)
            """,
            (lien["lien_id"], lien["county_id"], debtor, debtor,
             email.lower().strip(), phone, lien["state"], confidence,
             DBPR_SCORE, source),
        )
    conn.commit()
    return confidence


# ── Per-lead enrichment orchestration ────────────────────────────────────────────

def enrich_one(lien: dict, sources: set[str], ua_idx: int) -> dict:
    """Run the source chain for one lien. Returns
    {email, phone, source} (source is the originating source, '' if none)."""
    name  = (lien.get("search_name") or "").strip()
    biz   = lien.get("business_name") or lien.get("debtor_name") or ""
    city  = (lien.get("county_name") or "").replace(" County", "").strip()
    state = STATE_NAMES.get(lien["state"], lien["state"])
    found = {"email": None, "phone": None, "source": ""}

    # Source 1: Google CSE (only if quota remains).
    if "google" in sources and gcs_remaining() > 0:
        g = source_google_cse(name, city, state)
        found["phone"] = found["phone"] or g.get("phone")
        if g.get("email"):
            return {"email": g["email"], "phone": found["phone"], "source": "google_cse"}
        if g.get("website"):
            em, ph = scrape_website(g["website"], biz)
            if em:
                return {"email": em, "phone": found["phone"] or ph, "source": "google_cse"}

    # Source 2: SerpAPI Maps — only when CSE produced nothing.
    if "serp" in sources and SERPAPI_KEY:
        s = source_serpapi_maps(biz, city, state)
        found["phone"] = found["phone"] or s.get("phone")
        if s.get("email") and not is_bad_email(s["email"]):
            return {"email": s["email"], "phone": found["phone"], "source": "serpapi"}
        if s.get("website"):
            em, ph = scrape_website(s["website"], biz)
            if em:
                return {"email": em, "phone": found["phone"] or ph, "source": "serpapi"}

    # Source 3: BBB — company-style names only.
    if "bbb" in sources and is_company_name(biz):
        time.sleep(2.0)  # polite
        b = source_bbb(biz, city, state, ua_idx)
        found["phone"] = found["phone"] or b.get("phone")
        if b.get("email"):
            return {"email": b["email"], "phone": found["phone"], "source": "bbb"}
        if b.get("website"):
            em, ph = scrape_website(b["website"], biz)
            if em:
                return {"email": em, "phone": found["phone"] or ph, "source": "bbb"}

    return found


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Multi-source free email enrichment (TX/AZ/GA)")
    parser.add_argument("--dry-run", action="store_true", help="Show matches, don't insert")
    parser.add_argument("--state", choices=["TX", "AZ", "GA"], help="Limit to one state")
    parser.add_argument("--limit", type=int, default=None, help="Override the daily cap")
    parser.add_argument("--source", choices=["google", "serp", "bbb"], help="Run only one source")
    parser.add_argument("--reset-quota", action="store_true", help="Reset daily counters and exit")
    args = parser.parse_args()

    if args.reset_quota:
        reset_quota()
        return

    sources = {args.source} if args.source else {"google", "serp", "bbb"}
    states  = [args.state] if args.state else DEFAULT_STATES

    # Lead cap: driven by remaining Google CSE quota (the primary source), unless
    # overridden or Google isn't in play.
    if args.limit is not None:
        limit = max(0, args.limit)
    elif "google" in sources:
        limit = gcs_remaining()
    else:
        limit = 50

    print(f"\n{'='*66}")
    print(f"  Free Multi-Source Email Enrichment")
    print(f"  States  : {', '.join(states)}   Sources: {', '.join(sorted(sources))}")
    print(f"  Limit   : {limit}   (Google CSE remaining today: {gcs_remaining()}/{GCS_DAILY_LIMIT})")
    print(f"  {'DRY RUN — no DB writes' if args.dry_run else 'LIVE — inserting into lien_dbpr_contacts'}")
    print(f"{'='*66}\n")

    logger = None
    if not args.dry_run:
        try:
            from pipeline_log import PipelineLogger
            logger = PipelineLogger("free_email_enrichment")
            logger.start()
        except ImportError:
            logger = None

    if limit <= 0:
        print("  Daily Google CSE quota exhausted — nothing to do. (Use --limit or --reset-quota.)")
        if logger:
            logger.step_skip("enrich", "google quota exhausted")
            logger.finish({"enriched": 0, "reason": "quota_exhausted"})
        return

    # Per-source yield tracking.
    yields = {"google_cse": 0, "serpapi": 0, "bbb": 0}
    by_state = {s: {"searched": 0, "found": 0} for s in states}
    enriched = 0

    conn = get_connection()
    try:
        if not args.dry_run:
            ensure_source_column(conn)
        if logger:
            logger.step_start("enrich")

        leads = get_leads(conn, states, limit)
        if not leads:
            print("  No unmatched leads found.")
            if logger:
                logger.step_done("enrich", ok=True, detail="no leads")
                logger.finish({"enriched": 0})
            return

        print(f"  {len(leads)} leads to process\n")

        for i, lien in enumerate(leads):
            st = lien["state"]
            debtor = (lien.get("debtor_name") or lien.get("business_name") or "?").strip()
            by_state[st]["searched"] += 1

            res = enrich_one(lien, sources, ua_idx=i)
            email, phone, src = res["email"], res["phone"], res["source"]

            prefix = f"  [{i+1}/{len(leads)}] [{st}] {debtor[:30]:<30} ({lien.get('county_name','?')}) →"

            if email and not is_bad_email(email):
                by_state[st]["found"] += 1
                yields[src] = yields.get(src, 0) + 1
                if args.dry_run:
                    conf = "medium" if email_matches_business(email, lien.get("business_name") or debtor) else "low"
                    print(f"{prefix} {email} [{src}/{conf}]{' ☎'+phone if phone else ''} [DRY RUN]")
                else:
                    conf = insert_contact(conn, lien, email, phone, src)
                    enriched += 1
                    print(f"{prefix} ✅ {email} [{src}/{conf}]{' ☎'+phone if phone else ''}")
            else:
                print(f"{prefix} no email")

            # Stop early if Google was our engine and its quota just ran out.
            if "google" in sources and sources == {"google"} and gcs_remaining() <= 0:
                print("\n  Google CSE daily quota reached — stopping.")
                break

        # ── Summary ──
        print(f"\n{'─'*66}")
        for st in states:
            s = by_state[st]
            print(f"  {st}: searched {s['searched']} / found {s['found']} emails")
        final = (f"Enriched {0 if args.dry_run else enriched} new emails today | "
                 f"Google CSE: {gcs_used()}/{GCS_DAILY_LIMIT} used | "
                 f"SerpAPI: {serpapi_used()} credits used | "
                 f"BBB: {yields.get('bbb', 0)} matches")
        print(f"\n  {final}")
        print(f"{'─'*66}\n")

        if logger:
            logger.step_done("enrich", ok=True,
                             detail=f"{enriched} enriched "
                                    f"(g:{yields['google_cse']} s:{yields['serpapi']} b:{yields['bbb']})")
            logger.finish({
                "enriched":          enriched,
                "google_cse_yield":  yields["google_cse"],
                "serpapi_yield":     yields["serpapi"],
                "bbb_yield":         yields["bbb"],
                "google_cse_used":   gcs_used(),
                "serpapi_used":      serpapi_used(),
                "leads_processed":   sum(s["searched"] for s in by_state.values()),
            })
    except Exception as e:
        conn.rollback()
        if logger:
            logger.step_done("enrich", ok=False, error=str(e))
            logger.finish({"enriched": enriched, "error": str(e)})
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
