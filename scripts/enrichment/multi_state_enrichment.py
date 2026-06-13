"""
multi_state_enrichment.py
=========================
Unified multi-state contact enrichment pipeline.

Pulls contacts from all state license databases and normalizes
them into a single table: multi_state_contacts

For each state:
  Florida    → lien_dbpr_contacts (existing) ✅
  Texas      → texas_tdlr_contacts (Phase 2)
  Arizona    → arizona_roc_contacts (Phase 3)
  Georgia    → georgia_sos_contacts (Phase 4 — pending)
  California → california_cslb_contacts (Phase 5 — pending)
  New York   → new_york_dos_contacts (Phase 6 — pending)
  N Carolina → nc_lbgc_contacts (Phase 7 — pending)

Enrichment steps:
  1. Pull contacts from state DB tables
  2. Score confidence (name + address + phone = medium, + email = high)
  3. Append to multi_state_contacts unified table
  4. Flag for email sequence

Usage:
  python scripts/enrichment/multi_state_enrichment.py --state texas
  python scripts/enrichment/multi_state_enrichment.py --all
  python scripts/enrichment/multi_state_enrichment.py --stats
  python scripts/enrichment/multi_state_enrichment.py --export --state texas
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

DATA_DIR = LEADFLOW_DIR / "data" / "enrichment"
DATA_DIR.mkdir(parents=True, exist_ok=True)

try:
    from app.core.db import get_connection
    HAS_DB = True
except ImportError:
    HAS_DB = False

# ── Unified table schema ──────────────────────────────────────────────────────

CREATE_UNIFIED_TABLE = """
CREATE TABLE IF NOT EXISTS multi_state_contacts (
    id                  SERIAL PRIMARY KEY,
    state               VARCHAR(2)   NOT NULL,
    state_name          VARCHAR(50),
    county              VARCHAR(100),
    license_number      VARCHAR(100),
    license_type        VARCHAR(100),
    owner_name          VARCHAR(200),
    business_name       VARCHAR(200),
    business_address    VARCHAR(300),
    business_city       VARCHAR(100),
    business_zip        VARCHAR(20),
    phone               VARCHAR(30),
    email               VARCHAR(200),
    confidence          VARCHAR(20)  DEFAULT 'low',
    source_table        VARCHAR(100),
    has_lien_match      BOOLEAN      DEFAULT FALSE,
    lien_amount_range   VARCHAR(50),
    campaign_id         VARCHAR(100),
    email_step          INTEGER      DEFAULT 0,
    last_emailed_at     TIMESTAMP,
    replied             BOOLEAN      DEFAULT FALSE,
    unsubscribed        BOOLEAN      DEFAULT FALSE,
    created_at          TIMESTAMP    DEFAULT NOW(),
    updated_at          TIMESTAMP    DEFAULT NOW(),
    UNIQUE (state, license_number)
);

CREATE INDEX IF NOT EXISTS idx_ms_state
    ON multi_state_contacts(state);
CREATE INDEX IF NOT EXISTS idx_ms_county
    ON multi_state_contacts(county);
CREATE INDEX IF NOT EXISTS idx_ms_email
    ON multi_state_contacts(email)
    WHERE email IS NOT NULL AND email != '';
CREATE INDEX IF NOT EXISTS idx_ms_confidence
    ON multi_state_contacts(confidence);
CREATE INDEX IF NOT EXISTS idx_ms_lien_match
    ON multi_state_contacts(has_lien_match)
    WHERE has_lien_match = TRUE;
CREATE INDEX IF NOT EXISTS idx_ms_campaign
    ON multi_state_contacts(campaign_id);
"""

# ── State source configs ──────────────────────────────────────────────────────

STATE_SOURCES = {
    "fl": {
        "name":        "Florida",
        "table":       "lien_dbpr_contacts",
        "status":      "active",
        "field_map": {
            "county":           "county",
            "license_number":   "license_number",
            "license_type":     "license_type",
            "owner_name":       "owner_name",
            "business_name":    "business_name",
            "business_address": "business_address",
            "business_city":    "business_city",
            "business_zip":     "business_zip",
            "phone":            "phone",
            "email":            "email",
            "confidence":       "confidence",
        },
    },
    "tx": {
        "name":        "Texas",
        "table":       "texas_tdlr_contacts",
        "status":      "active",
        "field_map": {
            "county":           "business_county",
            "license_number":   "license_number",
            "license_type":     "license_type",
            "owner_name":       "owner_name",
            "business_name":    "business_name",
            "business_address": "business_address",
            "business_city":    "business_city",
            "business_zip":     "business_zip",
            "phone":            "business_phone",
            "email":            "email",
            "confidence":       "confidence",
        },
    },
    "az": {
        "name":        "Arizona",
        "table":       "arizona_roc_contacts",
        "status":      "active",
        "field_map": {
            "county":           "county",
            "license_number":   "license_number",
            "license_type":     "license_type",
            "owner_name":       "owner_name",
            "business_name":    "business_name",
            "business_address": "business_address",
            "business_city":    "business_city",
            "business_zip":     "business_zip",
            "phone":            "phone",
            "email":            "email",
            "confidence":       "confidence",
        },
    },
    # Placeholder entries for future states
    "ga": {
        "name":    "Georgia",
        "table":   "georgia_sos_contacts",
        "status":  "pending",
        "field_map": {},
    },
    "ca": {
        "name":    "California",
        "table":   "california_cslb_contacts",
        "status":  "pending",
        "field_map": {},
    },
    "ny": {
        "name":    "New York",
        "table":   "new_york_dos_contacts",
        "status":  "pending",
        "field_map": {},
    },
    "nc": {
        "name":    "North Carolina",
        "table":   "nc_lbgc_contacts",
        "status":  "pending",
        "field_map": {},
    },
}


# ── Table checker ─────────────────────────────────────────────────────────────

def table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = %s
            )
        """, (table_name,))
        return cur.fetchone()[0]


