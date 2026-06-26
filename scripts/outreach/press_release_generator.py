"""
press_release_generator.py
==========================
Auto-drafts a press release when the weekly IRS-lien intelligence report
contains newsworthy data, saves it, optionally emails it to Romy for review,
and (only when explicitly enabled) queues it to free PR distribution services.

Pipeline position: called by scripts/reports/weekly_intelligence.py AFTER the
weekly report is written. Logs via PipelineLogger("press_release").

Safety: auto-submission to PR services is DISABLED by default (SUBMIT_ENABLED).
A test/dry run generates + saves + prints the release and does NOT email or
submit. Nothing is published without an explicit flag.

CLI:
  python scripts/outreach/press_release_generator.py --dry-run          # latest report, generate+show
  python scripts/outreach/press_release_generator.py --report PATH      # specific report
  python scripts/outreach/press_release_generator.py --email            # also email Romy for review
  python scripts/outreach/press_release_generator.py --submit           # also queue to PR services (gated)
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import smtplib
import ssl
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

try:
    from scripts.outreach.outreach_db import record_outreach
except Exception:
    try:
        from outreach_db import record_outreach
    except Exception:
        record_outreach = None

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_OPS = BASE_DIR / "data" / "ops"
PR_DIR   = BASE_DIR / "data" / "outreach" / "press_releases"
LOG_CSV  = BASE_DIR / "data" / "outreach" / "press_release_log.csv"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
REVIEW_RECIPIENT  = os.getenv("PR_REVIEW_TO", "romy@taxcasereview.org")

# Auto-submission stays OFF until the output is reviewed and explicitly enabled.
SUBMIT_ENABLED = os.getenv("PR_SUBMIT_ENABLED", "false").lower() == "true"

# ── Newsworthiness thresholds ───────────────────────────────────────────────────
WOW_CHANGE_PCT   = 15.0    # week-over-week lien change (magnitude) in any state
TOTAL_LIENS_WEEK = 1500    # total new liens in a week
COUNTY_SPIKE_PCT = 50.0    # a single county's WoW spike
COUNTY_SPIKE_MIN = 100     # ...with at least this many filings (ignore tiny bases)

BUSINESS = {
    "name":    "TaxCase Review",
    "url":     "https://taxcasereview.org",
    "phone":   "(888) 334-5052",
    "contact": "Romy Cruz",
    "email":   "romy@taxcasereview.org",
}

BOILERPLATE = (
    "About TaxCase Review: TaxCase Review (taxcasereview.org) provides IRS tax "
    "resolution for contractors and small business owners across Florida, Texas, "
    "Arizona, Georgia, New York, and Illinois. Staffed by experienced Enrolled Agents and "
    "licensed tax professionals, the firm helps clients resolve federal tax liens, "
    "levies, payroll/941 tax debt, and back taxes through offers in compromise, "
    "installment agreements, penalty abatement, and lien withdrawal."
)

# Free PR distribution targets. NOTE: none of these expose a clean free public
# write-API — PRLog/OpenPR/24-7 are member web forms (PRLog also accepts member
# email submissions). "Auto-submit" therefore prepares the payload and records
# intent; actual posting stays manual until SUBMIT_ENABLED is turned on and a
# per-service integration is wired. Honest by design — see submit_to_services().
SUBMISSION_TARGETS = [
    {"name": "PRLog",            "url": "https://www.prlog.org/submit-free-press-release.html", "method": "web_form"},
    {"name": "OpenPR",           "url": "https://www.openpr.com/news/submit.html",              "method": "web_form"},
    {"name": "24-7PressRelease", "url": "https://www.24-7pressrelease.com/distribute-press-release", "method": "web_form_free_tier"},
]

LOG_COLUMNS = ["date", "headline", "state", "trigger_reasons", "drafted",
               "emailed_for_review", "submitted", "submitted_services",
               "resulted_in_coverage", "backlink_url"]


# ── Report selection + parsing ──────────────────────────────────────────────────
def find_latest_report() -> Path | None:
    reports = sorted(DATA_OPS.glob("weekly-report-*.md"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    return reports[0] if reports else None


def _to_int(s: str) -> int:
    s = (s or "").strip().lower()
    if s in ("zero", ""):
        return 0
    try:
        return int(s.replace(",", ""))
    except ValueError:
        return 0


def parse_report(path: Path) -> dict:
    """Best-effort extraction of the numbers the thresholds need. The raw text is
    also returned so the generator can pull specifics the regex misses."""
    text = path.read_text(encoding="utf-8")

    def fm(field: str) -> str:
        m = re.search(rf'^{field}:\s*"?(.*?)"?\s*$', text, re.M)
        return m.group(1).strip() if m else ""

    state    = fm("state") or "florida"
    week_of  = fm("week_of") or ""
    meta     = fm("metaDescription")
    rdate    = fm("date") or date.today().isoformat()

    # Total new liens — first integer in metaDescription, fallback to body.
    total_new = 0
    m = re.search(r"([\d,]+)\s+new\s+(?:federal\s+)?(?:IRS\s+)?(?:tax\s+)?liens", meta, re.I)
    if not m:
        m = re.search(r"(?:filed\s+)?([\d,]+)\s+(?:new\s+)?(?:federal\s+)?(?:tax\s+)?liens", text, re.I)
    if m:
        total_new = _to_int(m.group(1))

    # Week-over-week change magnitude + direction.
    wow_pct, wow_dir = 0.0, ""
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*%\s*(increase|jump|rise|surge|drop|decrease|decline)", text, re.I)
    if m:
        wow_pct = float(m.group(1).replace(",", ""))
        wow_dir = m.group(2).lower()

    # County spikes: "**X County:** N new liens ... (from M last week|up from zero)"
    counties = []
    for cm in re.finditer(
        r"\*\*([A-Za-z .'-]+?)(?:\s+County)?:\*\*\s*([\d,]+)\s+new\s+liens.*?"
        r"(?:from\s+([\d,]+|zero)\s+last week|up from\s+([\d,]+|zero))",
        text, re.I):
        name = cm.group(1).strip()
        now  = _to_int(cm.group(2))
        prev = _to_int(cm.group(3) or cm.group(4) or "0")
        counties.append({"county": name, "now": now, "prev": prev})

    # Spotlight county (e.g., "County Where It Was Worst: Fulton ... 705 new")
    spotlight = None
    sm = re.search(r"County[^\n:]*:\s*([A-Za-z .'-]+).*?\n.*?([\d,]+)\s+new\s+(?:federal\s+)?tax\s+liens",
                   text, re.I | re.S)
    if sm:
        spotlight = {"county": sm.group(1).strip(), "now": _to_int(sm.group(2))}

    return {
        "path": str(path), "state": state, "week_of": week_of, "date": rdate,
        "total_new": total_new, "wow_pct": wow_pct, "wow_dir": wow_dir,
        "counties": counties, "spotlight": spotlight, "raw": text,
    }


def evaluate_newsworthiness(d: dict) -> tuple[bool, list[str]]:
    reasons = []
    if d["wow_pct"] >= WOW_CHANGE_PCT:
        reasons.append(f"Week-over-week lien change of {d['wow_pct']:.1f}% "
                       f"({d['wow_dir'] or 'change'}) exceeds {WOW_CHANGE_PCT:.0f}%")
    if d["total_new"] > TOTAL_LIENS_WEEK:
        reasons.append(f"{d['total_new']:,} new liens this week exceeds {TOTAL_LIENS_WEEK:,}")
    for c in d["counties"]:
        if c["now"] >= COUNTY_SPIKE_MIN:
            if c["prev"] == 0:
                reasons.append(f"{c['county']} County surged to {c['now']:,} liens from zero")
            elif (c["now"] / c["prev"] - 1) * 100 >= COUNTY_SPIKE_PCT:
                pct = (c["now"] / c["prev"] - 1) * 100
                reasons.append(f"{c['county']} County spiked {pct:.0f}% WoW to {c['now']:,} liens")
    if not d["counties"] and d["spotlight"] and d["spotlight"]["now"] >= COUNTY_SPIKE_MIN:
        reasons.append(f"{d['spotlight']['county']} County concentration: "
                       f"{d['spotlight']['now']:,} liens")
    return (len(reasons) > 0, reasons)


# ── Generation ───────────────────────────────────────────────────────────────────
def call_claude(prompt: str, max_tokens: int = 1400) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-sonnet-4-5", "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()


def build_prompt(d: dict, reasons: list[str]) -> str:
    state_name = d["state"].title()
    return f"""Write a press release for TaxCase Review, an IRS tax-resolution firm.

