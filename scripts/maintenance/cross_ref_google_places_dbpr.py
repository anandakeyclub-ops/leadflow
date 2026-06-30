#!/usr/bin/env python3
"""
cross_ref_google_places_dbpr.py
================================
Cross-references FL google_places_contacts (which have phones but no emails —
many are IRS-FOIA-lien matched) against the DBPR contractor database
(dbpr_contacts_raw, 84k FL records with real emails) to enrich them with email
addresses, then bridges the FOIA-matched + email-enriched FL contacts into
lien_dbpr_contacts so send_email_sequence.py picks them up.

Pipeline:
  1. Ensure enrichment columns on google_places_contacts
     (email, dbpr_license, dbpr_matched, sms_opted_out — sms_sent already exists).
  2. Match FL google_places_contacts to dbpr_contacts_raw on a 6-char
     business-name prefix and copy email + license_number across.
       NOTE: a 6-char prefix is a LOOSE heuristic. Where a google_places business
       matches multiple DBPR rows, Postgres picks one arbitrarily. Treat the
       resulting emails as medium-confidence, not verified.
  3. Report: FL contacts enriched, how many are FOIA-matched (active lien),
     and how many are now email+phone+lien "ready".
  4. Bridge the FOIA-matched, email-enriched FL contacts into lien_dbpr_contacts.

Schema adaptation vs the original spec:
  lien_dbpr_contacts has no contact_name/county/lien_amount columns, confidence is
  TEXT (not numeric), lien_id is NOT NULL + FK to normalized_liens + UNIQUE, and
  there is no unique constraint on email. So we:
    - create a normalized_liens placeholder per contact (lien_source='irs_foia_dbpr',
      carrying the lien amount) to satisfy the FK,
    - resolve county_id from the counties table (create FL county if missing),
    - store the intended 0.75 confidence as dbpr_score=75.0 + confidence='medium',
      source='irs_foia_dbpr_match',
    - dedupe by email against the existing pool (no ON CONFLICT(email) available).

Usage:
  python -m scripts.maintenance.cross_ref_google_places_dbpr --dry-run
  python -m scripts.maintenance.cross_ref_google_places_dbpr
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

try:
    from app.core.db import get_connection
except ImportError:
    sys.exit("Run from leadflow root: python -m scripts.maintenance.cross_ref_google_places_dbpr")


def ensure_columns(cur) -> None:
    for ddl in [
        "ALTER TABLE google_places_contacts ADD COLUMN IF NOT EXISTS email TEXT",
        "ALTER TABLE google_places_contacts ADD COLUMN IF NOT EXISTS dbpr_license TEXT",
        "ALTER TABLE google_places_contacts ADD COLUMN IF NOT EXISTS dbpr_matched BOOLEAN DEFAULT FALSE",
        "ALTER TABLE google_places_contacts ADD COLUMN IF NOT EXISTS sms_sent BOOLEAN DEFAULT FALSE",
        "ALTER TABLE google_places_contacts ADD COLUMN IF NOT EXISTS sms_opted_out BOOLEAN DEFAULT FALSE",
    ]:
        cur.execute(ddl)


# WHERE clause shared by the dry-run preview and the live UPDATE.
_MATCH_WHERE = """
    g.state = 'FL'
    AND g.email IS NULL
    AND d.email IS NOT NULL AND d.email <> ''
    AND g.business_name IS NOT NULL AND d.business_name IS NOT NULL
    AND LENGTH(g.business_name) >= 5
    AND LENGTH(d.business_name) >= 5
    AND UPPER(LEFT(g.business_name, 6)) = UPPER(LEFT(d.business_name, 6))
