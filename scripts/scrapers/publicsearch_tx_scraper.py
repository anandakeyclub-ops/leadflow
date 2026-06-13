"""
publicsearch_tx_scraper.py
===========================
Texas County Lien Scraper — PublicSearch Portal (Neumo platform)

Covers: Dallas, Tarrant, Collin counties (same portal, different subdomain)

Dallas portal structure (OPPOSITE of Harris County):
  Grantor = U S A INTERNAL REVENUE SERVICE (the IRS files the lien)
  Grantee = taxpayer name (the person with the lien)
  Doc Type = FEDERAL TAX LIEN (active liens only — not releases/errors)

URL format:
  https://[county].tx.publicsearch.us/results?
    department=RP
    &keywordSearch=false
    &recordedDateRange=YYYYMMDD,YYYYMMDD
    &searchOcrText=false
    &searchType=quickSearch
    &searchValue=Internal+Revenue+Service

Pagination: ?page=1, ?page=2, etc.

Stores in: texas_liens table (shared with Harris County data)
Matches against: texas_tdlr_contacts

Usage:
  python scripts/scrapers/publicsearch_tx_scraper.py --county dallas --days 180
  python scripts/scrapers/publicsearch_tx_scraper.py --county tarrant --days 180
  python scripts/scrapers/publicsearch_tx_scraper.py --county collin --days 180
  python scripts/scrapers/publicsearch_tx_scraper.py --all --days 180
  python scripts/scrapers/publicsearch_tx_scraper.py --all --days 180 --match
  python scripts/scrapers/publicsearch_tx_scraper.py --stats
  python scripts/scrapers/publicsearch_tx_scraper.py --dry-run --county dallas

Schedule: Monthly 1st at 6:50 AM
  Arguments: scripts/scrapers/publicsearch_tx_scraper.py --all --days 35 --match
"""
from __future__ import annotations

import argparse
import json
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

DATA_DIR = LEADFLOW_DIR / "data" / "texas"
DATA_DIR.mkdir(parents=True, exist_ok=True)

try:
    from app.core.db import get_connection
    HAS_DB = True
except ImportError:
    HAS_DB = False

# ── County configs ────────────────────────────────────────────────────────────
COUNTIES = {
    "dallas": {
        "name":       "Dallas",
        "subdomain":  "dallas",
        "est_liens":  48000,
    },
    "tarrant": {
        "name":       "Tarrant",
        "subdomain":  "tarrant",
        "est_liens":  46000,
    },
    "collin": {
        "name":       "Collin",
        "subdomain":  "collin",
        "est_liens":  8000,
    },
}

# Doc types to KEEP (active liens only)
ACTIVE_LIEN_TYPES = {
    "FEDERAL TAX LIEN",
    "NOTICE OF FEDERAL TAX LIEN",
    "FED TAX LIEN",
    "FLTX",
    "NFL",
}

# Doc types to SKIP (releases, errors, partials)
SKIP_DOC_TYPES = {
    "RELEASE OF FEDERAL TAX LIEN",
    "PARTIAL RELEASE OF FEDERAL TAX LIEN",
    "NOTICE OF ERROR IN FILING FEDERAL TAX LIEN",
    "CERTIFICATE",
    "WITHDRAWAL",
}

