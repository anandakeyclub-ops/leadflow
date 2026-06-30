"""
bridge_to_email_pool.py
=======================
Bridges enriched contacts from ALL state sources into lien_dbpr_contacts
so send_email_sequence.py picks them up automatically.

Sources bridged:
  --source pdl    â†’ lien_pdl_contacts       (individuals + businesses via PDL API)
  --source tdlr   â†’ texas_tdlr_contacts     (TX licensed contractors, lien_match=TRUE)
  --source roc    â†’ arizona_roc_contacts    (AZ licensed contractors)
  --source all    â†’ all three sources

Why this exists:
  send_email_sequence.py reads ONLY from lien_dbpr_contacts.
  All other enrichment tables are dead ends without this bridge.
  This script is the connection between enrichment and outreach.

Logic per source:
  - Only bridge records that have a real email
  - Skip emails already in lien_dbpr_contacts (deduped by email)
  - Skip unsubscribed emails (email_suppression_list)
  - Sets county_id by looking up counties table
  - Sets lien_id by matching back to normalized_liens or texas_liens
  - confidence/dbpr_score mapped from source confidence fields

Usage:
  python bridge_to_email_pool.py --source all
  python bridge_to_email_pool.py --source pdl
  python bridge_to_email_pool.py --source tdlr
  python bridge_to_email_pool.py --source roc
  python bridge_to_email_pool.py --source all --dry-run
  python bridge_to_email_pool.py --stats

Schedule:
  Run daily after enrichment â€” add to Task Scheduler after email enrichment step
  Arguments: bridge_to_email_pool.py --source all
  Start in:  C:\\Users\\Dana\\Desktop\\leadflow
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

from app.core.db import get_connection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_suppressed_emails(conn) -> set:
    """Emails on unsubscribe/suppression list."""
    with conn.cursor() as cur:
        cur.execute("SELECT email FROM email_suppression_list")
        suppressed = {r[0].lower().strip() for r in cur.fetchall()}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT to_email FROM email_sends WHERE unsubscribed = TRUE"
        )
        suppressed |= {r[0].lower().strip() for r in cur.fetchall()}
    return suppressed


def get_existing_emails(conn) -> set:
    """Emails already in lien_dbpr_contacts."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT email FROM lien_dbpr_contacts "
            "WHERE email IS NOT NULL AND email != ''"
        )
        return {r[0].lower().strip() for r in cur.fetchall()}


def ensure_lien_dbpr_table(conn):
    """Make sure lien_dbpr_contacts exists (it should, but safety check)."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lien_dbpr_contacts (
                id              SERIAL PRIMARY KEY,
                lien_id         INTEGER REFERENCES normalized_liens(id),
                county_id       INTEGER NOT NULL,
                debtor_name     TEXT,
                full_name       TEXT,
                email           TEXT,
                phone           TEXT,
                mailing_address TEXT,
                city            TEXT,
                state           TEXT,
                zip             TEXT,
                license_number  TEXT,
                trade           TEXT,
                dbpr_score      NUMERIC(5,2),
                confidence      TEXT,
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (lien_id)
            );
            CREATE INDEX IF NOT EXISTS idx_lien_dbpr_email
                ON lien_dbpr_contacts (email);
            CREATE INDEX IF NOT EXISTS idx_lien_dbpr_county
                ON lien_dbpr_contacts (county_id);
        """)
    conn.commit()


def score_to_numeric(confidence: str) -> float:
    return {"high": 85.0, "medium": 60.0, "low": 40.0}.get(
        (confidence or "").lower(), 50.0
    )


# ---------------------------------------------------------------------------
# Source: lien_pdl_contacts
# ---------------------------------------------------------------------------

