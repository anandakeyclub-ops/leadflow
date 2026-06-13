"""
send_email_campaign.py
======================
Sends lien outreach emails via Gmail OAuth (google-auth library).
Embeds open-tracking pixel and UTM click links.
Records every send to email_sends table.

Requirements:
  pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client

Setup:
  1. Place your OAuth credentials at data/credentials/gmail_credentials.json
  2. First run opens browser for OAuth consent → saves token to data/credentials/gmail_token.json
  3. Set CAMPAIGN_ID in .env or pass --campaign flag

Usage:
  python -m app.workers.send_email_campaign
  python -m app.workers.send_email_campaign --limit 50 --campaign "april_2026"
  python -m app.workers.send_email_campaign --dry-run
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import time
import uuid
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from app.core.db import get_connection

BASE_DIR = Path(__file__).resolve().parents[2]
CREDS_PATH = BASE_DIR / "data" / "credentials" / "gmail_credentials.json"
TOKEN_PATH = BASE_DIR / "data" / "credentials" / "gmail_token.json"

# Tracking server base URL — update when you deploy the tracker
TRACKING_BASE = os.getenv("TRACKING_BASE_URL", "https://taxcasereview.org")
SENDER_EMAIL  = os.getenv("GMAIL_SENDER", "")
SENDER_NAME   = os.getenv("GMAIL_SENDER_NAME", "Romy")
MIN_LEAD_SCORE = int(os.getenv("MIN_LEAD_SCORE", "40"))
DAILY_LIMIT    = int(os.getenv("DAILY_EMAIL_LIMIT", "100"))


# ---------------------------------------------------------------------------
# Gmail OAuth
# ---------------------------------------------------------------------------

def get_gmail_service():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
    creds = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# Email template
# ---------------------------------------------------------------------------

def build_email_html(
    to_name: str,
    debtor_name: str,
    lien_type: str,
    lien_amount: Optional[float],
    county: str,
    filed_date: Optional[str],
    tracking_id: str,
    sender_name: str,
) -> tuple[str, str]:
    """Returns (subject, html_body)."""

    amount_str = f"${lien_amount:,.0f}" if lien_amount else "an outstanding amount"
    date_str   = filed_date or "recently"
    lien_label = lien_type or "tax lien"
    first_name = (to_name or debtor_name or "").split()[0].title() or "there"

    # UTM tracking links
    cta_url_raw = "https://taxcasereview.org?utm_source=email&utm_campaign=lien_outreach"
    cta_url     = f"{TRACKING_BASE}/track/click/{tracking_id}?url={cta_url_raw}"
    pixel_url   = f"{TRACKING_BASE}/track/open/{tracking_id}.gif"

    subject = f"Urgent: {lien_label.title()} Filed Against Your Property in {county} County"

    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:Arial,sans-serif;font-size:15px;color:#222;max-width:600px;margin:0 auto;padding:20px;">

<p>Hi {first_name},</p>

<p>I'm reaching out because our records show a <strong>{lien_label}</strong> was filed against
your property in <strong>{county} County</strong> on {date_str} for {amount_str}.</p>

<p>If this lien isn't addressed, it can:</p>
<ul>
  <li>Block the sale or refinancing of your property</li>
  <li>Accrue interest and penalties over time</li>
  <li>Result in a tax certificate sale or foreclosure</li>
</ul>

<p>We specialize in <strong>tax lien case reviews</strong> and have helped hundreds of Florida
property owners resolve liens quickly and affordably.</p>

<p style="margin:25px 0;">
  <a href="{cta_url}"
     style="background:#1a56db;color:#fff;padding:14px 28px;border-radius:6px;
            text-decoration:none;font-weight:bold;font-size:16px;">
    Get Your Free Case Review →
  </a>
</p>

<p>Our review covers:</p>
<ul>
  <li>Verification of lien validity and amount</li>
  <li>Available resolution options (payment plan, dispute, settlement)</li>
  <li>Timeline and next steps specific to {county} County</li>
</ul>

<p>The review is <strong>$399</strong> and includes a full written summary with our
recommended action plan. Most clients recover far more than the review cost.</p>

<p>Reply to this email or click the button above to get started.</p>

<p>Best,<br>
{sender_name}<br>
<small style="color:#666;">
  You received this because a public lien record was filed in your county.
  <a href="{TRACKING_BASE}/unsubscribe/{tracking_id}" style="color:#666;">Unsubscribe</a>
</small>
</p>

<!-- tracking pixel -->
<img src="{pixel_url}" width="1" height="1" border="0" alt="" style="display:none;">

</body>
</html>
"""
    return subject, html


