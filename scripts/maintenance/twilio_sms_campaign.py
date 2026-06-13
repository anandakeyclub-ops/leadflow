"""
twilio_sms_campaign.py
======================
Sends personalized SMS campaigns via Twilio to lien contacts.

Pulls phone numbers from:
1. lien_dbpr_contacts (verified business contacts)
2. lien_skiptrace_contacts (individual skip trace)

Setup:
  pip install twilio
  Set in .env:
    TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    TWILIO_AUTH_TOKEN=your_auth_token
    TWILIO_FROM_NUMBER=+1xxxxxxxxxx

Usage:
  python twilio_sms_campaign.py --dry-run          # preview only
  python twilio_sms_campaign.py --limit 50         # send to 50
  python twilio_sms_campaign.py --county miami-dade
  python twilio_sms_campaign.py --source dbpr      # dbpr only
  python twilio_sms_campaign.py --source skiptrace # individuals only
"""
from __future__ import annotations
import argparse, os, time, csv
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

try:
    from app.core.db import get_connection
except ImportError:
    import sys; sys.exit("Run from leadflow directory")

# ── Message templates ─────────────────────────────────────────────────────────

TEMPLATES = {
    "business": (
        "Hi {first_name}, this is TaxCase Review. "
        "We found an IRS tax lien filed in {county} County. "
        "We help businesses resolve liens fast — free 2-min assessment: "
        "taxcasereview.org. Reply STOP to opt out."
    ),
    "individual": (
        "Hi {first_name}, TaxCase Review here. "
        "There's an IRS tax lien on record in {county} County. "
        "We can help you resolve it — free case review: "
        "taxcasereview.org. Reply STOP to opt out."
    ),
    "short": (
        "IRS lien on file in {county} Co. "
        "TaxCaseReview.org — free assessment. "
        "Reply STOP to opt out."
    ),
}

def clean_phone(phone: str) -> str | None:
    """Normalize phone to E.164 format (+1XXXXXXXXXX)."""
    if not phone:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+{digits}"
    return None

def get_first_name(full_name: str) -> str:
    """Extract first name from full name."""
    if not full_name:
        return "there"
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0].title()
    # Check if format is "Last First" (common in lien records)
    # Use shorter word as likely first name
    first = parts[0].title()
    return first if len(first) > 1 else parts[-1].title()