# ── DB schema (shared with harris_county_liens) ───────────────────────────────
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS texas_liens (
    id                  SERIAL PRIMARY KEY,
    file_number         VARCHAR(50)  UNIQUE,
    grantor_name        VARCHAR(300),
    grantee_name        VARCHAR(300),
    instrument_type     VARCHAR(100),
    filing_date         DATE,
    county              VARCHAR(100),
    town                VARCHAR(100),
    legal_description   VARCHAR(500),
    tdlr_match_id       INTEGER,
    status              VARCHAR(30)  DEFAULT 'active',
    source              VARCHAR(50)  DEFAULT 'publicsearch',
    created_at          TIMESTAMP    DEFAULT NOW(),
    updated_at          TIMESTAMP    DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tx_liens_grantee
    ON texas_liens(grantee_name);
CREATE INDEX IF NOT EXISTS idx_tx_liens_grantor
    ON texas_liens(grantor_name);
CREATE INDEX IF NOT EXISTS idx_tx_liens_county
    ON texas_liens(county);
CREATE INDEX IF NOT EXISTS idx_tx_liens_date
    ON texas_liens(filing_date);
CREATE INDEX IF NOT EXISTS idx_tx_liens_match
    ON texas_liens(tdlr_match_id)
    WHERE tdlr_match_id IS NOT NULL;
"""


# ── Scraper ───────────────────────────────────────────────────────────────────

def build_url(subdomain: str, date_from: str,
              date_to: str, page: int = 1) -> str:
    """Build PublicSearch results URL."""
    base = (
        f"https://{subdomain}.tx.publicsearch.us/results"
        f"?department=RP"
        f"&keywordSearch=false"
        f"&recordedDateRange={date_from},{date_to}"
        f"&searchOcrText=false"
        f"&searchType=quickSearch"
        f"&searchValue=Internal+Revenue+Service"
    )
    if page > 1:
        base += f"&page={page}"
    return base


def parse_results_page(html: str, county: str) -> tuple[list[dict], bool]:
    """
    Parse PublicSearch HTML results page.

    Dallas format:
      Grantor | Grantee | Doc Type | Recorded Date | Doc Number | ...

    For federal tax liens:
      Grantor = U S A INTERNAL REVENUE SERVICE
      Grantee = taxpayer (the person with the lien)

    Returns: (records, has_more_pages)
    """
    records   = []
    has_more  = False

    # Clean HTML
    clean = re.sub(r'<script[^>]*>.*?</script>', ' ',
                   html, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<style[^>]*>.*?</style>', ' ',
                   clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<[^>]+>', ' ', clean)
    clean = re.sub(r'&nbsp;', ' ', clean)
    clean = re.sub(r'&amp;', '&', clean)
    clean = re.sub(r'&[a-z]+;', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()

    # Check for next page
    if re.search(r'▶|next page|page \d+ of \d+', clean, re.IGNORECASE):
        has_more = True

    # Pattern: rows between "menu icon" markers
    # Each row: Grantor | Grantee | Doc Type | Date | Doc# | ...
    row_pattern = re.compile(
        r'menu icon\s+'
        r'([A-Z0-9\s&\',\.\-\/]+?)\s+'   # Grantor
        r'([A-Z0-9\s&\',\.\-\/]+?)\s+'   # Grantee
        r'([A-Z0-9\s&\',\.\-\/]+?)\s+'   # Doc Type
        r'(\d{1,2}/\d{1,2}/\d{4})\s+'   # Date
        r'(\d{9,12})',                    # Doc Number
        re.IGNORECASE
    )

    for m in row_pattern.finditer(clean):
        grantor   = m.group(1).strip()
        grantee   = m.group(2).strip()
        doc_type  = m.group(3).strip().upper()
        rec_date  = m.group(4).strip()
        doc_num   = m.group(5).strip()

        # Skip releases, errors, partials
        if any(skip in doc_type for skip in [
            "RELEASE", "ERROR", "CERTIFICATE", "WITHDRAWAL", "PARTIAL"
        ]):
            continue

        # Only keep active federal tax liens
        is_ftl = any(t in doc_type for t in [
            "FEDERAL TAX LIEN", "FTL", "NFL", "FLTX"
        ])
        if not is_ftl:
            continue

        # Determine taxpayer:
        # If IRS is grantor → grantee is taxpayer
        # If IRS is grantee → grantor is taxpayer
        irs_names = {"U S A INTERNAL REVENUE", "INTERNAL REVENUE SERVICE",
                     "UNITED STATES", "U S A"}
        grantor_is_irs = any(irs in grantor.upper() for irs in irs_names)

        if grantor_is_irs:
            taxpayer = grantee
        else:
            taxpayer = grantor

        if not taxpayer or len(taxpayer) < 3:
            continue

        # Parse date
        filing_date = None
        try:
            filing_date = datetime.strptime(rec_date, "%m/%d/%Y").date()
        except ValueError:
            pass

        records.append({
            "file_number":      doc_num,
            "grantor_name":     grantor[:300],
            "grantee_name":     grantee[:300],
            "instrument_type":  doc_type[:100],
            "filing_date":      filing_date,
            "county":           county,
            "taxpayer_name":    taxpayer[:300],  # the person with the lien
        })

    return records, has_more


def scrape_county(county_key: str, days_back: int = 180,
                  dry_run: bool = False) -> list[dict]:
    """Scrape all federal tax liens for a county."""
    cfg       = COUNTIES[county_key]
    subdomain = cfg["subdomain"]
    end_date  = date.today()
    start     = end_date - timedelta(days=days_back)

    date_from = start.strftime("%Y%m%d")
    date_to   = end_date.strftime("%Y%m%d")

    print(f"  {cfg['name']} County: {start.strftime('%m/%d/%Y')} → "
          f"{end_date.strftime('%m/%d/%Y')}")

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept":          "text/html,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer":         f"https://{subdomain}.tx.publicsearch.us/",
    })

    all_records = []
    seen        = set()
    page        = 1
    max_pages   = 50  # safety limit

    while page <= max_pages:
        url = build_url(subdomain, date_from, date_to, page)
        print(f"  Page {page}...", end=" ", flush=True)

        try:
            r = session.get(url, timeout=30)
            if r.status_code != 200:
                print(f"HTTP {r.status_code}")
                break

            records, has_more = parse_results_page(r.text, cfg["name"])

            new = 0
            for rec in records:
                key = rec["file_number"] or rec["taxpayer_name"]
                if key not in seen:
                    seen.add(key)
                    all_records.append(rec)
                    new += 1

            print(f"{new} liens (total: {len(all_records)})")

            if not has_more or new == 0:
                break

            page += 1
            time.sleep(1.5)  # polite delay

        except Exception as e:
            print(f"Error: {e}")
            break

    print(f"  ✅ {cfg['name']}: {len(all_records)} active tax liens found")
    return all_records


# ── DB helpers ────────────────────────────────────────────────────────────────

def ensure_table():
    """Ensure texas_liens table has all required columns."""
    if not HAS_DB:
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Add columns that may be missing from earlier table versions
            alterations = [
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS grantor_name VARCHAR(300)",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS grantee_name VARCHAR(300)",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS file_number VARCHAR(50)",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS county VARCHAR(100)",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS town VARCHAR(100)",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS legal_description VARCHAR(500)",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS instrument_type VARCHAR(100)",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'publicsearch'",
                "CREATE INDEX IF NOT EXISTS idx_tx_liens_grantor ON texas_liens(grantor_name)",
                "CREATE INDEX IF NOT EXISTS idx_tx_liens_county ON texas_liens(county)",
                "CREATE INDEX IF NOT EXISTS idx_tx_liens_file ON texas_liens(file_number)",
                "CREATE INDEX IF NOT EXISTS idx_tx_liens_match ON texas_liens(tdlr_match_id) WHERE tdlr_match_id IS NOT NULL",
            ]
            for sql in alterations:
                try:
                    cur.execute(sql)
                except Exception:
                    pass  # Column/index already exists
        conn.commit()
        print("  ✅ Table ready: texas_liens")
    finally:
        conn.close()


def save_liens(liens: list[dict], dry_run: bool = False) -> dict:
    if not HAS_DB:
        out = DATA_DIR / f"texas_liens_{date.today().isoformat()}.json"
        out.write_text(json.dumps(liens, indent=2, default=str))
        print(f"  💾 Saved: {out}")
        return {"inserted": 0, "updated": 0}

    inserted = updated = skipped = 0
    conn     = get_connection()
    try:
        with conn.cursor() as cur:
            for lien in liens:
                try:
                    cur.execute("""
                        INSERT INTO texas_liens (
                            filing_number, grantor_name, grantee_name,
                            filing_type, filing_date, county, source
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (filing_number) DO UPDATE SET
                            filing_date  = EXCLUDED.filing_date,
                            county       = EXCLUDED.county,
                            updated_at   = NOW()
                        RETURNING (xmax = 0) AS was_inserted
                    """, (
                        lien["file_number"],
                        lien["taxpayer_name"],
                        lien["grantee_name"],
                        lien["instrument_type"],
                        lien["filing_date"],
                        lien["county"],
                        "publicsearch",
                    ))
                    row = cur.fetchone()
                    if row and row[0]:
                        inserted += 1
                    else:
                        updated += 1
                except Exception as e:
                    skipped += 1
                    if skipped <= 3:
                        print(f"  ⚠ DB: {e}")

        if not dry_run:
            conn.commit()
            print(f"  ✅ {inserted:,} new, {updated:,} updated, {skipped} errors")
        else:
            conn.rollback()
            print(f"  [DRY RUN] Would save {inserted+updated:,}")

    finally:
        conn.close()

    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def match_to_tdlr(county: str = None, dry_run: bool = False) -> dict:
    """Match texas_liens grantors against TDLR contacts using pg_trgm."""
    if not HAS_DB:
        return {"matched": 0}

    conn = get_connection()
    try:
        # Enable fuzzy matching
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        conn.commit()

        where = "WHERE tl.tdlr_match_id IS NULL"
        if county:
            where += f" AND tl.county = '{county}'"

        with conn.cursor() as cur:
            cur.execute(f"""
                UPDATE texas_liens tl
                SET tdlr_match_id = t.id
                FROM texas_tdlr_contacts t
                WHERE tl.tdlr_match_id IS NULL
                  {f"AND tl.county = '{county}'" if county else ""}
                  AND (
                    similarity(
                        regexp_replace(UPPER(COALESCE(tl.grantor_name, tl.debtor_name, '')),
                                       '(LLC|INC|CORP|LTD|CO|LP)', '', 'g'),
                        regexp_replace(UPPER(COALESCE(t.business_name, '')),
                                       '(LLC|INC|CORP|LTD|CO|LP)', '', 'g')
                    ) > 0.5
                    OR
                    similarity(
                        UPPER(COALESCE(tl.grantor_name, tl.debtor_name, '')),
                        UPPER(COALESCE(t.owner_name, ''))
                    ) > 0.5
                  )
                RETURNING tl.id, COALESCE(tl.grantor_name, tl.debtor_name),
                          t.business_name, t.license_type, t.business_county
            """)
            matched_rows = cur.fetchall()

        if not dry_run and matched_rows:
            # Mark TDLR contacts
            conn.commit()
            tdlr_ids = list({row[0] for row in matched_rows})
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE texas_tdlr_contacts
                    SET lien_match  = TRUE,
                        confidence  = 'high',
                        updated_at  = NOW()
                    WHERE id IN (
                        SELECT tdlr_match_id FROM texas_liens
                        WHERE tdlr_match_id IS NOT NULL
                    )
                """)
            conn.commit()

        matched = len(matched_rows)
        print(f"  ✅ Matched {matched:,} new liens to TDLR contacts")
        if matched_rows:
            for row in matched_rows[:10]:
                print(f"    {row[1][:40]:<40} → {(row[2] or '')[:30]} ({row[3]})")
            if matched > 10:
                print(f"    ... and {matched-10} more")

        return {"matched": matched}

    finally:
        conn.close()


def show_stats():
    if not HAS_DB:
        print("No DB")
        return
    conn = get_connection()
    try:
        print(f"\n{'='*60}")
        print(f"  Texas Liens (PublicSearch) Stats")
        print(f"  {date.today().isoformat()}")
        print(f"{'='*60}")
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    SELECT county, COUNT(*),
                           COUNT(*) FILTER (WHERE tdlr_match_id IS NOT NULL)
                    FROM texas_liens
                    WHERE source = 'publicsearch'
                    GROUP BY county ORDER BY COUNT(*) DESC
                """)
                rows = cur.fetchall()
                total = sum(r[1] for r in rows)
                matched = sum(r[2] for r in rows)
                print(f"  {'County':<15} {'Liens':>8}  {'Matched':>8}")
                print(f"  {'─'*15} {'─'*8}  {'─'*8}")
                for county, cnt, mat in rows:
                    print(f"  {county:<15} {cnt:>8,}  {mat:>8,}")
                print(f"  {'─'*15} {'─'*8}  {'─'*8}")
                print(f"  {'TOTAL':<15} {total:>8,}  {matched:>8,}")

                cur.execute("""
                    SELECT COUNT(*) FROM texas_tdlr_contacts
                    WHERE lien_match = TRUE
                """)
                tdlr_matched = cur.fetchone()[0]
                print(f"\n  TDLR contacts with lien match: {tdlr_matched:,}")

                cur.execute("""
                    SELECT COUNT(*) FROM texas_tdlr_contacts
                    WHERE lien_match = TRUE
                    AND email IS NOT NULL AND email != ''
                """)
                print(f"  Matched + email (ready to email): {cur.fetchone()[0]:,}")

            except Exception as e:
                print(f"  texas_liens table: {e}")
        print(f"{'='*60}\n")
    finally:
        conn.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="PublicSearch TX Lien Scraper — Dallas/Tarrant/Collin")
    parser.add_argument("--county", default=None,
                        choices=list(COUNTIES.keys()),
                        help="Single county to scrape")
    parser.add_argument("--all",    action="store_true",
                        help="Scrape all 3 counties")
    parser.add_argument("--days",   type=int, default=180,
                        help="Days back to scrape (default 180)")
    parser.add_argument("--match",  action="store_true",
                        help="Match liens to TDLR after scraping")
    parser.add_argument("--stats",  action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    if not args.county and not args.all:
        parser.print_help()
        return

    counties = list(COUNTIES.keys()) if args.all else [args.county]

    print(f"\n{'='*60}")
    print(f"  PublicSearch TX Lien Scraper")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    print(f"  Counties : {', '.join(c.title() for c in counties)}")
    print(f"  Days back: {args.days}")
    print(f"  {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"{'='*60}\n")

    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("publicsearch_tx_scraper")
        logger.start()
    except ImportError:
        logger = None

    ensure_table()

    all_liens = []
    results   = {}

    for county_key in counties:
        print(f"\n── {COUNTIES[county_key]['name']} County ──")
        if logger: logger.step_start(f"scrape_{county_key}")

        liens = scrape_county(county_key, days_back=args.days,
                              dry_run=args.dry_run)
        all_liens.extend(liens)

        if liens:
            # Save JSON backup
            out = DATA_DIR / f"{county_key}_liens_{date.today().isoformat()}.json"
            out.write_text(json.dumps(liens, indent=2, default=str))
            print(f"  💾 Backup: {out}")

            # Save to DB
            result = save_liens(liens, dry_run=args.dry_run)
            results[county_key] = result
        else:
            results[county_key] = {"inserted": 0, "updated": 0}

        if logger:
            logger.step_done(f"scrape_{county_key}", ok=True,
                             detail=f"{len(liens)} liens")

        time.sleep(2)  # polite delay between counties

    # Match to TDLR
    if args.match and not args.dry_run and all_liens:
        print(f"\n── Matching to TDLR Contacts ──")
        if logger: logger.step_start("match_tdlr")
        match_result = match_to_tdlr(dry_run=args.dry_run)
        if logger:
            logger.step_done("match_tdlr", ok=True,
                             detail=str(match_result))

    # Summary
    total = sum(r.get("inserted", 0) + r.get("updated", 0)
                for r in results.values())
    print(f"\n{'='*60}")
    print(f"  PublicSearch Scraper Complete")
    for county, result in results.items():
        name = COUNTIES[county]["name"]
        ins  = result.get("inserted", 0)
        upd  = result.get("updated", 0)
        print(f"  {name:<10} {ins:>5,} new  {upd:>5,} updated")
    print(f"{'='*60}\n")

    show_stats()

    if logger:
        logger.finish({
            "counties":  counties,
            "total":     total,
            "days_back": args.days,
            "dry_run":   args.dry_run,
        })


if __name__ == "__main__":
    main()