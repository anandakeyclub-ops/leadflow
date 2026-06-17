r"""
send_email_sequence.py (v4.0)
TaxCase Review 7-touch lien outreach engine.

Fixes from v2.3:
  - Stale queued rows no longer clog the pipeline.
  - Adds safe schema migrations for subject/CTA/sequence variant tracking.
  - Expands sequence from 3 touches to 7 touches.
  - Adds subject-line rotation and stores subject_variant + cta_variant.
  - Dry runs no longer insert fake queued rows by default.
  - Gmail daily-limit errors stop the run immediately instead of burning leads.
  - Connection-closed errors reconnect and retry once.
  - Spam traps, unsubscribes, and replies are permanently excluded.

Recommended install path:
  app/workers/send_email_sequence.py

Usage:
  python -m app.workers.send_email_sequence --status
  python -m app.workers.send_email_sequence --migrate-only
  python -m app.workers.send_email_sequence --auto --limit 50 --dry-run
  python -m app.workers.send_email_sequence --auto --limit 50
  python -m app.workers.send_email_sequence --step 4 --limit 50
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import smtplib
import ssl
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote, urlencode

from dotenv import load_dotenv
load_dotenv()

from app.core.db import get_connection

BASE_DIR = Path(__file__).resolve().parents[2]
LOG_FILE = BASE_DIR / "email_sequence_log.json"

SENDER_EMAIL = os.getenv("GMAIL_SENDER", "romy@taxcasereview.org")
APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
SENDER_NAME = os.getenv("GMAIL_SENDER_NAME", "Romy")
BOOKING_LINK = os.getenv("BOOKING_LINK", "https://taxcasereview.org/quiz")
TRACKING_BASE = os.getenv("TRACKING_BASE_URL", "http://localhost:8000")
# 50 matches the Gmail Workspace sending ceiling this account is throttled to
# (~56/day). Going higher just produces "550 5.4.5 Daily user sending limit
# exceeded" throttles. Raise only after migrating to a dedicated ESP (see
# docs/esp_migration_plan.md).
DAILY_LIMIT = int(os.getenv("DAILY_EMAIL_LIMIT", "50"))
CAMPAIGN_ID = os.getenv("CAMPAIGN_ID", "lien_outreach_2026")
STALE_QUEUE_HOURS = int(os.getenv("EMAIL_STALE_QUEUE_HOURS", "6"))

# Keep timing conservative. You can tighten later after deliverability improves.
STEP_DELAYS_DAYS = {
    1: 0,
    2: 3,
    3: 7,
    4: 12,
    5: 18,
    6: 25,
    7: 35,
}
MAX_STEP = 7

BUSINESS_WORDS = {
    "LLC", "INC", "CORP", "LTD", "CO", "LP", "LLP", "PA", "PL",
    "CONSTRUCTION", "SERVICES", "CONTRACTORS", "BUILDERS", "GROUP",
    "HOLDINGS", "ENTERPRISES", "SOLUTIONS", "MANAGEMENT", "PROPERTIES",
    "REALTY", "ROOFING", "ELECTRIC", "ELECTRICAL", "PLUMBING", "HVAC",
}

CONTRACTOR_TERMS = {
    "roof": "roofing contractor",
    "roofing": "roofing contractor",
    "hvac": "HVAC contractor",
    "air conditioning": "HVAC contractor",
    "a/c": "HVAC contractor",
    "electric": "electrical contractor",
    "electrical": "electrical contractor",
    "plumb": "plumbing contractor",
    "plumbing": "plumbing contractor",
    "general": "general contractor",
    "contractor": "contractor",
}


@dataclass(frozen=True)
class EmailContent:
    subject: str
    plain: str
    html: str
    subject_variant: str
    cta_variant: str
    sequence_theme: str


# ──────────────────────────────────────────────────────────────────────────────
# Tracking helpers
# ──────────────────────────────────────────────────────────────────────────────

def open_pixel_url(tracking_id: str) -> str:
    return f"{TRACKING_BASE}/t/o/{tracking_id}.gif"


def tracked_link(tracking_id: str, destination: str, extra: dict | None = None) -> str:
    destination_url = add_utm(destination, extra or {})
    encoded = quote(destination_url, safe="")
    return f"{TRACKING_BASE}/t/c/{tracking_id}?url={encoded}"


def add_utm(url: str, extra: dict) -> str:
    params = {
        "utm_source": "email",
        "utm_medium": "outreach",
        "utm_campaign": CAMPAIGN_ID,
    }
    params.update({k: v for k, v in extra.items() if v is not None and v != ""})
    sep = "&" if "?" in url else "?"
    return url + sep + urlencode(params)


# ──────────────────────────────────────────────────────────────────────────────
# Gmail
# ──────────────────────────────────────────────────────────────────────────────

def get_gmail_service():
    if not APP_PASSWORD:
        raise ValueError("GMAIL_APP_PASSWORD not set in .env")
    ctx = ssl.create_default_context()
    server = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx)
    server.login(SENDER_EMAIL, APP_PASSWORD)
    return server


def reconnect_gmail():
    return get_gmail_service()


def is_gmail_throttle_error(err: Exception | str) -> bool:
    msg = str(err).lower()
    return any(x in msg for x in [
        "daily user sending limit exceeded",
        "daily sending quota",
        "ratelimitexceeded",
        "user rate limit",
        "too many",
        "429",
        "throttl",
        "limit exceeded",
        "5.4.5",
    ])


def is_connection_error(err: Exception | str) -> bool:
    msg = str(err).lower()
    return any(x in msg for x in ["connection unexpectedly closed", "connection reset", "eof", "broken pipe", "server disconnected"])


# ──────────────────────────────────────────────────────────────────────────────
# Schema / migration
# ──────────────────────────────────────────────────────────────────────────────

def ensure_tables(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS email_sends (
            id              SERIAL PRIMARY KEY,
            lead_id         INTEGER,
            campaign_id     TEXT,
            sequence_step   INTEGER DEFAULT 1,
            to_email        TEXT,
            to_name         TEXT,
            subject         TEXT,
            tracking_id     UUID UNIQUE,
            sent_at         TIMESTAMPTZ DEFAULT NOW(),
            status          TEXT,
            error_message   TEXT,
            county_name     TEXT,
            lien_type       TEXT,
            lien_amount     NUMERIC,
            opened_at       TIMESTAMPTZ,
            clicked_at      TIMESTAMPTZ,
            reply_received  BOOLEAN DEFAULT FALSE,
            replied         BOOLEAN DEFAULT FALSE,
            unsubscribed    BOOLEAN DEFAULT FALSE
        );

        CREATE TABLE IF NOT EXISTS email_opens (
            id           SERIAL PRIMARY KEY,
            tracking_id  UUID,
            opened_at    TIMESTAMPTZ DEFAULT NOW(),
            ip_address   TEXT,
            user_agent   TEXT
        );

        CREATE TABLE IF NOT EXISTS email_clicks (
            id           SERIAL PRIMARY KEY,
            tracking_id  UUID,
            clicked_at   TIMESTAMPTZ DEFAULT NOW(),
            url          TEXT,
            ip_address   TEXT,
            user_agent   TEXT
        );
    """)

    # Safe additive migrations. Existing data is preserved.
    cur.execute("""
        ALTER TABLE email_sends ADD COLUMN IF NOT EXISTS subject_variant TEXT;
        ALTER TABLE email_sends ADD COLUMN IF NOT EXISTS cta_variant TEXT;
        ALTER TABLE email_sends ADD COLUMN IF NOT EXISTS sequence_theme TEXT;
        ALTER TABLE email_sends ADD COLUMN IF NOT EXISTS lead_trade TEXT;
        ALTER TABLE email_sends ADD COLUMN IF NOT EXISTS source_status TEXT;
        ALTER TABLE email_sends ADD COLUMN IF NOT EXISTS previous_step_sent_at TIMESTAMPTZ;
        ALTER TABLE email_sends ADD COLUMN IF NOT EXISTS cleaned_at TIMESTAMPTZ;

        CREATE INDEX IF NOT EXISTS idx_email_sends_email_campaign_step_status
            ON email_sends (to_email, campaign_id, sequence_step, status);
        CREATE INDEX IF NOT EXISTS idx_email_sends_variant
            ON email_sends (campaign_id, subject_variant, cta_variant);
        CREATE INDEX IF NOT EXISTS idx_email_sends_sent_at
            ON email_sends (sent_at);
    """)


