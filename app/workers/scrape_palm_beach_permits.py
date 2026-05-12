"""
scrape_palm_beach_permits.py
============================
Palm Beach County building permit scraper for LeadFlow.

Source: PBC Cloud Drive weekly .txt permit reports
  URL: https://pbcclouddrive.pbcgov.org/invitations/?share=0360c66968cea6b9e4c9
  Format: pipe-delimited .txt files, one per week/month

The .txt files are pipe-delimited with these confirmed columns:
  PERMITNO|ISSUED|STATUS|WORK_DESC|SITE_ADDR|OWNER|CONTRACTOR|VALUATION|...

Strategy:
  1. Selenium navigates to cloud drive, enters Building Permits folder
  2. Downloads latest weekly .txt files
  3. Parses pipe-delimited content into PermitRecord
  4. Imports to normalized_permits

Usage:
  python -m app.workers.scrape_palm_beach_permits --no-db
  python -m app.workers.scrape_palm_beach_permits --days-back 180
  python -m app.workers.scrape_palm_beach_permits --weeks-back 26
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_WDM = True
except ImportError:
    HAS_WDM = False

try:
    from app.core.db import get_connection
except ImportError:
    get_connection = None

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
COUNTY_NAME  = "Palm Beach"
SOURCE_NAME  = "palm_beach_permits_txt"

CLOUD_URL    = "https://pbcclouddrive.pbcgov.org/invitations/?share=0360c66968cea6b9e4c9"

BASE_DIR     = Path(__file__).resolve().parents[2]
RAW_DIR      = BASE_DIR / "data" / "raw" / "palm_beach" / "permits"
DOWNLOAD_DIR = RAW_DIR / "downloads"
for d in [RAW_DIR, DOWNLOAD_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Confirmed column names from Palm Beach weekly .txt files
# File is pipe-delimited
COLUMN_ALIASES = {
    "permit_number":  ["PERMITNO", "PERMIT_NO", "PERMIT NO", "PermitNo", "Permit#"],
    "issued_date":    ["ISSUED", "DATE_ISSUED", "IssuedDate", "LAST_ISSUED_DATE", "Issue Date"],
    "permit_type":    ["PERMIT_TYPE", "TYPE", "WORK_TYPE", "WorkType"],
    "address":        ["SITE_ADDR", "ADDRESS", "SITE_ADDRESS", "SiteAddress"],
    "owner_name":     ["OWNER", "OWNER_NAME", "OwnerName"],
    "contractor":     ["CONTRACTOR", "CONTRACTOR_NAME", "ContractorName"],
    "description":    ["WORK_DESC", "DESCRIPTION", "DESC", "WorkDesc"],
    "valuation":      ["VALUATION", "VALUE", "BLDG_VALUE"],
    "status":         ["STATUS", "PERMIT_STATUS"],
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class PermitRecord:
    permit_number:       str
    permit_type:         Optional[str]  = None
    status:              Optional[str]  = None
    issued_date:         Optional[date] = None
    address:             Optional[str]  = None
    owner_name:          Optional[str]  = None
    contractor_name:     Optional[str]  = None
    project_description: Optional[str]  = None
    valuation:           Optional[float]= None
    raw_payload:         Dict           = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()

def parse_dt(v: Any) -> Optional[date]:
    s = clean(v).split("T")[0].split(" ")[0]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    return None

def parse_float(v: Any) -> Optional[float]:
    s = re.sub(r"[^\d.]", "", str(v or ""))
    try:
        return float(s) if s else None
    except ValueError:
        return None

def nowstamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def find_col(headers: List[str], field: str) -> Optional[str]:
    """Find the actual column name for a logical field."""
    for alias in COLUMN_ALIASES.get(field, []):
        for h in headers:
            if h.strip().upper() == alias.upper():
                return h
    return None


# ---------------------------------------------------------------------------
# Parse .txt file
# ---------------------------------------------------------------------------
def parse_txt_file(path: Path, cutoff: Optional[date] = None) -> List[PermitRecord]:
    """Parse a Palm Beach pipe-delimited permit .txt file."""
    records = []
    seen = set()

    try:
        # Try pipe delimiter first, then comma
        for delimiter in ["|", ",", "\t"]:
            try:
                text = path.read_text(encoding="utf-8-sig", errors="ignore")
                reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
                rows = list(reader)
                if rows and len(rows[0]) > 3:
                    headers = list(rows[0].keys())
                    print(f"  {path.name}: {len(rows)} rows, {len(headers)} cols, delim={repr(delimiter)}")
                    print(f"  Columns: {headers[:8]}")
                    break
            except Exception:
                rows = []
                continue

        if not rows:
            print(f"  Could not parse {path.name}")
            return []

        headers = list(rows[0].keys())

        # Map column names
        col = {field: find_col(headers, field) for field in COLUMN_ALIASES}

        for row in rows:
            permit_num = clean(row.get(col["permit_number"] or "", ""))
            if not permit_num or permit_num in seen:
                continue

            issued = parse_dt(row.get(col["issued_date"] or "", ""))
            if cutoff and issued and issued < cutoff:
                continue

            seen.add(permit_num)
            val = parse_float(row.get(col["valuation"] or "", ""))

            records.append(PermitRecord(
                permit_number       = permit_num,
                permit_type         = clean(row.get(col["permit_type"] or "", "")) or None,
                status              = clean(row.get(col["status"] or "", "")) or None,
                issued_date         = issued,
                address             = clean(row.get(col["address"] or "", "")) or None,
                owner_name          = clean(row.get(col["owner_name"] or "", "")) or None,
                contractor_name     = clean(row.get(col["contractor"] or "", "")) or None,
                project_description = clean(row.get(col["description"] or "", "")) or None,
                valuation           = val,
                raw_payload         = dict(row),
            ))

    except Exception as e:
        print(f"  Parse error {path.name}: {e}")

    return records


# ---------------------------------------------------------------------------
# Download via Selenium
# ---------------------------------------------------------------------------
def make_driver(headless: bool = False) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1440,900")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_experimental_option("prefs", {
        "download.default_directory":   str(DOWNLOAD_DIR),
        "download.prompt_for_download": False,
        "download.directory_upgrade":   True,
        "safebrowsing.enabled":         False,
        "plugins.always_open_pdf_externally": True,
    })
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    if HAS_WDM:
        return webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), options=opts)
    return webdriver.Chrome(options=opts)


def download_permit_files(weeks_back: int = 26) -> List[Path]:
    """Navigate PBC Cloud Drive and download weekly permit .txt files."""
    print(f"\n[Palm Beach Permits] Downloading up to {weeks_back} weekly files")
    print(f"  Cloud Drive: {CLOUD_URL}")

    driver = make_driver(headless=False)
    downloaded: List[Path] = []

    try:
        driver.get(CLOUD_URL)
        time.sleep(6)
        print(f"  Page title: {driver.title}")

        # Click into Building Permits folder
        for xpath in [
            "//a[contains(text(),'Building Permits')]",
            "//div[contains(text(),'Building Permits')]",
            "//*[contains(@class,'folder') and contains(text(),'Building')]",
        ]:
            try:
                el = driver.find_element(By.XPATH, xpath)
                if el.is_displayed():
                    driver.execute_script("arguments[0].click();", el)
                    time.sleep(4)
                    print("  ✓ Entered Building Permits folder")
                    break
            except Exception:
                continue

        # Find all .txt file links
        before_files = set(DOWNLOAD_DIR.glob("*.txt"))

        file_links = driver.find_elements(By.XPATH,
            "//a[contains(@href,'.txt') or contains(text(),'.txt') or contains(text(),'Wk') or contains(text(),'Week')]"
        )

        print(f"  Found {len(file_links)} potential file links")

        # Download most recent files
        downloaded_count = 0
        for link in file_links[:weeks_back]:
            try:
                name = link.text.strip() or link.get_attribute("href") or ""
                print(f"  Downloading: {name[:60]}")
                driver.execute_script("arguments[0].click();", link)
                time.sleep(3)
                downloaded_count += 1
            except Exception as e:
                print(f"  Error clicking link: {e}")

        # Wait for downloads
        if downloaded_count > 0:
            print(f"  Waiting for {downloaded_count} downloads...")
            time.sleep(5)

        # Find newly downloaded files
        after_files = set(DOWNLOAD_DIR.glob("*.txt"))
        new_files = after_files - before_files
        downloaded = sorted(new_files, key=lambda f: f.stat().st_mtime, reverse=True)
        print(f"  Downloaded: {len(downloaded)} new .txt files")

    except Exception as e:
        print(f"  Download error: {e}")
        import traceback; traceback.print_exc()
    finally:
        driver.quit()

    return downloaded


def find_existing_files(days_back: int) -> List[Path]:
    """Find already-downloaded .txt files within date range."""
    cutoff_ts = datetime.now().timestamp() - (days_back * 86400)
    files = [
        f for f in DOWNLOAD_DIR.glob("*.txt")
        if f.stat().st_mtime >= cutoff_ts
    ]
    # Also check RAW_DIR
    files += [
        f for f in RAW_DIR.glob("*.txt")
        if f.stat().st_mtime >= cutoff_ts
    ]
    return sorted(set(files), key=lambda f: f.stat().st_mtime, reverse=True)


# ---------------------------------------------------------------------------
# DB import
# ---------------------------------------------------------------------------
def get_county_id(cur) -> int:
    cur.execute("SELECT id FROM counties WHERE county_name = %s", (COUNTY_NAME,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO counties (county_name, state, active, created_at) "
        "VALUES (%s,'FL',true,NOW()) RETURNING id", (COUNTY_NAME,)
    )
    return cur.fetchone()[0]


def import_records(records: List[PermitRecord]) -> Dict[str, int]:
    if not records or not get_connection:
        return {"inserted": 0, "updated": 0, "skipped": 0}

    conn = get_connection()
    conn.autocommit = False
    stats = {"inserted": 0, "updated": 0, "skipped": 0}

    try:
        with conn.cursor() as cur:
            county_id = get_county_id(cur)

            for rec in records:
                if not rec.permit_number:
                    stats["skipped"] += 1
                    continue

                source_id = f"{SOURCE_NAME}::{rec.permit_number}"
                payload   = json.dumps({
                    "permit_number": rec.permit_number,
                    "permit_type":   rec.permit_type,
                    "address":       rec.address,
                    "owner":         rec.owner_name,
                    "contractor":    rec.contractor_name,
                    "description":   rec.project_description,
                    "issued_date":   str(rec.issued_date) if rec.issued_date else None,
                    "valuation":     rec.valuation,
                }, default=str)

                raw_id = None
                try:
                    cur.execute("""
                        INSERT INTO raw_permits
                            (county_id, source_file, source_record_id,
                             raw_payload, issued_date)
                        VALUES (%s,%s,%s,%s::jsonb,%s)
                        ON CONFLICT (county_id, source_record_id) DO UPDATE SET
                            raw_payload = EXCLUDED.raw_payload,
                            issued_date = EXCLUDED.issued_date
                        RETURNING id, (xmax = 0) AS is_insert
                    """, (county_id, SOURCE_NAME, source_id,
                          payload, rec.issued_date))
                    rl = cur.fetchone()
                    if rl:
                        raw_id = rl[0]
                except Exception as e:
                    conn.rollback()
                    stats["skipped"] += 1
                    continue

                n_hash = f"pb_permit::{rec.permit_number}"
                try:
                    cur.execute("""
                        INSERT INTO normalized_permits (
                            county_id, raw_permit_id, permit_number,
                            permit_type, owner_name, business_name,
                            address_1, project_description,
                            issued_date, normalized_hash
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (normalized_hash) DO UPDATE SET
                            owner_name          = COALESCE(EXCLUDED.owner_name, normalized_permits.owner_name),
                            business_name       = COALESCE(EXCLUDED.business_name, normalized_permits.business_name),
                            project_description = COALESCE(EXCLUDED.project_description, normalized_permits.project_description),
                            issued_date         = COALESCE(EXCLUDED.issued_date, normalized_permits.issued_date),
                            updated_at          = NOW()
                        RETURNING id, (xmax = 0) AS is_insert
                    """, (
                        county_id, raw_id,
                        rec.permit_number, rec.permit_type,
                        rec.owner_name   or None,
                        rec.contractor_name or None,
                        rec.address      or None,
                        rec.project_description or None,
                        rec.issued_date, n_hash,
                    ))
                    result = cur.fetchone()
                    if result:
                        if result[1]:
                            stats["inserted"] += 1
                        else:
                            stats["updated"] += 1
                except Exception as e:
                    conn.rollback()
                    print(f"  Insert error {rec.permit_number}: {e}")
                    stats["skipped"] += 1
                    continue

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"  [DB] Error: {e}")
        raise
    finally:
        conn.close()

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Palm Beach County permit scraper")
    parser.add_argument("--days-back",   type=int, default=180)
    parser.add_argument("--weeks-back",  type=int, default=None)
    parser.add_argument("--no-db",       action="store_true")
    parser.add_argument("--no-download", action="store_true",
                        help="Skip download, use existing .txt files")
    args = parser.parse_args()

    days_back = args.days_back
    if args.weeks_back:
        days_back = args.weeks_back * 7

    cutoff = date.today() - timedelta(days=days_back)
    weeks_back = max(1, days_back // 7)

    # Step 1: Download or find files
    if args.no_download:
        txt_files = find_existing_files(days_back)
        print(f"\n[Palm Beach Permits] Using {len(txt_files)} existing .txt files")
    else:
        txt_files = download_permit_files(weeks_back=weeks_back)
        if not txt_files:
            print("  No new files downloaded — checking for existing files")
            txt_files = find_existing_files(days_back)

    if not txt_files:
        print("\nNo .txt files found.")
        print(f"Download manually from: {CLOUD_URL}")
        print(f"Save to: {DOWNLOAD_DIR}")
        return

    # Step 2: Parse all files
    all_records: List[PermitRecord] = []
    seen: set = set()

    for path in txt_files:
        print(f"\n  Parsing: {path.name}")
        recs = parse_txt_file(path, cutoff=cutoff)
        new = [r for r in recs if r.permit_number not in seen]
        seen.update(r.permit_number for r in new)
        all_records.extend(new)
        print(f"  +{len(new)} records")

    print(f"\nTotal unique permits: {len(all_records)}")

    if all_records:
        snap = RAW_DIR / f"pb_permits_{nowstamp()}.json"
        snap.write_text(
            json.dumps([{
                "permit_number": r.permit_number,
                "permit_type":   r.permit_type,
                "address":       r.address,
                "owner":         r.owner_name,
                "contractor":    r.contractor_name,
                "issued_date":   str(r.issued_date),
            } for r in all_records], indent=2, default=str),
            encoding="utf-8"
        )
        print(f"Saved: {snap}")
        print("\nSample:")
        for r in all_records[:5]:
            print(f"  {r.permit_number} | {r.permit_type} | {r.address} | {r.issued_date}")

    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    if not args.no_db and all_records:
        print("\nWriting to DB...")
        stats = import_records(all_records)

    print(f"\n--- Palm Beach Permit Summary ---")
    print(f"  Files parsed : {len(txt_files)}")
    print(f"  Scraped      : {len(all_records)}")
    print(f"  Inserted     : {stats.get('inserted', 0)}")
    print(f"  Updated      : {stats.get('updated', 0)}")
    print(f"  Skipped      : {stats.get('skipped', 0)}")


if __name__ == "__main__":
    main()