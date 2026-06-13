"""
review_request.py
=================
Sends automated Google review requests after paid case reviews.
Pulls completed TCR customers and sends review request email.

Usage:
  python review_request.py              # send to all new customers
  python review_request.py --dry-run   # preview only
  python review_request.py --limit 10
"""
from __future__ import annotations
import argparse, os, smtplib, time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USERNAME", "romy@taxcasereview.org")
SMTP_PASS     = os.getenv("SMTP_PASSWORD", "")
FROM_EMAIL    = os.getenv("GMAIL_SENDER", "romy@taxcasereview.org")
FROM_NAME     = os.getenv("GMAIL_SENDER_NAME", "Romy")
GOOGLE_REVIEW = os.getenv("GOOGLE_REVIEW_URL",
    "https://g.page/r/YOUR_GOOGLE_PLACE_ID/review")

try:
    from app.core.db import get_connection
    HAS_DB = True
except ImportError:
    HAS_DB = False

REVIEW_EMAIL_SUBJECT = "Quick favor — how was your TaxCase Review experience?"

REVIEW_EMAIL_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Georgia,serif;font-size:16px;color:#222;max-width:560px;margin:0 auto;padding:24px;line-height:1.7;">
<p>Hi {first_name},</p>
<p>Thank you for choosing TaxCase Review for your case review. I hope you found it valuable and came away with a clearer picture of your options.</p>
<p>If you have a moment, would you mind leaving us a quick Google review? It takes about 60 seconds and helps other Floridians in similar situations find us.</p>
<p style="margin:28px 0;">
  <a href="{review_url}" style="background:#0F1B2D;color:#D4A843;padding:14px 28px;border-radius:4px;text-decoration:none;font-weight:bold;font-size:15px;font-family:Arial,sans-serif;">
    Leave a Google Review →
  </a>
</p>
<p>No pressure at all — and if there's anything about your experience we could improve, just reply to this email and let me know directly.</p>
<p>Thank you,<br>{sender_name}<br>TaxCase Review</p>
</body></html>"""

REVIEW_EMAIL_PLAIN = """Hi {first_name},

Thank you for choosing TaxCase Review. I hope your case review was valuable.

If you have a moment, would you leave us a quick Google review?
{review_url}

It takes about 60 seconds and helps other Floridians find us.

If anything could be improved, just reply to this email.

{sender_name}
TaxCase Review"""


def get_customers_needing_review() -> list[dict]:
    """Get paid customers who haven't received a review request yet."""
    if not HAS_DB:
        return []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Check if review_requested column exists
            cur.execute("""
                ALTER TABLE tcr_leads
                ADD COLUMN IF NOT EXISTS review_requested_at TIMESTAMPTZ
            """)
            conn.commit()

            cur.execute("""
                SELECT id, email, first_name, paid_at
                FROM tcr_leads
                WHERE paid_at IS NOT NULL
                AND paid_at < NOW() - INTERVAL '48 hours'
                AND review_requested_at IS NULL
                AND unsubscribed_at IS NULL
                ORDER BY paid_at DESC
            """)
            rows = cur.fetchall()
            return [{"id": r[0], "email": r[1],
                     "first_name": r[2] or "there",
                     "paid_at": r[3]} for r in rows]
    finally:
        conn.close()


def send_review_request(customer: dict, dry_run: bool = False) -> bool:
    first = (customer.get("first_name") or "there").split()[0].title()
    subject = REVIEW_EMAIL_SUBJECT
    html = REVIEW_EMAIL_HTML.format(
        first_name=first,
        review_url=GOOGLE_REVIEW,
        sender_name=FROM_NAME,
    )
    plain = REVIEW_EMAIL_PLAIN.format(
        first_name=first,
        review_url=GOOGLE_REVIEW,
        sender_name=FROM_NAME,
    )

    if dry_run:
        print(f"  [DRY RUN] → {customer['email']} | Hi {first}")
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"]  = subject
        msg["From"]     = f"{FROM_NAME} <{FROM_EMAIL}>"
        msg["To"]       = customer["email"]
        msg["Reply-To"] = FROM_EMAIL
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo(); server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, customer["email"], msg.as_string())

        # Mark as requested
        if HAS_DB:
            conn = get_connection()
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tcr_leads SET review_requested_at=NOW() WHERE id=%s",
                    (customer["id"],))
            conn.close()

        print(f"  ✓ Sent to {customer['email']}")
        return True
    except Exception as e:
        print(f"  ✗ Failed {customer['email']}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit",   type=int, default=50)
    args = parser.parse_args()

    print(f"\n[Review Request] {'DRY RUN' if args.dry_run else 'LIVE'}")

    customers = get_customers_needing_review()[:args.limit]
    print(f"  Customers to contact: {len(customers)}")

    sent = failed = 0
    for customer in customers:
        ok = send_review_request(customer, args.dry_run)
        if ok: sent += 1
        else:  failed += 1
        time.sleep(2)

    print(f"\n  Sent: {sent} | Failed: {failed}")


if __name__ == "__main__":
    main()
