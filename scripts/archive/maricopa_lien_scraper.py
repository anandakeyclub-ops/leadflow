"""
maricopa_lien_scraper.py  (v6 - confirmed working endpoints)
=============================================================
CONFIRMED ENDPOINTS (June 5, 2026):

  LIST:   GET /documents/search?documentCode=FL&beginDate=...&endDate=...
          &pageSize=20&pageNumber=N&maxResults=500
          Response: {"searchResults":[{"recordingNumber":20250036083,
                     "recordingDate":"1-22-2025","names":"", ...}],
                    "totalResults":501}
          NOTE: names is always "" in list — must fetch detail per record.

  DETAIL: GET /documents/{recordingNumber}
          Response: {"names":["BELTRAN ANTHONY G","INTERNAL REVENUE SERVICE"],
                     "recordingDate":"1-22-2025","recordingNumber":"20250036083",...}

PIPELINE:
  1. List all recording numbers (501 results / 26 pages)
  2. Fetch detail for each → extract non-IRS debtor name
  3. Import into normalized_liens (state=AZ)
  4. --match → match ROC contacts via pg_trgm
  5. --enrich → SerpAPI on matched (July 1)
  6. bridge → email sequence

DB: localhost:5434, leadflow, postgres/postgres
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg2
import requests
from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

DATA_DIR = LEADFLOW_DIR / "data" / "arizona"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB = dict(host="localhost", port=5434, dbname="leadflow",
          user="postgres", password="postgres")

LIST_URL   = "https://publicapi.recorder.maricopa.gov/documents/search"
DETAIL_URL = "https://publicapi.recorder.maricopa.gov/documents/{}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":  "application/json, */*",
    "Referer": "https://recorder.maricopa.gov/",
    "Origin":  "https://recorder.maricopa.gov",
}


# ── Step 1: Fetch all recording numbers ───────────────────────────────────────
def fetch_all_recording_numbers(begin: str, end: str,
                                 session: requests.Session) -> list[dict]:
    """
    Page through /documents/search and collect all recording numbers + dates.
    Returns list of {recording_number, recording_date} stubs.
    """
    stubs    = []
    page_num = 1
    total    = 9999  # sentinel; replaced by real value from page 1

    while True:
        params = {
            "businessNames": "",
            "firstNames":    "",
            "lastNames":     "",
            "middleNameIs":  "",
            "documentCode":  "FL",
            "beginDate":     begin,
            "endDate":       end,
            "pageSize":      20,
            "pageNumber":    page_num,
            "maxResults":    500,
        }
        try:
            r = session.get(LIST_URL, params=params,
                            headers=HEADERS, timeout=20)
            if r.status_code != 200:
                print(f"    List page {page_num}: HTTP {r.status_code}")
                break
            data  = r.json()
            items = data.get("searchResults", [])
            # totalResults only reliable on page 1; -1 on subsequent pages
            page_total = int(data.get("totalResults", -1))
            if page_total > 0:
                total = page_total
        except Exception as e:
            print(f"    List page {page_num} error: {e}")
            break

        for item in items:
            stubs.append({
                "recording_number": str(item["recordingNumber"]),
                "recording_date":   item.get("recordingDate", ""),
            })

        print(f"    Page {page_num}: {len(items)} records "
              f"({len(stubs)}/{total} total)")

        if len(items) < 20:
            break  # last page
        if len(stubs) >= total:
            break  # got everything
        page_num += 1
        time.sleep(0.2)

    return stubs


# ── Step 2: Fetch debtor name for one recording number ─────────────────────────
def fetch_debtor(recording_number: str,
                 session: requests.Session) -> str:
    """
    GET /documents/{recordingNumber}
    Returns the non-IRS name, or "" if not found.
    """
    url = DETAIL_URL.format(recording_number)
    try:
        r = session.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return ""
        data  = r.json()
        names = data.get("names", [])
        if isinstance(names, list):
            for name in names:
                if isinstance(name, str) and "INTERNAL REVENUE" not in name.upper():
                    return name.strip()
        elif isinstance(names, str) and names:
            return names.strip()
    except Exception:
        pass
    return ""


# ── Full scrape ────────────────────────────────────────────────────────────────
def scrape_maricopa(days_back: int = 365,
                    dry_run: bool = False) -> list[dict]:
    """
    Full scrape:
    1. Collect all recording numbers via list endpoint
    2. Fetch debtor name for each via detail endpoint
    3. Return list of complete lien stubs
    """
    end_dt   = date.today()
    begin_dt = end_dt - timedelta(days=days_back)
    begin    = begin_dt.strftime("%Y-%m-%d")
    end      = end_dt.strftime("%Y-%m-%d")

    session = requests.Session()

    # ── Phase 1: recording numbers ─────────────────────────────────────
    # ── Phase 1: recording numbers ─────────────────────────────────────
    print(f"  Phase 1: Collecting recording numbers {begin} → {end}...")

    # API caps at ~500 records per query regardless of date range.
    # Split into 60-day chunks so each stays well under the cap.
    chunks = []
    chunk_start = begin_dt
    while chunk_start < end_dt:
        chunk_end = min(chunk_start + timedelta(days=60), end_dt)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)

    print(f"  Splitting into {len(chunks)} x 60-day chunks...")

    stubs = []
    for i, (cs, ce) in enumerate(chunks):
        chunk_stubs = fetch_all_recording_numbers(
            cs.strftime("%Y-%m-%d"), ce.strftime("%Y-%m-%d"), session
        )
        stubs.extend(chunk_stubs)
        print(f"  Chunk {i+1}/{len(chunks)}: {cs} -> {ce} = "
              f"{len(chunk_stubs)} records ({len(stubs)} total)")
        time.sleep(0.3)

    print(f"  Found {len(stubs)} recording numbers total")

    if not stubs:
        return []

    if dry_run:
        print(f"\n  [DRY RUN] Sample recording numbers:")
        for s in stubs[:5]:
            print(f"    {s['recording_number']}  {s['recording_date']}")
        print(f"  Would fetch {len(stubs)} debtor names then import")
        return stubs  # Return stubs so caller can see count

    # ── Phase 2: debtor names ──────────────────────────────────────────
    print(f"\n  Phase 2: Fetching {len(stubs)} debtor names...")
    print(f"  Estimated time: ~{len(stubs) * 0.35 / 60:.1f} min")

    # Check progress file — resume if interrupted
    progress_file = DATA_DIR / f"maricopa_{date.today().isoformat()}_names.csv"
    fetched: dict[str, str] = {}
    if progress_file.exists():
        with open(progress_file, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("debtor_name") is not None:
                    fetched[row["recording_number"]] = row["debtor_name"]
        print(f"  Resuming: {len(fetched)} already fetched")

    complete_stubs = []
    errors         = 0

    with open(progress_file, "a", newline="", encoding="utf-8") as csvf:
        writer = csv.DictWriter(csvf, fieldnames=[
            "recording_number", "recording_date", "debtor_name"
        ])
        if not fetched:
            writer.writeheader()

        for i, stub in enumerate(stubs):
            rec_num = stub["recording_number"]

            # Resume from progress
            if rec_num in fetched:
                debtor = fetched[rec_num]
            else:
                debtor = fetch_debtor(rec_num, session)
                writer.writerow({
                    "recording_number": rec_num,
                    "recording_date":   stub["recording_date"],
                    "debtor_name":      debtor,
                })
                csvf.flush()
                time.sleep(0.3)

            complete_stubs.append({
                "recording_number": rec_num,
                "recording_date":   stub["recording_date"],
                "debtor_name":      debtor,
            })

            # Progress report every 50
            if (i + 1) % 50 == 0 or (i + 1) == len(stubs):
                with_name = sum(1 for s in complete_stubs if s["debtor_name"])
                print(f"    [{i+1}/{len(stubs)}] "
                      f"{with_name} with names, {errors} errors")

    with_name = sum(1 for s in complete_stubs if s["debtor_name"])
    print(f"\n  Complete: {len(complete_stubs)} records, "
          f"{with_name} with debtor names")
    return complete_stubs


# ── Build normalized lien record ───────────────────────────────────────────────
def build_lien_record(stub: dict) -> dict | None:
    debtor  = stub.get("debtor_name", "").strip()
    rec_num = stub.get("recording_number", "").strip()
    date_str = stub.get("recording_date", "").strip()

    if not rec_num or not debtor:
        return None
    if "INTERNAL REVENUE" in debtor.upper():
        return None

    filed_date = date.today()
    for fmt in ["%m-%d-%Y", "%m/%d/%Y", "%Y-%m-%d"]:
        try:
            filed_date = datetime.strptime(date_str, fmt).date()
            break
        except Exception:
            pass

    debtor_clean  = re.sub(r"\s+", " ", debtor.upper().strip())
    is_individual = bool(re.match(r"^[A-Z\-\']+,\s+[A-Z]", debtor_clean))
    biz_name      = "" if is_individual else debtor_clean
    norm_hash     = hashlib.md5(
        f"maricopa|{debtor_clean}|Maricopa|AZ|{rec_num}".encode()
    ).hexdigest()

    return {
        "debtor_name":     debtor_clean[:250],
        "business_name":   biz_name[:250],
        "filing_type":     "FEDERAL TAX LIEN",
        "lien_type":       "IRS FEDERAL",
        "lien_source":     "maricopa_recorder",
        "filed_date":      filed_date,
        "lien_amount":     None,
        "doc_number":      rec_num[:100],
        "normalized_hash": norm_hash,
    }


# ── Import to normalized_liens ─────────────────────────────────────────────────
def import_to_db(records: list[dict], dry_run: bool = False) -> dict:
    if not records:
        print("  No records to import"); return {"imported": 0, "skipped": 0}

    conn = psycopg2.connect(**DB)
    imported = skipped = 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO counties (county_name, state, active, created_at)
                SELECT 'Maricopa', 'AZ', TRUE, NOW()
                WHERE NOT EXISTS (
                    SELECT 1 FROM counties
                    WHERE county_name='Maricopa' AND state='AZ'
                )
            """)
            cur.execute(
                "SELECT id FROM counties WHERE county_name='Maricopa' AND state='AZ'"
            )
            county_id = cur.fetchone()[0]
        conn.commit()

        for rec in records:
            if dry_run:
                print(f"  [DRY] {rec['debtor_name'][:55]:<55} {str(rec['filed_date'])[:10]}")
                imported += 1
                continue
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO normalized_liens (
                        county_id, debtor_name, business_name,
                        filing_type, lien_type, lien_source,
                        filed_date, amount,
                        normalized_hash, state
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'AZ')
                    ON CONFLICT (normalized_hash) DO NOTHING
                """, (
                    county_id,
                    rec["debtor_name"], rec["business_name"],
                    rec["filing_type"], rec["lien_type"],
                    rec["lien_source"], rec["filed_date"],
                    rec["lien_amount"],
                    rec["normalized_hash"],
                ))
                imported += cur.rowcount
                skipped  += 1 - cur.rowcount
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"  Import error: {e}")
        import traceback; traceback.print_exc()
    finally:
        conn.close()
    return {"imported": imported, "skipped": skipped}


# ── Import from CSV ────────────────────────────────────────────────────────────
def import_from_csv(csv_path: str, dry_run: bool = False) -> dict:
    path = Path(csv_path)
    if not path.exists():
        print(f"  File not found: {csv_path}"); return {"imported": 0}
    stubs = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        sample = f.read(2048); f.seek(0)
        reader = csv.DictReader(f, delimiter="\t" if "\t" in sample else ",")
        for row in reader:
            stubs.append({
                "recording_number": row.get("recording_number",""),
                "recording_date":   row.get("recording_date",""),
                "debtor_name":      row.get("debtor_name",""),
            })
    records = [r for s in stubs if (r := build_lien_record(s))]
    print(f"  Parsed {len(records)} valid liens from {path.name}")
    return import_to_db(records, dry_run)


# ── Match ROC contacts ─────────────────────────────────────────────────────────
def match_roc_to_liens(min_similarity: float = 0.45) -> dict:
    conn = psycopg2.connect(**DB)
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(id) FROM normalized_liens WHERE state='AZ'")
            az_count = cur.fetchone()[0]
        if az_count == 0:
            print("  No AZ liens yet — run --scrape first"); return {"matched": 0}
        print(f"  Matching against {az_count:,} AZ liens...")

        with conn.cursor() as cur:
            cur.execute("""
                UPDATE arizona_roc_contacts arc
                SET lien_match = TRUE
                FROM normalized_liens nl
                WHERE nl.state = 'AZ'
                  AND arc.lien_match IS NOT TRUE
                  AND arc.business_name IS NOT NULL
                  AND LENGTH(arc.business_name) > 3
                  AND (
                      similarity(UPPER(arc.business_name),
                                 UPPER(nl.debtor_name)) > %s
                      OR (LENGTH(arc.business_name) > 8
                          AND UPPER(nl.debtor_name) LIKE
                              '%%' || SPLIT_PART(UPPER(arc.business_name),' ',1) || '%%')
                  )
            """, (min_similarity,))
            biz_matched = cur.rowcount
        conn.commit()

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
        print(f"  Business matches : {biz_matched:,}")
        print(f"  Owner matches    : {owner_matched:,}")
        print(f"  Total            : {total:,}")

        if total > 0:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT arc.business_name, arc.owner_name,
                           nl.debtor_name, nl.filed_date
                    FROM arizona_roc_contacts arc
                    JOIN normalized_liens nl ON (
                        nl.state = 'AZ'
                        AND (
                            similarity(UPPER(COALESCE(arc.business_name,'')),
                                       UPPER(nl.debtor_name)) > %s
                            OR similarity(UPPER(COALESCE(arc.owner_name,'')),
                                          UPPER(nl.debtor_name)) > %s
                        )
                    )
                    WHERE arc.lien_match = TRUE
                    ORDER BY nl.filed_date DESC LIMIT 10
                """, (min_similarity, min_similarity + 0.1))
                for biz, owner, debtor, filed in cur.fetchall():
                    roc = (biz or owner or "?")[:35]
                    print(f"    {roc:<35} <-> {debtor[:35]:<35} {str(filed)[:10]}")

        return {"matched": total}
    finally:
        conn.close()