"""


def preview_match_count(cur) -> int:
    cur.execute(f"""
        SELECT COUNT(DISTINCT g.id)
        FROM google_places_contacts g
        JOIN dbpr_contacts_raw d ON UPPER(LEFT(g.business_name, 6)) = UPPER(LEFT(d.business_name, 6))
        WHERE {_MATCH_WHERE}
    """)
    return cur.fetchone()[0] or 0


def run_match(cur) -> int:
    # DISTINCT ON keeps one DBPR row per google_places contact (lowest id) so the
    # UPDATE is deterministic instead of relying on arbitrary join order.
    cur.execute(f"""
        WITH best AS (
            SELECT DISTINCT ON (g.id)
                   g.id AS gid, d.email AS email, d.license_number AS lic
            FROM google_places_contacts g
            JOIN dbpr_contacts_raw d ON UPPER(LEFT(g.business_name, 6)) = UPPER(LEFT(d.business_name, 6))
            WHERE {_MATCH_WHERE}
            ORDER BY g.id, d.id
        )
        UPDATE google_places_contacts g
        SET email = best.email,
            dbpr_license = best.lic,
            dbpr_matched = TRUE
        FROM best
        WHERE g.id = best.gid
    """)
    return cur.rowcount


def report(cur) -> dict:
    stats = {}
    cur.execute("SELECT COUNT(*) FROM google_places_contacts WHERE state='FL' AND dbpr_matched = TRUE")
    stats["fl_enriched"] = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(DISTINCT g.id)
        FROM google_places_contacts g
        JOIN irs_foia_liens f ON f.matched_contact_id = g.id
        WHERE g.state='FL' AND g.dbpr_matched = TRUE
    """)
    stats["fl_enriched_foia"] = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(DISTINCT g.id)
        FROM google_places_contacts g
        JOIN irs_foia_liens f ON f.matched_contact_id = g.id
        WHERE g.state='FL' AND g.dbpr_matched = TRUE
          AND g.email IS NOT NULL AND g.email <> ''
          AND g.phone IS NOT NULL AND g.phone <> ''
    """)
    stats["fl_ready"] = cur.fetchone()[0]
    return stats


def get_existing_emails(cur) -> set:
    cur.execute("SELECT DISTINCT email FROM lien_dbpr_contacts WHERE email IS NOT NULL AND email <> ''")
    return {r[0].lower().strip() for r in cur.fetchall()}


def get_county_id(cur, county_name: str) -> int:
    # counties.county_name is globally UNIQUE (not scoped by state), so look up
    # by name alone and only insert when truly absent.
    name = (county_name or "Unknown").strip() or "Unknown"
    cur.execute("SELECT id FROM counties WHERE county_name = %s", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO counties (county_name, state, active, created_at) "
        "VALUES (%s, 'FL', TRUE, NOW()) "
        "ON CONFLICT (county_name) DO UPDATE SET county_name = EXCLUDED.county_name "
        "RETURNING id",
        (name,),
    )
    return cur.fetchone()[0]


def bridge_to_lien_pool(conn, dry_run: bool) -> int:
    """Bridge FOIA-matched, email-enriched FL google_places contacts into
    lien_dbpr_contacts (creating normalized_liens placeholders)."""
    with conn.cursor() as cur:
        existing = get_existing_emails(cur)
        # Highest-amount FOIA lien per contact.
        cur.execute("""
            SELECT DISTINCT ON (g.id)
                   g.id, g.business_name, g.email, g.phone, g.city, g.county,
                   g.dbpr_license, f.amount, f.county
            FROM google_places_contacts g
            JOIN irs_foia_liens f ON f.matched_contact_id = g.id
            WHERE g.state='FL' AND g.dbpr_matched = TRUE
              AND g.email IS NOT NULL AND g.email <> ''
              AND g.phone IS NOT NULL AND g.phone <> ''
            ORDER BY g.id, f.amount DESC NULLS LAST
        """)
        rows = cur.fetchall()

    bridged = 0
    for (gid, biz, email, phone, gcity, gcounty, lic, amount, fcounty) in rows:
        email_lc = (email or "").lower().strip()
        if not email_lc or email_lc in existing:
            continue
        county_name = (fcounty or gcounty or "Unknown").strip() or "Unknown"

        if dry_run:
            print(f"  [BRIDGE DRY] {email_lc:40} | {biz[:30]:30} | {county_name} | ${float(amount or 0):,.0f}")
            existing.add(email_lc)
            bridged += 1
            continue

        with conn.cursor() as cur:
            county_id = get_county_id(cur, county_name)
            h = hashlib.md5(f"foia_dbpr|{gid}|FL|{county_name}".encode()).hexdigest()
            cur.execute("""
                INSERT INTO normalized_liens
                    (county_id, debtor_name, business_name, filing_type,
                     lien_type, lien_source, normalized_hash, state, amount)
                VALUES (%s, %s, %s, 'TAX LIEN', 'TAX LIEN', 'irs_foia_dbpr', %s, 'FL', %s)
                ON CONFLICT (normalized_hash) DO NOTHING
                RETURNING id
            """, (county_id, (biz or "")[:250], (biz or "")[:250], h, amount))
            ret = cur.fetchone()
            if ret:
                lien_id = ret[0]
            else:
                cur.execute("SELECT id FROM normalized_liens WHERE normalized_hash = %s", (h,))
                lien_id = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO lien_dbpr_contacts
                    (lien_id, county_id, debtor_name, full_name, email, phone,
                     city, state, license_number, dbpr_score, confidence, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'FL', %s, 75.0, 'medium', 'irs_foia_dbpr_match')
                ON CONFLICT (lien_id) DO NOTHING
            """, (lien_id, county_id, (biz or "")[:250], (biz or "")[:250],
                  email_lc[:200], (phone or "")[:50], (gcity or "")[:100], (lic or "")[:50]))
            conn.commit()
        existing.add(email_lc)
        bridged += 1

    return bridged


