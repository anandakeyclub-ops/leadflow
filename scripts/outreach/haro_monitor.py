"""
haro_monitor.py
===============
Monitors the HARO (Help A Reporter Out) email feed in Romy's inbox, extracts
journalist queries, filters for tax/IRS/contractor relevance, drafts expert
responses via Claude, and emails the drafts to Romy for one-click forwarding.

Run modes:
  --sample            parse a built-in sample HARO email (no IMAP), show queries
  --sample --draft    also generate a Claude draft for the top query (real API)
  --run               live: IMAP-fetch today's HARO emails, draft, email Romy
  --run --no-email    live fetch + draft, but don't email (preview)

Source note: HARO was rebranded "Connectively" and the classic haro@helpareporter.com
feed may be inactive — confirm the live sender/feed before relying on --run. The
parser targets the classic HARO format and the keyword/draft logic is source-agnostic.

Scheduling (run 30 min after each HARO send, 6 AM / 1 PM / 6 PM ET):
  schtasks /Create /TN "LeadFlow - HARO Monitor" /SC DAILY /ST 06:00 /TR ^
    "python C:\\Users\\Dana\\Desktop\\leadflow\\scripts\\outreach\\haro_monitor.py --run"
  (repeat for 13:00 and 18:00, or use three tasks / a single task with multiple triggers)

HARO signup for Romy: https://www.helpareporter.com/sources/  (recommend categories
"Business & Finance" and "General"). Connectively equivalent: https://connectively.us/

Logs via PipelineLogger("haro_monitor").
"""
from __future__ import annotations

import argparse
import csv
import email
import imaplib
import os
import re
import smtplib
import ssl
from datetime import date, datetime
from email.header import decode_header
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
LOG_CSV  = BASE_DIR / "data" / "outreach" / "haro_log.csv"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Reuse the existing Gmail credentials for romy@'s mailbox (IMAP) + sending.
IMAP_USER = os.getenv("GMAIL_SENDER", "romy@taxcasereview.org")
IMAP_PASS = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
REVIEW_TO = os.getenv("HARO_REVIEW_TO", "romy@taxcasereview.org")
HARO_FROM = os.getenv("HARO_SENDER", "haro@helpareporter.com")

# ── Relevance keywords → priority ────────────────────────────────────────────────
HIGH_KEYWORDS = [
    "irs", "tax lien", "tax debt", "offer in compromise", "tax resolution",
    "back taxes", "tax relief", "installment agreement", "payroll tax",
    "irs levy", "irs garnishment", "wage garnishment", "tax levy",
]
MEDIUM_KEYWORDS = [
    "contractor", "small business tax", "self-employed tax", "tax professional",
    "enrolled agent", "cpa", "tax penalty", "1099", "quarterly taxes",
]
LOW_KEYWORDS = [
    "debt relief", "financial hardship", "business debt", "tax planning",
    "tax season", "tax filing",
]

EXPERT_BIO = (
    "Romy Cruz is a licensed tax professional and former IRS Revenue Officer "
    "with over a decade of experience representing contractors and small "
    "business owners in IRS tax resolution cases. He is the lead advisor at "
    "TaxCase Review (taxcasereview.org)."
)
CONTACT_BLOCK = ("Romy Cruz, Licensed Tax Professional & Former IRS Revenue Officer\n"
                 "TaxCase Review · https://taxcasereview.org\n"
                 "(888) 334-5052 · romy@taxcasereview.org")

LOG_COLUMNS = ["date", "outlet", "query_summary", "priority_score", "priority",
               "response_drafted", "response_sent", "resulted_in_coverage",
               "backlink_url"]


# ── HARO email parsing ────────────────────────────────────────────────────────
def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def parse_haro_email(body: str) -> list[dict]:
    """Extract individual queries from a HARO digest body. Targets the classic
    HARO layout (numbered queries with Summary/Category/Email/Media Outlet/
    Deadline/Query fields, separated by rows of asterisks)."""
    queries = []
    # Split into per-query blocks. HARO numbers each query "N) Summary:" and
    # separates with rows of '*' — handle both.
    blocks = re.split(r"\n\s*\*{3,}\s*\n", body)
    for block in blocks:
        if "Summary:" not in block and "Query:" not in block:
            continue

        def field(name: str) -> str:
            m = re.search(rf"{name}:\s*(.+?)(?:\n[A-Z][A-Za-z /&]+:|\n\n|$)",
                          block, re.S)
            return _clean(m.group(1)) if m else ""

        summary = field("Summary")
        m_id = re.search(r"\b(\d+)\)\s*Summary:", block)
        query_id = m_id.group(1) if m_id else ""
        category = field("Category")
        outlet   = field("Media Outlet") or field("Outlet")
        reporter = field("Name") or field("Reporter") or "Anonymous"
        deadline = field("Deadline")
        sub_email = field("Email")
        if not sub_email:
            em = re.search(r"[\w.\-+]+@(?:helpareporter\.com|connectively\.us|query\.[\w.\-]+)",
                           block)
            sub_email = em.group(0) if em else ""
        # Query body: text after "Query:" up to "Requirements:" or block end.
        qm = re.search(r"Query:\s*(.+?)(?:\nRequirements:|\Z)", block, re.S)
        query_text = _clean(qm.group(1)) if qm else summary

        if not (summary or query_text):
            continue
        queries.append({
            "query_id": query_id, "category": category, "outlet": outlet or "(unknown)",
            "reporter": reporter, "deadline": deadline, "submission_email": sub_email,
            "summary": summary, "query_text": query_text,
        })
    return queries


