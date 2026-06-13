"""
texas_lien_scraper.py
=====================
Texas IRS Federal Tax Lien Scraper.

Data source: Texas Secretary of State UCC/Federal Lien Portal
  https://webservices.sos.state.tx.us/UCC/search

Federal tax liens in Texas are filed with the TX Secretary of State
as "Notice of Federal Lien" (NFL) filings. The SOS portal includes
both UCC filings and federal lien notices in one searchable index.

Two modes:

MODE 1 — MATCH MODE (recommended first step):
  Takes existing TDLR contacts → searches TX SOS for each name
  → marks lien_match=TRUE on contacts with confirmed federal liens
  → these become priority email enrichment targets

MODE 2 — BULK SCRAPE MODE:
  Searches TX SOS by common debtor name patterns
  → builds texas_liens table from scratch
  → runs monthly to find new filings

TX SOS Bulk Order (paid alternative):
  If you purchase the Master Unload from TX SOS (~$100-500):
  Email ucc_assist@sos.texas.gov
  Includes all federal lien notices in JSON format
  Use --import-bulk to load the JSON file directly

Usage:
  python scripts/scrapers/texas_lien_scraper.py --match --limit 100
  python scripts/scrapers/texas_lien_scraper.py --match --county HARRIS --limit 500
  python scripts/scrapers/texas_lien_scraper.py --scrape --days 90
  python scripts/scrapers/texas_lien_scraper.py --import-bulk data/texas/sos_bulk.json
  python scripts/scrapers/texas_lien_scraper.py --stats
  python scripts/scrapers/texas_lien_scraper.py --dry-run --match --limit 10

Schedule:
  Monthly 1st → python scripts/scrapers/texas_lien_scraper.py --match --limit 1000
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

DATA_DIR  = LEADFLOW_DIR / "data" / "texas"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS  = DATA_DIR / "lien_match_progress.json"

try:
    from app.core.db import get_connection
    HAS_DB = True
except ImportError:
    HAS_DB = False

# ── TX SOS Search API ─────────────────────────────────────────────────────────
# Texas SOS UCC/Federal Lien search portal
TX_SOS_SEARCH   = "https://webservices.sos.state.tx.us/UCC/search"
TX_SOS_PORTAL   = "https://direct.sos.state.tx.us/ucc/ucc-index.asp"

# Federal lien filing type codes
FEDERAL_LIEN_TYPES = {
    "NFL",  # Notice of Federal Lien
    "NFLT", # Notice of Federal Lien (Tax)
    "FLN",  # Federal Lien Notice
    "IRS",  # IRS lien
}

# ── DB schema ─────────────────────────────────────────────────────────────────

CREATE_TEXAS_LIENS_TABLE = """
CREATE TABLE IF NOT EXISTS texas_liens (
    id                  SERIAL PRIMARY KEY,
    filing_number       VARCHAR(50)  UNIQUE,
    debtor_name         VARCHAR(300),
    debtor_address      VARCHAR(300),
    debtor_city         VARCHAR(100),
    debtor_state        VARCHAR(10),
    debtor_zip          VARCHAR(20),
    secured_party       VARCHAR(300),
    filing_type         VARCHAR(50),
    filing_date         DATE,
    expiration_date     DATE,
    lien_amount         NUMERIC(15,2),
    county              VARCHAR(100),
    status              VARCHAR(30)  DEFAULT 'active',
    source              VARCHAR(50)  DEFAULT 'tx_sos',
    tdlr_match_id       INTEGER,
    created_at          TIMESTAMP    DEFAULT NOW(),
    updated_at          TIMESTAMP    DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tx_liens_debtor
    ON texas_liens(debtor_name);
CREATE INDEX IF NOT EXISTS idx_tx_liens_date
    ON texas_liens(filing_date);
CREATE INDEX IF NOT EXISTS idx_tx_liens_type
    ON texas_liens(filing_type);
CREATE INDEX IF NOT EXISTS idx_tx_liens_match
    ON texas_liens(tdlr_match_id)
    WHERE tdlr_match_id IS NOT NULL;
"""


# ── TX SOS search ─────────────────────────────────────────────────────────────

def search_tx_sos(debtor_name: str,
                  session: requests.Session = None) -> list[dict]:
    """
    Search TX SOS for federal lien filings.

    NOTE: TX SOS migrated to new SOS Portal in Aug 2025.
    The old webservices API no longer works.
    Searches now require a paid SOS Portal account ($1.00/search).

    This function is kept for future use if bulk data is purchased.
    Current primary source: Harris County Clerk (free).
    Bulk data: email ucc_assist@sos.texas.gov for Master Unload pricing.
    """
    # TX SOS API deprecated Aug 2025 — returns empty results
    # Uncomment below if SOS Portal API credentials are added
    # if not TX_SOS_API_KEY:
    #     return []
    return []


def normalize_name_for_search(name: str) -> list[str]:
    """
    Generate search variations for a business/person name.
    TX SOS search is inexact so we try multiple variations.
    """
    if not name:
        return []

    name     = name.strip().upper()
    searches = [name]

    # Remove common business suffixes for broader search
    suffixes = [" LLC", " INC", " CORP", " LTD", " LP", " LLP",
                " CO", " COMPANY", " & ASSOCIATES", " AND ASSOCIATES"]
    clean = name
    for s in suffixes:
        if clean.endswith(s):
            clean = clean[:-len(s)].strip()
            if clean and clean not in searches:
                searches.append(clean)
            break

    # For person names (LAST, FIRST format) try FIRST LAST
    if "," in name:
        parts = name.split(",", 1)
        if len(parts) == 2:
            reversed_name = f"{parts[1].strip()} {parts[0].strip()}"
            if reversed_name not in searches:
                searches.append(reversed_name)

    return searches[:2]  # max 2 searches per contact


# ── Match mode: check TDLR contacts against TX SOS ───────────────────────────

def match_tdlr_to_liens(limit: int = 100,
                         county: str = None,
                         resume: bool = False,
                         dry_run: bool = False) -> dict:
    """
    For each TDLR contact, search TX SOS for federal lien filings.
    Mark lien_match=TRUE on contacts with confirmed liens.
    """
    if not HAS_DB:
        print("  ❌ No DB connection")
        return {"matched": 0}

    # Load progress
    prog      = {}
    if PROGRESS.exists() and resume:
        try:
            prog = json.loads(PROGRESS.read_text())
        except Exception:
            pass
    last_id   = prog.get("last_id", 0)
    matched   = prog.get("matched", 0) if resume else 0
    searched  = prog.get("searched", 0) if resume else 0

    conn = get_connection()
    try:
        # Ensure liens table exists
        with conn.cursor() as cur:
            cur.execute(CREATE_TEXAS_LIENS_TABLE)
        conn.commit()

        # Get TDLR contacts to check
        with conn.cursor() as cur:
            where = [f"id > {last_id}",
                     "lien_match = FALSE",
                     "(business_name IS NOT NULL OR owner_name IS NOT NULL)"]
            if county:
                where.append(f"business_county = '{county.upper()}'")

            # Prioritize business names over personal names
            cur.execute(f"""
                SELECT id, business_name, owner_name,
                       business_city, business_county
                FROM texas_tdlr_contacts
                WHERE {' AND '.join(where)}
                ORDER BY
                    CASE WHEN business_name IS NOT NULL THEN 0 ELSE 1 END,
                    id
                LIMIT {limit}
            """)
            contacts = cur.fetchall()

        print(f"  Contacts to check : {len(contacts):,}")
        print(f"  Resuming from ID  : {last_id}" if resume else "")

        session = requests.Session()
        session.headers.update({
            "User-Agent": "TaxCaseReview/1.0 (info@taxcasereview.org)"
        })

        current_id = last_id

        for i, (rec_id, biz_name, owner_name,
                city, county_name) in enumerate(contacts):

            current_id   = rec_id
            search_name  = biz_name or owner_name
            if not search_name:
                searched += 1
                continue

            print(f"  [{i+1}/{len(contacts)}] "
                  f"{search_name[:45]:<45} ({county_name})",
                  end=" ... ", flush=True)

            if dry_run:
                print("[DRY RUN]")
                searched += 1
                continue

            # Search TX SOS for this name
            all_liens = []
            for search_variant in normalize_name_for_search(search_name):
                liens = search_tx_sos(search_variant, session)
                all_liens.extend(liens)
                if liens:
                    break
                time.sleep(0.5)

            searched += 1

            if all_liens:
                matched += 1
                print(f"✅ LIEN FOUND ({len(all_liens)} filing(s))")

                with conn.cursor() as cur:
                    # Mark TDLR contact as lien match
                    cur.execute("""
                        UPDATE texas_tdlr_contacts
                        SET lien_match  = TRUE,
                            confidence  = 'high',
                            updated_at  = NOW()
                        WHERE id = %s
                    """, (rec_id,))

                    # Save lien records
                    for lien in all_liens:
                        filing_date = None
                        exp_date    = None
                        if lien.get("filing_date"):
                            try:
                                filing_date = datetime.strptime(
                                    lien["filing_date"][:10], "%Y-%m-%d").date()
                            except Exception:
                                pass
                        if lien.get("expiration_date"):
                            try:
                                exp_date = datetime.strptime(
                                    lien["expiration_date"][:10], "%Y-%m-%d").date()
                            except Exception:
                                pass

                        cur.execute("""
                            INSERT INTO texas_liens (
                                filing_number, debtor_name, debtor_address,
                                debtor_city, debtor_state, debtor_zip,
                                secured_party, filing_type, filing_date,
                                expiration_date, county, tdlr_match_id
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (filing_number) DO UPDATE SET
                                tdlr_match_id = EXCLUDED.tdlr_match_id,
                                updated_at    = NOW()
                        """, (
                            lien.get("filing_number") or f"TX-{rec_id}-{i}",
                            lien.get("debtor_name", search_name),
                            lien.get("debtor_address", ""),
                            lien.get("debtor_city", city or ""),
                            lien.get("debtor_state", "TX"),
                            lien.get("debtor_zip", ""),
                            lien.get("secured_party", "IRS"),
                            lien.get("filing_type", "NFL"),
                            filing_date,
                            exp_date,
                            county_name,
                            rec_id,
                        ))

                conn.commit()

                # Also update multi_state_contacts if it exists
                try:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE multi_state_contacts
                            SET has_lien_match = TRUE,
                                updated_at     = NOW()
                            WHERE state = 'TX'
                              AND license_number = (
                                  SELECT license_number
                                  FROM texas_tdlr_contacts
                                  WHERE id = %s
                              )
                        """, (rec_id,))
                    conn.commit()
                except Exception:
                    pass

            else:
                print("no lien")

            # Save progress every 25 records
            if (i + 1) % 25 == 0:
                PROGRESS.write_text(json.dumps({
                    "last_id":  current_id,
                    "matched":  matched,
                    "searched": searched,
                    "date":     date.today().isoformat(),
                }, indent=2))
                print(f"\n  ── Progress: {matched} matched / "
                      f"{searched} searched ──\n")

            time.sleep(0.8)  # polite delay

        # Final progress save
        PROGRESS.write_text(json.dumps({
            "last_id":  current_id,
            "matched":  matched,
            "searched": searched,
            "date":     date.today().isoformat(),
        }, indent=2))

    finally:
        conn.close()

    match_rate = round(matched / max(searched, 1) * 100, 1)
    return {
        "matched":    matched,
        "searched":   searched,
        "match_rate": match_rate,
    }


# ── Bulk import mode (if TX SOS bulk data purchased) ─────────────────────────

def import_bulk_json(json_path: str, dry_run: bool = False) -> dict:
    """
    Import federal lien data from TX SOS bulk JSON file.
    Purchase from: ucc_assist@sos.texas.gov
    """
    path = Path(json_path)
    if not path.exists():
        print(f"  ❌ File not found: {json_path}")
        return {"imported": 0}

    print(f"  Loading: {path} ({path.stat().st_size/1024/1024:.1f} MB)")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # Handle various JSON structures from TX SOS
    records = []
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = (data.get("filings") or
                  data.get("records") or
                  data.get("data") or [])

    print(f"  Total records: {len(records):,}")

    # Filter for federal liens only
    federal = [r for r in records
               if any(t in str(r.get("filingType", "")).upper()
                      for t in FEDERAL_LIEN_TYPES)]
    print(f"  Federal liens: {len(federal):,}")

    if not HAS_DB:
        print("  No DB — saving filtered CSV")
        out = DATA_DIR / "tx_federal_liens.json"
        out.write_text(json.dumps(federal, indent=2))
        return {"imported": 0, "filtered": len(federal)}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TEXAS_LIENS_TABLE)
        conn.commit()

        imported = skipped = 0
        with conn.cursor() as cur:
            for r in federal:
                try:
                    filing_date = None
                    if r.get("filingDate"):
                        try:
                            filing_date = datetime.strptime(
                                str(r["filingDate"])[:10], "%Y-%m-%d").date()
                        except Exception:
                            pass

                    cur.execute("""
                        INSERT INTO texas_liens (
                            filing_number, debtor_name, debtor_address,
                            debtor_city, debtor_state, debtor_zip,
                            secured_party, filing_type, filing_date
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (filing_number) DO NOTHING
                    """, (
                        r.get("filingNumber", ""),
                        r.get("debtorName", ""),
                        r.get("debtorAddress", ""),
                        r.get("debtorCity", ""),
                        r.get("debtorState", "TX"),
                        r.get("debtorZip", ""),
                        r.get("securedParty", "IRS"),
                        r.get("filingType", "NFL"),
                        filing_date,
                    ))
                    imported += 1
                except Exception:
                    skipped += 1

        if not dry_run:
            conn.commit()
            print(f"  ✅ Imported: {imported:,} federal liens")
        else:
            conn.rollback()
            print(f"  [DRY RUN] Would import: {imported:,}")

        return {"imported": imported, "skipped": skipped}

    finally:
        conn.close()


