"""
scripts/bookings/retarget_abandoned_bookings.py
================================================
Checks for bookings where Calendly fired but no payment received.
Sends 3-touch retargeting sequence over 5 days.
Also sends feedback survey on day 3.

Schedule: Daily at 10:00 AM via Task Scheduler
  python scripts/bookings/retarget_abandoned_bookings.py
"""
from __future__ import annotations

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

from app.core.db import get_connection
from app.workers.send_email_sequence import send_single_email

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

SITE_URL  = "https://taxcasereview.org"
PHONE     = "(561) 247-0678"
FROM_EMAIL = os.getenv("FROM_EMAIL", "romy@taxcasereview.org")
FROM_NAME  = "Romy | TaxCase Review"


# ── Email templates ───────────────────────────────────────────────────────────

def build_retarget_email_1(booking: dict) -> dict:
    """24h after booking — complete your payment."""
    name       = booking.get("name", "").split()[0] if booking.get("name") else "there"
    county     = booking.get("lien_county", "your county")
    sched_raw  = booking.get("scheduled_at")
    sched_str  = ""
    if sched_raw:
        try:
            dt = datetime.fromisoformat(str(sched_raw).replace("Z", "+00:00"))
            sched_str = dt.strftime("%A, %B %d at %-I:%M %p ET")
        except Exception:
            sched_str = str(sched_raw)

    subject = "Your IRS case review is reserved — one step remaining"
    body = f"""Hi {name},

You scheduled a case review{f" for {sched_str}" if sched_str else ""} — but the $399 payment didn't go through.

Your appointment is still held for the next 48 hours.

Complete your payment here:
{SITE_URL}/book

If you have questions about what the case review covers before you commit, just reply to this email. I'm happy to answer.

{"Based on public records in " + county + " County, cases like yours typically need attention within the next 60–90 days." if county else ""}

Romy
TaxCase Review | {PHONE}

--
To unsubscribe reply STOP.
"""
    return {"subject": subject, "body": body}


def build_retarget_email_2(booking: dict) -> dict:
    """Day 3 — soft check-in + address objections."""
    name       = booking.get("name", "").split()[0] if booking.get("name") else "there"
    county     = booking.get("lien_county", "")
    amount     = booking.get("lien_amount")
    amount_str = f"${amount:,.0f}" if amount else ""
    feedback_url = f"{SITE_URL}/feedback?bid={booking['id']}"

    subject = "Quick question about your appointment"
    body = f"""Hi {name},

I noticed you reserved a case review but didn't complete the payment. That's completely fine — these situations can feel overwhelming and it makes sense to think it over.

{f"The public record shows a lien of {amount_str} in {county} County. " if amount_str and county else ""}I want to be straightforward: situations like yours typically escalate if nothing is done within 60–90 days. I'm not saying that to pressure you — I say it because I'd rather you know.

If cost is a concern, or if you're not sure what you'd get for $399, I'll answer any question here. Just reply to this email.

Or if you're ready to move forward:
{SITE_URL}/book

If this isn't the right time, I understand completely. Click here to tell me why — it takes 60 seconds and helps me understand how to help people like you better:
{feedback_url}

Romy
TaxCase Review | {PHONE}

--
To unsubscribe reply STOP.
"""
    return {"subject": subject, "body": body}


def build_retarget_email_3(booking: dict) -> dict:
    """Day 5 — final touch, close the loop."""
    name = booking.get("name", "").split()[0] if booking.get("name") else "there"
    feedback_url = f"{SITE_URL}/feedback?bid={booking['id']}"

    subject = "Should I close your file?"
    body = f"""Hi {name},

Last note from me on the case review you reserved.

If you've handled this another way or it's not the right time — no problem at all. I'll close the file.

If you'd still like to talk but aren't sure about paying first, I can do a free 10-minute call to answer questions before you commit. No obligation, no pitch:
https://calendly.com/taxcasereview/free-consult

Otherwise the $399 review is still available:
{SITE_URL}/book

Either way — good luck with everything.

Romy
{PHONE}

P.S. If you have a minute, I'd genuinely like to know what stopped you:
{feedback_url}

--
To unsubscribe reply STOP.
"""
    return {"subject": subject, "body": body}


# ── Main retargeting logic ────────────────────────────────────────────────────