def build_plain_text(to_name: str, debtor_name: str, lien_type: str,
                     lien_amount: Optional[float], county: str,
                     filed_date: Optional[str], sender_name: str) -> str:
    amount_str = f"${lien_amount:,.0f}" if lien_amount else "an outstanding amount"
    first_name = (to_name or debtor_name or "").split()[0].title() or "there"
    return f"""Hi {first_name},

Our records show a {lien_type or 'tax lien'} was filed against your property in {county} County
on {filed_date or 'recently'} for {amount_str}.

If left unresolved, this lien can block property sales, accrue penalties, or lead to foreclosure.

We offer a $399 Tax Lien Case Review covering:
- Lien validity verification
- Resolution options (payment plan, dispute, settlement)
- County-specific next steps

Reply to this email to get started.

Best,
{sender_name}
"""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_leads_to_contact(cur, limit: int, campaign_id: str) -> list:
    """Get enriched leads from lien_dbpr_contacts not yet emailed."""
    cur.execute("""
        SELECT
            d.id               AS lead_id,
            nl.county_id,
            c.county_name,
            d.email,
            d.full_name,
            nl.debtor_name,
            nl.lien_type,
            nl.amount          AS lien_amount,
            nl.filed_date,
            d.dbpr_score       AS lead_score,
            d.dbpr_score       AS match_score
        FROM lien_dbpr_contacts d
        JOIN normalized_liens nl ON nl.id = d.lien_id
        JOIN counties c ON c.id = nl.county_id
        WHERE d.email IS NOT NULL
          AND d.email NOT LIKE '%%leadflow.invalid'
          AND d.email NOT LIKE '%%noemail%%'
          AND d.email LIKE '%%@%%'
          AND NOT EXISTS (
              SELECT 1 FROM email_sends es
              WHERE es.lead_id = d.id AND es.campaign_id = %s
          )
        GROUP BY d.id, nl.county_id, c.county_name, d.email,
                 d.full_name, nl.debtor_name, nl.lien_type,
                 nl.amount, nl.filed_date, d.dbpr_score
        ORDER BY d.dbpr_score DESC, nl.filed_date DESC
        LIMIT %s
    """, (campaign_id, limit))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def record_send(cur, lead: dict, tracking_id: str, subject: str,
                campaign_id: str, status: str, error: str = None) -> None:
    cur.execute("""
        INSERT INTO email_sends (
            lead_id, campaign_id, to_email, to_name, subject,
            tracking_id, sent_at, status, error_message,
            county_name, lien_type, lien_amount
        ) VALUES (%s,%s,%s,%s,%s,%s::uuid,NOW(),%s,%s,%s,%s,%s)
        ON CONFLICT (tracking_id) DO NOTHING
    """, (
        lead["lead_id"], campaign_id,
        lead["email"], lead["full_name"] or lead["debtor_name"],
        subject, tracking_id, status, error,
        lead["county_name"], lead["lien_type"], lead["lien_amount"],
    ))

    if status == "sent":
        pass  # lien_dbpr_contacts does not need status update


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def ensure_email_sends_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS email_sends (
            id          SERIAL PRIMARY KEY,
            lead_id     INTEGER,
            campaign_id TEXT,
            to_email    TEXT,
            to_name     TEXT,
            subject     TEXT,
            tracking_id UUID UNIQUE,
            sent_at     TIMESTAMPTZ DEFAULT NOW(),
            status      TEXT,
            error_message TEXT,
            county_name TEXT,
            lien_type   TEXT,
            lien_amount NUMERIC
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_email_sends_lead ON email_sends(lead_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_email_sends_campaign ON email_sends(campaign_id)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",    type=int, default=DAILY_LIMIT)
    parser.add_argument("--campaign", default=f"campaign_{datetime.now().strftime('%Y%m')}")
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--delay",    type=float, default=2.0,
                        help="Seconds between sends (avoid spam filters)")
    args = parser.parse_args()

    print(f"[Email Campaign] {args.campaign} | limit={args.limit} | dry_run={args.dry_run}")

    if not args.dry_run:
        service = get_gmail_service()

    conn = get_connection()
    with conn.cursor() as cur:
        ensure_email_sends_table(cur)
    conn.commit()
    sent = failed = skipped = 0

    try:
        with conn:
            with conn.cursor() as cur:
                leads = get_leads_to_contact(cur, args.limit, args.campaign)
                print(f"  Leads queued: {len(leads)}")

                for lead in leads:
                    tracking_id = str(uuid.uuid4())
                    subject, html = build_email_html(
                        to_name     = lead["full_name"] or "",
                        debtor_name = lead["debtor_name"] or "",
                        lien_type   = lead["lien_type"] or "tax lien",
                        lien_amount = lead["lien_amount"],
                        county      = lead["county_name"],
                        filed_date  = str(lead["filed_date"]) if lead["filed_date"] else None,
                        tracking_id = tracking_id,
                        sender_name = SENDER_NAME,
                    )
                    plain = build_plain_text(
                        to_name     = lead["full_name"] or "",
                        debtor_name = lead["debtor_name"] or "",
                        lien_type   = lead["lien_type"] or "tax lien",
                        lien_amount = lead["lien_amount"],
                        county      = lead["county_name"],
                        filed_date  = str(lead["filed_date"]) if lead["filed_date"] else None,
                        sender_name = SENDER_NAME,
                    )

                    if args.dry_run:
                        print(f"  [DRY RUN] → {lead['email']} | {lead['county_name']} | score={lead['lead_score']}")
                        record_send(cur, lead, tracking_id, subject, args.campaign, "queued")
                        skipped += 1
                        continue

                    # Build MIME message
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = subject
                    msg["From"]    = f"{SENDER_NAME} <{SENDER_EMAIL}>"
                    msg["To"]      = lead["email"]
                    msg["Reply-To"] = SENDER_EMAIL
                    msg.attach(MIMEText(plain, "plain"))
                    msg.attach(MIMEText(html,  "html"))

                    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

                    try:
                        service.users().messages().send(
                            userId="me", body={"raw": raw}
                        ).execute()
                        record_send(cur, lead, tracking_id, subject, args.campaign, "sent")
                        print(f"  ✓ {lead['email']} | {lead['county_name']} | score={lead['lead_score']}")
                        sent += 1
                        time.sleep(args.delay)
                    except Exception as e:
                        err = str(e)[:200]
                        record_send(cur, lead, tracking_id, subject, args.campaign, "failed", err)
                        print(f"  ✗ {lead['email']} | {err[:60]}")
                        failed += 1

    finally:
        conn.close()

    print(f"\n--- Campaign summary ---")
    print(f"  Sent   : {sent}")
    print(f"  Failed : {failed}")
    print(f"  Queued : {skipped}")
    print(f"\nNext: python -m app.workers.daily_summary")


if __name__ == "__main__":
    main()