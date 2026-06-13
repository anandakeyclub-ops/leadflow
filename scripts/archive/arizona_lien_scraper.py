"""
arizona_lien_scraper.py  (v2 - Maricopa HTML scraper)
======================================================
Scrapes federal tax liens from Maricopa County recorder public search.

CONFIRMED working URL pattern (from live site):
  https://recorder.maricopa.gov/recording/document-search-results.html
    ?documentTypeSelector=code
    &documentCode=FL
    &beginDate=2025-01-01
    &endDate=2026-06-05
    (no page param — site uses pagination links)

Results structure (from live PDF):
  - 500+ results for Jan 2025 - Jun 2026
  - 25 pages, 20 results per page
  - Each row: Recording Number | Recording Date | Document Code(s)
  - Document detail popup: NAME(S) — debtor name + INTERNAL REVENUE SERVICE
  - Individual PDF: publicapi.recorder.maricopa.gov/preview/pdf?recordingNumber=XXXXX

Strategy:
  1. Scrape list pages to get recording numbers + dates
  2. For each recording number, fetch document detail (JSON API or detail page)
     to get debtor name
  3. Import debtor name + date + recording number into normalized_liens
  4. Optionally download PDF for lien amount (not required for matching)

Pipeline:
  scrape -> normalized_liens (AZ) -> match ROC -> enrich -> bridge -> email

Usage:
  python arizona_lien_scraper.py --scrape --days 365 --dry-run
  python arizona_lien_scraper.py --scrape --days 365
  python arizona_lien_scraper.py --match
  python arizona_lien_scraper.py --validate
  python arizona_lien_scraper.py --stats
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

DATA_DIR = LEADFLOW_DIR / "data" / "arizona"
DATA_DIR.mkdir(parents=True, exist_ok=True)

try:
    from app.core.db import get_connection
    HAS_DB = True
except ImportError:
    HAS_DB = False

# ── Constants ──────────────────────────────────────────────────────────────────
BASE_URL   = "https://recorder.maricopa.gov"
SEARCH_URL = (
    f"{BASE_URL}/recording/document-search-results.html"
    "?documentTypeSelector=code&documentCode=FL"
    "&beginDate={begin}&endDate={end}&page={page}"
)
DETAIL_API = "https://publicapi.recorder.maricopa.gov"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         BASE_URL,
}


# ── Page scraper ───────────────────────────────────────────────────────────────
def fetch_list_page(begin: str, end: str, page: int,
                    session: requests.Session) -> tuple[list[dict], bool]:
    """
    Fetch one page of search results.
    Returns (records, has_more_pages).

    Each record: {recording_number, recording_date, doc_code}
    Debtor names are NOT on the list — must fetch per-record.
    """
    url = SEARCH_URL.format(begin=begin, end=end, page=page)
    try:
        r = session.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"    Page {page}: HTTP {r.status_code}")
            return [], False

        soup = BeautifulSoup(r.text, "html.parser")

        # Parse table rows
        records = []
        # Find the results table — recording number | date | doc code
        rows = soup.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            rec_num  = cells[0].get_text(strip=True)
            rec_date = cells[1].get_text(strip=True)
            doc_code = cells[2].get_text(strip=True)

            # Validate recording number format (8+ digits)
            if not re.match(r"^\d{8,}$", rec_num):
                continue
            if "FED TAX" not in doc_code.upper() and "FL" not in doc_code.upper():
                continue

            records.append({
                "recording_number": rec_num,
                "recording_date":   rec_date,
                "doc_code":         doc_code,
            })

        # Check for next page
        pagination = soup.find_all("a", string=re.compile(r"^\d+$"))
        page_nums  = [int(a.get_text()) for a in pagination
                      if a.get_text().strip().isdigit()]
        has_more   = (page + 1) in page_nums or len(records) == 20

        return records, has_more

    except Exception as e:
        print(f"    Page {page} error: {e}")
        return [], False


def fetch_debtor_name(recording_number: str,
                      session: requests.Session) -> tuple[str, float | None]:
    """
    Fetch debtor name for a recording number.
    Tries the public API detail endpoint first, then falls back to PDF parse.

    Returns (debtor_name, lien_amount_or_None).
    """
    # Try JSON detail API first
    detail_endpoints = [
        f"{DETAIL_API}/api/document/{recording_number}",
        f"{DETAIL_API}/document/details/{recording_number}",
        f"{BASE_URL}/recording/document-detail/{recording_number}",
    ]

    for endpoint in detail_endpoints:
        try:
            r = session.get(endpoint, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                try:
                    data = r.json()
                    # Try common field names
                    for name_field in ["grantor", "grantorName", "GrantorName",
                                       "debtor", "names", "name"]:
                        val = data.get(name_field)
                        if val and isinstance(val, str) and len(val) > 2:
                            # Filter out IRS entries
                            if "INTERNAL REVENUE" not in val.upper():
                                return val.strip(), data.get("amount")
                    # If names is a list
                    names = data.get("names", [])
                    if isinstance(names, list):
                        for n in names:
                            name_val = n.get("name", n) if isinstance(n, dict) else str(n)
                            if "INTERNAL REVENUE" not in name_val.upper():
                                return name_val.strip(), None
                except Exception:
                    # Not JSON — try HTML parse
                    soup  = BeautifulSoup(r.text, "html.parser")
                    names = soup.find_all(
                        string=re.compile(
                            r"[A-Z]{2,}.*[A-Z]{2,}",
                            re.IGNORECASE
                        )
                    )
                    for name_text in names:
                        clean = name_text.strip()
                        if (len(clean) > 4
                                and "INTERNAL REVENUE" not in clean.upper()
                                and "MARICOPA" not in clean.upper()
                                and "RECORDER" not in clean.upper()):
                            return clean, None
        except Exception:
            continue

    return "", None


def scrape_maricopa_liens(days_back: int = 365,
                           dry_run: bool = False,
                           max_pages: int = 25) -> list[dict]:
    """
    Full Maricopa scrape: list pages → recording numbers → debtor names.
    Returns list of lien dicts ready for import.
    """
    end_dt   = date.today()
    begin_dt = end_dt - timedelta(days=days_back)
    begin    = begin_dt.strftime("%Y-%m-%d")
    end      = end_dt.strftime("%Y-%m-%d")

    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"  Fetching recording numbers {begin} → {end}...")

    # Step 1: Collect all recording numbers from list pages
    all_stubs = []
    for page in range(1, max_pages + 1):
        stubs, has_more = fetch_list_page(begin, end, page, session)
        all_stubs.extend(stubs)
        print(f"    Page {page}: {len(stubs)} records "
              f"({len(all_stubs)} total)", end="")
        if not has_more or len(stubs) == 0:
            print(" [done]")
            break
        print()
        time.sleep(0.8)

    print(f"  Found {len(all_stubs)} recording numbers total")

    if not all_stubs:
        return []

    # Step 2: Fetch debtor name for each recording number
    print(f"  Fetching debtor names (this takes a few minutes)...")
    liens = []
    errors = 0

    # Save progress to CSV so we can resume
    progress_file = DATA_DIR / f"maricopa_progress_{date.today().isoformat()}.csv"

    # Load already-fetched names if resuming
    fetched: dict[str, str] = {}
    if progress_file.exists():
        with open(progress_file, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("debtor_name"):
                    fetched[row["recording_number"]] = row["debtor_name"]
        print(f"  Resuming: {len(fetched)} already fetched")

    with open(progress_file, "a", newline="", encoding="utf-8") as csvf:
        writer = csv.DictWriter(csvf, fieldnames=[
            "recording_number", "recording_date", "debtor_name", "lien_amount"
        ])
        if not fetched:
            writer.writeheader()

        for i, stub in enumerate(all_stubs):
            rec_num = stub["recording_number"]

            # Skip if already fetched
            if rec_num in fetched:
                debtor = fetched[rec_num]
            else:
                if dry_run:
                    # Dry run: just show what we have from list
                    debtor = f"[DRY RUN - recording {rec_num}]"
                    lien_amount = None
                else:
                    debtor, lien_amount = fetch_debtor_name(rec_num, session)
                    time.sleep(0.4)  # polite delay

                if not debtor:
                    errors += 1
                    continue

                writer.writerow({
                    "recording_number": rec_num,
                    "recording_date":   stub["recording_date"],
                    "debtor_name":      debtor,
                    "lien_amount":      lien_amount or "",
                })
                csvf.flush()

            # Parse date
            try:
                filed_date = datetime.strptime(
                    stub["recording_date"], "%m-%d-%Y"
                ).date()
            except Exception:
                try:
                    filed_date = datetime.strptime(
                        stub["recording_date"], "%m/%d/%Y"
                    ).date()
                except Exception:
                    filed_date = date.today()

            # Build normalized record
            debtor_clean  = re.sub(r"\s+", " ", debtor.upper().strip())
            is_individual = bool(re.match(r"^[A-Z\-\']+,\s+[A-Z]", debtor_clean))
            biz_name      = "" if is_individual else debtor_clean
            norm_hash     = hashlib.md5(
                f"maricopa_recorder|{debtor_clean}|Maricopa|AZ|{rec_num}".encode()
            ).hexdigest()

            lien_rec = {
                "debtor_name":     debtor_clean[:250],
                "business_name":   biz_name[:250],
                "county_name":     "Maricopa",
                "state":           "AZ",
                "filing_type":     "FEDERAL TAX LIEN",
                "lien_type":       "IRS FEDERAL",
                "lien_source":     "maricopa_recorder",
                "filed_date":      filed_date,
                "lien_amount":     None,
                "doc_number":      rec_num[:100],
                "normalized_hash": norm_hash,
                "is_individual":   is_individual,
            }
            liens.append(lien_rec)

            if (i + 1) % 50 == 0:
                print(f"    [{i+1}/{len(all_stubs)}] "
                      f"{len(liens)} names fetched, {errors} failed")

    print(f"  Complete: {len(liens)} liens, {errors} name-fetch failures")
    return liens


# ── Import to DB ───────────────────────────────────────────────────────────────
def import_to_db(records: list[dict], dry_run: bool = False) -> dict:
    if not HAS_DB:
        print("  No DB"); return {"imported": 0, "skipped": 0}
    if not records:
        print("  No records"); return {"imported": 0, "skipped": 0}

    conn = get_connection()
    imported = skipped = errors = 0

    try:
        # Ensure Maricopa county exists
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO counties (county_name, state, active, created_at)
                VALUES ('Maricopa', 'AZ', TRUE, NOW())
                ON CONFLICT (county_name, state) DO NOTHING
            """)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM counties WHERE county_name='Maricopa' AND state='AZ'"
            )
            county_id = cur.fetchone()[0]

        for rec in records:
            if dry_run:
                debtor_disp = rec["debtor_name"]
                if "[DRY RUN" not in debtor_disp:
                    print(f"  [DRY] {debtor_disp[:45]:<45} "
                          f"{str(rec['filed_date'])[:10]}")
                imported += 1
                continue

            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO normalized_liens (
                        county_id, debtor_name, business_name,
                        filing_type, lien_type, lien_source,
                        filed_date, lien_amount, doc_number,
                        normalized_hash, state
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'AZ')
                    ON CONFLICT (normalized_hash) DO NOTHING
                """, (
                    county_id,
                    rec["debtor_name"], rec["business_name"],
                    rec["filing_type"], rec["lien_type"],
                    rec["lien_source"], rec["filed_date"],
                    rec["lien_amount"], rec["doc_number"],
                    rec["normalized_hash"],
                ))
                if cur.rowcount > 0:
                    imported += 1
                else:
                    skipped += 1

        conn.commit()

    except Exception as e:
        conn.rollback()
        print(f"  Import error: {e}")
        import traceback; traceback.print_exc()
        errors += 1
    finally:
        conn.close()

    return {"imported": imported, "skipped": skipped, "errors": errors}


# ── Import from CSV (for manual download fallback) ─────────────────────────────
def import_from_csv(csv_path: str, dry_run: bool = False) -> dict:
    """
    Import from a CSV file with columns:
      recording_number, recording_date, debtor_name, lien_amount

    Use this if manual browser export is faster than scraping.
    """
    path = Path(csv_path)
    if not path.exists():
        print(f"  File not found: {csv_path}")
        return {"imported": 0}

    records = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        # Try to detect delimiter
        sample = f.read(2048)
        f.seek(0)
        delimiter = "\t" if "\t" in sample else ","
        reader = csv.DictReader(f, delimiter=delimiter)

        for row in reader:
            # Flexible column name matching
            debtor = (
                row.get("debtor_name") or row.get("GRANTOR") or
                row.get("Name") or row.get("names") or
                row.get("debtor") or ""
            ).strip()

            if not debtor or "INTERNAL REVENUE" in debtor.upper():
                continue

            rec_num = (
                row.get("recording_number") or row.get("RECORDING NUMBER") or
                row.get("RecordingNumber") or row.get("doc_number") or ""
            ).strip()

            date_str = (
                row.get("recording_date") or row.get("RECORDING DATE") or
                row.get("RecordingDate") or row.get("filed_date") or ""
            ).strip()

            try:
                filed_date = datetime.strptime(date_str, "%m-%d-%Y").date()
            except Exception:
                try:
                    filed_date = datetime.strptime(date_str, "%m/%d/%Y").date()
                except Exception:
                    try:
                        filed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    except Exception:
                        filed_date = date.today()

            amount_raw = (
                row.get("lien_amount") or row.get("Amount") or
                row.get("amount") or ""
            ).strip().replace("$", "").replace(",", "")
            try:
                lien_amount = float(amount_raw) if amount_raw else None
            except Exception:
                lien_amount = None

            debtor_clean  = re.sub(r"\s+", " ", debtor.upper().strip())
            is_individual = bool(re.match(r"^[A-Z\-\']+,\s+[A-Z]", debtor_clean))
            biz_name      = "" if is_individual else debtor_clean
            norm_hash     = hashlib.md5(
                f"maricopa_recorder|{debtor_clean}|Maricopa|AZ|{rec_num}".encode()
            ).hexdigest()

            records.append({
                "debtor_name":     debtor_clean[:250],
                "business_name":   biz_name[:250],
                "county_name":     "Maricopa",
                "state":           "AZ",
                "filing_type":     "FEDERAL TAX LIEN",
                "lien_type":       "IRS FEDERAL",
                "lien_source":     "maricopa_recorder",
                "filed_date":      filed_date,
                "lien_amount":     lien_amount,
                "doc_number":      rec_num[:100],
                "normalized_hash": norm_hash,
                "is_individual":   is_individual,
            })

    print(f"  Parsed {len(records)} liens from {path.name}")
    return import_to_db(records, dry_run)


# ── Match ROC contacts against AZ liens ────────────────────────────────────────
def match_roc_to_liens(min_similarity: float = 0.45) -> dict:
    """
    Match arizona_roc_contacts against normalized_liens WHERE state='AZ'.
    Uses pg_trgm trigram similarity.
    Sets lien_match=TRUE on confirmed matches.
    """
    if not HAS_DB:
        print("  No DB"); return {"matched": 0}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(id) FROM normalized_liens WHERE state = 'AZ'"
            )
            az_count = cur.fetchone()[0]

        if az_count == 0:
            print("  No AZ liens — run --scrape first")
            return {"matched": 0, "error": "no_az_liens"}

        print(f"  Matching against {az_count:,} AZ liens...")

        # Business name similarity match
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE arizona_roc_contacts arc
                SET lien_match = TRUE
                FROM normalized_liens nl
                WHERE nl.state = 'AZ'
                  AND arc.lien_match IS NOT TRUE
                  AND arc.business_name IS NOT NULL
                  AND LENGTH(arc.business_name) > 3
                  AND nl.business_name IS NOT NULL
                  AND LENGTH(nl.business_name) > 3
                  AND (
                      similarity(UPPER(arc.business_name),
                                 UPPER(nl.business_name)) > %s
                      OR (
                          LENGTH(arc.business_name) > 8
                          AND UPPER(nl.debtor_name) LIKE
                              '%%' ||
                              SPLIT_PART(UPPER(arc.business_name), ' ', 1)
                              || '%%'
                      )
                  )
            """, (min_similarity,))
            biz_matched = cur.rowcount
        conn.commit()

        # Owner name match (individuals)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE arizona_roc_contacts arc
                SET lien_match = TRUE
                FROM normalized_liens nl
                WHERE nl.state = 'AZ'
                  AND arc.lien_match IS NOT TRUE
                  AND arc.owner_name IS NOT NULL
                  AND LENGTH(arc.owner_name) > 4
                  AND similarity(UPPER(arc.owner_name),
                                 UPPER(nl.debtor_name)) > %s
            """, (min_similarity + 0.1,))
            owner_matched = cur.rowcount
        conn.commit()

        total = biz_matched + owner_matched
        print(f"  Business matches : {biz_matched}")
        print(f"  Owner matches    : {owner_matched}")
        print(f"  Total new matches: {total}")

        # Show sample
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    arc.business_name, arc.owner_name, arc.county,
                    nl.debtor_name, nl.lien_amount, nl.filed_date
                FROM arizona_roc_contacts arc
                JOIN normalized_liens nl ON (
                    nl.state = 'AZ'
                    AND (
                        similarity(UPPER(COALESCE(arc.business_name,'')),
                                   UPPER(COALESCE(nl.business_name,''))) > %s
                        OR similarity(UPPER(COALESCE(arc.owner_name,'')),
                                      UPPER(nl.debtor_name)) > %s
                    )
                )
                WHERE arc.lien_match = TRUE
                ORDER BY nl.lien_amount DESC NULLS LAST
                LIMIT 10
            """, (min_similarity, min_similarity + 0.1))
            samples = cur.fetchall()

        if samples:
            print(f"\n  Top matches:")
            for biz, owner, county, lien_debtor, amount, filed in samples:
                roc  = (biz or owner or "?")[:35]
                lien = lien_debtor[:35]
                amt  = f"${amount:,.0f}" if amount else "no amount"
                print(f"    {roc:<35} <-> {lien:<35} {amt}")

        return {"matched": total, "business": biz_matched, "owner": owner_matched}

    finally:
        conn.close()