def mark_stale_queued(cur) -> int:
    """Do not delete. Mark old queued rows stale so they no longer block sends."""
    cur.execute("""
        UPDATE email_sends
        SET status = 'stale_queued',
            cleaned_at = NOW(),
            error_message = COALESCE(error_message, '') ||
                CASE WHEN COALESCE(error_message, '') = '' THEN '' ELSE ' | ' END ||
                'Auto-marked stale_queued by send_email_sequence v4'
        WHERE campaign_id = %s
          AND status = 'queued'
          AND sent_at <= NOW() - (%s || ' hours')::interval
    """, (CAMPAIGN_ID, STALE_QUEUE_HOURS))
    return cur.rowcount


# ──────────────────────────────────────────────────────────────────────────────
# Lead helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_first_name(lead: dict) -> str:
    name = (lead.get("full_name") or lead.get("debtor_name") or "").strip()
    if not name:
        return "there"
    parts = name.split()
    if any(p.upper().rstrip(".,") in BUSINESS_WORDS for p in parts):
        return "there"
    # DBPR often stores LAST FIRST MIDDLE. Use second token when plausible.
    if len(parts) >= 2:
        candidate = parts[1].title()
        if len(candidate) > 1 and candidate.upper() not in {"JR", "SR", "II", "III"}:
            return candidate
    return parts[0].title()


def infer_trade(lead: dict) -> str:
    raw = " ".join(str(lead.get(k) or "") for k in ["trade", "full_name", "debtor_name"]).lower()
    for key, label in CONTRACTOR_TERMS.items():
        if key in raw:
            return label
    return "business owner"


def stable_choice(options: list[str], seed_parts: list[object]) -> tuple[str, int]:
    seed = "|".join(str(x) for x in seed_parts)
    digest = hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()
    idx = int(digest[:8], 16) % len(options)
    return options[idx], idx