# ── Stats ──────────────────────────────────────────────────────────────────────
def show_stats():
    conn = psycopg2.connect(**DB)
    try:
        print(f"\n{'='*60}")
        print("  AZ Lien Pipeline Status")
        print(f"{'='*60}")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(id),
                       COUNT(CASE WHEN business_name IS NULL
                                    OR business_name='' THEN 1 END),
                       MIN(filed_date), MAX(filed_date)
                FROM normalized_liens WHERE state='AZ'
            """)
            r = cur.fetchone()
        az = r[0]
        print(f"  normalized_liens (AZ) : {az:,}")
        if az:
            print(f"    Businesses          : {az - r[1]:,}")
            print(f"    Individuals         : {r[1]:,}")
            print(f"    Date range          : {r[2]} → {r[3]}")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(id),
                       COUNT(CASE WHEN lien_match=TRUE THEN 1 END),
                       COUNT(CASE WHEN lien_match=TRUE
                                  AND email IS NOT NULL
                                  AND email!='' THEN 1 END)
                FROM arizona_roc_contacts
            """)
            roc = cur.fetchone()
        print(f"\n  arizona_roc_contacts  : {roc[0]:,}")
        print(f"    Matched to lien     : {roc[1]:,}")
        print(f"    Matched + email     : {roc[2]:,}  <- ready for bridge")
        if az == 0:    print(f"\n  NEXT: --scrape")
        elif roc[1]==0: print(f"\n  NEXT: --match")
        elif roc[2]==0: print(f"\n  NEXT: SerpAPI enrichment on {roc[1]:,} matched (July 1)")
        else:           print(f"\n  NEXT: bridge_to_email_pool.py --source roc")
        print(f"{'='*60}\n")
    finally:
        conn.close()


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Maricopa Federal Tax Lien Scraper v6")
    ap.add_argument("--scrape",  action="store_true")
    ap.add_argument("--file",    default=None)
    ap.add_argument("--days",    type=int, default=365)
    ap.add_argument("--match",   action="store_true")
    ap.add_argument("--min-similarity", type=float, default=0.45)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stats",   action="store_true")
    args = ap.parse_args()

    print(f"\n{'='*60}")
    print(f"  Maricopa Federal Tax Lien Scraper v6")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    print(f"  {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"{'='*60}\n")

    if args.stats:  show_stats(); return
    if args.match:
        match_roc_to_liens(args.min_similarity); show_stats(); return
    if args.file:
        result = import_from_csv(args.file, args.dry_run)
        print(f"  Imported {result.get('imported',0):,}"); show_stats(); return

    if args.scrape:
        stubs = scrape_maricopa(args.days, args.dry_run)
        if args.dry_run:
            print(f"\n[DRY RUN] {len(stubs)} recording numbers found")
            print("Run without --dry-run to fetch names and import")
            return
        records = [r for s in stubs if (r := build_lien_record(s))]
        print(f"  Valid lien records: {len(records):,}")
        # CSV backup
        if stubs:
            csv_path = DATA_DIR / f"maricopa_{date.today().isoformat()}_complete.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["recording_number","recording_date","debtor_name"])
                w.writeheader()
                w.writerows(stubs)
            print(f"  CSV backup: {csv_path}")
        result = import_to_db(records)
        print(f"  Imported : {result['imported']:,}")
        print(f"  Skipped  : {result['skipped']:,}")
        if result["imported"] > 0:
            print(f"\n  Next: python maricopa_lien_scraper.py --match")
        show_stats(); return

    ap.print_help()

if __name__ == "__main__":
    main()