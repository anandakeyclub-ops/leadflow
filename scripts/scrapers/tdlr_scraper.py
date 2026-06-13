"""
tdlr_scraper.py
===============
Texas TDLR License Database — Bulk Download + Enrichment Pipeline.

Downloads contractor license data directly from TDLR's public bulk
download page (no scraping required — official CSV files).

Target license types (highest IRS lien correlation):
  - Air Conditioning Contractors
  - Electricians (contractors only)
  - All Licenses (fallback — 180MB)

Filters for:
  - Active licenses only
  - Target counties (Harris, Dallas, Tarrant, Bexar, Travis, etc.)
  - Business entities and individual contractors

Normalizes and loads into PostgreSQL:
  - Table: texas_tdlr_contacts
  - Deduplicates on license_number
  - Cross-references with normalized_liens (future — when TX lien data available)

Usage:
  python scripts/scrapers/tdlr_scraper.py --download --import
  python scripts/scrapers/tdlr_scraper.py --download --type ac
  python scripts/scrapers/tdlr_scraper.py --download --type electricians
  python scripts/scrapers/tdlr_scraper.py --download --type all
  python scripts/scrapers/tdlr_scraper.py --stats
  python scripts/scrapers/tdlr_scraper.py --dry-run

Data source:
  https://www.tdlr.texas.gov/LicenseSearch/licfile.asp
  Public bulk downloads — no authentication required.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

DATA_DIR   = LEADFLOW_DIR / "data" / "texas"
DATA_DIR.mkdir(parents=True, exist_ok=True)

try:
    from app.core.db import get_connection
    HAS_DB = True
except ImportError:
    HAS_DB = False

# ── TDLR bulk download URLs ───────────────────────────────────────────────────
# Source: https://www.tdlr.texas.gov/LicenseSearch/licfile.asp
TDLR_BASE = "https://www.tdlr.texas.gov/dbproduction2"

DOWNLOAD_URLS = {
    "ac":           f"{TDLR_BASE}/ltairref.csv",      # A/C Contractors (3.6 MB)
    "ac_tech":      f"{TDLR_BASE}/ltactech.csv",      # A/C Technicians (9.9 MB)
    "electricians": f"{TDLR_BASE}/Ltmstele.csv",    # Master/Sign Electricians
    "mold":         f"{TDLR_BASE}/Ltmold.csv",       # Mold Assessors/Remediators
    "all":           f"{TDLR_BASE}/ltlicfile.csv",      # All Licenses (180 MB)
}

# ── Target counties for Texas ─────────────────────────────────────────────────
TARGET_COUNTIES = {
    "HARRIS", "DALLAS", "TARRANT", "BEXAR", "TRAVIS",
    "COLLIN", "DENTON", "FORT BEND", "MONTGOMERY", "EL PASO",
    "WILLIAMSON", "NUECES", "HIDALGO", "CAMERON", "BRAZORIA",
    "GALVESTON", "JEFFERSON", "SMITH", "LUBBOCK", "WEBB",
}

# ── License types most correlated with IRS lien activity ─────────────────────
HIGH_VALUE_LICENSE_TYPES = {
    "AIR CONDITIONING CONTRACTOR",
    "AC CONTRACTOR",
    "ELECTRICAL CONTRACTOR",
    "ELECTRICIAN CONTRACTOR",
    "MASTER ELECTRICIAN",
    "MASTER SIGN ELECTRICIAN",
    "MOLD ASSESSMENT CONTRACTOR",
    "MOLD REMEDIATION CONTRACTOR",
    "PROPERTY TAX CONSULTANT",
}

# ── DB schema ─────────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS texas_tdlr_contacts (
    id                  SERIAL PRIMARY KEY,
    license_number      VARCHAR(50)  UNIQUE NOT NULL,
    license_type        VARCHAR(100),
    license_subtype     VARCHAR(100),
    status              VARCHAR(50),
    expiration_date     DATE,
    owner_name          VARCHAR(200),
    business_name       VARCHAR(200),
    business_address    VARCHAR(300),
    business_city       VARCHAR(100),
    business_state      VARCHAR(10),
    business_zip        VARCHAR(20),
    business_county     VARCHAR(100),
    business_phone      VARCHAR(30),
    mailing_address     VARCHAR(300),
    mailing_city        VARCHAR(100),
    mailing_state       VARCHAR(10),
    mailing_zip         VARCHAR(20),
    mailing_county      VARCHAR(100),
    owner_phone         VARCHAR(30),
    email               VARCHAR(200),
    confidence          VARCHAR(20)  DEFAULT 'low',
    source              VARCHAR(50)  DEFAULT 'tdlr',
    created_at          TIMESTAMP    DEFAULT NOW(),
    updated_at          TIMESTAMP    DEFAULT NOW(),
    emailed             BOOLEAN      DEFAULT FALSE,
    lien_match          BOOLEAN      DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_tx_county
    ON texas_tdlr_contacts(business_county);
CREATE INDEX IF NOT EXISTS idx_tx_license_type
    ON texas_tdlr_contacts(license_type);
CREATE INDEX IF NOT EXISTS idx_tx_email
    ON texas_tdlr_contacts(email)
    WHERE email IS NOT NULL AND email != '';
CREATE INDEX IF NOT EXISTS idx_tx_status
    ON texas_tdlr_contacts(status);
"""