def bridge_pdl(conn, existing_emails: set, suppressed: set,
               dry_run: bool) -> int:
    """
    Bridge lien_pdl_contacts â†’ lien_dbpr_contacts.
    PDL records are tied to normalized_liens via normalized_lien_id.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                lpc.normalized_lien_id,
                lpc.email,
                lpc.full_name,
                lpc.debtor_name,
                lpc.phone,
                lpc.job_title   AS trade,
                nl.county_id,
                nl.filed_date
            FROM lien_pdl_contacts lpc
            JOIN normalized_liens nl ON nl.id = lpc.normalized_lien_id
            WHERE lpc.email IS NOT NULL
              AND lpc.email != ''
              AND lpc.pdl_status = 'found'
        """)
        rows = cur.fetchall()

    bridged = 0
    for lien_id, email, full_name, debtor, phone, trade, county_id, filed_date in rows:
        email_lc = email.lower().strip()
        if email_lc in existing_emails or email_lc in suppressed:
            continue

        if dry_run:
            print(f"  [PDL DRY] {email_lc} | {debtor}")
            existing_emails.add(email_lc)
            bridged += 1
            continue

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO lien_dbpr_contacts
                    (lien_id, county_id, debtor_name, full_name, email,
                     phone, trade, dbpr_score, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (lien_id) DO UPDATE SET
                    email      = EXCLUDED.email,
                    full_name  = COALESCE(EXCLUDED.full_name, lien_dbpr_contacts.full_name),
                    phone      = COALESCE(EXCLUDED.phone, lien_dbpr_contacts.phone),
                    confidence = EXCLUDED.confidence
            """, (
                lien_id, county_id,
                (debtor or "")[:250],
                (full_name or debtor or "")[:250],
                email_lc[:200],
                (phone or "")[:50],
                (trade or "")[:100],
                65.0,  # PDL match score
                "medium",
            ))
        conn.commit()
        existing_emails.add(email_lc)
        bridged += 1

    return bridged


# ---------------------------------------------------------------------------
# Source: texas_tdlr_contacts
# ---------------------------------------------------------------------------

def bridge_tdlr(conn, existing_emails: set, suppressed: set,
                dry_run: bool) -> int:
    """
    Bridge texas_tdlr_contacts (lien_match=TRUE, has email) â†’ lien_dbpr_contacts.
    TDLR contacts link back to texas_liens via tdlr_match_id.
    We need a normalized_liens row â€” create one if missing.
    """
    # Ensure Dallas (and any TX county) is in counties table
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ttc.id, ttc.email, ttc.owner_name, ttc.business_name,
                   ttc.business_phone, ttc.license_type, ttc.confidence,
                   ttc.business_city, ttc.business_county, ttc.business_zip,
                   ttc.mailing_address, ttc.business_state
            FROM texas_tdlr_contacts ttc
            WHERE ttc.lien_match = TRUE
              AND ttc.email IS NOT NULL
              AND ttc.email != ''
        """)
        rows = cur.fetchall()

    bridged = 0

    for (tdlr_id, email, owner_name, biz_name, phone, lic_type,
         confidence, city, county_name, zipcode, address, state) in rows:

        email_lc = email.lower().strip()
        if email_lc in existing_emails or email_lc in suppressed:
            continue

        full_name = biz_name or owner_name or ""
        debtor    = full_name

        # Get or create county
        county_name_clean = (county_name or "Unknown").strip()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM counties WHERE county_name = %s AND state = 'TX'",
                (county_name_clean,)
            )
            row = cur.fetchone()
            if row:
                county_id = row[0]
            else:
                cur.execute(
                    "INSERT INTO counties (county_name, state, active, created_at) "
                    "VALUES (%s, 'TX', TRUE, NOW()) RETURNING id",
                    (county_name_clean,)
                )
                county_id = cur.fetchone()[0]
                conn.commit()

        # Find a linked normalized_lien via texas_liens.tdlr_match_id
        with conn.cursor() as cur:
            cur.execute("""
                SELECT nl.id
                FROM texas_liens tl
                JOIN normalized_liens nl ON nl.lien_source = 'texas_tdlr'
                    AND nl.county_id = %s
                    AND UPPER(nl.debtor_name) = UPPER(%s)
                WHERE tl.tdlr_match_id = %s
                LIMIT 1
            """, (county_id, debtor, tdlr_id))
            nl_row = cur.fetchone()
            lien_id = nl_row[0] if nl_row else None

        # If no normalized_lien exists yet, create a placeholder
        if not lien_id:
            import hashlib
            h = hashlib.md5(f"tdlr|{tdlr_id}|TX|{county_name_clean}".encode()).hexdigest()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO normalized_liens
                        (county_id, debtor_name, business_name, filing_type,
                         lien_type, lien_source, normalized_hash, state)
                    VALUES (%s, %s, %s, 'TAX LIEN', 'TAX LIEN', 'texas_tdlr', %s, 'TX')
                    ON CONFLICT (normalized_hash) DO NOTHING
                    RETURNING id
                """, (county_id, debtor[:250], debtor[:250], h))
                ret = cur.fetchone()
                if ret:
                    lien_id = ret[0]
                else:
                    cur.execute(
                        "SELECT id FROM normalized_liens WHERE normalized_hash = %s", (h,)
                    )
                    lien_id = cur.fetchone()[0]
                conn.commit()

        if dry_run:
            print(f"  [TDLR DRY] {email_lc} | {full_name} | {county_name_clean}")
            existing_emails.add(email_lc)
            bridged += 1
            continue

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO lien_dbpr_contacts
                    (lien_id, county_id, debtor_name, full_name, email,
                     phone, mailing_address, city, state, zip,
                     trade, dbpr_score, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (lien_id) DO UPDATE SET
                    email      = EXCLUDED.email,
                    full_name  = COALESCE(EXCLUDED.full_name, lien_dbpr_contacts.full_name),
                    phone      = COALESCE(EXCLUDED.phone, lien_dbpr_contacts.phone),
                    confidence = EXCLUDED.confidence
            """, (
                lien_id, county_id,
                debtor[:250], full_name[:250],
                email_lc[:200],
                (phone or "")[:50],
                (address or "")[:200],
                (city or "")[:100],
                (state or "TX")[:10],
                (zipcode or "")[:20],
                (lic_type or "")[:100],
                score_to_numeric(confidence),
                confidence or "medium",
            ))
        conn.commit()
        existing_emails.add(email_lc)
        bridged += 1

    return bridged