def run_retargeting(dry_run: bool = False):
    conn = get_connection()
    now  = datetime.now(timezone.utc)

    sent_1 = sent_2 = sent_3 = 0
    errors = 0

    try:
        with conn.cursor() as cur:
            # Fetch all pending bookings older than 20 hours (give Stripe window)
            cur.execute("""
                SELECT
                    id, email, name, phone,
                    lien_county, lien_amount, scheduled_at,
                    calendly_booked_at,
                    retarget_email_1_sent,
                    retarget_email_2_sent,
                    feedback_sent
                FROM bookings
                WHERE status = 'pending'
                  AND calendly_booked_at < NOW() - INTERVAL '20 hours'
                  AND calendly_booked_at > NOW() - INTERVAL '7 days'
                ORDER BY calendly_booked_at
            """)
            pending = cur.fetchall()
            cols = ["id","email","name","phone","lien_county","lien_amount",
                    "scheduled_at","calendly_booked_at",
                    "retarget_email_1_sent","retarget_email_2_sent","feedback_sent"]
            bookings = [dict(zip(cols, row)) for row in pending]

        logger.info(f"Found {len(bookings)} pending abandoned bookings")

        for b in bookings:
            booked_at = b["calendly_booked_at"]
            if booked_at.tzinfo is None:
                booked_at = booked_at.replace(tzinfo=timezone.utc)
            hours_since = (now - booked_at).total_seconds() / 3600

            try:
                # Email 1: 20-48h after booking
                if not b["retarget_email_1_sent"] and 20 <= hours_since:
                    email_data = build_retarget_email_1(b)
                    if not dry_run:
                        send_single_email(
                            to_email=b["email"],
                            to_name=b.get("name",""),
                            subject=email_data["subject"],
                            body=email_data["body"],
                            from_email=FROM_EMAIL,
                            from_name=FROM_NAME,
                        )
                        with conn.cursor() as cur:
                            cur.execute("""
                                UPDATE bookings SET retarget_email_1_sent = TRUE,
                                updated_at = NOW() WHERE id = %s
                            """, (b["id"],))
                        conn.commit()
                    logger.info(f"  Email 1 → {b['email']} ({hours_since:.0f}h since booking)")
                    sent_1 += 1

                # Email 2 + feedback survey: 72h+ after booking
                elif (b["retarget_email_1_sent"] and not b["retarget_email_2_sent"]
                      and hours_since >= 72):
                    email_data = build_retarget_email_2(b)
                    if not dry_run:
                        send_single_email(
                            to_email=b["email"],
                            to_name=b.get("name",""),
                            subject=email_data["subject"],
                            body=email_data["body"],
                            from_email=FROM_EMAIL,
                            from_name=FROM_NAME,
                        )
                        with conn.cursor() as cur:
                            cur.execute("""
                                UPDATE bookings SET
                                    retarget_email_2_sent = TRUE,
                                    feedback_sent = TRUE,
                                    updated_at = NOW()
                                WHERE id = %s
                            """, (b["id"],))
                        conn.commit()
                    logger.info(f"  Email 2+feedback → {b['email']} ({hours_since:.0f}h)")
                    sent_2 += 1

                # Email 3: 120h+ after booking
                elif (b["retarget_email_1_sent"] and b["retarget_email_2_sent"]
                      and hours_since >= 120):
                    email_data = build_retarget_email_3(b)
                    if not dry_run:
                        send_single_email(
                            to_email=b["email"],
                            to_name=b.get("name",""),
                            subject=email_data["subject"],
                            body=email_data["body"],
                            from_email=FROM_EMAIL,
                            from_name=FROM_NAME,
                        )
                        with conn.cursor() as cur:
                            cur.execute("""
                                UPDATE bookings SET
                                    status = 'abandoned',
                                    updated_at = NOW()
                                WHERE id = %s
                            """, (b["id"],))
                        conn.commit()
                    logger.info(f"  Email 3+abandon → {b['email']} ({hours_since:.0f}h)")
                    sent_3 += 1

            except Exception as e:
                logger.error(f"  Error processing {b['email']}: {e}")
                errors += 1

    finally:
        conn.close()

    print(f"\n{'='*50}")
    print(f"  Abandoned Booking Retargeting")
    print(f"  {'DRY RUN — ' if dry_run else ''}{ datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Pending bookings found : {len(bookings)}")
    print(f"  Email 1 sent (24h)     : {sent_1}")
    print(f"  Email 2 sent (72h)     : {sent_2}")
    print(f"  Email 3 sent (120h)    : {sent_3}")
    print(f"  Errors                 : {errors}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_retargeting(dry_run=args.dry_run)