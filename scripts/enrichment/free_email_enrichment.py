#!/usr/bin/env python3
"""
free_email_enrichment.py
========================
Multi-source email enrichment for unmatched liens across TX / AZ / GA.
Runs daily (Task Scheduler, 6:00 AM) before the email sends so fresh leads are
ready.

Patterns reused from app/workers/enrich_liens_from_web.py (requests session,
BeautifulSoup contact-page scraping) and scripts/enrichment/
multi_state_email_enrichment.py (ValueSerp call, junk-email filtering,
registrable-domain matching, daily quota counters).

Sources, processed per lien in this order:
  1. SAM.gov registry  — match against the local entity extract
                          (data/raw/sam_gov_entities.dat, pipe-delimited; .csv
                          fallback) if present (free).
  2. BBB scraper        — company-name leads only. Rotating UAs, 2s delay (free).
  3. SerpAPI            — Google Maps for the official website (fast), Google
                          organic as fallback. Capped at 33 calls/day.
  4. ValueSerp          — PAYG ($2.50/1k) organic search, capped at 130/day.
  5. Website scraper    — fired whenever a source returns a website URL; tries
                          /, /contact, /about, /contact-us (free).

dbpr_score by source : SAM=70, SerpAPI=60, ValueSerp=60, BBB=55, website=50.
confidence           : 'medium' if the email domain matches the business name,
                       else 'low'.
A `source` column records 'sam_gov', 'bbb', 'serpapi', 'valueserp', or 'website'.

Usage:
  python scripts/enrichment/free_email_enrichment.py
  python scripts/enrichment/free_email_enrichment.py --dry-run
  python scripts/enrichment/free_email_enrichment.py --state AZ
  python scripts/enrichment/free_email_enrichment.py --limit 50
  python scripts/enrichment/free_email_enrichment.py --source bbb
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
VALUESERP_KEY  = os.getenv("VALUESERP_KEY", "")
VALUESERP_CAP  = 130          # daily call cap (PAYG cost control)
SERPAPI_KEY    = os.getenv("SERPAPI_KEY", "")
SERPAPI_CAP    = 33           # daily call cap (1,000/mo ÷ 30)

DEFAULT_STATES = ["TX", "AZ", "GA"]
DEFAULT_LIMIT  = 200
# SAM.gov entity extract: a pipe-delimited .dat (primary), CSV as fallback.
SAM_DAT        = LEADFLOW_DIR / "data" / "raw" / "sam_gov_entities.dat"
SAM_CSV        = LEADFLOW_DIR / "data" / "raw" / "sam_gov_entities.csv"
SAM_NAME_COLS  = ["LEGAL_BUSINESS_NAME", "DBA_NAME"]
SAM_EMAIL_COLS = ["PHYSICAL_ADDRESS_EMAIL_ADDRESS", "GOVT_BUS_POC_EMAIL",
                  "ALT_GOVT_BUS_POC_EMAIL", "PAST_PERF_POC_EMAIL"]
SAM_STATE_COL  = "PHYSICAL_ADDRESS_PROVINCE_OR_STATE"


def sam_path():
    """Return the SAM.gov source file (.dat preferred, .csv fallback), or None."""
    if SAM_DAT.exists():
        return SAM_DAT
    if SAM_CSV.exists():
        return SAM_CSV
    return None

OPS_DIR        = LEADFLOW_DIR / "data" / "ops"
OPS_DIR.mkdir(parents=True, exist_ok=True)
VALUESERP_COUNT_FILE = OPS_DIR / "valueserp_daily_count.json"
SERPAPI_COUNT_FILE   = OPS_DIR / "serpapi_daily_count.json"

# dbpr_score per source — SAM is the most authoritative, website the least.
SOURCE_SCORE = {"sam_gov": 70, "serpapi": 60, "valueserp": 60, "bbb": 55, "website": 50}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b")

STATE_NAMES = {"TX": "Texas", "AZ": "Arizona", "GA": "Georgia"}

# Three standard browser UAs to rotate (BBB is sensitive to a static UA).
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

# Names that qualify for a BBB lookup (companies, not individuals).
BBB_COMPANY_RE = re.compile(
    r"\b(LLC|L\.L\.C\.|INC|INCORPORATED|CO|CORP|CORPORATION|COMPANY|"
    r"SERVICES?|CONSTRUCTION)\b", re.IGNORECASE)

# Email rejects, plus basic sanity.
BAD_EMAIL_SUBSTR = ("noreply@", "no-reply@", "@sentry", "@wix", "@wordpress",
                    "info@example.com")
BAD_EMAIL_DOMAINS = {"example.com", "domain.com", "email.com", "test.com",
                     "sentry.io", "wix.com", "wixpress.com", "wordpress.com",
                     "godaddy.com", "squarespace.com"}
GENERIC_LOCALPARTS = {"info", "contact", "hello", "office", "sales", "admin",
                      "support", "team", "mail", "service", "billing"}
ENTITY_WORDS = {"llc", "inc", "incorporated", "corp", "corporation", "co",
                "company", "ltd", "pllc", "plc", "lp", "llp", "pc"}
_BIZ_STOP = {"the", "and", "of", "for", "services", "service", "solutions",
             "group", "construction", "contractors", "contractor", "systems",
             "management", "enterprises", "holdings", "associates"}

SESSION = requests.Session()
SESSION.headers.update({"Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                        "Accept-Language": "en-US,en;q=0.9"})


# ── Daily quota counter (ValueSerp; reset at midnight) ───────────────────────────

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


def valueserp_used() -> int:
    return _load_count(VALUESERP_COUNT_FILE)["used"]


def valueserp_remaining() -> int:
    return max(0, VALUESERP_CAP - valueserp_used())


def valueserp_increment():
    d = _load_count(VALUESERP_COUNT_FILE)
    d["used"] += 1
    _save_count(VALUESERP_COUNT_FILE, d)


def serpapi_used() -> int:
    return _load_count(SERPAPI_COUNT_FILE)["used"]


def serpapi_remaining() -> int:
    return max(0, SERPAPI_CAP - serpapi_used())


def serpapi_increment():
    d = _load_count(SERPAPI_COUNT_FILE)
    d["used"] += 1
    _save_count(SERPAPI_COUNT_FILE, d)


def reset_quota():
    today = date.today().isoformat()
    _save_count(VALUESERP_COUNT_FILE, {"date": today, "used": 0})
    _save_count(SERPAPI_COUNT_FILE, {"date": today, "used": 0})
    print("  ValueSerp + SerpAPI quota counters reset for", today)


# ── Email helpers ───────────────────────────────────────────────────────────────

def registrable_domain(host: str) -> str:
    host = (host or "").lower().split(":")[0].strip(".")
    host = re.sub(r"^www\.", "", host)
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _biz_tokens(business_name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (business_name or "").lower())
            if len(t) > 2 and t not in _BIZ_STOP and t not in ENTITY_WORDS]


def email_matches_domain(email: str, site_url: str) -> bool:
    """True when the email's registrable domain equals the website's."""
    if not email or "@" not in email or not site_url:
        return False
    e = registrable_domain(email.rsplit("@", 1)[1])
    s = registrable_domain(urlparse(site_url if "//" in site_url else "//" + site_url).netloc)
    return bool(e and s and e == s)


def email_matches_business(email: str, business_name: str) -> bool:
    if not email or "@" not in email:
        return False
    dom = registrable_domain(email.rsplit("@", 1)[1]).replace(".", "")
    return any(t in dom for t in _biz_tokens(business_name))


def is_bad_email(email: str) -> bool:
    email = (email or "").lower().strip()
    if "@" not in email or len(email) > 100:
        return True
    if any(b in email for b in BAD_EMAIL_SUBSTR):
        return True
    local, _, domain = email.partition("@")
    if len(local) <= 1:
        return True
    if domain in BAD_EMAIL_DOMAINS:
        return True
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    if len(tld) < 2 or not tld.isalpha():
        return True
    if email.rsplit(".", 1)[-1] in ("png", "jpg", "jpeg", "gif", "css", "js", "svg", "webp"):
        return True
    return False


def pick_best_email(emails: list[str], business_name: str,
                    site_url: str = "") -> str | None:
    """Filter junk; prefer (1) domain == website domain, (2) domain matches
    business name, (3) a non-generic local part; else first survivor."""
    clean, seen = [], set()
    for e in emails:
        e = (e or "").lower().strip()
        if e and e not in seen and not is_bad_email(e):
            seen.add(e)
            clean.append(e)
    if not clean:
        return None
    if site_url:
        site_match = [e for e in clean if email_matches_domain(e, site_url)]
        if site_match:
            clean = site_match
    biz_match = [e for e in clean if email_matches_business(e, business_name)]
    pool = biz_match or clean
    non_generic = [e for e in pool if e.split("@", 1)[0] not in GENERIC_LOCALPARTS]
    return sorted(non_generic or pool)[0]


def confidence_for(email: str, business_name: str) -> str:
    return "medium" if email_matches_business(email, business_name) else "low"


def is_company_name(name: str) -> bool:
    """True when the name has a company indicator — BBB has no individual listings."""
    return bool(BBB_COMPANY_RE.search(name or ""))


def normalize_name(name: str) -> str:
    n = (name or "").lower()
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = " ".join(t for t in n.split() if t not in ENTITY_WORDS)
    return re.sub(r"\s+", " ", n).strip()


def _ua_headers(i: int) -> dict:
    return {"User-Agent": USER_AGENTS[i % len(USER_AGENTS)]}


# ── Source 4: website contact-page scraper ───────────────────────────────────────

def scrape_website(url: str, business_name: str) -> tuple[str | None, str | None]:
    """Fetch a site's home/contact/about pages → (best_email, phone)."""
    if not url:
        return None, None
    if "//" not in url:
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
            if pick_best_email(emails, business_name, root):
                break
        except Exception:
            continue
        time.sleep(0.3)
    return pick_best_email(emails, business_name, root), phone