# ── Stats ──────────────────────────────────────────────────────────────────────
def show_stats():
    if not HAS_DB:
        print("No DB"); return

    conn = get_connection()
    try:
        print(f"\n{'='*60}")
        print("  AZ Lien Pipeline Status")
        print(f"{'='*60}")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(id) FROM normalized_liens WHERE state = 'AZ'"
            )
            az_liens = cur.fetchone()[0]

        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(id),
                       COUNT(CASE WHEN is_individual IS FALSE
                                  OR is_individual IS NULL THEN 1 END),
                       MIN(filed_date), MAX(filed_date)
                FROM normalized_liens WHERE state = 'AZ'
            """)
            row = cur.fetchone()
            businesses = row[1] if row else 0
            min_date   = row[2] if row else None
            max_date   = row[3] if row else None

        print(f"  normalized_liens (AZ) : {az_liens:,}")
        print(f"    Businesses          : {businesses:,}")
        print(f"    Individuals         : {az_liens - businesses:,}")
        if min_date:
            print(f"    Date range          : {min_date} → {max_date}")

        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(id)                                              AS total,
                    COUNT(CASE WHEN lien_match = TRUE THEN 1 END)          AS matched,
                    COUNT(CASE WHEN lien_match = TRUE
                               AND email IS NOT NULL
                               AND email != '' THEN 1 END)               AS matched_email,
                    COUNT(CASE WHEN lien_match IS NOT TRUE THEN 1 END)     AS unmatched
                FROM arizona_roc_contacts
            """)
            r = cur.fetchone()
            print(f"\n  arizona_roc_contacts")
            print(f"    Total             : {r[0]:,}")
            print(f"    Matched           : {r[1]:,}")
            print(f"    Matched + email   : {r[2]:,}  <- ready for bridge")
            print(f"    Unmatched         : {r[3]:,}  <- do not enrich")

        print(f"\n  Pipeline status:")
        if az_liens == 0:
            print(f"    STEP 1 NEEDED: Scrape or import AZ lien data")
        elif r[1] == 0:
            print(f"    STEP 2 NEEDED: Run --match to match ROC against liens")
        elif r[2] == 0:
            print(f"    STEP 3 NEEDED: Run SerpAPI enrichment on {r[1]:,} matched contacts")
        else:
            print(f"    STEP 4 NEEDED: Run bridge_to_email_pool.py --source roc")

        print(f"{'='*60}\n")
    finally:
        conn.close()


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Maricopa County Federal Tax Lien Scraper + ROC Matcher")
    parser.add_argument("--scrape",  action="store_true",
                        help="Scrape Maricopa recorder for FTL filings")
    parser.add_argument("--file",    default=None,
                        help="Import from CSV file instead of scraping")
    parser.add_argument("--days",    type=int, default=365)
    parser.add_argument("--pages",   type=int, default=25,
                        help="Max pages to scrape (20 records/page, default 25)")
    parser.add_argument("--match",   action="store_true",
                        help="Match ROC contacts against AZ normalized_liens")
    parser.add_argument("--min-similarity", type=float, default=0.45)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stats",   action="store_true")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  Maricopa Federal Tax Lien Scraper v2")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    print(f"  {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"{'='*60}\n")

    if args.stats:
        show_stats(); return

    if args.file:
        print(f"Importing from {args.file}...")
        result = import_from_csv(args.file, args.dry_run)
        print(f"  Imported: {result.get('imported',0):,}  "
              f"Skipped: {result.get('skipped',0):,}")
        show_stats(); return

    if args.match:
        print("Matching ROC contacts against AZ liens...")
        result = match_roc_to_liens(args.min_similarity)
        print(f"\nTotal new matches: {result.get('matched', 0):,}")
        show_stats(); return

    if args.scrape:
        print(f"Scraping Maricopa County ({args.days} days, "
              f"max {args.pages} pages)...")
        liens = scrape_maricopa_liens(
            days_back=args.days,
            dry_run=args.dry_run,
            max_pages=args.pages,
        )
        if liens and not args.dry_run:
            print(f"\nImporting {len(liens)} liens to DB...")
            result = import_to_db(liens, dry_run=False)
            print(f"  Imported : {result['imported']:,}")
            print(f"  Skipped  : {result['skipped']:,}")
            print(f"\nNext: python arizona_lien_scraper.py --match")
        elif args.dry_run:
            print(f"\n[DRY RUN] Would import {len(liens)} liens")
            print(f"Run without --dry-run to write to DB")
        show_stats(); return

    parser.print_help()


if __name__ == "__main__":
    main()