"""
scrape_broward_bcs.py
=====================
Broward County unincorporated building permits via the BCS portal.
Portal: https://dpepp.broward.org/BCS/Default.aspx (Posse ASP.NET)

Covers: unincorporated Broward + cities that feed into the county BCS system.
No login required — public guest search.

Usage:
  python -m app.workers.scrape_broward_bcs --visible --days-back 90
  python -m app.workers.scrape_broward_bcs --visible --days-back 30
  python -m app.workers.scrape_broward_bcs --no-db --days-back 7 (test mode)
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    ChromeDriverManager = None

try:
    from app.core.db import get_connection
except ImportError:
    get_connection = None

import argparse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
COUNTY_NAME  = "Broward"
SOURCE_NAME  = "broward_bcs"
BCS_BASE     = "https://dpepp.broward.org/BCS"
SEARCH_PRESENTATIONS = [
    "SearchForPermitByDate",
    "SearchForPermitGuestByDate",
    "SearchForPermitGuest",
    "PermitSearchByDate",
]

BASE_DIR      = Path(__file__).resolve().parents[2]
RAW_DIR       = BASE_DIR / "data" / "raw" / "broward" / "permits"
DEBUG_DIR     = BASE_DIR / "data" / "debug" / "broward_bcs"
for d in [RAW_DIR, DEBUG_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class PermitRecord:
    permit_number:       str
    owner_name:          Optional[str]  = None
    business_name:       Optional[str]  = None
    address_1:           Optional[str]  = None
    city:                Optional[str]  = None
    state:               str            = "FL"
    zip:                 Optional[str]  = None
    permit_type:         Optional[str]  = None
    project_description: Optional[str]  = None
    issued_date:         Optional[date] = None
    status:              Optional[str]  = None
    project_value:       Optional[float] = None
    raw_payload:         Dict            = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()

def parse_date(v: Any) -> Optional[date]:
    s = clean(v)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

def parse_money(v: Any) -> Optional[float]:
    s = re.sub(r"[^\d.]", "", str(v or ""))
    try:
        return float(s) if s else None
    except ValueError:
        return None

def make_driver(visible: bool = False) -> webdriver.Chrome:
    opts = Options()
    if not visible:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1440,900")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    if ChromeDriverManager:
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=opts)
    return webdriver.Chrome(options=opts)

def safe_find(driver, by, value):
    try:
        return driver.find_element(by, value)
    except Exception:
        return None

def save_debug(driver, label: str) -> None:
    try:
        (DEBUG_DIR / f"{label}.html").write_text(driver.page_source, encoding="utf-8", errors="ignore")
        driver.save_screenshot(str(DEBUG_DIR / f"{label}.png"))
        print(f"  [debug] {label}.png")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# BCS scraper
# ---------------------------------------------------------------------------

def find_and_fill_dates(driver, start_str: str, end_str: str) -> bool:
    """Try all known BCS date field ID patterns."""
    start_ids = [
        "ctl00_PlaceHolderMain_IssuedDateFrom", "IssuedDateFrom",
        "ctl00_PlaceHolderMain_IssueDate", "IssueDate", "DateFrom",
        "startDate", "dateFrom", "Param_0", "fromDate",
    ]
    end_ids = [
        "ctl00_PlaceHolderMain_IssuedDateTo", "IssuedDateTo",
        "ctl00_PlaceHolderMain_IssuedDate2", "IssuedDate2", "DateTo",
        "endDate", "dateTo", "Param_1", "toDate",
    ]

    filled_start = False
    for fid in start_ids:
        el = safe_find(driver, By.ID, fid)
        if el and el.is_displayed():
            driver.execute_script("arguments[0].value = arguments[1];", el, start_str)
            driver.execute_script("arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", el)
            el.send_keys(Keys.TAB)
            print(f"  [BCS] Start date → #{fid}")
            filled_start = True
            break

    filled_end = False
    for fid in end_ids:
        el = safe_find(driver, By.ID, fid)
        if el and el.is_displayed():
            driver.execute_script("arguments[0].value = arguments[1];", el, end_str)
            driver.execute_script("arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", el)
            el.send_keys(Keys.TAB)
            print(f"  [BCS] End date → #{fid}")
            filled_end = True
            break

    # XPath fallback — find date-like text inputs
    if not filled_start or not filled_end:
        date_inputs = driver.find_elements(By.XPATH,
            "//input[@type='text' and ("
            "contains(@id,'ate') or contains(@name,'ate') or "
            "contains(@placeholder,'mm/dd') or contains(@placeholder,'date'))]"
        )
        print(f"  [BCS] XPath fallback: {len(date_inputs)} date inputs found")
        if len(date_inputs) >= 2:
            for el in date_inputs[:1]:
                driver.execute_script("arguments[0].value = arguments[1];", el, start_str)
            for el in date_inputs[1:2]:
                driver.execute_script("arguments[0].value = arguments[1];", el, end_str)
            return True

    return filled_start or filled_end


def submit_search(driver) -> bool:
    """Click the search/submit button."""
    for xpath in [
        "//input[@value='Search' or @value='Submit' or @value='Find']",
        "//button[contains(text(),'Search') or contains(text(),'Submit')]",
        "//a[contains(text(),'Search') and not(contains(@href,'#'))]",
        "//input[@type='submit']",
        "//input[@type='button' and contains(@value,'Search')]",
    ]:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(5)
            print(f"  [BCS] Submitted via: {xpath[:60]}")
            return True
        except Exception:
            continue
    return False


def extract_table_rows(driver) -> List[dict]:
    """Extract all permit rows from BCS results table."""
    rows = []
    headers = []

    try:
        tables = driver.find_elements(By.TAG_NAME, "table")
        best_table = None
        best_score = 0

        for table in tables:
            text = clean(table.text).lower()
            score = sum(1 for kw in ["permit", "address", "owner", "issued", "status"] if kw in text)
            if score > best_score:
                best_score = score
                best_table = table

        if not best_table or best_score < 2:
            return rows

        # Extract headers
        header_row = best_table.find_elements(By.TAG_NAME, "th")
        if header_row:
            headers = [clean(h.text).lower().replace(" ", "_") for h in header_row]
        else:
            first_row = best_table.find_elements(By.XPATH, ".//tr[1]/td")
            headers = [f"col_{i}" for i in range(len(first_row))]

        # Extract data rows
        for tr in best_table.find_elements(By.TAG_NAME, "tr")[1:]:
            cells = tr.find_elements(By.TAG_NAME, "td")
            if not cells:
                continue
            row = {}
            for i, cell in enumerate(cells):
                key = headers[i] if i < len(headers) else f"col_{i}"
                row[key] = clean(cell.text)
            if any(row.values()):
                rows.append(row)

    except Exception as e:
        print(f"  [BCS] Table extraction error: {e}")

    return rows


def map_row_to_permit(row: dict) -> Optional[PermitRecord]:
    """Map a raw BCS table row to a PermitRecord."""
    # Find permit number
    permit_num = ""
    for key in ["permitno", "permit_no", "permit_number", "permit#", "master_permit", "application_number"]:
        if key in row and row[key]:
            permit_num = row[key]
            break
    if not permit_num:
        # Scan all values for permit-number pattern
        for v in row.values():
            if re.match(r"[A-Z0-9]{3,}-?\d{4,}", v or ""):
                permit_num = v
                break
    if not permit_num:
        return None

    def pick(*keys):
        for k in keys:
            for rk, rv in row.items():
                if k in rk.lower() and rv:
                    return rv
        return ""

    address_raw = pick("address", "location", "site_address", "full_address", "job_address")
    city_zip = pick("city", "municipality")
    zip_code = ""
    city = city_zip
    if re.search(r"\d{5}", city_zip):
        zip_code = re.search(r"\d{5}", city_zip).group(0)
        city = city_zip[:city_zip.index(zip_code)].strip().rstrip(",")

    # Try to get zip from address if not found
    if not zip_code:
        zip_m = re.search(r"\b(\d{5})\b", address_raw)
        if zip_m:
            zip_code = zip_m.group(1)

    value_raw = pick("valuation", "value", "final_valuation", "estimated_value", "job_value")

    return PermitRecord(
        permit_number       = permit_num,
        owner_name          = pick("owner_name", "owner", "applicant"),
        business_name       = pick("contractor_name", "contractor", "licensee"),
        address_1           = address_raw,
        city                = city or "Broward County",
        state               = "FL",
        zip                 = zip_code,
        permit_type         = pick("permit_description", "permit_type", "type", "description"),
        project_description = pick("work_desc", "work_description", "scope"),
        issued_date         = parse_date(pick("last_issued_date", "issue_date", "issued_date", "date_issued")),
        status              = pick("status", "permit_status"),
        project_value       = parse_money(value_raw),
        raw_payload         = row,
    )


def next_page(driver) -> bool:
    """Navigate to next results page."""
    for xpath in [
        "//a[contains(text(),'Next') and not(contains(@class,'disabled'))]",
        "//input[@value='Next']",
        "//a[@title='Next page']",
        "//a[contains(@id,'Next')]",
    ]:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(3)
            return True
        except Exception:
            continue
    return False


def scrape_bcs(start: date, end: date, visible: bool, max_pages: int = 50) -> List[PermitRecord]:
    """Main BCS scraper — searches by date range and extracts all permits."""
    start_str = start.strftime("%m/%d/%Y")
    end_str   = end.strftime("%m/%d/%Y")
    print(f"\n[BCS] Scraping {start_str} → {end_str}")

    driver = make_driver(visible=visible)
    records: List[PermitRecord] = []
    seen: set = set()

    try:
        # BCS portal — direct Posse URLs are disabled.
        # Must navigate via the public guest search page and click the left nav.
        # URL: https://dpepp.broward.org/BCS/Default.aspx?PossePresentation=SearchForPermitGuest
        
        print(f"  [BCS] Loading guest search portal...")
        driver.get(f"{BCS_BASE}/Default.aspx?PossePresentation=SearchForPermitGuest")
        time.sleep(5)
        save_debug(driver, "01_search_page")

        # Click left nav link for "Search by Date" or "Issued Date" search
        landed = False
        nav_xpaths = [
            "//a[contains(text(),'Issue Date') or contains(text(),'Issued Date')]",
            "//a[contains(text(),'Date Range') or contains(text(),'By Date')]",
            "//a[contains(text(),'Search by Date') or contains(text(),'Date Search')]",
            "//li//a[contains(@href,'Date') or contains(@href,'date')]",
            # Try all left nav links
            "//div[contains(@class,'nav') or contains(@class,'left') or contains(@id,'nav')]//a",
        ]
        for xpath in nav_xpaths:
            try:
                els = driver.find_elements(By.XPATH, xpath)
                if els:
                    print(f"  [BCS] Nav links found via: {xpath[:60]}")
                    for el in els[:5]:
                        print(f"    → {el.text!r} href={el.get_attribute('href','')[:60]}")
                    # Click the first promising link
                    driver.execute_script("arguments[0].click();", els[0])
                    time.sleep(4)
                    src = driver.page_source.lower()
                    if any(kw in src for kw in ["issueddate", "issued_date", "dateissued", "date from", "param_0"]):
                        print(f"  [BCS] Date fields found after nav click ✓")
                        landed = True
                        break
            except Exception:
                continue

        # If still no date form, dump all links to diagnose
        if not landed:
            print("  [BCS] Dumping all page links for diagnosis:")
            save_debug(driver, "01_no_date_form")
            all_links = driver.find_elements(By.TAG_NAME, "a")
            for a in all_links[:20]:
                text = a.text.strip()
                href = a.get_attribute("href") or ""
                if text or "BCS" in href:
                    print(f"    '{text}' → {href[:80]}")

        if landed:
            # Fill dates and submit
            fill_ok = find_and_fill_dates(driver, start_str, end_str)
            save_debug(driver, "02_dates_filled")

            if submit_search(driver):
                save_debug(driver, "03_results")

                # Paginate
                for page_num in range(1, max_pages + 1):
                    rows = extract_table_rows(driver)
                    print(f"  [BCS] Page {page_num}: {len(rows)} rows")

                    for row in rows:
                        rec = map_row_to_permit(row)
                        if rec and rec.permit_number not in seen:
                            seen.add(rec.permit_number)
                            records.append(rec)

                    if not rows or not next_page(driver):
                        break
            else:
                print("  [BCS] Could not submit search")
                save_debug(driver, "03_submit_failed")

    except Exception as e:
        print(f"  [BCS] Error: {e}")
        save_debug(driver, "error")
    finally:
        driver.quit()

    print(f"  [BCS] Total records: {len(records)}")
    return records


# ---------------------------------------------------------------------------
# Database import
# ---------------------------------------------------------------------------

def get_county_id(cur) -> int:
    cur.execute("SELECT id FROM counties WHERE county_name = %s", (COUNTY_NAME,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO counties (county_name, state, active, created_at) "
        "VALUES (%s, 'FL', true, NOW()) RETURNING id", (COUNTY_NAME,)
    )
    return cur.fetchone()[0]


def import_records(records: List[PermitRecord]) -> Dict[str, int]:
    if not records or not get_connection:
        return {"raw": 0, "normalized": 0, "skipped": 0}

    conn = get_connection()
    conn.autocommit = False
    stats = {"raw": 0, "normalized": 0, "skipped": 0}

    try:
        with conn.cursor() as cur:
            county_id = get_county_id(cur)
            for rec in records:
                if not rec.permit_number:
                    stats["skipped"] += 1
                    continue

                source_record_id = f"{SOURCE_NAME}::{rec.permit_number}"
                payload = json.dumps(asdict(rec), default=str)

                cur.execute("""
                    INSERT INTO raw_permits
                        (county_id, source_file, source_record_id, raw_payload, issued_date)
                    VALUES (%s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (county_id, source_record_id) DO UPDATE SET
                        raw_payload = EXCLUDED.raw_payload,
                        issued_date = EXCLUDED.issued_date
                    RETURNING id, (xmax = 0) AS is_insert
                """, (county_id, SOURCE_NAME, source_record_id, payload, rec.issued_date))
                rp = cur.fetchone()
                raw_id = rp[0]
                if rp[1]:
                    stats["raw"] += 1

                cur.execute("""
                    INSERT INTO normalized_permits (
                        county_id, raw_permit_id, owner_name, business_name,
                        address_1, city, state, zip,
                        permit_number, permit_type, project_description,
                        issued_date, trade, normalized_hash
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (county_id, permit_number) DO UPDATE SET
                        owner_name          = EXCLUDED.owner_name,
                        address_1           = EXCLUDED.address_1,
                        permit_type         = EXCLUDED.permit_type,
                        project_description = EXCLUDED.project_description,
                        issued_date         = EXCLUDED.issued_date
                    RETURNING id, (xmax = 0) AS is_insert
                """, (
                    county_id, raw_id,
                    rec.owner_name, rec.business_name,
                    rec.address_1, rec.city, "FL", rec.zip,
                    rec.permit_number, rec.permit_type, rec.project_description,
                    rec.issued_date,
                    (rec.permit_type or "").lower()[:100] if rec.permit_type else None,
                    f"bcs::{rec.permit_number}",
                ))
                np = cur.fetchone()
                if np and np[1]:
                    stats["normalized"] += 1

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
    parser = argparse.ArgumentParser(description="Broward BCS permit scraper")
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--visible",   action="store_true")
    parser.add_argument("--no-db",     action="store_true")
    parser.add_argument("--pages",     type=int, default=50)
    args = parser.parse_args()

    end   = date.today()
    start = end - timedelta(days=args.days_back)

    records = scrape_bcs(start, end, visible=args.visible, max_pages=args.pages)

    # Save raw snapshot
    if records:
        snap = RAW_DIR / f"broward_bcs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        snap.write_text(json.dumps([asdict(r) for r in records], default=str, indent=2))
        print(f"Saved: {snap}")

    stats = {"raw": 0, "normalized": 0, "skipped": 0}
    if not args.no_db and records:
        stats = import_records(records)

    print(f"\n--- Broward BCS summary ---")
    print(f"  Records scraped    : {len(records)}")
    print(f"  raw inserted       : {stats['raw']}")
    print(f"  normalized inserted: {stats['normalized']}")
    print(f"  skipped            : {stats['skipped']}")
    if len(records) == 0:
        print(f"\n  [tip] Check debug screenshots: {DEBUG_DIR}")
        print(f"  [tip] Run with --visible to watch the browser")


if __name__ == "__main__":
    main()