# ── Source 2: BBB scraper ─────────────────────────────────────────────────────────

def source_bbb(business_name: str, city: str, state: str, ua_idx: int = 0) -> dict:
    out = {"email": None, "website": None, "phone": None}
    try:
        r = SESSION.get(
            "https://www.bbb.org/search",
            params={"find_text": business_name, "find_loc": f"{city} {state}"},
            headers=_ua_headers(ua_idx), timeout=10,
        )
        if r.status_code != 200:
            return out
        soup = BeautifulSoup(r.text, "lxml")
        mailtos = [a.get("href", "")[7:].split("?")[0]
                   for a in soup.find_all("a", href=True)
                   if a["href"].lower().startswith("mailto:")]
        text_emails = EMAIL_RE.findall(soup.get_text(" "))
        out["email"] = pick_best_email(mailtos + text_emails, business_name)
        m = PHONE_RE.search(soup.get_text(" "))
        if m:
            out["phone"] = m.group(0).strip()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and "bbb.org" not in href and "google" not in href:
                out["website"] = href
                break
    except Exception:
        return out
    return out


# ── Source 3: ValueSerp ────────────────────────────────────────────────────────────

def source_valueserp(name: str, city: str, state: str) -> dict:
    """Returns {email, website, phone}. Spends one ValueSerp call (caller checks
    the daily cap before calling)."""
    out = {"email": None, "website": None, "phone": None}
    if not VALUESERP_KEY:
        return out
    query = f'"{name}" "{city}" {state} contractor email'
    try:
        r = SESSION.get(
            "https://api.valueserp.com/search",
            params={"api_key": VALUESERP_KEY, "q": query, "num": 5},
            timeout=15,
        )
        valueserp_increment()
        if r.status_code != 200:
            return out
        results = r.json().get("organic_results", []) or []
    except Exception:
        return out

    snippet_emails = []
    for it in results:
        blob = f"{it.get('title','')} {it.get('snippet','')}"
        snippet_emails.extend(EMAIL_RE.findall(blob))
        if not out["phone"]:
            m = PHONE_RE.search(blob)
            if m:
                out["phone"] = m.group(0).strip()
    out["email"] = pick_best_email(snippet_emails, name)

    for it in results:
        link = it.get("link", "")
        dom = urlparse(link).netloc.lower()
        if link and not any(s in dom for s in (
                "yelp.", "facebook.", "linkedin.", "bbb.org", "youtube.",
                "google.", "indeed.", "mapquest.", "yellowpages.")):
            out["website"] = link
            break
    return out