def main():
    ap = argparse.ArgumentParser(description="Cross-ref FL google_places vs DBPR; enrich + bridge")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    logger = None
    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("sms_campaign")  # reuse run_type per task spec
        logger.start()
    except Exception:
        pass

    conn = get_connection()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            ensure_columns(cur)
            conn.commit()

        print(f"\n[cross_ref_google_places_dbpr]  Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")

        if args.dry_run:
            with conn.cursor() as cur:
                would = preview_match_count(cur)
            print(f"  Would enrich (new FL email matches): {would:,}")
            matched = would
        else:
            with conn.cursor() as cur:
                matched = run_match(cur)
                conn.commit()
            print(f"  FL contacts newly enriched with DBPR email: {matched:,}")

        with conn.cursor() as cur:
            stats = report(cur)

        print(f"\n  Bridging FOIA-matched FL contacts into lien_dbpr_contacts...")
        bridged = bridge_to_lien_pool(conn, args.dry_run)

        print(f"\n{'='*64}")
        print(f"  CROSS-REFERENCE RESULTS  ({'DRY RUN — no writes' if args.dry_run else 'LIVE'})")
        print(f"{'='*64}")
        print(f"  FL google_places enriched with DBPR email : {stats['fl_enriched']:,}")
        print(f"    ...of which FOIA-matched (active lien)   : {stats['fl_enriched_foia']:,}")
        print(f"  Email+phone+lien READY for email sequence  : {stats['fl_ready']:,}")
        print(f"  New leads bridged into lien_dbpr_contacts  : {bridged:,}")
        print(f"{'='*64}\n")

        if logger:
            logger.finish({
                "newly_enriched": matched,
                "fl_enriched": stats["fl_enriched"],
                "fl_enriched_foia": stats["fl_enriched_foia"],
                "fl_ready": stats["fl_ready"],
                "bridged": bridged,
                "dry_run": args.dry_run,
            })
    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        import traceback; traceback.print_exc()
        if logger:
            logger.finish({"error": str(e)})
    finally:
        conn.close()


if __name__ == "__main__":
    main()