# ── Relevance scoring ─────────────────────────────────────────────────────────
def score_query(q: dict) -> tuple[str, int, list[str]]:
    blob = f"{q.get('summary','')} {q.get('query_text','')} {q.get('category','')}".lower()
    hits = []
    score = 0
    for kw in HIGH_KEYWORDS:
        if kw in blob:
            hits.append(kw); score += 10
    for kw in MEDIUM_KEYWORDS:
        if kw in blob:
            hits.append(kw); score += 4
    for kw in LOW_KEYWORDS:
        if kw in blob:
            hits.append(kw); score += 1
    if score >= 10:
        pri = "high"
    elif score >= 4:
        pri = "medium"
    elif score >= 1:
        pri = "low"
    else:
        pri = "none"
    return pri, score, hits


# ── Claude draft ──────────────────────────────────────────────────────────────
def call_claude(prompt: str, max_tokens: int = 900) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-sonnet-4-5", "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()


def draft_response(q: dict) -> str:
    prompt = f"""You are drafting a HARO pitch response for a journalist query, on behalf of:
{EXPERT_BIO}

JOURNALIST QUERY:
Outlet: {q['outlet']}
Category: {q.get('category','')}
Summary: {q.get('summary','')}
Query: {q.get('query_text','')}

Write a HARO response that:
- Opens with the expert bio (one or two sentences, as above).
- Answers the SPECIFIC question in 200-300 words with genuine expertise: cite IRS
  Data Book FY2025 figures where relevant (e.g., ~214,000 Notices of Federal Tax
  Lien filed/year; ~14% Offer in Compromise acceptance), give concrete resolution
  strategies, and include a brief real-world contractor example.
- Is factual and helpful, not promotional. No hype.
- Closes by offering availability for a follow-up interview.
Then append this contact block verbatim on its own lines:
{CONTACT_BLOCK}
Output ONLY the response text."""
    return call_claude(prompt)


# ── Email the draft to Romy ─────────────────────────────────────────────────────
def email_draft(q: dict, response_text: str) -> bool:
    sender = os.getenv("GMAIL_SUMMARY_SENDER", IMAP_USER)
    pwd    = os.getenv("GMAIL_SUMMARY_PASSWORD", IMAP_PASS).replace(" ", "")
    if not sender or not pwd:
        print("  [email] Gmail creds not set — skipping")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"HARO opportunity: {q['outlet']} — deadline {q.get('deadline','(see body)')}"
    msg["From"] = f"TaxCase Review <{sender}>"
    msg["To"] = REVIEW_TO
    body = (f"⏰ DEADLINE: {q.get('deadline','(check HARO email)')}\n"
            f"Outlet: {q['outlet']}  |  Category: {q.get('category','')}\n"
            f"Submit to: {q.get('submission_email','(see HARO email)')}\n\n"
            f"--- JOURNALIST QUERY ---\n{q.get('query_text', q.get('summary',''))}\n\n"
            f"--- DRAFTED RESPONSE (review, then forward to the submission email above) ---\n\n"
            f"{response_text}\n")
    msg.attach(MIMEText(body, "plain"))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
            s.login(sender, pwd)
            s.sendmail(sender, [REVIEW_TO], msg.as_string())
        print(f"  [email] draft sent to {REVIEW_TO}")
        return True
    except Exception as e:
        print(f"  [email] failed: {e}")
        return False


def log_query(q: dict, pri: str, score: int, drafted: bool, sent: bool) -> None:
    # System of record is the backlink_outreach DB table (not CSV).
    if record_outreach is None:
        return
    summary = (q.get("summary") or q.get("query_text", ""))[:120]
    outlet  = (q.get("outlet") or "unknown").lower()
    status  = "sent" if sent else ("drafted" if drafted else "reviewed")
    record_outreach(
        f"{outlet}#{summary[:24]}", "haro",
        contact_email=q.get("submission_email", ""), subject=summary, status=status,
        notes=f"priority={pri} score={score} deadline={q.get('deadline','')}",
    )