# ── Source 3b: SerpAPI (Google Maps for website, Google organic for email) ───────

def serpapi_maps(name: str, city: str, state: str) -> dict:
    """engine=google_maps → {email, website, phone} from the top local result.
    Spends one SerpAPI credit (caller checks the daily cap first)."""
    out = {"email": None, "website": None, "phone": None}
    if not SERPAPI_KEY:
        return out
    try:
        r = SESSION.get(
            "https://serpapi.com/search",
            params={"engine": "google_maps", "type": "search",
                    "q": f"{name} {city} {state}", "api_key": SERPAPI_KEY},
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
        val = top.get("email") or top.get("emails")
        if isinstance(val, list) and val:
            out["email"] = val[0]
        elif isinstance(val, str) and val:
            out["email"] = val
    return out


def serpapi_google(name: str, city: str, state: str) -> dict:
    """engine=google organic → {email, website} from result snippets/links.
    Spends one SerpAPI credit (caller checks the daily cap first)."""
    out = {"email": None, "website": None, "phone": None}
    if not SERPAPI_KEY:
        return out
    try:
        r = SESSION.get(
            "https://serpapi.com/search",
            params={"engine": "google", "q": f"{name} {city} {state} email",
                    "num": 3, "api_key": SERPAPI_KEY},
            timeout=15,
        )
        serpapi_increment()
        if r.status_code != 200:
            return out
        results = r.json().get("organic_results", []) or []
    except Exception:
        return out

    snippet_emails = []
    for it in results:
        blob = f"{it.get('title','')} {it.get('snippet','')}"
        snippet_emails.extend(EMAIL_RE.findall(blob))
        if not out["phone"]:
            m = PHONE_RE.search(blob)
            if m:
                out["phone"] = m.group(0).strip()
    out["email"] = pick_best_email(snippet_emails, name)
    for it in results:
        link = it.get("link", "")
        dom = urlparse(link).netloc.lower()
        if link and not any(s in dom for s in (
                "yelp.", "facebook.", "linkedin.", "bbb.org", "youtube.",
                "google.", "indeed.", "mapquest.", "yellowpages.")):
            out["website"] = link
            break
    return out


# ── Source 1: SAM.gov federal contractor registry (pipe-delimited .dat / CSV) ────

_SAM_INDEX: dict | None = None
_SAM_LOADED = False


def load_sam_index() -> dict | None:
    """Load the SAM.gov entity extract (pipe-delimited .dat preferred, .csv
    fallback) into {normalized_business_name: email}. Returns None when no file
    is present, or {} when the file lacks the expected columns.

    Reads with pandas (sep='|'), tries the known SAM email columns in priority
    order, indexes both LEGAL_BUSINESS_NAME and DBA_NAME, and pre-filters to the
    states we enrich (PHYSICAL_ADDRESS_PROVINCE_OR_STATE) to keep the index small."""
    global _SAM_INDEX, _SAM_LOADED
    if _SAM_LOADED:
        return _SAM_INDEX
    _SAM_LOADED = True

    path = sam_path()
    if not path:
        _SAM_INDEX = None
        return None

    try:
        import pandas as pd
    except ImportError:
        print("  ⚠ pandas not installed — cannot load SAM.gov file")
        _SAM_INDEX = {}
        return _SAM_INDEX

    try:
        df = pd.read_csv(path, sep="|", low_memory=False, encoding="utf-8",
                         on_bad_lines="skip", dtype=str)
    except Exception as e:
        print(f"  ⚠ SAM.gov load failed: {e}")
        _SAM_INDEX = {}
        return _SAM_INDEX

    cols       = set(df.columns)
    name_cols  = [c for c in SAM_NAME_COLS if c in cols]
    email_cols = [c for c in SAM_EMAIL_COLS if c in cols]
    if not name_cols or not email_cols:
        print(f"  ⚠ SAM.gov file missing expected name/email columns "
              f"(found {len(cols)} cols) — skipping SAM matching")
        _SAM_INDEX = {}
        return _SAM_INDEX

    keep = name_cols + email_cols
    if SAM_STATE_COL in cols:
        keep = keep + [SAM_STATE_COL]
    sub = df[keep]
    if SAM_STATE_COL in cols:
        sub = sub[sub[SAM_STATE_COL].astype(str).str.strip().str.upper().isin(DEFAULT_STATES)]

    idx: dict[str, str] = {}
    for row in sub.itertuples(index=False, name=None):
        d = dict(zip(sub.columns, row))
        em = None
        for ec in email_cols:
            v = d.get(ec)
            if isinstance(v, str) and "@" in v and not is_bad_email(v):
                em = v.strip().lower()
                break
        if not em:
            continue
        for nc in name_cols:
            raw = d.get(nc)
            if not isinstance(raw, str):
                continue
            nm = normalize_name(raw)
            if nm:
                idx.setdefault(nm, em)

    _SAM_INDEX = idx
    return idx


def source_sam_gov(business_name: str) -> dict:
    out = {"email": None, "website": None, "phone": None}
    idx = load_sam_index()
    if idx:
        out["email"] = idx.get(normalize_name(business_name))
    return out


# ── DB ────────────────────────────────────────────────────────────────────────────

def ensure_source_column(conn):
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE lien_dbpr_contacts ADD COLUMN IF NOT EXISTS source TEXT")
    conn.commit()


def get_leads(conn, states: list[str], limit: int) -> list[dict]:
    """Unmatched liens (no lien_dbpr_contacts row with an email) in the given
    states, highest amount first."""
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
                   source: str) -> str:
    """Insert/update enriched contact. dbpr_score per source; confidence
    medium/low by domain match. ON CONFLICT fills email/phone only when the
    existing value is null (fill-if-empty)."""
    debtor = (lien.get("debtor_name") or lien.get("business_name") or "")[:250]
    biz    = lien.get("business_name") or lien.get("debtor_name") or ""
    confidence = confidence_for(email, biz)
    score = SOURCE_SCORE.get(source, 50)
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
             email.lower().strip(), phone, lien["state"], confidence, score, source),
        )
    conn.commit()
    return confidence