def ensure_campaign_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sms_campaign_log (
            id              SERIAL PRIMARY KEY,
            lien_id         INTEGER REFERENCES normalized_liens(id),
            to_number       TEXT NOT NULL,
            from_number     TEXT NOT NULL,
            message_sid     TEXT,
            status          TEXT,
            debtor_name     TEXT,
            county          TEXT,
            source          TEXT,
            message_body    TEXT,
            sent_at         TIMESTAMPTZ DEFAULT NOW(),
            error_message   TEXT
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_sms_log_lien
        ON sms_campaign_log(lien_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_sms_log_number
        ON sms_campaign_log(to_number)
    """)

def get_contacts(cur, args) -> list[dict]:
    contacts = []

    county_filter = f"AND c.county_name ILIKE '%{args.county}%'" if args.county else ""

    # ── DBPR contacts ────────────────────────────────────────────────────────
    if args.source in ("all", "dbpr"):
        cur.execute(f"""
            SELECT
                nl.id as lien_id,
                d.full_name,
                d.phone,
                d.email,
                c.county_name,
                nl.lien_type,
                nl.filed_date,
                'dbpr' as source
            FROM lien_dbpr_contacts d
            JOIN normalized_liens nl ON nl.id = d.lien_id
            JOIN counties c ON c.id = nl.county_id
            WHERE d.phone IS NOT NULL
            AND d.phone != ''
            AND d.email IS NOT NULL
            {county_filter}
            AND nl.id NOT IN (
                SELECT lien_id FROM sms_campaign_log WHERE status = 'sent'
            )
            ORDER BY nl.filed_date DESC NULLS LAST
            LIMIT {args.limit}
        """)
        for row in cur.fetchall():
            contacts.append({
                "lien_id":    row[0],
                "name":       row[1] or "",
                "phone":      row[2],
                "email":      row[3],
                "county":     row[4],
                "lien_type":  row[5],
                "filed_date": row[6],
                "source":     "dbpr",
                "template":   "business",
            })

    # ── Skip trace contacts ──────────────────────────────────────────────────
    if args.source in ("all", "skiptrace"):
        remaining = args.limit - len(contacts)
        if remaining > 0:
            try:
                cur.execute(f"""
                    SELECT
                        nl.id as lien_id,
                        s.debtor_name,
                        s.phone,
                        NULL as email,
                        c.county_name,
                        nl.lien_type,
                        nl.filed_date,
                        'skiptrace' as source
                    FROM lien_skiptrace_contacts s
                    JOIN normalized_liens nl ON nl.id = s.normalized_lien_id
                    JOIN counties c ON c.id = nl.county_id
                    WHERE s.phone IS NOT NULL
                    AND s.phone != ''
                    {county_filter}
                    AND nl.id NOT IN (
                        SELECT lien_id FROM sms_campaign_log WHERE status = 'sent'
                    )
                    ORDER BY nl.filed_date DESC NULLS LAST
                    LIMIT {remaining}
                """)
                for row in cur.fetchall():
                    contacts.append({
                        "lien_id":    row[0],
                        "name":       row[1] or "",
                        "phone":      row[2],
                        "email":      row[3],
                        "county":     row[4],
                        "lien_type":  row[5],
                        "filed_date": row[6],
                        "source":     "skiptrace",
                        "template":   "individual",
                    })
            except Exception as e:
                print(f"  Skip trace table not ready: {e}")

    return contacts

def send_sms(client, from_number: str, to_number: str, body: str) -> dict:
    """Send SMS via Twilio."""
    try:
        msg = client.messages.create(
            body=body,
            from_=from_number,
            to=to_number,
        )
        return {"sid": msg.sid, "status": msg.status, "error": None}
    except Exception as e:
        return {"sid": None, "status": "failed", "error": str(e)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",  action="store_true",
                        help="Preview messages without sending")
    parser.add_argument("--limit",    type=int,  default=50)
    parser.add_argument("--county",   default=None)
    parser.add_argument("--source",   default="all",
                        choices=["all", "dbpr", "skiptrace"])
    parser.add_argument("--template", default="auto",
                        choices=["auto", "business", "individual", "short"])
    parser.add_argument("--delay",    type=float, default=1.0,
                        help="Seconds between messages (default 1.0)")
    args = parser.parse_args()

    # Twilio credentials
    account_sid  = os.getenv("TWILIO_ACCOUNT_SID", "")
    auth_token   = os.getenv("TWILIO_AUTH_TOKEN", "")
    from_number  = os.getenv("TWILIO_FROM_NUMBER", "")

    if not args.dry_run:
        if not all([account_sid, auth_token, from_number]):
            print("ERROR: Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
                  "TWILIO_FROM_NUMBER in .env")
            return
        try:
            from twilio.rest import Client
            client = Client(account_sid, auth_token)
        except ImportError:
            print("ERROR: pip install twilio")
            return
    else:
        client = None

    conn = get_connection(); conn.autocommit = False

    try:
        with conn.cursor() as cur:
            ensure_campaign_table(cur)
            conn.commit()
            contacts = get_contacts(cur, args)

        print(f"\n[Twilio SMS Campaign]")
        print(f"  Mode      : {'DRY RUN' if args.dry_run else 'LIVE'}")
        print(f"  Contacts  : {len(contacts)}")
        print(f"  Source    : {args.source}")
        print(f"  From      : {from_number or 'not set'}")
        print(f"  Delay     : {args.delay}s between messages")
        print()

        if not contacts:
            print("  No contacts found — run enrichment first")
            return

        sent = failed = skipped = 0
        preview_rows = []

        for i, contact in enumerate(contacts):
            phone = clean_phone(contact["phone"])
            if not phone:
                skipped += 1
                continue

            first_name = get_first_name(contact["name"])
            county     = (contact["county"] or "").replace(" County", "")
            tmpl_key   = (args.template if args.template != "auto"
                          else contact["template"])
            body = TEMPLATES[tmpl_key].format(
                first_name=first_name,
                county=county,
            )

            print(f"  [{i+1}/{len(contacts)}] {contact['name'][:30]} | "
                  f"{phone} | {county}")
            print(f"    {body[:80]}...")

            if args.dry_run:
                preview_rows.append({
                    "name": contact["name"], "phone": phone,
                    "county": county, "message": body, "source": contact["source"]
                })
                continue

            result = send_sms(client, from_number, phone, body)
            time.sleep(args.delay)

            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO sms_campaign_log
                        (lien_id, to_number, from_number, message_sid,
                         status, debtor_name, county, source, message_body,
                         error_message)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    contact["lien_id"], phone, from_number,
                    result["sid"], result["status"],
                    contact["name"], county, contact["source"],
                    body, result["error"]
                ))
            conn.commit()

            if result["status"] in ("queued", "sent", "delivered"):
                sent += 1
                print(f"    ✓ {result['sid']}")
            else:
                failed += 1
                print(f"    ✗ {result['error']}")

        # Export preview CSV
        if args.dry_run and preview_rows:
            out = Path("data/exports") / \
                f"sms_preview_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=preview_rows[0].keys())
                w.writeheader(); w.writerows(preview_rows)
            print(f"\n  Preview exported: {out}")

        print(f"\n{'='*60}")
        if args.dry_run:
            print(f"  DRY RUN — {len(preview_rows)} messages previewed")
        else:
            print(f"  Sent    : {sent}")
            print(f"  Failed  : {failed}")
            print(f"  Skipped : {skipped} (bad phone format)")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        import traceback; traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    main()