# ── IMAP fetch (live) ───────────────────────────────────────────────────────────
def fetch_haro_bodies(since_days: int = 1) -> list[str]:
    if not IMAP_PASS:
        raise RuntimeError("GMAIL_APP_PASSWORD not set for IMAP")
    bodies = []
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(IMAP_USER, IMAP_PASS)
    M.select("INBOX")
    since = (date.today()).strftime("%d-%b-%Y")
    typ, data = M.search(None, f'(FROM "{HARO_FROM}" SINCE {since})')
    for num in (data[0].split() if data and data[0] else []):
        typ, msg_data = M.fetch(num, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    bodies.append(part.get_payload(decode=True).decode("utf-8", "ignore"))
                    break
        else:
            bodies.append(msg.get_payload(decode=True).decode("utf-8", "ignore"))
    M.logout()
    return bodies


# ── Sample fixture (for --sample / demos) ────────────────────────────────────────
SAMPLE_HARO = """HARO - Help A Reporter Out
Business & Finance Queries

1) Summary: Experts on IRS tax liens for small businesses
Category: Business & Finance
Email: query-48217@helpareporter.com
Media Outlet: Business Insider
Deadline: 5:00 PM EST - June 19, 2026
Query:
I'm writing about the rise in IRS tax lien filings against small businesses and
contractors in 2026. Looking for tax professionals who can explain what triggers
a federal tax lien, how it differs from a levy, and the realistic options a small
business owner has to get one removed. Specific, actionable advice preferred.
Requirements:
Must be a licensed tax professional (CPA, EA, or tax attorney).
***************
2) Summary: Best budget travel backpacks for 2026
Category: Lifestyle
Email: query-48219@helpareporter.com
Media Outlet: Travel Weekly
Deadline: 12:00 PM EST - June 20, 2026
Query:
Looking for gear reviewers to recommend carry-on backpacks under $150.
Requirements:
Must have tested the product.
***************
3) Summary: How contractors can avoid payroll tax problems
Category: Business & Finance
Email: query-48224@helpareporter.com
Media Outlet: Construction Dive
Deadline: 9:00 AM EST - June 21, 2026
Query:
Seeking experts on payroll/941 tax compliance for construction businesses. What
are the most common mistakes that lead to Trust Fund Recovery Penalty exposure,
and how should owners fix a missed deposit before it escalates?
Requirements:
Tax or accounting background required.
***************
"""


def run(sample: bool, do_draft: bool, do_email: bool, logger=None) -> dict:
    if sample:
        bodies = [SAMPLE_HARO]
    else:
        try:
            bodies = fetch_haro_bodies()
        except Exception as e:
            print(f"  IMAP fetch failed: {e}")
            if logger: logger.finish({"error": str(e), "reviewed": 0})
            return {"reviewed": 0, "error": str(e)}

    reviewed = high = drafts_sent = 0
    for body in bodies:
        for q in parse_haro_email(body):
            reviewed += 1
            pri, score, hits = score_query(q)
            print(f"  [{pri.upper():6} {score:>2}] {q['outlet'][:24]:24} | {q['summary'][:50]}")
            if pri in ("high", "medium"):
                if pri == "high":
                    high += 1
                drafted = sent = False
                if do_draft:
                    try:
                        resp = draft_response(q)
                        drafted = True
                        if do_email:
                            sent = email_draft(q, resp)
                            if sent:
                                drafts_sent += 1
                        else:
                            # preview to stdout
                            print("\n" + "-" * 70)
                            print(f"DRAFT for {q['outlet']} (deadline {q.get('deadline','')}):\n")
                            print(resp)
                            print("-" * 70 + "\n")
                    except Exception as e:
                        print(f"    draft failed: {e}")
                log_query(q, pri, score, drafted, sent)
            else:
                log_query(q, pri, score, drafted=False, sent=False)

    print(f"\n  Reviewed {reviewed} queries | high-priority {high} | drafts emailed {drafts_sent}")
    if logger:
        logger.finish({"reviewed": reviewed, "high_priority": high,
                       "drafts_sent": drafts_sent})
    return {"reviewed": reviewed, "high_priority": high, "drafts_sent": drafts_sent}


def main() -> None:
    ap = argparse.ArgumentParser(description="HARO monitor + responder")
    ap.add_argument("--sample", action="store_true", help="Use built-in sample HARO email (no IMAP)")
    ap.add_argument("--run", action="store_true", help="Live: IMAP-fetch today's HARO emails")
    ap.add_argument("--draft", action="store_true", help="Generate Claude drafts for high/medium queries")
    ap.add_argument("--no-email", action="store_true", help="With --run: draft but don't email Romy")
    args = ap.parse_args()
    if not args.sample and not args.run:
        ap.print_help(); return

    logger = None
    if args.run:
        try:
            from pipeline_log import PipelineLogger
            logger = PipelineLogger("haro_monitor"); logger.start()
        except Exception:
            logger = None

    run(sample=args.sample,
        do_draft=args.draft or args.run,
        do_email=args.run and not args.no_email,
        logger=logger)


if __name__ == "__main__":
    main()