SUBJECT_LINES: dict[int, list[str]] = {
    # Step 1: public-record awareness / curiosity.
    # WINNER ONLY. "Quick question about your {county} County filing" now at
    # 28.8% open (up from 17.2%) vs <5% for every retired variant — killed
    # s1_v2, s1_v3, s1_v4, s1_v5, s1_v6, s1_v12 (all sub-5% open). Per data,
    # step 1 no longer rotates — every contact gets the winner, personalized by
    # county. choose_subject() handles the no-county fallback (-> state, then
    # generic) and avoids "County County" duplication.
    1: [
        "Quick question about your {county} County filing",
    ],
    # Step 2: soft follow-up / credibility.
    2: [
        "Following up on the public filing",
        "Public record follow-up",
        "Still reviewing {county} County lien records",
        "One thing people miss about IRS liens",
        "Have you already resolved this?",
        "Did anyone help you with this yet?",
        "Still dealing with this tax issue?",
        "About that lien record",
    ],
    # Step 3: education/curiosity bridge.
    3: [
        "What most people misunderstand",
        "Lien vs levy — important difference",
        "This part surprises people",
        "The lien is public. The next step is private.",
        "The part most people miss",
        "Before this turns into something bigger",
    ],
    # Step 4: consequence/timeline without panic.
    4: [
        "What happens if nothing changes",
        "IRS collection timeline",
        "Before this becomes a levy issue",
        "A practical next step",
        "What the IRS usually does next",
        "Check your risk level",
    ],
    # Step 5: authority / former IRS officer angle.
    5: [
        "Former IRS officer perspective",
        "What the IRS usually looks for",
        "A case review can clarify this",
        "The IRS has more than one path",
        "What I would check first",
        "Before you call the IRS",
    ],
    # Step 6: trade/business-owner personalization.
    6: [
        "For {trade}s with IRS debt",
        "Business tax issue follow up",
        "Payroll tax and lien question",
        "If this is business-related",
        "Contractor tax issue follow-up",
        "Question about the business side",
    ],
    # Step 7: close-out / reply trigger.
    7: [
        "Last note from me",
        "Closing the loop",
        "Should I close your file?",
        "Final follow up on the public record",
        "Should I stop reaching out?",
        "Did you get this handled?",
    ],
}

CTA_LABELS = [
    "Check My Risk Level",
    "See Possible IRS Options",
    "Start 60-Second Assessment",
    "See What The IRS May Do Next",
]


# counties.state is stored as a 2-letter abbreviation; map to the full name so
# the step-1 state fallback reads naturally ("your Florida filing", not "your FL").
US_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def _state_full_name(state) -> str:
    """Full state name from an abbreviation (falls back to the raw value if it's
    not a known 2-letter code, or already a full name)."""
    s = str(state or "").strip()
    if not s or s.lower() in ("unknown", "none"):
        return ""
    return US_STATE_NAMES.get(s.upper(), s)


def choose_subject(step: int, lead: dict, first_name: str, county: str, trade: str) -> tuple[str, str]:
    # Normalize county: strip a trailing "County" so the templates' " County"
    # suffix never doubles ("Miami-Dade County" -> "Miami-Dade"), and treat the
    # build_email placeholder ("your county") / blanks as "no county".
    raw = (county or "").strip()
    if raw.lower() in ("", "your county", "your", "unknown", "none"):
        county_clean = ""
    elif raw.lower().endswith("county"):
        county_clean = raw[:-6].strip()
    else:
        county_clean = raw

    # Step 1: force the proven winner (17.2% open). Personalized by county for
    # every contact; if county is missing, fall back to the contact's state
    # ("Quick question about your Florida filing"); then a generic fallback.
    if step == 1:
        if county_clean:
            return f"Quick question about your {county_clean} County filing", "s1_v1"
        state_name = _state_full_name(lead.get("state"))
        if state_name:
            return f"Quick question about your {state_name} filing", "s1_v1_state"
        return "Quick question about your tax filing", "s1_v1_nc"

    template, idx = stable_choice(SUBJECT_LINES[step], [lead.get("lead_id"), lead.get("email"), step, CAMPAIGN_ID])
    subject = template.format(first_name=first_name, county=county_clean or "your", trade=trade)
    # Avoid ugly "there," subject.
    subject = subject.replace("there, quick question", "Quick question")
    return subject, f"s{step}_v{idx+1}"


def choose_cta(step: int, lead: dict) -> tuple[str, str]:
    label, idx = stable_choice(CTA_LABELS, [lead.get("lead_id"), lead.get("email"), step, "cta"])
    return label, f"cta_v{idx+1}"


# ──────────────────────────────────────────────────────────────────────────────
# Email copy
# ──────────────────────────────────────────────────────────────────────────────

def wrap_html(body: str, tracking_id: str, cta_url: str) -> str:
    pixel = open_pixel_url(tracking_id)
    unsub_url = tracked_link(tracking_id, f"https://taxcasereview.org/unsubscribe?tid={tracking_id}", {"utm_content": "unsubscribe"})
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:Arial,Helvetica,sans-serif;font-size:16px;color:#222;max-width:580px;margin:0 auto;padding:24px;line-height:1.65;background:#ffffff;">
{body}
<p style="font-size:13px;color:#777;margin-top:30px;">TaxCase Review · {SENDER_EMAIL} · {BOOKING_LINK}</p>
<p style="font-size:12px;color:#999;line-height:1.4;margin-top:18px;">This message references public record information. TaxCase Review is not the IRS or a government agency. Results vary by case facts. If this is not relevant, you can opt out below.</p>
<p style="margin-top:16px;font-size:12px;color:#aaa;"><a href="{unsub_url}" style="color:#999;text-decoration:underline;">Unsubscribe</a></p>
<img src="{pixel}" width="1" height="1" style="display:block;width:1px;height:1px;border:0;" alt="" />
</body></html>"""


def button_html(label: str, cta_url: str) -> str:
    return f"""<p style="margin:24px 0;">
