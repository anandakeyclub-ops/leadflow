"""
guest_post_outreach.py
=====================
Guest-post outreach pipeline for Romy Cruz / TaxCase Review.

Maintains a 40-target prospect list, researches each (accepts guest posts?
guidelines URL, editor email, DA estimate, past tax/IRS coverage), drafts a
personalized 150-word pitch per target via Claude (3 tailored article ideas),
tracks status, sends via Gmail SMTP at <=5/day, auto-follows-up once after 7
days, and on acceptance drafts the full article.

Outward actions (send / follow-up) are GATED behind flags and OFF by default.

CLI:
  python scripts/outreach/guest_post_outreach.py --seed                 # write targets JSON
  python scripts/outreach/guest_post_outreach.py --sample-pitches 3     # show N sample pitches (Claude)
  python scripts/outreach/guest_post_outreach.py --research --limit 5   # web-research N targets
  python scripts/outreach/guest_post_outreach.py --send --limit 5       # send up to 5 pitches (gated)
  python scripts/outreach/guest_post_outreach.py --followup             # 7-day follow-ups (gated)
  python scripts/outreach/guest_post_outreach.py --draft-article --target "Inc." --topic "..."

Logs via PipelineLogger("guest_post_outreach").
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import smtplib
import ssl
import time
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

try:
    from scripts.outreach.outreach_db import record_outreach, rows_for
except Exception:
    try:
        from outreach_db import record_outreach, rows_for
    except Exception:
        record_outreach = None
        rows_for = None

BASE_DIR     = Path(__file__).resolve().parents[2]
TARGETS_JSON = BASE_DIR / "data" / "outreach" / "guest_post_targets.json"
TRACKER_CSV  = BASE_DIR / "data" / "outreach" / "guest_post_tracker.csv"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SERPAPI_KEY       = os.getenv("SERPAPI_KEY", "")
DAILY_PITCH_CAP   = 5
FOLLOWUP_DAYS     = 7

# Email discovery: regex + editorial-prefix priority + junk filter.
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
EDITORIAL_PREFIXES = ("editor", "editorial", "contribute", "contributor",
                      "submissions", "submission", "submit", "tips", "pitch",
                      "press", "news")
_EMAIL_JUNK = ("example.com", "example.org", "domain.com", "yourdomain.com",
               "email.com", "sentry.io", "wixpress.com", "godaddy.com",
               ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css")

ROMY_BIO = ("Romy Cruz, Licensed Tax Professional and former IRS Revenue Officer, "
            "lead advisor at TaxCase Review (taxcasereview.org). She represents "
            "contractors and small business owners in IRS tax resolution.")

# Per-category article-idea theme + a seed example (Claude personalizes 3 ideas).
CATEGORY_THEMES = {
    "contractor": "tax liability hidden in contractor/subcontractor operations, "
                  "payroll/941 + Trust Fund Recovery Penalty, how IRS liens hurt bonding",
    "small_business": "the scale of IRS lien filings (~214,000 NFTLs/yr) and how "
                      "owners avoid becoming one; resolution basics",
    "accounting": "what CPAs/tax pros should know about contractor clients with IRS "
                  "liens; co-advisory and referral angles",
    "regional": "state-level IRS lien trends and what local contractors/businesses "
                "need to know",
}
SEED_IDEA = {
    "contractor": "5 Signs Your Subcontractors Are Creating a Tax Liability for Your Business",
    "small_business": "The IRS Files 214,000 Tax Liens a Year — Here's How to Avoid Being One of Them",
    "accounting": "What CPAs Should Know About Contractor Clients With IRS Liens",
    "regional": "{region} Leads the Nation in IRS Tax Lien Filings — What Contractors Need to Know",
}

# ── 40 seed targets ───────────────────────────────────────────────────────────
def _t(name, domain, category, region=""):
    return {"name": name, "domain": domain, "category": category, "region": region,
            "accepts_guest_posts": None, "guidelines_url": "", "editor_email": "",
            "da_estimate": None, "tax_articles": []}

SEED_TARGETS = [
    # Contractor / trades
    _t("Contractor Magazine", "contractormag.com", "contractor"),
    _t("Remodeling Magazine", "remodeling.hw.net", "contractor"),
    _t("ProRemodeler", "proremodeler.com", "contractor"),
    _t("HVAC Business Magazine", "hvacbusiness.com", "contractor"),
    _t("Roofing Contractor", "roofingcontractor.com", "contractor"),
    _t("Equipment World", "equipmentworld.com", "contractor"),
    _t("Construction Executive", "constructionexec.com", "contractor"),
    _t("Construction Dive", "constructiondive.com", "contractor"),
    _t("For Construction Pros", "forconstructionpros.com", "contractor"),
    _t("Builder Magazine", "builderonline.com", "contractor"),
    _t("Plumbing & Mechanical", "pmmag.com", "contractor"),
    _t("Electrical Contractor", "ecmag.com", "contractor"),
    # Small business / entrepreneur
    _t("Entrepreneur", "entrepreneur.com", "small_business"),
    _t("Inc.", "inc.com", "small_business"),
    _t("AllBusiness", "allbusiness.com", "small_business"),
    _t("SmallBizTrends", "smallbiztrends.com", "small_business"),
    _t("SCORE Blog", "score.org/blog", "small_business"),
    _t("QuickBooks Resource Center", "quickbooks.intuit.com/r", "small_business"),
    _t("Business.com", "business.com", "small_business"),
    _t("StartupNation", "startupnation.com", "small_business"),
    _t("Small Business Bonfire", "smallbusinessbonfire.com", "small_business"),
    _t("Foundr", "foundr.com", "small_business"),
    # Tax / accounting
    _t("Journal of Accountancy", "journalofaccountancy.com", "accounting"),
    _t("CPA Practice Advisor", "cpapracticeadvisor.com", "accounting"),
    _t("Accounting Today", "accountingtoday.com", "accounting"),
    _t("Tax Pro Center (Intuit)", "proconnect.intuit.com/taxprocenter", "accounting"),
    _t("AccountingWEB", "accountingweb.com", "accounting"),
    _t("Going Concern", "goingconcern.com", "accounting"),
    _t("The Tax Adviser", "thetaxadviser.com", "accounting"),
    _t("TR Tax & Accounting Blog", "tax.thomsonreuters.com/blog", "accounting"),
    # Florida / Texas / regional business
    _t("Florida Trend", "floridatrend.com", "regional", "Florida"),
    _t("Business Observer (FL)", "businessobserverfl.com", "regional", "Florida"),
    _t("Dallas Morning News — Business", "dallasnews.com/business", "regional", "Texas"),
    _t("Houston Business Journal", "bizjournals.com/houston", "regional", "Texas"),
    _t("Tampa Bay Business Journal", "bizjournals.com/tampabay", "regional", "Florida"),
    _t("South Florida Business Journal", "bizjournals.com/southflorida", "regional", "Florida"),
    _t("Texas CEO Magazine", "texasceomagazine.com", "regional", "Texas"),
    _t("Dallas Business Journal", "bizjournals.com/dallas", "regional", "Texas"),
    _t("Orlando Business Journal", "bizjournals.com/orlando", "regional", "Florida"),
    _t("Austin Business Journal", "bizjournals.com/austin", "regional", "Texas"),
]

TRACKER_COLUMNS = ["target", "contact_email", "pitched_date", "response_status",
                   "article_assigned", "published_date", "backlink_url"]


# ── Targets JSON ──────────────────────────────────────────────────────────────
def seed_targets(force: bool = False) -> int:
    TARGETS_JSON.parent.mkdir(parents=True, exist_ok=True)
    if TARGETS_JSON.exists() and not force:
        print(f"  Targets file already exists: {TARGETS_JSON} (use --seed --force to overwrite)")
        return 0
    TARGETS_JSON.write_text(json.dumps(SEED_TARGETS, indent=2), encoding="utf-8")
    print(f"  Seeded {len(SEED_TARGETS)} targets -> {TARGETS_JSON}")
    return len(SEED_TARGETS)


def load_targets() -> list[dict]:
    if not TARGETS_JSON.exists():
        seed_targets()
    return json.loads(TARGETS_JSON.read_text(encoding="utf-8"))


def save_targets(targets: list[dict]) -> None:
    TARGETS_JSON.write_text(json.dumps(targets, indent=2), encoding="utf-8")


# ── Research (web search) ─────────────────────────────────────────────────────
def _serpapi_search(query: str, num: int = 8) -> list[dict]:
    """Run one SerpAPI Google search; return organic_results (or [] on any error)."""
    if not SERPAPI_KEY:
        return []
    try:
        r = requests.get("https://serpapi.com/search.json",
                         params={"engine": "google", "q": query,
                                 "num": num, "api_key": SERPAPI_KEY},
                         timeout=20)
        if r.status_code != 200:
            return []
        return r.json().get("organic_results", []) or []
    except Exception:
        return []


def _extract_emails(text: str) -> list[str]:
    """Pull plausible email addresses out of free text, dropping obvious junk."""
    out: list[str] = []
    for m in EMAIL_RE.findall(text or ""):
        e = m.strip().strip(".").lower()
        if any(j in e for j in _EMAIL_JUNK):
            continue
        if e not in out:
            out.append(e)
    return out


def _score_email(email: str, base_domain: str) -> int:
    """Rank an email by how likely it's the right editorial contact.
    4 = editorial prefix on the publication's own domain; 3 = own domain;
    2 = editorial prefix on any domain; 1 = anything else."""
    local, _, dom = email.partition("@")
    is_editorial = any(local.startswith(p) for p in EDITORIAL_PREFIXES)
    domain_match = bool(base_domain) and (dom == base_domain or dom.endswith("." + base_domain))
    if is_editorial and domain_match:
        return 4
    if domain_match:
        return 3
    if is_editorial:
        return 2
    return 1


def _fetch_page_emails(url: str) -> list[str]:
    """Fetch a page and scan its HTML for email addresses. Best-effort."""
    try:
        r = requests.get(url, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0 (guest-post-research)"})
        if r.status_code != 200:
            return []
        return _extract_emails(r.text)
    except Exception:
        return []


_GUIDELINE_HINTS = ("write-for-us", "write for us", "contribute", "contributor",
                    "submission", "submit", "guidelines", "editorial", "contact")


def research_target(t: dict) -> dict:
    """Best-effort enrichment via SerpAPI: find an editor/contributor email and a
    submissions/guidelines URL for the publication.

    Searches SerpAPI twice (guest-post angle + editorial-contact angle), harvests
    emails from result titles/snippets/URLs, then fetches the top organic result
    and scans its HTML for domain/editorial-pattern emails. The best-scoring email
    is stored in editor_email; if none is found a submissions/contact URL is kept
    in guidelines_url so the target isn't left empty. Rate-limited to ~1 req/sec.
    Non-fatal: leaves fields blank if SerpAPI is unavailable."""
    if not SERPAPI_KEY:
        print(f"    [research] {t['name']}: no SERPAPI_KEY — skipped")
        return t

    name = t["name"]
    base_domain = t["domain"].split("/")[0].lower()
    queries = [
        f"{name} write for us guest post editor email",
        f"{name} editorial contact submissions",
    ]

    candidates: dict[str, int] = {}      # email -> best score seen
    guidelines_url = t.get("guidelines_url", "")
    top_url = ""

    for i, q in enumerate(queries):
        for it in _serpapi_search(q):
            link    = it.get("link", "") or ""
            title   = it.get("title", "") or ""
            snippet = it.get("snippet", "") or ""
            for e in _extract_emails(" ".join([title, snippet, link])):
                candidates[e] = max(candidates.get(e, 0), _score_email(e, base_domain))
            if not top_url and link:
                top_url = link
            # Remember a plausible submissions/guidelines page as a fallback.
            if not guidelines_url and any(h in (link + " " + title).lower()
                                          for h in _GUIDELINE_HINTS):
                guidelines_url = link
                t["accepts_guest_posts"] = True
        if i < len(queries) - 1:
            time.sleep(1)  # rate-limit between SerpAPI searches

    # Scan the top organic result page for emails (often a contact/contribute page).
    if top_url:
        time.sleep(1)
        for e in _fetch_page_emails(top_url):
            candidates[e] = max(candidates.get(e, 0), _score_email(e, base_domain))

    # Pick the best candidate; require an editorial prefix or domain match (>=2).
    best = ""
    if candidates:
        email, score = max(candidates.items(), key=lambda kv: kv[1])
        if score >= 2:
            best = email
    if best:
        t["editor_email"] = best
    # Keep a submissions/contact URL so the target isn't left empty.
    if guidelines_url and not t.get("guidelines_url"):
        t["guidelines_url"] = guidelines_url

    print(f"    [research] {name}: editor_email={t.get('editor_email') or 'n/a'}, "
          f"guidelines={'yes' if t.get('guidelines_url') else 'n/a'}")
    return t


# ── Claude ────────────────────────────────────────────────────────────────────
def call_claude(prompt: str, max_tokens: int = 700) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-sonnet-4-5", "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=120)
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()


def generate_pitch(t: dict) -> dict:
    cat = t["category"]
    theme = CATEGORY_THEMES.get(cat, CATEGORY_THEMES["small_business"])
    seed_idea = SEED_IDEA.get(cat, SEED_IDEA["small_business"]).replace(
        "{region}", t.get("region") or "Your State")
    audience = {
        "contractor": "contractors and trades business owners",
        "small_business": "small business owners and entrepreneurs",
        "accounting": "CPAs and tax professionals",
        "regional": f"{t.get('region','local')} business owners and contractors",
    }.get(cat, "business owners")
    prompt = f"""Write a SHORT guest-post pitch email from this expert:
{ROMY_BIO}