# ── TDLR CSV column mapping ───────────────────────────────────────────────────
# TDLR CSV format (from file format documentation):
# LICENSE TYPE | LICENSE NUMBER | BUSINESS COUNTY | BUSINESS NAME |
# BUSINESS ADDRESS LINE1 | BUSINESS ADDRESS LINE2 | BUSINESS CITY STATE ZIP |
# BUSINESS TELEPHONE | LICENSE EXPIRATION DATE | OWNER NAME |
# MAILING ADDRESS LINE1 | MAILING ADDRESS LINE2 | MAILING ADDRESS CITY STATE ZIP |
# MAILING ADDRESS COUNTY | OWNER TELEPHONE | LICENSE SUBTYPE | CE FLAG

def parse_city_state_zip(raw: str) -> tuple[str, str, str]:
    """Parse 'City, TX 77001' → (city, state, zip)"""
    if not raw:
        return "", "", ""
    raw = raw.strip()
    parts = raw.rsplit(" ", 1)
    zip_code = parts[-1].strip() if len(parts) > 1 else ""
    rest     = parts[0].strip() if len(parts) > 1 else raw
    if "," in rest:
        city_parts = rest.rsplit(",", 1)
        city  = city_parts[0].strip()
        state = city_parts[1].strip() if len(city_parts) > 1 else "TX"
    else:
        city  = rest
        state = "TX"
    return city, state, zip_code

def parse_expiration_date(raw: str) -> date | None:
    """Parse MMDDCCYY or MM/DD/YYYY to date."""
    if not raw or raw.strip() == "":
        return None
    raw = raw.strip()
    try:
        if "/" in raw:
            return datetime.strptime(raw, "%m/%d/%Y").date()
        if len(raw) == 8:
            return datetime.strptime(raw, "%m%d%Y").date()
    except ValueError:
        pass
    return None

def clean_phone(raw: str) -> str:
    if not raw:
        return ""
    import re
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return raw.strip()