<a href="{cta_url}" style="background:#0f1b2d;color:#d4a843;padding:13px 22px;border-radius:6px;text-decoration:none;font-weight:bold;font-size:15px;display:inline-block;">{label} →</a>
</p>"""


def build_email(step: int, lead: dict, tracking_id: str) -> EmailContent:
    first_name = get_first_name(lead)
    name = first_name or "there"
    county = lead.get("county_name") or "your county"
    lien_type = lead.get("lien_type") or "federal tax lien"
    amount = lead.get("amount") or lead.get("lien_amount")
    trade = infer_trade(lead)
    cta_label, cta_variant = choose_cta(step, lead)
    subject, subject_variant = choose_subject(step, lead, name, county, trade)
    theme = [
        "",
        "public_record_awareness",
        "common_misunderstanding",
        "case_study",
        "what_happens_next",
        "former_irs_officer_insight",
        "business_contractor_specific",
        "final_close_loop",
    ][step]

    cta_url = tracked_link(tracking_id, BOOKING_LINK, {
        "utm_content": f"step_{step}_{subject_variant}_{cta_variant}",
        "step": step,
    })
    amount_line = f" The filing amount appears to be about ${float(amount):,.0f}." if amount not in (None, "") else ""

    if step == 1:
        plain = f"""Hi {name},

I was reviewing public records in {county} County and saw a {lien_type} record that may be connected to you or your business.{amount_line}

I am not reaching out to scare you. The useful question is usually: what is the IRS likely to do next, and what options are still available?

You can start here: {BOOKING_LINK}

It takes about 60 seconds to answer the first questions.

If this is already handled, no problem. Just ignore this or unsubscribe below.

Romy
TaxCase Review | (561) 247-0678

Unsubscribe: https://taxcasereview.org/unsubscribe?tid={tracking_id}"""
        html_body = f"""
<p>Hi {name},</p>
<p>I was reviewing public records in <strong>{county} County</strong> and saw a <strong>{lien_type}</strong> record that may be connected to you or your business.{amount_line}</p>
<p>I am not reaching out to scare you. The useful question is usually:</p>
<p style="font-size:18px;color:#0f1b2d;"><strong>What is the IRS likely to do next, and what options are still available?</strong></p>
{button_html(cta_label, cta_url)}
<p>It takes about 60 seconds to answer the first questions.</p>
<p>If this is already handled, no problem.</p>
<p>Romy<br>TaxCase Review<br>(561) 247-0678</p>"""

    elif step == 2:
        plain = f"""Hi {name},

Following up on my note about the {county} County lien record.

One thing people often misunderstand: a lien and a levy are not the same thing. A lien is the public claim. A levy is when the IRS starts taking money from accounts, wages, or payments.

The better time to review options is before the levy stage.

Check your risk level here: {BOOKING_LINK}

Romy
TaxCase Review | (561) 247-0678

Unsubscribe: https://taxcasereview.org/unsubscribe?tid={tracking_id}"""
        html_body = f"""
<p>Hi {name},</p>
<p>Following up on my note about the <strong>{county} County</strong> lien record.</p>
<p>One thing people often misunderstand: <strong>a lien and a levy are not the same thing.</strong></p>
<p>A lien is the public claim. A levy is when the IRS starts taking money from accounts, wages, or payments.</p>
<p>The better time to review options is before the levy stage.</p>
{button_html(cta_label, cta_url)}
<p>Romy<br>TaxCase Review</p>"""

    elif step == 3:
        plain = f"""Hi {name},

A lot of people wait because they assume the IRS only gives two choices: pay everything now or get forced into collection.

That is not how most cases are actually resolved.

Depending on the facts, possible paths may include an installment agreement, penalty relief, currently-not-collectible status, lien withdrawal, or an Offer in Compromise.

The starting point is knowing which path fits your situation: {BOOKING_LINK}

Romy
TaxCase Review | (561) 247-0678

Unsubscribe: https://taxcasereview.org/unsubscribe?tid={tracking_id}"""
        html_body = f"""
<p>Hi {name},</p>
<p>A lot of people wait because they assume the IRS only gives two choices: pay everything now or get forced into collection.</p>
<p><strong>That is not how most cases are actually resolved.</strong></p>
<p>Depending on the facts, possible paths may include an installment agreement, penalty relief, currently-not-collectible status, lien withdrawal, or an Offer in Compromise.</p>
<p>The starting point is knowing which path fits your situation.</p>
{button_html(cta_label, cta_url)}
<p>Romy<br>TaxCase Review</p>"""

    elif step == 4:
        plain = f"""Hi {name},

If a federal tax lien stays unresolved, the biggest risk is usually not the lien itself.

It is what can come after it: levy notices, bank account levies, wage garnishments, or pressure on business cash flow.

There are usually intervention points before things get that far.

If you want to see where you may stand: {BOOKING_LINK}

Romy
TaxCase Review | (561) 247-0678

Unsubscribe: https://taxcasereview.org/unsubscribe?tid={tracking_id}"""
        html_body = f"""
<p>Hi {name},</p>
<p>If a federal tax lien stays unresolved, the biggest risk is usually not the lien itself.</p>
<p>It is what can come after it: <strong>levy notices, bank account levies, wage garnishments, or pressure on business cash flow.</strong></p>
<p>There are usually intervention points before things get that far.</p>
{button_html(cta_label, cta_url)}
<p>Romy<br>TaxCase Review</p>"""

    elif step == 5:
        plain = f"""Hi {name},

Former IRS Revenue Officers tend to look at these cases differently.

The question is not just "how much is owed?"

The better questions are: Are all returns filed? Is the IRS still inside the collection window? Is there ability to pay? Is there a lien, levy threat, or business payroll issue?

Those answers determine the real options.

You can start the review here: {BOOKING_LINK}

Romy
TaxCase Review | (561) 247-0678

Unsubscribe: https://taxcasereview.org/unsubscribe?tid={tracking_id}"""
        html_body = f"""
<p>Hi {name},</p>
<p>Former IRS Revenue Officers tend to look at these cases differently.</p>
<p>The question is not just <em>"how much is owed?"</em></p>
<p>The better questions are: Are all returns filed? Is the IRS still inside the collection window? Is there ability to pay? Is there a lien, levy threat, or business payroll issue?</p>
<p>Those answers determine the real options.</p>
{button_html(cta_label, cta_url)}
<p>Romy<br>TaxCase Review</p>"""

    elif step == 6:
        plain = f"""Hi {name},

If this is business-related, it is worth taking seriously.

For {trade}s and small business owners, IRS problems often get worse when payroll taxes, estimated taxes, or business cash flow are involved.

An LLC or corporation does not always protect the person responsible for payroll tax decisions.

If this sounds close to your situation, start here: {BOOKING_LINK}

Romy
TaxCase Review | (561) 247-0678

Unsubscribe: https://taxcasereview.org/unsubscribe?tid={tracking_id}"""
        html_body = f"""
<p>Hi {name},</p>
<p>If this is business-related, it is worth taking seriously.</p>
<p>For <strong>{trade}s</strong> and small business owners, IRS problems often get worse when payroll taxes, estimated taxes, or business cash flow are involved.</p>
<p>An LLC or corporation does not always protect the person responsible for payroll tax decisions.</p>
{button_html(cta_label, cta_url)}
<p>Romy<br>TaxCase Review</p>"""

    else:
        plain = f"""Hi {name},

Last note from me. I do not want to keep showing up in your inbox.

If the {county} County lien issue is already handled, no problem.

If it is still open and you want to understand possible IRS resolution paths, the starting point is here: {BOOKING_LINK}

Otherwise, I will leave you alone.

Romy
TaxCase Review | (561) 247-0678

Unsubscribe: https://taxcasereview.org/unsubscribe?tid={tracking_id}"""
        html_body = f"""
<p>Hi {name},</p>
<p>Last note from me. I do not want to keep showing up in your inbox.</p>
<p>If the <strong>{county} County</strong> lien issue is already handled, no problem.</p>
<p>If it is still open and you want to understand possible IRS resolution paths, the starting point is here:</p>
{button_html(cta_label, cta_url)}
<p>Otherwise, I will leave you alone.</p>
<p>Romy<br>TaxCase Review</p>"""

    html = wrap_html(html_body, tracking_id, cta_url)
    return EmailContent(subject, plain, html, subject_variant, cta_variant, theme)