# ── Enrichment ────────────────────────────────────────────────────────────────

def enrich_state(state_code: str,
                 dry_run: bool = False) -> dict:
    """Pull contacts from state table → normalize → insert into unified table."""
    cfg = STATE_SOURCES.get(state_code.lower())
    if not cfg:
        print(f"  ❌ Unknown state: {state_code}")
        return {"inserted": 0, "updated": 0, "error": "unknown state"}

    if cfg["status"] == "pending":
        print(f"  ⏳ {cfg['name']}: source table not yet built — skipping")
        return {"inserted": 0, "updated": 0, "status": "pending"}

    conn = get_connection()
    try:
        if not table_exists(conn, cfg["table"]):
            print(f"  ⚠  {cfg['name']}: table '{cfg['table']}' does not exist yet")
            print(f"      Run the {state_code.upper()} scraper first")
            return {"inserted": 0, "updated": 0, "error": "table missing"}

        fm = cfg["field_map"]
        # Build SELECT with field mapping
        select_fields = ", ".join(
            f"{src} AS {dst}" for dst, src in fm.items()
            if src  # skip empty mappings
        )

        query = f"""
            SELECT {select_fields}
            FROM {cfg['table']}
            WHERE email IS NOT NULL AND email != ''
            ORDER BY confidence DESC, id
        """

        with conn.cursor() as cur:
            cur.execute(query)
            cols    = [d[0] for d in cur.description]
            rows    = cur.fetchall()
            records = [dict(zip(cols, r)) for r in rows]

        print(f"  {cfg['name']}: {len(records):,} emailable contacts")

        inserted = updated = skipped = 0
        with conn.cursor() as cur:
            for rec in records:
                try:
                    cur.execute("""
                        INSERT INTO multi_state_contacts (
                            state, state_name, county, license_number,
                            license_type, owner_name, business_name,
                            business_address, business_city, business_zip,
                            phone, email, confidence, source_table
                        ) VALUES (
                            %(state)s, %(state_name)s, %(county)s,
                            %(license_number)s, %(license_type)s,
                            %(owner_name)s, %(business_name)s,
                            %(business_address)s, %(business_city)s,
                            %(business_zip)s, %(phone)s, %(email)s,
                            %(confidence)s, %(source_table)s
                        )
                        ON CONFLICT (state, license_number) DO UPDATE SET
                            email       = COALESCE(EXCLUDED.email,
                                          multi_state_contacts.email),
                            confidence  = EXCLUDED.confidence,
                            updated_at  = NOW()
                        RETURNING (xmax = 0) AS was_inserted
                    """, {
                        **rec,
                        "state":        state_code.upper(),
                        "state_name":   cfg["name"],
                        "source_table": cfg["table"],
                        "license_number": rec.get("license_number") or f"{state_code}_{hash(rec.get('email',''))}",
                    })
                    row = cur.fetchone()
                    if row and row[0]:
                        inserted += 1
                    else:
                        updated += 1
                except Exception as e:
                    skipped += 1
                    if skipped <= 3:
                        print(f"  ⚠ Insert error: {e}")

        if not dry_run:
            conn.commit()
            print(f"  ✅ {inserted:,} new, {updated:,} updated, {skipped} errors")
        else:
            conn.rollback()
            print(f"  [DRY RUN] Would insert/update {inserted+updated:,}")

        return {"inserted": inserted, "updated": updated, "skipped": skipped}

    finally:
        conn.close()


# ── Export ────────────────────────────────────────────────────────────────────