# ---------------------------------------------------------------------------
# Source: arizona_roc_contacts
# ---------------------------------------------------------------------------

def bridge_roc(conn, existing_emails: set, suppressed: set,
               dry_run: bool) -> int:
    """
    Bridge arizona_roc_contacts (has email) â†’ lien_dbpr_contacts.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, email, owner_name, business_name, phone,
                   license_type, business_city, county, business_zip
            FROM arizona_roc_contacts
            WHERE email IS NOT NULL AND email != ''
        """)
        rows = cur.fetchall()

    bridged = 0

    for (roc_id, email, owner_name, biz_name, phone,
         lic_type, city, county_name, zipcode) in rows:

        email_lc = email.lower().strip()
        if email_lc in existing_emails or email_lc in suppressed:
            continue

        full_name         = biz_name or owner_name or ""
        county_name_clean = (county_name or "Unknown").strip()

        # Get or create AZ county
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM counties WHERE county_name = %s AND state = 'AZ'",
                (county_name_clean,)
            )
            row = cur.fetchone()
            if row:
                county_id = row[0]
            else:
                cur.execute(
                    "INSERT INTO counties (county_name, state, active, created_at) "
                    "VALUES (%s, 'AZ', TRUE, NOW()) RETURNING id",
                    (county_name_clean,)
                )
                county_id = cur.fetchone()[0]
                conn.commit()

        # Create normalized_lien placeholder for ROC contact
        import hashlib
        h = hashlib.md5(f"roc|{roc_id}|AZ|{county_name_clean}".encode()).hexdigest()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO normalized_liens
                    (county_id, debtor_name, business_name, filing_type,
                     lien_type, lien_source, normalized_hash, state)
                VALUES (%s, %s, %s, 'TAX LIEN', 'TAX LIEN', 'arizona_roc', %s, 'AZ')
                ON CONFLICT (normalized_hash) DO NOTHING
                RETURNING id
            """, (county_id, full_name[:250], full_name[:250], h))
            ret = cur.fetchone()
            if ret:
                lien_id = ret[0]
            else:
                cur.execute(
                    "SELECT id FROM normalized_liens WHERE normalized_hash = %s", (h,)
                )
                lien_id = cur.fetchone()[0]
            conn.commit()

        if dry_run:
            print(f"  [ROC DRY] {email_lc} | {full_name} | {county_name_clean}")
            existing_emails.add(email_lc)
            bridged += 1
            continue

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO lien_dbpr_contacts
                    (lien_id, county_id, debtor_name, full_name, email,
                     phone, city, state, zip, trade, dbpr_score, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (lien_id) DO UPDATE SET
                    email      = EXCLUDED.email,
                    full_name  = COALESCE(EXCLUDED.full_name, lien_dbpr_contacts.full_name),
                    phone      = COALESCE(EXCLUDED.phone, lien_dbpr_contacts.phone),
                    confidence = EXCLUDED.confidence
            """, (
                lien_id, county_id,
                full_name[:250], full_name[:250],
                email_lc[:200],
                (phone or "")[:50],
                (city or "")[:100],
                "AZ",
                (zipcode or "")[:20],
                (lic_type or "")[:100],
                70.0,
                "medium",
            ))
        conn.commit()
        existing_emails.add(email_lc)
        bridged += 1

    return bridged


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def show_stats(conn):
    print(f"\n{'='*60}")
    print(f"  Bridge Stats â€” Email Pool Status")
    print(f"{'='*60}")

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*)                                    AS total,
                COUNT(email)                                AS with_email,
                COUNT(CASE WHEN confidence='high'   THEN 1 END) AS high,
                COUNT(CASE WHEN confidence='medium' THEN 1 END) AS medium,
                COUNT(CASE WHEN confidence='low'    THEN 1 END) AS low
            FROM lien_dbpr_contacts
        """)
        r = cur.fetchone()
        print(f"  lien_dbpr_contacts (email pool):")
        print(f"    Total              : {r[0]:,}")
        print(f"    With email         : {r[1]:,}")
        print(f"    High confidence    : {r[2]:,}")
        print(f"    Medium confidence  : {r[3]:,}")
        print(f"    Low confidence     : {r[4]:,}")

    print()

    for label, table, email_col in [
        ("lien_pdl_contacts",      "lien_pdl_contacts",     "email"),
        ("texas_tdlr_contacts",    "texas_tdlr_contacts",   "email"),
        ("arizona_roc_contacts",   "arizona_roc_contacts",  "email"),
    ]:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT COUNT(*), COUNT({email_col})
                FROM {table}
            """)
            total, with_email = cur.fetchone()
            print(f"  {label:<30} total={total:>7,}  with_email={with_email:>6,}")

    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(DISTINCT to_email)
            FROM email_sends
            WHERE campaign_id = 'lien_outreach_2026'
              AND status = 'sent'
        """)
        sent = cur.fetchone()[0]
        print(f"\n  Already emailed (campaign)     : {sent:,}")

    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Bridge enriched contacts to email pool")
    parser.add_argument("--source",  default="all",
                        choices=["all", "pdl", "tdlr", "roc"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stats",   action="store_true")
    args = parser.parse_args()

    conn = get_connection()
    conn.autocommit = False

    try:
        ensure_lien_dbpr_table(conn)

        if args.stats:
            show_stats(conn)
            return

        suppressed      = get_suppressed_emails(conn)
        existing_emails = get_existing_emails(conn)

        print(f"\n[bridge_to_email_pool]")
        print(f"  Mode            : {'DRY RUN' if args.dry_run else 'LIVE'}")
        print(f"  Source          : {args.source}")
        print(f"  Existing pool   : {len(existing_emails):,} emails")
        print(f"  Suppressed      : {len(suppressed):,} emails\n")

        total_bridged = 0

        if args.source in ("all", "pdl"):
            print("  Bridging PDL contacts...")
            n = bridge_pdl(conn, existing_emails, suppressed, args.dry_run)
            print(f"    -> {n:,} new contacts added")
            total_bridged += n

        if args.source in ("all", "tdlr"):
            print("  Bridging TDLR contacts...")
            n = bridge_tdlr(conn, existing_emails, suppressed, args.dry_run)
            print(f"    -> {n:,} new contacts added")
            total_bridged += n

        if args.source in ("all", "roc"):
            print("  Bridging ROC contacts...")
            n = bridge_roc(conn, existing_emails, suppressed, args.dry_run)
            print(f"    -> {n:,} new contacts added")
            total_bridged += n

        print(f"\n{'='*55}")
        print(f"  BRIDGE COMPLETE")
        print(f"{'='*55}")
        print(f"  Total new contacts added to email pool: {total_bridged:,}")
        if args.dry_run:
            print(f"  [DRY RUN] No changes written.")
        print(f"\nNext step:")
        print(f"  python -m app.workers.send_email_sequence --auto --limit 75")

        show_stats(conn)

    except Exception as e:
        conn.rollback()
        print(f"\nERROR: {e}")
        import traceback; traceback.print_exc()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
