"""
twilio_sms_campaign.py  (v2 — AZ/GA Multi-Source Targeting)
============================================================
Sends personalized SMS campaigns via Twilio to lien contacts.

Contact sources:
  1. lien_dbpr_contacts     — FL verified business contacts (DBPR matched)
  2. lien_skiptrace_contacts — individual skip trace results
  3. arizona_roc_contacts    — AZ contractor license holders
  4. texas_tdlr_contacts     — TX contractor license holders with lien match

Usage:
  python -m scripts.maintenance.twilio_sms_campaign --dry-run
  python -m scripts.maintenance.twilio_sms_campaign --state AZ --source roc --limit 25
  python -m scripts.maintenance.twilio_sms_campaign --state GA --limit 30

Task Scheduler (Saturday 10AM, elevated):
  $action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument '/c cd /d C:\\Users\\Dana\\Desktop\\leadflow && .venv\\Scripts\\python.exe -m scripts.maintenance.twilio_sms_campaign --state AZ --source roc' `
    -WorkingDirectory "C:\\Users\\Dana\\Desktop\\leadflow"
  $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 10:00AM
  $principal = New-ScheduledTaskPrincipal -UserId "Dana" -LogonType S4U -RunLevel Highest
  Register-ScheduledTask -TaskName "LeadFlow - SMS Campaign" `
    -Action $action -Trigger $trigger -Principal $principal -Force
"""
from __future__ import annotations

import argparse
import csv
import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

try:
    from app.core.db import get_connection
except ImportError:
    import sys
    sys.exit("Run from leadflow root: python -m scripts.maintenance.twilio_sms_campaign")

# ── Config ─────────────────────────────────────────────────────────────────────
ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID", "")
AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN", "")
FROM_NUMBER   = os.getenv("TWILIO_FROM_NUMBER", "")
MAX_DAILY_SMS = int(os.getenv("TWILIO_DAILY_LIMIT", "50"))
SITE_URL      = "taxcasereview.org"