# ── Stats ─────────────────────────────────────────────────────────────────────

def show_stats():
    if not HAS_DB:
        print("No DB")
        return

    conn = get_connection()
    try:
        print(f"\n{'='*60}")
        print(f"  Texas Lien Matching Stats")
        print(f"  {date.today().isoformat()}")
        print(f"{'='*60}")

        with conn.cursor() as cur:
            # TDLR match stats
            cur.execute("""
                SELECT
                    COUNT(*)                                    AS total,
                    COUNT(*) FILTER (WHERE lien_match = TRUE)  AS matched,
                    COUNT(*) FILTER (WHERE lien_match = TRUE
                                     AND email IS NOT NULL
                                     AND email != '')          AS matched_with_email
                FROM texas_tdlr_contacts
            """)
            r = cur.fetchone()
            total, matched, with_email = r

            print(f"\n  TDLR Contacts:")
            print(f"  Total         : {total:,}")
            print(f"  Lien matched  : {matched:,} "
                  f"({round(matched/max(total,1)*100,1)}%)")
            print(f"  Matched + email: {with_email:,}")

            # Liens table stats
            try:
                cur.execute("SELECT COUNT(*) FROM texas_liens")
                lien_total = cur.fetchone()[0]
                cur.execute("""
                    SELECT filing_type, COUNT(*)
                    FROM texas_liens GROUP BY filing_type
                    ORDER BY COUNT(*) DESC
                """)
                types = cur.fetchall()

                print(f"\n  Texas Liens Table:")
                print(f"  Total filings : {lien_total:,}")
                for t, cnt in types:
                    print(f"    {t:<20} {cnt:>8,}")
            except Exception:
                print(f"\n  Texas Liens Table: not yet created")

            # Progress
            if PROGRESS.exists():
                try:
                    prog = json.loads(PROGRESS.read_text())
                    print(f"\n  Last matching run:")
                    print(f"  Searched : {prog.get('searched',0):,}")
                    print(f"  Matched  : {prog.get('matched',0):,}")
                    print(f"  Last ID  : {prog.get('last_id',0):,}")
                    print(f"  Date     : {prog.get('date','—')}")
                except Exception:
                    pass

            # County breakdown of matched contacts
            cur.execute("""
                SELECT business_county, COUNT(*)
                FROM texas_tdlr_contacts
                WHERE lien_match = TRUE
                  AND business_county IS NOT NULL
                GROUP BY business_county
                ORDER BY COUNT(*) DESC LIMIT 10
            """)
            counties = cur.fetchall()
            if counties:
                print(f"\n  Matched contacts by county:")
                for county, cnt in counties:
                    print(f"    {county:<20} {cnt:>6,}")

        print(f"{'='*60}\n")

    finally:
        conn.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Texas Federal Tax Lien Scraper")
    parser.add_argument("--match",        action="store_true",
                        help="Match TDLR contacts against TX SOS liens")
    parser.add_argument("--import-bulk",  default=None, metavar="JSON_FILE",
                        help="Import TX SOS bulk JSON file")
    parser.add_argument("--limit",        type=int, default=100,
                        help="Max contacts to check (default 100)")
    parser.add_argument("--county",       default=None,
                        help="Filter by county (e.g. HARRIS)")
    parser.add_argument("--resume",       action="store_true",
                        help="Resume from last position")
    parser.add_argument("--dry-run",      action="store_true")
    parser.add_argument("--stats",        action="store_true")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    print(f"\n{'='*60}")
    print(f"  Texas Lien Scraper")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    print(f"{'='*60}\n")

    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("texas_lien_scraper")
        logger.start()
    except ImportError:
        logger = None

    if args.import_bulk:
        if logger: logger.step_start("import_bulk")
        result = import_bulk_json(args.import_bulk, dry_run=args.dry_run)
        if logger:
            logger.step_done("import_bulk", ok=True, detail=str(result))
            logger.finish(result)
        return

    if args.match:
        print(f"\n{'='*60}")
        print(f"  TX SOS API Status: DEPRECATED (Aug 2025)")
        print(f"  TX SOS migrated to new portal requiring paid account.")
        print(f"  Current lien sources:")
        print(f"    ✅ Harris County Clerk (free — building scraper)")
        print(f"    ⏳ TX SOS Bulk Order (email ucc_assist@sos.texas.gov)")
        print(f"    ⏳ FOIA request (filed — 60-180 days)")
        print(f"{'='*60}\n")

        if logger:
            logger.finish({"status": "tx_sos_deprecated",
                          "action": "use_harris_county_scraper"})
        return

    parser.print_help()


if __name__ == "__main__":
    main()