Recipient publication: {t['name']} ({t['domain']}), whose audience is {audience}.
Topic focus area: {theme}

Output exactly:
SUBJECT: <one personalized subject line tuned to {t['name']}'s audience>
BODY:
<email body, 150 words MAX: 1-2 sentences on who Romy is and why this audience
cares about IRS tax-lien/resolution topics, then 3 specific article ideas as a
bulleted list, each a title + one-line description. Make the ideas concrete and
tailored to {audience}. Use this as inspiration for tone/specificity (do not copy
verbatim): "{seed_idea}". End with a one-line offer to send a full draft.>

Be concrete and credible, not salesy. 150 words max in the body."""
    text = call_claude(prompt)
    subj = ""
    for line in text.splitlines():
        if line.strip().upper().startswith("SUBJECT:"):
            subj = line.split(":", 1)[1].strip(); break
    return {"target": t["name"], "subject": subj or f"Guest post ideas for {t['name']}",
            "full": text}


# ── Send (gated) ──────────────────────────────────────────────────────────────
def _smtp_creds() -> tuple[str, str]:
    sender = os.getenv("GMAIL_SENDER", "romy@taxcasereview.org")
    pwd    = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
    return sender, pwd


def send_email(to_email: str, subject: str, body: str) -> bool:
    sender, pwd = _smtp_creds()
    if not pwd or not to_email:
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Romy Cruz <{sender}>"
    msg["To"] = to_email
    msg["Reply-To"] = "romy@taxcasereview.org"
    msg.attach(MIMEText(body, "plain"))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
            s.login(sender, pwd)
            s.sendmail(sender, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"    send failed: {e}")
        return False


def _gp_rows() -> list[dict]:
    """Guest-post rows from the backlink_outreach table (system of record)."""
    return rows_for("guest_post") if rows_for else []


def _pitched_today(rows: list[dict]) -> int:
    today = date.today()
    n = 0
    for r in rows:
        pa = r.get("pitched_at")
        if pa is not None and hasattr(pa, "date") and pa.date() == today:
            n += 1
    return n


def send_pitches(limit: int, logger=None) -> dict:
    if record_outreach is None:
        print("  outreach DB unavailable — cannot record pitches")
        if logger: logger.finish({"pitches_sent": 0, "error": "no_db"})
        return {"pitches_sent": 0}
    targets = load_targets()
    rows = _gp_rows()
    already = {r["domain"] for r in rows if r.get("pitched_at")}
    budget = min(limit, DAILY_PITCH_CAP - _pitched_today(rows))
    if budget <= 0:
        print(f"  Daily cap reached ({DAILY_PITCH_CAP}/day). Nothing sent.")
        if logger: logger.finish({"pitches_sent": 0, "cap_reached": True})
        return {"pitches_sent": 0}
    sent = 0
    for t in targets:
        if sent >= budget:
            break
        if t["domain"] in already:
            continue
        to = t.get("editor_email", "")
        if not to:
            print(f"  [skip] {t['name']}: no editor_email (run --research first)")
            continue
        pitch = generate_pitch(t)
        ok = send_email(to, pitch["subject"], pitch["full"].split("BODY:", 1)[-1].strip())
        record_outreach(t["domain"], "guest_post", contact_email=to,
                        subject=pitch["subject"],
                        pitched_at=date.today().isoformat() if ok else None,
                        status="pitched" if ok else "send_failed")
        if ok:
            sent += 1
            print(f"  [sent] {t['name']} -> {to}")
    print(f"\n  Pitches sent: {sent} (cap {DAILY_PITCH_CAP}/day)")
    if logger: logger.finish({"pitches_sent": sent})
    return {"pitches_sent": sent}


def send_followups(logger=None) -> dict:
    if record_outreach is None:
        if logger: logger.finish({"followups_sent": 0, "error": "no_db"})
        return {"followups_sent": 0}
    rows   = _gp_rows()
    cutoff = date.today() - timedelta(days=FOLLOWUP_DAYS)
    names  = {t["domain"]: t.get("name", t["domain"]) for t in load_targets()}
    sent = 0
    for r in rows:
        pa = r.get("pitched_at")
        if (r.get("status") != "pitched" or not pa or not r.get("contact_email")
                or not hasattr(pa, "date") or pa.date() > cutoff):
            continue
        name = names.get(r["domain"], r["domain"])
        body = (f"Hi — following up on my note about guest article ideas for "
                f"{name}'s audience on IRS tax-lien risks for contractors and small "
                f"businesses. Happy to send a full draft on whichever angle fits. "
                f"Thank you! — Romy Cruz, TaxCase Review")
        if send_email(r["contact_email"], f"Following up: guest post ideas for {name}", body):
            record_outreach(r["domain"], "guest_post", status="followed_up")
            sent += 1
            print(f"  [follow-up] {name}")
    print(f"\n  Follow-ups sent: {sent}")
    if logger: logger.finish({"followups_sent": sent})
    return {"followups_sent": sent}


def draft_article(target_name: str, topic: str) -> str:
    targets = {t["name"]: t for t in load_targets()}
    t = targets.get(target_name, {"name": target_name, "category": "small_business",
                                  "guidelines_url": ""})
    guide = f"\nFollow this publication's style guide if relevant: {t.get('guidelines_url','')}" if t.get("guidelines_url") else ""
    prompt = f"""Write a complete, publication-ready guest article for {target_name}.