def normalize_row(row: dict) -> dict | None:
    """
    Normalize a raw TDLR CSV row into our schema.
    Returns None if row should be filtered out.
    """
    # Get key fields — TDLR CSV uses uppercase column names
    keys = {k.strip().upper(): v.strip() for k, v in row.items()}

    license_type   = keys.get("LICENSE TYPE", "").upper()
    license_number = keys.get("LICENSE NUMBER", "").strip()
    county         = keys.get("BUSINESS COUNTY", "").upper().strip()
    status         = keys.get("STATUS", "ACTIVE").upper().strip()

    if not license_number:
        return None

    # Filter: active licenses only
    if status and status not in ("", "ACTIVE", "REG", "CER", "A"):
        return None

    # Filter: target counties only (skip if county filtering enabled)
    if county and TARGET_COUNTIES and county not in TARGET_COUNTIES:
        return None

    # Parse city/state/zip
    biz_csz = keys.get("BUSINESS CITY, STATE ZIP", "") or \
               keys.get("BUSINESS CITY STATE ZIP", "")
    biz_city, biz_state, biz_zip = parse_city_state_zip(biz_csz)

    mail_csz = keys.get("MAILING ADDRESS CITY, STATE ZIP", "") or \
               keys.get("MAILING ADDRESS CITY STATE ZIP", "")
    mail_city, mail_state, mail_zip = parse_city_state_zip(mail_csz)

    exp_raw  = keys.get("LICENSE EXPIRATION DATE", "") or \
               keys.get("EXPIRATION DATE", "")
    exp_date = parse_expiration_date(exp_raw)

    # Skip expired licenses
    if exp_date and exp_date < date.today():
        return None

    owner_name    = keys.get("OWNER NAME", "").strip()
    business_name = keys.get("BUSINESS NAME", "").strip()
    biz_phone     = clean_phone(keys.get("BUSINESS TELEPHONE", ""))
    owner_phone   = clean_phone(keys.get("OWNER TELEPHONE", ""))

    # Confidence scoring
    confidence = "low"
    if business_name and biz_phone:
        confidence = "medium"
    if business_name and biz_phone and county in TARGET_COUNTIES:
        confidence = "high"

    return {
        "license_number":   license_number,
        "license_type":     license_type[:100] if license_type else None,
        "license_subtype":  keys.get("LICENSE SUBTYPE", "")[:100] or None,
        "status":           status or "ACTIVE",
        "expiration_date":  exp_date,
        "owner_name":       owner_name[:200] if owner_name else None,
        "business_name":    business_name[:200] if business_name else None,
        "business_address": (keys.get("BUSINESS ADDRESS LINE1", "") + " " +
                             keys.get("BUSINESS ADDRESS LINE2", "")).strip()[:300] or None,
        "business_city":    biz_city[:100] or None,
        "business_state":   biz_state[:10] or "TX",
        "business_zip":     biz_zip[:20] or None,
        "business_county":  county[:100] or None,
        "business_phone":   biz_phone[:30] or None,
        "mailing_address":  (keys.get("MAILING ADDRESS LINE1", "") + " " +
                             keys.get("MAILING ADDRESS LINE2", "")).strip()[:300] or None,
        "mailing_city":     mail_city[:100] or None,
        "mailing_state":    mail_state[:10] or None,
        "mailing_zip":      mail_zip[:20] or None,
        "mailing_county":   keys.get("MAILING ADDRESS COUNTY", "")[:100] or None,
        "owner_phone":      owner_phone[:30] or None,
        "email":            None,   # TDLR doesn't provide emails — enriched separately
        "confidence":       confidence,
        "source":           "tdlr",
    }


# ── Downloader ────────────────────────────────────────────────────────────────

def download_tdlr_file(license_type: str = "ac",
                       force: bool = False) -> Path | None:
    url      = DOWNLOAD_URLS.get(license_type)
    if not url:
        print(f"  ❌ Unknown license type: {license_type}")
        print(f"  Available: {', '.join(DOWNLOAD_URLS.keys())}")
        return None

    filename = url.split("/")[-1]
    out_path = DATA_DIR / filename

    if out_path.exists() and not force:
        age_hours = (datetime.now().timestamp() - out_path.stat().st_mtime) / 3600
        if age_hours < 168:  # 7 days
            print(f"  ✅ Using cached: {out_path} ({age_hours:.0f}h old)")
            return out_path
        print(f"  📥 Cache stale ({age_hours:.0f}h) — re-downloading...")

    print(f"  📥 Downloading {license_type} from TDLR...")
    print(f"  URL: {url}")

    try:
        headers = {
            "User-Agent": "TaxCaseReview/1.0 (info@taxcasereview.org)",
            "Accept":     "text/csv,*/*",
        }
        r = requests.get(url, headers=headers, stream=True, timeout=120)
        r.raise_for_status()

        total = int(r.headers.get("content-length", 0))
        downloaded = 0

        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    print(f"\r  Progress: {pct:.1f}% ({downloaded/1024/1024:.1f} MB)",
                          end="", flush=True)
        print()
        print(f"  ✅ Saved: {out_path} ({out_path.stat().st_size/1024/1024:.1f} MB)")
        return out_path

    except requests.RequestException as e:
        print(f"  ❌ Download failed: {e}")
        return None


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_tdlr_csv(csv_path: Path,
                   all_counties: bool = False) -> list[dict]:
    """Parse TDLR CSV, normalize, and filter rows."""
    records  = []
    skipped  = 0
    filtered = 0
    errors   = 0

    print(f"  Parsing: {csv_path.name}")
    print(f"  County filter: {'All Texas' if all_counties else ', '.join(sorted(TARGET_COUNTIES)[:5])} ...")

    # TDLR files use cp1252 encoding
    encodings = ["cp1252", "latin-1", "utf-8"]
    content   = None

    for enc in encodings:
        try:
            content = csv_path.read_text(encoding=enc, errors="replace")
            break
        except Exception:
            continue

    if not content:
        print(f"  ❌ Could not read file")
        return []

    lines = content.splitlines()
    print(f"  Total rows: {len(lines):,}")

    reader = csv.DictReader(io.StringIO(content))

    for i, row in enumerate(reader):
        try:
            if all_counties:
                # Temporarily remove county filter
                county = row.get("BUSINESS COUNTY", row.get("Business County", "")).upper().strip()
                if county:
                    TARGET_COUNTIES.add(county)

            normalized = normalize_row(row)
            if normalized:
                records.append(normalized)
            else:
                filtered += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  ⚠ Row {i} error: {e}")

        if i > 0 and i % 10000 == 0:
            print(f"  ... {i:,} rows processed, {len(records):,} kept")

    print(f"  ✅ Parsed: {len(records):,} records kept, {filtered:,} filtered, {errors} errors")
    return records