NEWSWORTHY DATA (from this week's intelligence report):
- State: {state_name}
- Week of: {d['week_of']}
- New federal tax liens this week: {d['total_new']:,}
- Week-over-week change: {d['wow_pct']:.1f}% ({d['wow_dir'] or 'change'})
- Trigger(s): {'; '.join(reasons)}
- Source report excerpt:
\"\"\"
{d['raw'][:1800]}
\"\"\"

REQUIREMENTS — produce a complete press release in markdown, 400-500 words:
- HEADLINE: data-driven and specific, mentioning the metric and contractors/{state_name} where it fits
  (e.g., "IRS Tax Liens Against Florida Contractors Up 23% in June 2026"). Lead with the strongest,
  most contractor-relevant angle (a county spike is a stronger hook than a statewide drop).
- DATELINE: "WELLINGTON, FL — {datetime.now().strftime('%B %d, %Y')}"
- LEDE: the single key data point in one sentence.
- BODY: exactly 3 paragraphs — (1) the data and what's happening, (2) what it means for contractors
  and small business owners, (3) what TaxCase Review recommends (specific resolution paths).
- QUOTE: one quote attributed to "Romy Cruz, licensed tax professional and Enrolled Agent at TaxCase Review".
- BOILERPLATE: include this verbatim as an "About" paragraph: {BOILERPLATE}
- CONTACT block: "Romy Cruz · {BUSINESS['email']} · {BUSINESS['phone']}"
Use only the data provided; do not invent statistics. Where you cite national figures, you may
reference IRS Data Book FY2025 generally (e.g., ~14% Offer in Compromise acceptance). Keep it factual,
not hype. Output ONLY the press release."""


def extract_headline(pr_text: str) -> str:
    for line in pr_text.splitlines():
        s = line.strip().lstrip("#").strip().strip("*")
        if s:
            return s[:140]
    return "TaxCase Review IRS Lien Update"


def generate_press_release(d: dict, reasons: list[str]) -> tuple[str, str]:
    pr_text  = call_claude(build_prompt(d, reasons))
    headline = extract_headline(pr_text)
    return pr_text, headline


def save_press_release(pr_text: str, d: dict) -> Path:
    PR_DIR.mkdir(parents=True, exist_ok=True)
    out = PR_DIR / f"{d['date']}.md"
    out.write_text(pr_text + "\n", encoding="utf-8")
    return out


# ── Review email (Gmail SMTP — reuses summary/sender creds) ──────────────────────
def email_for_review(pr_text: str, headline: str, d: dict, reasons: list[str]) -> bool:
    sender = os.getenv("GMAIL_SUMMARY_SENDER", os.getenv("GMAIL_SENDER", ""))
    pwd    = os.getenv("GMAIL_SUMMARY_PASSWORD", os.getenv("GMAIL_APP_PASSWORD", "")).replace(" ", "")
    if not sender or not pwd:
        print("  [email] Gmail creds not set — skipping review email")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Press release draft for review: {headline[:80]}"
    msg["From"] = f"TaxCase Review <{sender}>"
    msg["To"] = REVIEW_RECIPIENT
    body = (f"Auto-generated from {Path(d['path']).name}.\n"
            f"Triggers: {'; '.join(reasons)}\n\n"
            f"Review, then submit to PR services if approved.\n\n"
            f"{'='*60}\n{pr_text}\n")
    msg.attach(MIMEText(body, "plain"))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
            s.login(sender, pwd)
            s.sendmail(sender, [REVIEW_RECIPIENT], msg.as_string())
        print(f"  [email] review draft sent to {REVIEW_RECIPIENT}")
        return True
    except Exception as e:
        print(f"  [email] send failed: {e}")
        return False


# ── Distribution (gated, scaffolding only) ───────────────────────────────────────
def submit_to_services(pr_text: str, headline: str) -> list[str]:
    """Queue/submit to free PR services. Gated behind SUBMIT_ENABLED — these are
    member web forms with no reliable free write-API, so until a per-service
    integration is wired this only records intent. Returns the services touched."""
    if not SUBMIT_ENABLED:
        print("  [submit] disabled (set PR_SUBMIT_ENABLED=true after review). "
              f"Would target: {', '.join(t['name'] for t in SUBMISSION_TARGETS)}")
        return []
    touched = []
    for t in SUBMISSION_TARGETS:
        # Per-service integration goes here (web-form automation or member email
        # submission). Intentionally not implemented until reviewed/approved.
        print(f"  [submit] {t['name']}: manual web form at {t['url']} "
              f"(auto-post not yet wired)")
        touched.append(t["name"])
    return touched


def log_entry(d: dict, headline: str, reasons: list[str], drafted: bool,
              emailed: bool, submitted_services: list[str]) -> None:
    # System of record is the backlink_outreach DB table (not CSV).
    if record_outreach is None or not drafted:
        return
    slug = re.sub(r"[^a-z0-9]+", "-", headline.lower())[:40]
    status = "submitted" if submitted_services else "drafted"
    record_outreach(
        f"pr-{d['date']}-{slug}", "press_release",
        subject=headline, pitched_at=d["date"] or None, status=status,
        notes=(f"state={d['state']}; emailed={'y' if emailed else 'n'}; "
               f"triggers: {' | '.join(reasons)}"),
    )


# ── Entry point used by weekly_intelligence.py ───────────────────────────────────
def maybe_generate_from_report(report_path: str | Path | None = None,
                               logger=None,
                               email_review: bool = False,
                               submit: bool = False,
                               show: bool = False) -> dict:
    path = Path(report_path) if report_path else find_latest_report()
    if not path or not path.exists():
        print("  No weekly report found.")
        if logger: logger.finish({"newsworthy": False, "error": "no report"})
        return {"newsworthy": False}

    d = parse_report(path)
    newsworthy, reasons = evaluate_newsworthiness(d)
    print(f"  Report: {path.name} | state={d['state']} total_new={d['total_new']:,} "
          f"wow={d['wow_pct']:.1f}% ({d['wow_dir'] or '—'})")
    print(f"  Newsworthy: {newsworthy} | reasons: {reasons or 'none'}")

    if not newsworthy:
        log_entry(d, "(not newsworthy)", reasons, drafted=False,
                  emailed=False, submitted_services=[])
        if logger:
            logger.finish({"newsworthy": False, "reasons": reasons,
                           "total_new": d["total_new"], "wow_pct": d["wow_pct"]})
        return {"newsworthy": False, "reasons": reasons}

    pr_text, headline = generate_press_release(d, reasons)
    out = save_press_release(pr_text, d)
    print(f"  Drafted: {headline}")
    print(f"  Saved:   {out}")
    if show:
        print("\n" + "=" * 70 + "\n" + pr_text + "\n" + "=" * 70)

    emailed = email_for_review(pr_text, headline, d, reasons) if email_review else False
    services = submit_to_services(pr_text, headline) if submit else []
    log_entry(d, headline, reasons, drafted=True, emailed=emailed,
              submitted_services=services)

    if logger:
        logger.finish({"newsworthy": True, "headline": headline,
                       "reasons": reasons, "emailed": emailed,
                       "submitted_services": services,
                       "total_new": d["total_new"], "wow_pct": d["wow_pct"]})
    return {"newsworthy": True, "headline": headline, "path": str(out),
            "emailed": emailed, "submitted_services": services}


def main() -> None:
    ap = argparse.ArgumentParser(description="Weekly press release generator")
    ap.add_argument("--report", default=None, help="Path to a weekly report (default: latest)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Generate + save + show; no email, no submit")
    ap.add_argument("--email", action="store_true", help="Email the draft to Romy for review")
    ap.add_argument("--submit", action="store_true", help="Queue to PR services (gated by PR_SUBMIT_ENABLED)")
    args = ap.parse_args()

    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("press_release")
        logger.start()
    except Exception:
        logger = None

    maybe_generate_from_report(
        report_path=args.report,
        logger=logger,
        email_review=args.email and not args.dry_run,
        submit=args.submit and not args.dry_run,
        show=True,
    )


if __name__ == "__main__":
    main()