def export_state_contacts(state_code: str,
                           confidence: str = None) -> Path:
    """Export emailable contacts for a state to CSV."""
    conn = get_connection()
    try:
        where = f"WHERE state = '{state_code.upper()}'"
        if confidence:
            where += f" AND confidence = '{confidence}'"
        where += " AND email IS NOT NULL AND email != ''"
        where += " AND unsubscribed = FALSE"

        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT state, county, business_name, owner_name,
                       email, phone, license_type, confidence,
                       has_lien_match
                FROM multi_state_contacts
                {where}
                ORDER BY confidence DESC, county, business_name
            """)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()

        records = [dict(zip(cols, r)) for r in rows]
        out     = DATA_DIR / f"{state_code.lower()}_contacts_{date.today().isoformat()}.csv"

        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            writer.writerows(records)

        print(f"  💾 Exported: {out} ({len(records):,} contacts)")
        return out

    finally:
        conn.close()


# ── Stats ─────────────────────────────────────────────────────────────────────

def show_stats():
    if not HAS_DB:
        print("No DB")
        return
    conn = get_connection()
    try:
        # Check if unified table exists
        if not table_exists(conn, "multi_state_contacts"):
            print("  multi_state_contacts table not yet created.")
            print("  Run: python scripts/enrichment/multi_state_enrichment.py --state fl")
            conn.close()
            return

        with conn.cursor() as cur:
            cur.execute("""
                SELECT state, state_name,
                    COUNT(*)                                          AS total,
                    COUNT(*) FILTER (WHERE email IS NOT NULL
                                      AND email != '')               AS with_email,
                    COUNT(*) FILTER (WHERE confidence = 'high')      AS high_conf,
                    COUNT(*) FILTER (WHERE has_lien_match = TRUE)     AS lien_matches,
                    COUNT(*) FILTER (WHERE email_step > 0)           AS emailed
                FROM multi_state_contacts
                GROUP BY state, state_name
                ORDER BY total DESC
            """)
            rows = cur.fetchall()

        print(f"\n{'='*75}")
        print(f"  Multi-State Contact Database")
        print(f"  {date.today().isoformat()}")
        print(f"{'='*75}")
        print(f"  {'State':<15} {'Total':>8} {'Email':>8} {'High':>8} "
              f"{'Lien':>8} {'Emailed':>8}")
        print(f"  {'─'*15} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*8}")

        total_all = 0
        for row in rows:
            state, name, total, w_email, high, lien, emailed = row
            total_all += total
            print(f"  {(name or state):<15} {total:>8,} {w_email:>8,} "
                  f"{high:>8,} {lien:>8,} {emailed:>8,}")

        print(f"  {'─'*15} {'─'*8}")
        print(f"  {'TOTAL':<15} {total_all:>8,}")
        print(f"{'='*75}\n")

        # Pending states
        print("  Pending state tables:")
        for code, cfg in STATE_SOURCES.items():
            if cfg["status"] == "pending":
                print(f"    {cfg['name']:<20} — scraper not built yet")
        print()

    finally:
        conn.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Multi-State Contact Enrichment Pipeline")
    parser.add_argument("--state",    default=None,
                        choices=list(STATE_SOURCES.keys()))
    parser.add_argument("--all",      action="store_true",
                        help="Enrich all available states")
    parser.add_argument("--export",   action="store_true",
                        help="Export contacts to CSV")
    parser.add_argument("--confidence", default=None,
                        choices=["high", "medium", "low"])
    parser.add_argument("--stats",    action="store_true")
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--setup",    action="store_true",
                        help="Create unified table only")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    if not HAS_DB:
        print("❌ No DB connection — check .env")
        return

    # Setup table
    if args.setup or args.all or args.state:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(CREATE_UNIFIED_TABLE)
            conn.commit()
            print("  ✅ multi_state_contacts table ready")
        finally:
            conn.close()

    if args.setup:
        return

    print(f"\n{'='*55}")
    print(f"  Multi-State Enrichment Pipeline")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    print(f"{'='*55}\n")

    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("multi_state_enrichment")
        logger.start()
    except ImportError:
        logger = None

    states_to_run = (
        [s for s, c in STATE_SOURCES.items() if c["status"] == "active"]
        if args.all
        else [args.state] if args.state
        else []
    )

    results = {}
    for state in states_to_run:
        print(f"\n── {STATE_SOURCES[state]['name']} ──")
        if logger: logger.step_start(f"enrich_{state}")
        result = enrich_state(state, dry_run=args.dry_run)
        results[state] = result
        if logger:
            logger.step_done(f"enrich_{state}",
                             ok="error" not in result,
                             detail=str(result))

        if args.export and "error" not in result:
            export_state_contacts(state, confidence=args.confidence)

    print(f"\n{'='*55}")
    print(f"  Enrichment Complete")
    for state, result in results.items():
        name = STATE_SOURCES[state]["name"]
        ins  = result.get("inserted", 0)
        upd  = result.get("updated", 0)
        print(f"  {name:<15} {ins:>6,} new  {upd:>6,} updated")
    print(f"{'='*55}\n")

    show_stats()

    if logger:
        logger.finish({"states": states_to_run, "results": results,
                       "dry_run": args.dry_run})


if __name__ == "__main__":
    main()