# ──────────────────────────────────────────────────────────────────────────────
# Send
# ──────────────────────────────────────────────────────────────────────────────

def send_message(service, to_email: str, subject: str, plain: str, html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    msg["Subject"] = subject
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = to_email
    msg["Reply-To"] = SENDER_EMAIL
    service.sendmail(SENDER_EMAIL, to_email, msg.as_string())


# ──────────────────────────────────────────────────────────────────────────────
# Lead selection
# ──────────────────────────────────────────────────────────────────────────────

def _blocking_status_sql() -> str:
    # Stale queued rows should NOT block. Recent queued rows may be from an active/manual queue.
    return """
    (
        es.status = 'sent'
        OR es.status = 'spam_trap'
        OR es.unsubscribed = TRUE
        OR es.reply_received = TRUE
        OR (es.status = 'queued' AND es.sent_at > NOW() - (%s || ' hours')::interval)
    )
    """


def get_step1_leads(cur, limit: int, county_filter: str | None = None) -> list[dict]:
    where_county = "AND c.county_name ILIKE %s" if county_filter else ""
    params: list = [CAMPAIGN_ID, STALE_QUEUE_HOURS]
    if county_filter:
        params.append(f"%{county_filter}%")
    params.append(limit)

    cur.execute(f"""
        WITH ranked AS (
            SELECT DISTINCT ON (LOWER(ldc.email))
                ldc.id AS lead_id, ldc.email, ldc.full_name, ldc.debtor_name,
                ldc.phone, ldc.confidence, ldc.trade, ldc.lead_score,
                c.county_name, c.state, nl.lien_type, nl.filed_date, nl.amount, nl.pdf_path
            FROM lien_dbpr_contacts ldc
            JOIN normalized_liens nl ON ldc.lien_id = nl.id
            JOIN counties c ON ldc.county_id = c.id
            WHERE ldc.email IS NOT NULL
              AND ldc.email != ''
              AND ldc.email NOT LIKE '%%@example.com'
              {where_county}
              AND NOT EXISTS (
                  SELECT 1 FROM email_sends es
                  WHERE LOWER(es.to_email) = LOWER(ldc.email)
                    AND es.campaign_id = %s
                    AND {_blocking_status_sql()}
              )
            ORDER BY LOWER(ldc.email), ldc.dbpr_score DESC NULLS LAST, ldc.id ASC
        )
        -- Highest lead_score first so the best leads go out before the daily
        -- cap is hit; lead_id breaks ties for deterministic ordering.
        SELECT * FROM ranked ORDER BY lead_score DESC NULLS LAST, lead_id LIMIT %s
    """, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_followup_leads(cur, step: int, limit: int, county_filter: str | None = None) -> list[dict]:
    prev_step = step - 1
    delay_days = STEP_DELAYS_DAYS[step] - STEP_DELAYS_DAYS[prev_step]
    where_county = "AND es_prev.county_name ILIKE %s" if county_filter else ""
    params: list = [CAMPAIGN_ID, prev_step, delay_days, CAMPAIGN_ID, step, STALE_QUEUE_HOURS]
    if county_filter:
        params.append(f"%{county_filter}%")
    params.append(limit)

    cur.execute(f"""
        SELECT DISTINCT ON (LOWER(es_prev.to_email))
            es_prev.lead_id,
            es_prev.to_email AS email,
            es_prev.to_name AS full_name,
            es_prev.county_name,
            es_prev.lien_type,
            es_prev.lien_amount AS amount,
            es_prev.sent_at AS previous_step_sent_at,
            ldc.debtor_name,
            ldc.phone,
            ldc.trade
        FROM email_sends es_prev
        LEFT JOIN lien_dbpr_contacts ldc ON ldc.id = es_prev.lead_id
        WHERE es_prev.campaign_id = %s
          AND es_prev.sequence_step = %s
          AND es_prev.status = 'sent'
          AND es_prev.sent_at <= NOW() - (%s || ' days')::interval
          AND COALESCE(es_prev.reply_received, FALSE) = FALSE
          AND COALESCE(es_prev.unsubscribed, FALSE) = FALSE
          AND NOT EXISTS (
              SELECT 1 FROM email_sends es_next
              WHERE LOWER(es_next.to_email) = LOWER(es_prev.to_email)
                AND es_next.campaign_id = %s
                AND es_next.sequence_step = %s
                AND (
                    es_next.status = 'sent'
                    OR es_next.status = 'spam_trap'
                    OR es_next.unsubscribed = TRUE
                    OR es_next.reply_received = TRUE
                    OR (es_next.status = 'queued' AND es_next.sent_at > NOW() - (%s || ' hours')::interval)
                )
          )
          AND NOT EXISTS (
              SELECT 1 FROM email_sends es_bad
              WHERE LOWER(es_bad.to_email) = LOWER(es_prev.to_email)
                AND es_bad.campaign_id = es_prev.campaign_id
                AND (es_bad.status = 'spam_trap' OR es_bad.unsubscribed = TRUE OR es_bad.reply_received = TRUE)
          )
          {where_county}
        ORDER BY LOWER(es_prev.to_email), es_prev.sent_at ASC
        LIMIT %s
    """, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_leads_for_step(cur, step: int, limit: int, county_filter: str | None = None) -> list[dict]:
    if step == 1:
        return get_step1_leads(cur, limit, county_filter)
    return get_followup_leads(cur, step, limit, county_filter)


# ──────────────────────────────────────────────────────────────────────────────
# Metrics
# ──────────────────────────────────────────────────────────────────────────────

def _one(cur, sql: str, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] or 0 if row else 0


def get_pipeline_status(cur) -> dict:
    total = _one(cur, """
        SELECT COUNT(DISTINCT email) FROM lien_dbpr_contacts
        WHERE email IS NOT NULL AND email != '' AND email NOT LIKE '%%@example.com'
    """)
    steps = {}
    ready = {}
    for step in range(1, MAX_STEP + 1):
        steps[step] = _one(cur, """
            SELECT COUNT(DISTINCT to_email) FROM email_sends
            WHERE campaign_id=%s AND sequence_step=%s AND status='sent'
        """, (CAMPAIGN_ID, step))
        ready[step] = len(get_leads_for_step(cur, step, 100000)) if step > 1 else 0

    step1 = steps[1]
    replied = _one(cur, "SELECT COUNT(DISTINCT to_email) FROM email_sends WHERE campaign_id=%s AND reply_received=TRUE", (CAMPAIGN_ID,))
    unsubscribed = _one(cur, "SELECT COUNT(DISTINCT to_email) FROM email_sends WHERE campaign_id=%s AND unsubscribed=TRUE", (CAMPAIGN_ID,))
    failed = _one(cur, "SELECT COUNT(*) FROM email_sends WHERE campaign_id=%s AND status='failed'", (CAMPAIGN_ID,))
    throttled = _one(cur, "SELECT COUNT(*) FROM email_sends WHERE campaign_id=%s AND status='throttled'", (CAMPAIGN_ID,))
    spam_trap = _one(cur, "SELECT COUNT(*) FROM email_sends WHERE campaign_id=%s AND status='spam_trap'", (CAMPAIGN_ID,))
    stale_queued = _one(cur, "SELECT COUNT(*) FROM email_sends WHERE campaign_id=%s AND status='stale_queued'", (CAMPAIGN_ID,))
    queued_recent = _one(cur, """
        SELECT COUNT(*) FROM email_sends
        WHERE campaign_id=%s AND status='queued'
          AND sent_at > NOW() - (%s || ' hours')::interval
    """, (CAMPAIGN_ID, STALE_QUEUE_HOURS))
    sent_today = _one(cur, """
        SELECT COUNT(DISTINCT to_email) FROM email_sends
        WHERE campaign_id=%s AND status='sent' AND sent_at::date=CURRENT_DATE
    """, (CAMPAIGN_ID,))
    opens = _one(cur, """
        SELECT COUNT(DISTINCT es.to_email)
        FROM email_sends es JOIN email_opens eo ON eo.tracking_id = es.tracking_id
        WHERE es.campaign_id=%s
    """, (CAMPAIGN_ID,))
    clicks = _one(cur, """
        SELECT COUNT(DISTINCT es.to_email)
        FROM email_sends es JOIN email_clicks ec ON ec.tracking_id = es.tracking_id
        WHERE es.campaign_id=%s
    """, (CAMPAIGN_ID,))

    # Best/worst subject variants by open rate. Good enough for daily operational guidance.
    cur.execute("""
        SELECT COALESCE(subject_variant,'unknown') AS variant,
               COUNT(DISTINCT es.to_email) AS sent,
               COUNT(DISTINCT eo.tracking_id) AS opens,
               ROUND(COUNT(DISTINCT eo.tracking_id)::numeric / NULLIF(COUNT(DISTINCT es.to_email),0) * 100, 1) AS open_rate
        FROM email_sends es
        LEFT JOIN email_opens eo ON eo.tracking_id = es.tracking_id
        WHERE es.campaign_id=%s AND es.status='sent'
        GROUP BY COALESCE(subject_variant,'unknown')
        HAVING COUNT(DISTINCT es.to_email) >= 10
        ORDER BY open_rate DESC NULLS LAST, sent DESC
        LIMIT 5
    """, (CAMPAIGN_ID,))
    variants = [{"variant": r[0], "sent": r[1], "opens": r[2], "open_rate": float(r[3] or 0)} for r in cur.fetchall()]

    return {
        "total_contacts": total,
        "waiting": max(total - step1 - unsubscribed, 0),
        "steps": steps,
        "ready": ready,
        "replied": replied,
        "unsubscribed": unsubscribed,
        "failed": failed,
        "throttled": throttled,
        "spam_trap": spam_trap,
        "stale_queued": stale_queued,
        "queued_recent": queued_recent,
        "sent_today": sent_today,
        "opens": opens,
        "clicks": clicks,
        "open_rate": round(opens / max(step1, 1) * 100, 1),
        "click_rate": round(clicks / max(step1, 1) * 100, 1),
        "variants": variants,
    }


def print_status(status: dict) -> None:
    print("\n" + "=" * 70)
    print(f"  Email Sequence Status — {date.today().isoformat()}")
    print("=" * 70)
    print(f"  Total contacts     : {status['total_contacts']:>7,}")
    print(f"  Not contacted      : {status['waiting']:>7,}")
    for step in range(1, MAX_STEP + 1):
        ready_txt = f" | ready {status['ready'].get(step,0):,}" if step > 1 else ""
        print(f"  Email {step} sent     : {status['steps'].get(step,0):>7,}{ready_txt}")
    print(f"  Replied           : {status['replied']:>7,}")
    print(f"  Unsubscribed      : {status['unsubscribed']:>7,}")
    print(f"  Failed            : {status['failed']:>7,}")
    print(f"  Throttled         : {status['throttled']:>7,}")
    print(f"  Spam traps        : {status['spam_trap']:>7,}")
    print(f"  Stale queued      : {status['stale_queued']:>7,}")
    print(f"  Recent queued     : {status['queued_recent']:>7,}")
    print(f"  Today's sends     : {status['sent_today']:>7,}")
    print(f"  Opens             : {status['opens']:>7,} ({status['open_rate']}%)")
    print(f"  Clicks            : {status['clicks']:>7,} ({status['click_rate']}%)")
    if status.get("variants"):
        print("\n  Top subject variants:")
        for v in status["variants"]:
            print(f"    {v['variant']}: {v['open_rate']}% open ({v['opens']}/{v['sent']})")
    print("=" * 70 + "\n")


# ──────────────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────────────

def insert_send_record(cur, lead: dict, content: EmailContent, tracking_id: str, status: str, error: str = "") -> None:
    cur.execute("""
        INSERT INTO email_sends (
            lead_id, campaign_id, sequence_step, to_email, to_name, subject,
            tracking_id, status, error_message, county_name, lien_type, lien_amount,
            subject_variant, cta_variant, sequence_theme, lead_trade,
            previous_step_sent_at
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (tracking_id) DO NOTHING
    """, (
        lead.get("lead_id"), CAMPAIGN_ID, lead["sequence_step"], lead["email"],
        lead.get("full_name") or lead.get("debtor_name") or "", content.subject,
        tracking_id, status, error[:500] if error else "", lead.get("county_name"),
        lead.get("lien_type"), lead.get("amount") or lead.get("lien_amount"),
        content.subject_variant, content.cta_variant, content.sequence_theme,
        infer_trade(lead), lead.get("previous_step_sent_at"),
    ))


def run_step(step: int, leads: list[dict], service, cur, dry_run: bool, delay: float) -> tuple[int, int, bool]:
    sent = failed = 0
    stopped_for_throttle = False
    RECONNECT_EVERY = 45  # reconnect Gmail every N emails to avoid per-session throttle

    for raw_lead in leads:
        lead = dict(raw_lead)
        lead["sequence_step"] = step
        tracking_id = str(uuid.uuid4())
        content = build_email(step, lead, tracking_id)
        to_email = lead["email"]
        county = lead.get("county_name", "")

        if dry_run:
            print(f"  [DRY] Step {step} → {to_email} | {county} | {content.subject} | {content.subject_variant}/{content.cta_variant}")
            sent += 1
            continue

        # Proactive reconnect every 45 sends to avoid Gmail per-session throttle
        if sent > 0 and sent % RECONNECT_EVERY == 0:
            print(f"  ↺ Proactive reconnect after {sent} sends...")
            try:
                service = reconnect_gmail()
                print(f"  ✅ Reconnected — continuing")
                time.sleep(3)  # brief pause after reconnect
            except Exception as re:
                print(f"  ⚠ Reconnect failed: {re}")

        try:
            send_message(service, to_email, content.subject, content.plain, content.html)
            insert_send_record(cur, lead, content, tracking_id, "sent")
            print(f"  ✓ Step {step} → {to_email} | {county} | {content.subject_variant}/{content.cta_variant}")
            sent += 1
            time.sleep(delay)

        except Exception as e:
            err = str(e)[:500]
            if is_gmail_throttle_error(e):
                print("\n  ⚠ GMAIL DAILY LIMIT / THROTTLE HIT — stopping this run now.")
                insert_send_record(cur, lead, content, tracking_id, "throttled", err)
                failed += 1
                stopped_for_throttle = True
                break

            if is_connection_error(e):
                print(f"  ↺ Connection closed for {to_email}; reconnecting and retrying once...")
                try:
                    service = reconnect_gmail()
                    send_message(service, to_email, content.subject, content.plain, content.html)
                    insert_send_record(cur, lead, content, tracking_id, "sent")
                    print(f"  ✓ Step {step} → {to_email} | {county} [reconnected]")
                    sent += 1
                    time.sleep(delay)
                    continue
                except Exception as e2:
                    err = str(e2)[:500]

            insert_send_record(cur, lead, content, tracking_id, "failed", err)
            print(f"  ✗ Step {step} → {to_email} | {err[:100]}")
            failed += 1

    return sent, failed, stopped_for_throttle


def write_sequence_log(run_stats: dict) -> None:
    log = []
    if LOG_FILE.exists():
        try:
            log = json.loads(LOG_FILE.read_text())
        except Exception:
            log = []
    log.append({"date": date.today().isoformat(), "time": datetime.now().strftime("%H:%M"), **run_stats})
    LOG_FILE.write_text(json.dumps(log[-365:], indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description="TaxCase Review 7-touch email sequence v4.0")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--step", type=int, choices=list(range(1, MAX_STEP + 1)))
    parser.add_argument("--limit", type=int, default=DAILY_LIMIT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--county", default=None)
    parser.add_argument("--migrate-only", action="store_true")
    parser.add_argument("--no-cleanup", action="store_true", help="Do not auto-mark stale queued rows.")
    args = parser.parse_args()

    if not args.auto and not args.step and not args.status and not args.migrate_only:
        parser.print_help()
        return

    print("\n" + "=" * 70)
    print(f"  TaxCase Review Email Sequence v4.0 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Campaign : {CAMPAIGN_ID}")
    print(f"  From     : {SENDER_NAME} <{SENDER_EMAIL}>")
    print(f"  Tracking : {TRACKING_BASE}")
    print(f"  Mode     : {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"  Limit    : {args.limit} per step")
    print("=" * 70 + "\n")

    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("email_sequence")
        logger.start()
    except Exception:
        logger = None

    conn = get_connection()
    conn.autocommit = False
    run_stats = {"total_sent": 0, "total_failed": 0, "dry_run": args.dry_run, "steps": {}}

    try:
        with conn.cursor() as cur:
            ensure_tables(cur)
            if not args.no_cleanup:
                stale = mark_stale_queued(cur)
                if stale:
                    print(f"  ✅ Marked {stale:,} stale queued rows as stale_queued")
            conn.commit()

            if args.migrate_only:
                print("  ✅ Schema migration complete. No emails sent.")
                return

            if args.status:
                status = get_pipeline_status(cur)
                print_status(status)
                return

            service = None
            if not args.dry_run:
                service = get_gmail_service()
                print("  ✅ Gmail authenticated\n")

            # Process DEEPEST step first (7..1) and share ONE daily budget across
            # all steps. The Gmail account is capped at ~50 sends/day; step 1 has a
            # multi-thousand-contact backlog, so the old ascending order (1..7) with
            # a per-step limit let step 1 burn the entire daily quota and throttle
            # out during step 2 — steps 3-7 were never reached. Follow-ups now take
            # priority over new top-of-funnel sends (finish what we started).
            steps = [args.step] if args.step else list(range(MAX_STEP, 0, -1))
            stop_all = False
            remaining = args.limit  # shared daily budget, not per-step
            for step in steps:
                if stop_all or remaining <= 0:
                    break
                leads = get_leads_for_step(cur, step, remaining, args.county)
                print(f"\n--- Step {step} — {len(leads)} leads ready "
                      f"(daily budget left: {remaining}) ---")
                if not leads:
                    continue
                sent, failed, throttle = run_step(step, leads, service, cur, args.dry_run, args.delay)
                conn.commit()
                run_stats["steps"][step] = {"sent": sent, "failed": failed}
                run_stats["total_sent"] += sent
                run_stats["total_failed"] += failed
                remaining -= sent
                if throttle:
                    stop_all = True

            status = get_pipeline_status(cur)
            print("\n" + "=" * 70)
            print(f"  Run complete — {run_stats['total_sent']} sent, {run_stats['total_failed']} failed")
            print("=" * 70)
            print_status(status)
            run_stats.update({"status_snapshot": status})
            write_sequence_log(run_stats)

            if logger:
                logger.finish({"total_sent": run_stats["total_sent"], "total_failed": run_stats["total_failed"], "waiting": status["waiting"], "opens": status["opens"], "clicks": status["clicks"]})

    except Exception as e:
        conn.rollback()
        print(f"\nERROR: {e}")
        if logger:
            logger.finish({"error": str(e)})
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()