Author: {ROMY_BIO}
Agreed topic: "{topic}"
Audience: {t.get('category','small_business')} readers.{guide}

900-1100 words, with an H1 title, subheads, concrete IRS-resolution guidance,
IRS Data Book FY2025 figures where relevant, and a 1-2 sentence author bio at the
end. Factual and useful, not promotional. Output markdown only."""
    return call_claude(prompt, max_tokens=2200)


def sample_pitches(n: int) -> None:
    targets = load_targets()
    # one from each distinct category for variety, then fill to n
    seen, picks = set(), []
    for t in targets:
        if t["category"] not in seen:
            picks.append(t); seen.add(t["category"])
        if len(picks) >= n:
            break
    for t in targets:
        if len(picks) >= n:
            break
        if t not in picks:
            picks.append(t)
    for i, t in enumerate(picks[:n], 1):
        p = generate_pitch(t)
        print("\n" + "=" * 72)
        print(f"SAMPLE {i}/{n} — {t['name']} ({t['category']}, {t['domain']})")
        print("=" * 72)
        print(f"SUBJECT: {p['subject']}")
        print(p["full"].split("BODY:", 1)[-1].strip())


def main() -> None:
    ap = argparse.ArgumentParser(description="Guest-post outreach pipeline")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--research", action="store_true")
    ap.add_argument("--sample-pitches", type=int, default=0)
    ap.add_argument("--send", action="store_true", help="Send pitches (gated, <=5/day)")
    ap.add_argument("--followup", action="store_true", help="Send 7-day follow-ups (gated)")
    ap.add_argument("--draft-article", action="store_true")
    ap.add_argument("--target", default="")
    ap.add_argument("--topic", default="")
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    if args.seed:
        seed_targets(force=args.force); return
    if args.sample_pitches:
        sample_pitches(args.sample_pitches); return
    if args.research:
        targets = load_targets()
        n = min(args.limit, len(targets))
        for i, t in enumerate(targets[:args.limit], 1):
            try:
                research_target(t)
            except Exception as e:
                print(f"    [research] {t['name']}: error — {e}")
            # Persist after each target so progress survives a mid-run error.
            save_targets(targets)
            print(f"    [research] saved progress ({i}/{n})")
        print(f"  Researched {n} targets."); return
    if args.draft_article:
        if not args.target or not args.topic:
            print("  --draft-article needs --target and --topic"); return
        print(draft_article(args.target, args.topic)); return

    logger = None
    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("guest_post_outreach"); logger.start()
    except Exception:
        logger = None

    if args.send:
        send_pitches(args.limit, logger=logger)
    elif args.followup:
        send_followups(logger=logger)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