# ── DB importer ───────────────────────────────────────────────────────────────

def ensure_table():
    if not HAS_DB:
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        conn.commit()
        print("  ✅ Table ready: texas_tdlr_contacts")
    finally:
        conn.close()

def import_to_db(records: list[dict], dry_run: bool = False) -> dict:
    if not HAS_DB:
        print("  ⚠  No DB connection — saving to CSV only")
        return {"inserted": 0, "updated": 0, "skipped": 0}

    inserted = updated = skipped = 0
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            for rec in records:
                try:
                    cur.execute("""
                        INSERT INTO texas_tdlr_contacts (
                            license_number, license_type, license_subtype,
                            status, expiration_date, owner_name, business_name,
                            business_address, business_city, business_state,
                            business_zip, business_county, business_phone,
                            mailing_address, mailing_city, mailing_state,
                            mailing_zip, mailing_county, owner_phone,
                            email, confidence, source
                        ) VALUES (
                            %(license_number)s, %(license_type)s, %(license_subtype)s,
                            %(status)s, %(expiration_date)s, %(owner_name)s,
                            %(business_name)s, %(business_address)s, %(business_city)s,
                            %(business_state)s, %(business_zip)s, %(business_county)s,
                            %(business_phone)s, %(mailing_address)s, %(mailing_city)s,
                            %(mailing_state)s, %(mailing_zip)s, %(mailing_county)s,
                            %(owner_phone)s, %(email)s, %(confidence)s, %(source)s
                        )
                        ON CONFLICT (license_number) DO UPDATE SET
                            status          = EXCLUDED.status,
                            expiration_date = EXCLUDED.expiration_date,
                            business_name   = EXCLUDED.business_name,
                            business_phone  = EXCLUDED.business_phone,
                            business_county = EXCLUDED.business_county,
                            confidence      = EXCLUDED.confidence,
                            updated_at      = NOW()
                        RETURNING (xmax = 0) AS was_inserted
                    """, rec)
                    row = cur.fetchone()
                    if row and row[0]:
                        inserted += 1
                    else:
                        updated += 1
                except Exception as e:
                    skipped += 1
                    if skipped <= 3:
                        print(f"  ⚠ DB error: {e}")

            if not dry_run:
                conn.commit()
                print(f"  ✅ Imported: {inserted:,} new, {updated:,} updated, {skipped} errors")
            else:
                conn.rollback()
                print(f"  [DRY RUN] Would import: {inserted + updated:,} records")

    finally:
        conn.close()

    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def save_to_csv(records: list[dict], filename: str = None):
    """Save normalized records to CSV as backup."""
    if not records:
        return
    out = DATA_DIR / (filename or f"tdlr_normalized_{date.today().isoformat()}.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
    print(f"  💾 CSV saved: {out} ({len(records):,} records)")
    return out


# ── Stats ─────────────────────────────────────────────────────────────────────

def show_stats():
    if not HAS_DB:
        print("No DB connection")
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM texas_tdlr_contacts")
            total = cur.fetchone()[0]

            cur.execute("""
                SELECT business_county, COUNT(*)
                FROM texas_tdlr_contacts
                WHERE business_county IS NOT NULL
                GROUP BY business_county
                ORDER BY COUNT(*) DESC LIMIT 15
            """)
            counties = cur.fetchall()

            cur.execute("""
                SELECT license_type, COUNT(*)
                FROM texas_tdlr_contacts
                GROUP BY license_type
                ORDER BY COUNT(*) DESC LIMIT 10
            """)
            types = cur.fetchall()

            cur.execute("""
                SELECT confidence, COUNT(*)
                FROM texas_tdlr_contacts
                GROUP BY confidence
            """)
            conf = cur.fetchall()

            cur.execute("""
                SELECT COUNT(*) FROM texas_tdlr_contacts
                WHERE email IS NOT NULL AND email != ''
            """)
            with_email = cur.fetchone()[0]

        print(f"\n{'='*55}")
        print(f"  Texas TDLR Database Stats")
        print(f"{'='*55}")
        print(f"  Total records  : {total:,}")
        print(f"  With email     : {with_email:,}")
        print(f"\n  Top Counties:")
        for county, cnt in counties:
            print(f"    {county:<20} {cnt:>6,}")
        print(f"\n  By License Type:")
        for lt, cnt in types:
            print(f"    {(lt or 'Unknown'):<35} {cnt:>6,}")
        print(f"\n  By Confidence:")
        for c, cnt in conf:
            print(f"    {c:<10} {cnt:>6,}")
        print(f"{'='*55}\n")
    finally:
        conn.close()


# ── Pipeline logger ───────────────────────────────────────────────────────────

def get_logger(run_type: str):
    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger(run_type)
        logger.start()
        return logger
    except ImportError:
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Texas TDLR License Database Downloader + Importer")
    parser.add_argument("--download",     action="store_true",
                        help="Download from TDLR")
    parser.add_argument("--import",       action="store_true", dest="do_import",
                        help="Import to PostgreSQL")
    parser.add_argument("--type",         default="ac",
                        choices=list(DOWNLOAD_URLS.keys()),
                        help="License type to download (default: ac)")
    parser.add_argument("--all-counties", action="store_true",
                        help="Import all counties, not just target list")
    parser.add_argument("--force",        action="store_true",
                        help="Re-download even if cached")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Parse only, don't write to DB")
    parser.add_argument("--stats",        action="store_true",
                        help="Show DB stats")
    parser.add_argument("--csv-only",     action="store_true",
                        help="Save to CSV only, skip DB")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    if not args.download and not args.do_import and not args.dry_run:
        parser.print_help()
        return

    print(f"\n{'='*55}")
    print(f"  Texas TDLR Scraper")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    print(f"  Type     : {args.type}")
    print(f"  Counties : {'All' if args.all_counties else len(TARGET_COUNTIES)}")
    print(f"  {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"{'='*55}\n")

    logger = get_logger("tdlr_scraper")

    # ── Download ──────────────────────────────────────────────────────────────
    csv_path = None
    if args.download or args.dry_run:
        if logger: logger.step_start("download")
        csv_path = download_tdlr_file(args.type, force=args.force)
        if not csv_path:
            print("❌ Download failed — aborting")
            return
        if logger:
            logger.step_done("download", ok=True,
                             detail=str(csv_path))

    # ── Parse ─────────────────────────────────────────────────────────────────
    if csv_path:
        if logger: logger.step_start("parse")
        records = parse_tdlr_csv(csv_path, all_counties=args.all_counties)
        if logger:
            logger.step_done("parse", ok=True,
                             detail=f"{len(records):,} records")

        # Always save CSV backup
        save_to_csv(records)

        # ── Import ────────────────────────────────────────────────────────────
        if args.do_import or (not args.csv_only and not args.dry_run):
            if not HAS_DB:
                print("  ⚠  No DB — saved to CSV only")
            else:
                if logger: logger.step_start("ensure_table")
                ensure_table()
                if logger: logger.step_done("ensure_table", ok=True)

                if logger: logger.step_start("import_db")
                result = import_to_db(records, dry_run=args.dry_run)
                if logger:
                    logger.step_done("import_db", ok=True,
                                     detail=f"{result['inserted']:,} new, "
                                            f"{result['updated']:,} updated")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"  TDLR Scraper Complete")
    if csv_path:
        print(f"  File     : {csv_path.name}")
        print(f"  Records  : {len(records) if csv_path else 0:,}")
    print(f"{'='*55}\n")

    if logger:
        logger.finish({
            "type":    args.type,
            "records": len(records) if csv_path else 0,
            "dry_run": args.dry_run,
        })

    # Show stats after import
    if args.do_import and not args.dry_run and HAS_DB:
        show_stats()


if __name__ == "__main__":
    main()