# ── Per-lead enrichment orchestration ────────────────────────────────────────────

def enrich_one(lien: dict, sources: set[str], ua_idx: int) -> dict:
    """Run the active sources in order SAM → BBB → SerpAPI → ValueSerp →
    Website. Returns {email, phone, source}."""
    biz   = lien.get("business_name") or lien.get("debtor_name") or ""
    city  = (lien.get("county_name") or "").replace(" County", "").strip()
    state = lien["state"]
    found = {"email": None, "phone": None, "source": ""}
    website = None

    # 1) SAM.gov (local CSV).
    if "sam" in sources:
        s = source_sam_gov(biz)
        if s.get("email"):
            return {"email": s["email"], "phone": found["phone"], "source": "sam_gov"}
        website = website or s.get("website")

    # 2) BBB (company-style names only).
    if "bbb" in sources and is_company_name(biz):
        time.sleep(2.0)  # polite
        b = source_bbb(biz, city, state, ua_idx)
        found["phone"] = found["phone"] or b.get("phone")
        if b.get("email"):
            return {"email": b["email"], "phone": found["phone"], "source": "bbb"}
        website = website or b.get("website")

    # 3) SerpAPI Google Maps — fast official-website lookup (and email if the
    #    listing exposes one). Falls back to Google organic only when Maps gives
    #    us nothing and the cap still allows. Each HTTP call spends one credit.
    if "serpapi" in sources and SERPAPI_KEY and serpapi_remaining() > 0:
        m = serpapi_maps(biz, city, state)
        found["phone"] = found["phone"] or m.get("phone")
        if m.get("email"):
            return {"email": m["email"], "phone": found["phone"], "source": "serpapi"}
        website = website or m.get("website")
        if not website and serpapi_remaining() > 0:
            g = serpapi_google(biz, city, state)
            found["phone"] = found["phone"] or g.get("phone")
            if g.get("email"):
                return {"email": g["email"], "phone": found["phone"], "source": "serpapi"}
            website = website or g.get("website")

    # 4) ValueSerp organic (broader email hunt; only while the daily cap allows).
    if "valueserp" in sources and VALUESERP_KEY and valueserp_remaining() > 0:
        v = source_valueserp(biz, city, state)
        found["phone"] = found["phone"] or v.get("phone")
        if v.get("email"):
            return {"email": v["email"], "phone": found["phone"], "source": "valueserp"}
        website = website or v.get("website")

    # 5) Website scraper — fired on any URL surfaced above.
    if "website" in sources and website:
        em, ph = scrape_website(website, biz)
        if em:
            return {"email": em, "phone": found["phone"] or ph, "source": "website"}

    return found


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Multi-source email enrichment (TX/AZ/GA)")
    parser.add_argument("--dry-run", action="store_true", help="Show matches, don't insert")
    parser.add_argument("--state", choices=["TX", "AZ", "GA"], help="Limit to one state")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max leads (default 200)")
    parser.add_argument("--source", choices=["sam", "bbb", "serpapi", "valueserp", "website"],
                        help="Run only one source")
    parser.add_argument("--reset-quota", action="store_true", help="Reset daily API counters and exit")
    args = parser.parse_args()

    if args.reset_quota:
        reset_quota()
        return

    sources = {args.source} if args.source else {"sam", "bbb", "serpapi", "valueserp", "website"}
    states  = [args.state] if args.state else DEFAULT_STATES
    limit   = max(1, args.limit)

    print(f"\n{'='*68}")
    print(f"  Multi-Source Email Enrichment")
    print(f"  States  : {', '.join(states)}   Sources: {', '.join(sorted(sources))}")
    print(f"  Limit   : {limit}   (SerpAPI: {serpapi_remaining()}/{SERPAPI_CAP}, "
          f"ValueSerp: {valueserp_remaining()}/{VALUESERP_CAP} remaining today)")
    print(f"  {'DRY RUN — no DB writes' if args.dry_run else 'LIVE — inserting into lien_dbpr_contacts'}")
    print(f"{'='*68}\n")

    if "sam" in sources and not sam_path():
        print(f"  ⚠ SAM.gov extract not found ({SAM_DAT.name} or {SAM_CSV.name}) in data/raw/")
        print(f"    Download the entity extract from https://sam.gov/data-services")
        print(f"    and save it there to enable SAM matching (skipped for now).\n")

    logger = None
    if not args.dry_run:
        try:
            from pipeline_log import PipelineLogger
            logger = PipelineLogger("free_email_enrichment")
            logger.start()
        except ImportError:
            logger = None

    yields = {"sam_gov": 0, "bbb": 0, "serpapi": 0, "valueserp": 0, "website": 0}
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
                    conf = confidence_for(email, lien.get("business_name") or debtor)
                    print(f"{prefix} {email} [{src}/{conf}]{' ☎'+phone if phone else ''} [DRY RUN]")
                else:
                    conf = insert_contact(conn, lien, email, phone, src)
                    enriched += 1
                    print(f"{prefix} ✅ {email} [{src}/{conf}]{' ☎'+phone if phone else ''}")
            else:
                print(f"{prefix} no email")

        # ── Summary ──
        print(f"\n{'─'*68}")
        for st in states:
            s = by_state[st]
            print(f"  {st}: searched {s['searched']} / found {s['found']} emails")
        shown = sum(by_state[s]["found"] for s in states) if args.dry_run else enriched
        print(f"\n  Enriched {shown} new emails today | "
              f"SAM: {yields['sam_gov']} | BBB: {yields['bbb']} | "
              f"SerpAPI: {yields['serpapi']} ({serpapi_used()}/{SERPAPI_CAP} used) | "
              f"ValueSerp: {yields['valueserp']} ({valueserp_used()}/{VALUESERP_CAP} used) | "
              f"Website: {yields['website']}")
        print(f"{'─'*68}\n")

        if logger:
            logger.step_done("enrich", ok=True,
                             detail=f"{enriched} enriched (sam:{yields['sam_gov']} "
                                    f"bbb:{yields['bbb']} sa:{yields['serpapi']} "
                                    f"vs:{yields['valueserp']} web:{yields['website']})")
            logger.finish({
                "enriched":        enriched,
                "sam_gov_yield":   yields["sam_gov"],
                "bbb_yield":       yields["bbb"],
                "serpapi_yield":   yields["serpapi"],
                "valueserp_yield": yields["valueserp"],
                "website_yield":   yields["website"],
                "serpapi_used":    serpapi_used(),
                "valueserp_used":  valueserp_used(),
                "leads_processed": sum(s["searched"] for s in by_state.values()),
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