# ── Message templates ──────────────────────────────────────────────────────────
TEMPLATES = {
    "business": (
        "Hi {first_name}, this is TaxCase Review. "
        "We found an IRS tax lien filed in {county} County. "
        "We help businesses resolve liens fast - free 2-min assessment: "
        "taxcasereview.org. Reply STOP to opt out."
    ),
    "individual": (
        "Hi {first_name}, TaxCase Review here. "
        "There's an IRS tax lien on record in {county} County. "
        "We can help you resolve it - free case review: "
        "taxcasereview.org. Reply STOP to opt out."
    ),
    "short": (
        "IRS lien on file in {county} Co. "
        "TaxCaseReview.org - free assessment. "
        "Reply STOP to opt out."
    ),
    "contractor_az": (
        "Hi {first_name}, TaxCase Review here. "
        "Your AZ contractor license in {county} County shows an IRS lien. "
        "We help contractors resolve tax debt fast - "
        "free review: taxcasereview.org/arizona/{county_slug} "
        "Reply STOP to opt out."
    ),
    "business_az": (
        "Hi {first_name}, Romy here from TaxCase Review. "
        "Found an IRS lien on record in {county} County, AZ. "
        "We help contractors resolve these fast - "
        "free 60-sec review: taxcasereview.org/arizona/{county_slug} "
        "Reply STOP to opt out."
    ),
    "business_ga": (
        "Hi {first_name}, Romy here from TaxCase Review. "
        "IRS lien on record in {county} County, GA. "
        "We help business owners resolve liens and stop levies - "
        "free case review: taxcasereview.org/georgia/{county_slug} "
        "Reply STOP to opt out."
    ),
    "business_tx": (
        "Hi {first_name}, Romy from TaxCase Review. "
        "IRS lien on record in {county} County, TX. "
        "We help business owners resolve liens fast - "
        "free case review: taxcasereview.org/texas/{county_slug} "
        "Reply STOP to opt out."
    ),
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def county_to_slug(county: str) -> str:
    return (
        (county or "")
        .lower()
        .strip()
        .replace(" county", "")
        .replace(" ", "-")
        .replace(".", "")
        .replace("'", "")
    )


def clean_phone(phone: str) -> str | None:
    if not phone:
        return None
    digits = "".join(c for c in str(phone) if c.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+{digits}"
    return None


def get_first_name(full_name: str) -> str:
    if not full_name:
        return "there"
    biz_indicators = [
        "llc", "inc", "corp", "co.", "ltd", "services", "construction",
        "roofing", "hvac", "plumbing", "electric", "group", "properties",
        "trucking", "restaurant", "realty", "holdings", "enterprises",
        "floors", "masonry", "solutions", "contractors",
    ]
    lower = full_name.lower()
    if any(ind in lower for ind in biz_indicators):
        return "there"
    # Handle "LAST, FIRST" format
    if "," in full_name:
        parts = full_name.split(",", 1)
        first = parts[1].strip().split()[0].title() if parts[1].strip() else "there"
        return first if len(first) > 1 else "there"
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0].title()
    return parts[0].title()


def pick_template(state: str | None, source: str) -> str:
    if state == "AZ":
        return "contractor_az" if source == "roc" else "business_az"
    if state == "GA":
        return "business_ga"
    if state == "TX":
        return "business_tx"
    return "business" if source in ("dbpr", "roc", "tdlr") else "individual"


def format_message(template_key: str, first_name: str, county: str) -> str:
    slug = county_to_slug(county)
    tmpl = TEMPLATES.get(template_key, TEMPLATES["business"])
    return tmpl.format(
        first_name=first_name,
        county=county,
        county_slug=slug,
    )


# ── DB helpers ─────────────────────────────────────────────────────────────────

def ensure_campaign_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sms_campaign_log (
            id            SERIAL PRIMARY KEY,
            lien_id       INTEGER,
            contact_id    INTEGER,
            contact_table TEXT,
            to_number     TEXT NOT NULL,
            from_number   TEXT NOT NULL,
            message_sid   TEXT,
            status        TEXT,
            debtor_name   TEXT,
            county        TEXT,
            state         TEXT,
            source        TEXT,
            message_body  TEXT,
            sent_at       TIMESTAMPTZ DEFAULT NOW(),
            error_message TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sms_log_number ON sms_campaign_log(to_number)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sms_log_sent_at ON sms_campaign_log(sent_at)")


def get_sends_today(cur) -> int:
    cur.execute("""
        SELECT COUNT(*) FROM sms_campaign_log
        WHERE status = 'sent' AND DATE(sent_at) = CURRENT_DATE
    """)
    return cur.fetchone()[0] or 0


def get_contacts(cur, state: str | None, source: str, limit: int, county: str | None) -> list[dict]:
    contacts    = []
    seen_phones = set()  # dedup within this run

    # Already-sent phone numbers
    cur.execute("SELECT DISTINCT to_number FROM sms_campaign_log WHERE status = 'sent'")
    sent_numbers = {row[0] for row in cur.fetchall()}

    county_sql = f"AND c.county_name ILIKE '%{county}%'" if county else ""
    state_sql  = f"AND ldc.state = '{state}'" if state else ""
    remaining  = limit

    def add(contact: dict) -> bool:
        phone = clean_phone(contact["phone"])
        if not phone:
            return False
        if phone in sent_numbers or phone in seen_phones:
            return False
        seen_phones.add(phone)
        contact["phone_e164"] = phone
        contacts.append(contact)
        return True

    # ── Source 1: lien_dbpr_contacts ─────────────────────────────────────────
    if source in ("all", "dbpr") and remaining > 0:
        cur.execute(f"""
            SELECT nl.id, ldc.id, ldc.full_name, ldc.phone, ldc.email,
                   c.county_name, c.state, nl.lien_type, nl.filed_date
            FROM lien_dbpr_contacts ldc
            JOIN normalized_liens nl ON nl.id = ldc.lien_id
            JOIN counties c ON c.id = nl.county_id
            WHERE ldc.phone IS NOT NULL AND ldc.phone != ''
            {state_sql}
            {county_sql}
            ORDER BY nl.filed_date DESC NULLS LAST
            LIMIT {remaining}
        """)
        for row in cur.fetchall():
            added = add({
                "lien_id":       row[0],
                "contact_id":    row[1],
                "contact_table": "lien_dbpr_contacts",
                "name":          row[2] or "",
                "phone":         row[3],
                "email":         row[4],
                "county":        row[5],
                "state":         row[6],
                "source":        "dbpr",
            })
            if added:
                remaining -= 1

    # ── Source 2: lien_skiptrace_contacts ─────────────────────────────────────
    if source in ("all", "skiptrace") and remaining > 0:
        try:
            cur.execute(f"""
                SELECT nl.id, s.id, s.debtor_name, s.phone,
                       c.county_name, c.state, nl.lien_type
                FROM lien_skiptrace_contacts s
                JOIN normalized_liens nl ON nl.id = s.normalized_lien_id
                JOIN counties c ON c.id = nl.county_id
                WHERE s.phone IS NOT NULL AND s.phone != ''
                {f"AND c.state = '{state}'" if state else ""}
                ORDER BY nl.filed_date DESC NULLS LAST
                LIMIT {remaining}
            """)
            for row in cur.fetchall():
                added = add({
                    "lien_id":       row[0],
                    "contact_id":    row[1],
                    "contact_table": "lien_skiptrace_contacts",
                    "name":          row[2] or "",
                    "phone":         row[3],
                    "email":         None,
                    "county":        row[4],
                    "state":         row[5],
                    "source":        "skiptrace",
                })
                if added:
                    remaining -= 1
        except Exception as e:
            print(f"  Skip trace table unavailable: {e}")

    # ── Source 3: arizona_roc_contacts ───────────────────────────────────────
    if source in ("all", "roc") and remaining > 0:
        if not state or state == "AZ":
            az_county = f"AND county ILIKE '%{county}%'" if county else ""
            cur.execute(f"""
                SELECT id, business_name, owner_name, phone, county, license_class
                FROM arizona_roc_contacts
                WHERE phone IS NOT NULL AND phone != ''
                AND emailed = false
                {az_county}
                ORDER BY id
                LIMIT {remaining * 3}
            """)
            for row in cur.fetchall():
                if remaining <= 0:
                    break
                added = add({
                    "lien_id":       None,
                    "contact_id":    row[0],
                    "contact_table": "arizona_roc_contacts",
                    "name":          row[2] or row[1] or "",
                    "business_name": row[1] or "",
                    "phone":         row[3],
                    "email":         None,
                    "county":        row[4] or "Maricopa",
                    "state":         "AZ",
                    "source":        "roc",
                })
                if added:
                    remaining -= 1

    # ── Source 4: texas_tdlr_contacts ────────────────────────────────────────
    if source in ("all", "tdlr") and remaining > 0:
        if not state or state == "TX":
            tx_county = f"AND business_county ILIKE '%{county}%'" if county else ""
            cur.execute(f"""
                SELECT id, business_name, owner_name, business_phone, business_county, license_type
                FROM texas_tdlr_contacts
                WHERE business_phone IS NOT NULL AND business_phone != ''
                AND emailed = false
                AND lien_match = true
                {tx_county}
                ORDER BY RANDOM()
                LIMIT {remaining * 3}
            """)
            for row in cur.fetchall():
                if remaining <= 0:
                    break
                added = add({
                    "lien_id":       None,
                    "contact_id":    row[0],
                    "contact_table": "texas_tdlr_contacts",
                    "name":          row[2] or row[1] or "",
                    "business_name": row[1] or "",
                    "phone":         row[3],
                    "email":         None,
                    "county":        row[4] or "Harris",
                    "state":         "TX",
                    "source":        "tdlr",
                })
                if added:
                    remaining -= 1

    return contacts


# ── Send ───────────────────────────────────────────────────────────────────────

def send_sms(client, from_number: str, to_number: str, body: str) -> dict:
    try:
        msg = client.messages.create(body=body, from_=from_number, to=to_number)
        return {"sid": msg.sid, "status": msg.status, "error": None}
    except Exception as e:
        return {"sid": None, "status": "failed", "error": str(e)}


def mark_emailed(cur, contact: dict):
    table = contact.get("contact_table", "")
    cid   = contact.get("contact_id")
    if not cid:
        return
    if table == "arizona_roc_contacts":
        cur.execute("UPDATE arizona_roc_contacts SET emailed=true WHERE id=%s", (cid,))
    elif table == "texas_tdlr_contacts":
        cur.execute("UPDATE texas_tdlr_contacts SET emailed=true WHERE id=%s", (cid,))


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TaxCase Review SMS Campaign v2")
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--limit",    type=int, default=50)
    parser.add_argument("--state",    default=None, choices=["AZ", "GA", "TX", "FL"])
    parser.add_argument("--county",   default=None)
    parser.add_argument("--source",   default="all",
                        choices=["all", "dbpr", "skiptrace", "roc", "tdlr"])
    parser.add_argument("--template", default="auto",
                        choices=["auto"] + list(TEMPLATES.keys()))
    parser.add_argument("--delay",    type=float, default=1.5)
    args = parser.parse_args()

    if not args.dry_run:
        if not all([ACCOUNT_SID, AUTH_TOKEN, FROM_NUMBER]):
            print("ERROR: Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER in .env")
            return
        try:
            from twilio.rest import Client
            client = Client(ACCOUNT_SID, AUTH_TOKEN)
        except ImportError:
            print("ERROR: pip install twilio")
            return
    else:
        client = None

    logger = None
    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("sms_campaign")
        logger.start()
    except Exception:
        pass

    conn = get_connection()
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            ensure_campaign_table(cur)
            conn.commit()

            sends_today   = get_sends_today(cur)
            remaining_cap = MAX_DAILY_SMS - sends_today

            if remaining_cap <= 0 and not args.dry_run:
                print(f"  Daily SMS cap reached ({sends_today}/{MAX_DAILY_SMS}). "
                      f"Increase TWILIO_DAILY_LIMIT in .env to send more.")
                if logger:
                    logger.finish({"sent": 0, "cap_reached": True})
                return

            effective_limit = min(args.limit, remaining_cap) if not args.dry_run else args.limit
            contacts = get_contacts(cur, args.state, args.source,
                                    effective_limit, args.county)

        print(f"\n{'='*60}")
        print(f"  TaxCase Review SMS Campaign v2")
        print(f"  Mode     : {'DRY RUN' if args.dry_run else 'LIVE'}")
        print(f"  State    : {args.state or 'ALL'}")
        print(f"  Source   : {args.source}")
        print(f"  Contacts : {len(contacts)}")
        print(f"  From     : {FROM_NUMBER or 'not set'}")
        print(f"  Cap      : {sends_today}/{MAX_DAILY_SMS} sent today")
        print(f"  Delay    : {args.delay}s between messages")
        print(f"{'='*60}\n")

        if not contacts:
            print("  No eligible contacts found.")
            if logger:
                logger.finish({"sent": 0, "no_contacts": True})
            return

        sent = failed = skipped = 0
        preview_rows = []

        for i, contact in enumerate(contacts, 1):
            phone      = contact["phone_e164"]
            first_name = get_first_name(contact["name"])
            county     = (contact["county"] or "").replace(" County", "").strip()

            tmpl_key = (
                args.template if args.template != "auto"
                else pick_template(contact.get("state") or args.state, contact["source"])
            )
            body = format_message(tmpl_key, first_name, county)

            print(f"  [{i}/{len(contacts)}] {contact['name'][:28]:28} | "
                  f"{phone} | {county} | {contact['source']}")
            print(f"    {body[:110]}{'...' if len(body) > 110 else ''}")

            if args.dry_run:
                preview_rows.append({
                    "name":     contact["name"],
                    "phone":    phone,
                    "county":   county,
                    "state":    contact.get("state", args.state or ""),
                    "source":   contact["source"],
                    "template": tmpl_key,
                    "message":  body,
                })
                continue

            result = send_sms(client, FROM_NUMBER, phone, body)
            time.sleep(args.delay)

            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO sms_campaign_log
                        (lien_id, contact_id, contact_table, to_number, from_number,
                         message_sid, status, debtor_name, county, state,
                         source, message_body, error_message)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    contact.get("lien_id"),
                    contact.get("contact_id"),
                    contact.get("contact_table"),
                    phone, FROM_NUMBER,
                    result["sid"], result["status"],
                    contact["name"], county,
                    contact.get("state", args.state or ""),
                    contact["source"], body,
                    result["error"],
                ))
                if result["status"] in ("queued", "sent", "delivered"):
                    mark_emailed(cur, contact)
                conn.commit()

            if result["status"] in ("queued", "sent", "delivered"):
                sent += 1
                print(f"    ✅ {result['sid']}")
            else:
                failed += 1
                print(f"    ❌ {result['error']}")

        if args.dry_run and preview_rows:
            out = Path("data/exports") / \
                  f"sms_preview_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=preview_rows[0].keys())
                w.writeheader()
                w.writerows(preview_rows)
            print(f"\n  Preview CSV: {out}")

        print(f"\n{'='*60}")
        if args.dry_run:
            print(f"  DRY RUN — {len(preview_rows)} messages previewed")
        else:
            print(f"  Sent    : {sent}")
            print(f"  Failed  : {failed}")
            print(f"  Skipped : {skipped}")
            print(f"  Today   : {sends_today + sent}/{MAX_DAILY_SMS}")
        print(f"  State   : {args.state or 'ALL'}")
        print(f"  Source  : {args.source}")
        print(f"{'='*60}\n")

        if logger:
            logger.finish({
                "sent":      sent if not args.dry_run else 0,
                "failed":    failed,
                "skipped":   skipped,
                "previewed": len(preview_rows) if args.dry_run else 0,
                "state":     args.state or "ALL",
                "source":    args.source,
                "dry_run":   args.dry_run,
            })

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        import traceback; traceback.print_exc()
        if logger:
            logger.finish({"error": str(e), "sent": 0})
    finally:
        conn.close()


if __name__ == "__main__":
    main()
