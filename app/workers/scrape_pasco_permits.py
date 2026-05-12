"""
scrape_pasco_permits.py
================================
Pasco County building permits via Accela Citizen Access.
Portal: https://aca-prod.accela.com/PASCO/Cap/CapHome.aspx?module=Building

Strategy: Search per permit type (19 types) with date-split to bypass 59-row cap.
Confirmed working approach from permit_bot download_pasco_weekly.py.

Usage:
  python -m app.workers.scrape_pasco_permits --days-back 30 --visible
  python -m app.workers.scrape_pasco_permits --days-back 7
"""
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select

try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    ChromeDriverManager = None

try:
    from app.core.db import get_connection
except ImportError:
    get_connection = None

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
COUNTY_NAME = "Pasco"
SOURCE_NAME = "pasco_accela"
MODULE_URL  = "https://aca-prod.accela.com/PASCO/Cap/CapHome.aspx?module=Building&TabName=Building"

# Confirmed permit types from live portal 2026-04-10
SEARCH_TYPES = [
    "Residential Roofing",
    "Residential Electrical",
    "Residential Mechanical",
    "Residential Plumbing",
    "Residential Pool and Spa",
    "Residential New",
    "Residential Alteration",
    "Residential Addition",
    "Residential Alternative Energy Source",
    "Commercial New",
    "Commercial Alteration",
    "Commercial Electrical",
    "Commercial Mechanical",
    "Commercial Plumbing",
    "Commercial Roofing",
]

DATE_FIELD_START = "ctl00_PlaceHolderMain_generalSearchForm_txtGSStartDate"
DATE_FIELD_END   = "ctl00_PlaceHolderMain_generalSearchForm_txtGSEndDate"
TYPE_DROPDOWN_ID = "ctl00_PlaceHolderMain_generalSearchForm_ddlGSPermitType"

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR  = BASE_DIR / "data" / "raw" / "pasco" / "permits"
RAW_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class PermitRecord:
    permit_number:       str
    permit_type:         Optional[str]  = None
    owner_name:          Optional[str]  = None
    business_name:       Optional[str]  = None
    address_1:           Optional[str]  = None
    city:                str            = "Tampa"
    state:               str            = "FL"
    zip:                 Optional[str]  = None
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
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
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

def nowstamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def make_driver(visible: bool = False) -> webdriver.Chrome:
    opts = Options()
    if not visible:
        opts.add_argument("--headless=new")
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    if ChromeDriverManager:
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=opts)
    return webdriver.Chrome(options=opts)


def fill_date(driver, field_id: str, value: str) -> None:
    try:
        el = driver.find_element(By.ID, field_id)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        el.click()
        el.send_keys(Keys.CONTROL + "a")
        el.send_keys(Keys.DELETE)
        el.send_keys(value)
        el.send_keys(Keys.TAB)
        time.sleep(0.3)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

def navigate_to_search(driver) -> None:
    driver.get(MODULE_URL)
    time.sleep(3)
    for xpath in [
        "//a[contains(text(),'Search Applications')]",
        "//a[contains(text(),'Search Permit')]",
        "//a[contains(text(),'Search')]",
    ]:
        try:
            driver.find_element(By.XPATH, xpath).click()
            time.sleep(2)
            break
        except Exception:
            continue


def search_one_type(driver, permit_type: str, start_str: str, end_str: str) -> List[dict]:
    navigate_to_search(driver)

    # Wait for dropdown
    for _ in range(10):
        try:
            el = driver.find_element(By.ID, TYPE_DROPDOWN_ID)
            if el.is_displayed():
                break
        except Exception:
            pass
        time.sleep(1)

    # Dump all available types on first call
    if not getattr(search_one_type, "_dumped", False):
        search_one_type._dumped = True
        try:
            sel_el = driver.find_element(By.ID, TYPE_DROPDOWN_ID)
            all_opts = [o.text.strip() for o in sel_el.find_elements(By.TAG_NAME, "option") if o.text.strip()]
            print(f"\n    *** AVAILABLE PERMIT TYPES ({len(all_opts)}) ***")
            for opt in all_opts:
                print(f"      {opt!r}")
            print()
        except Exception as e:
            print(f"    Could not dump options: {e}")

    # Select permit type
    try:
        sel = Select(driver.find_element(By.ID, TYPE_DROPDOWN_ID))
        sel.select_by_visible_text(permit_type)
        time.sleep(4)  # Accela may auto-submit on change
    except Exception as e:
        print(f"    Type select failed: {e}")
        return []

    # Fill dates
    fill_date(driver, DATE_FIELD_START, start_str)
    fill_date(driver, DATE_FIELD_END,   end_str)

    # Submit
    for btn_id in [
        "ctl00_PlaceHolderMain_btnNewSearch",
        "ctl00_PlaceHolderMain_btnSearch",
    ]:
        try:
            btn = driver.find_element(By.ID, btn_id)
            driver.execute_script("arguments[0].click();", btn)
            break
        except Exception:
            continue
    time.sleep(5)

    # Paginate and extract
    all_rows: List[dict] = []
    for page in range(1, 15):
        soup = BeautifulSoup(driver.page_source, "lxml")
        found = False
        for tbl in soup.find_all("table"):
            ths = [th.get_text(strip=True) for th in tbl.find_all("th")]
            if any(h in ths for h in ["Record #", "Address", "Status", "Permit #", "Record Number"]):
                found = True
                for tr in (tbl.find("tbody") or tbl).find_all("tr"):
                    cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                    if len(cells) >= 2 and any(c.strip() for c in cells):
                        row = dict(zip(ths, cells))
                        row["_permit_type"] = permit_type
                        all_rows.append(row)
                break
        if not found:
            break
        # Next page
        next_btn = None
        for xpath in [
            "//a[contains(@id,'lbtnNextPage')]",
            "//a[contains(@title,'Next page')]",
        ]:
            try:
                b = driver.find_element(By.XPATH, xpath)
                if b.is_displayed() and b.is_enabled():
                    next_btn = b
                    break
            except Exception:
                continue
        if not next_btn:
            break
        driver.execute_script("arguments[0].click();", next_btn)
        time.sleep(3)

    return all_rows


def row_to_permit(row: dict) -> Optional[PermitRecord]:
    """Map a raw Accela table row to a PermitRecord."""
    def pick(*keys) -> str:
        for k in keys:
            for rk, rv in row.items():
                if k.lower() in rk.lower() and rv and rv.strip():
                    return clean(rv)
        return ""

    permit_num = pick("Record #", "Record Number", "Permit #", "Permit Number", "PERMITNO")
    if not permit_num:
        return None
    
    # Reject garbage rows — valid Pasco permit numbers look like:
    # BCP-25-0001234, E-25-001234, 25-0001234
    # Must contain digits and be reasonable length
    # Reject if it looks like UI chrome (dropdown options, button labels)
    if len(permit_num) > 50:  # dropdown option list
        return None
    if any(kw in permit_num.lower() for kw in [
        "select", "cancel", "search", "permit type", "--", "spell check",
        "add |", "* name", "description:", "admin payment"
    ]):
        return None
    # Must contain at least one digit
    if not re.search(r"\d", permit_num):
        return None

    # Validate date
    date_raw = pick("Issued Date", "Date", "Applied Date", "Start Date", "LAST_ISSUED_DATE")
    issued   = parse_date(date_raw)

    address_raw = pick("Address", "Site Address", "FULL_ADDRESS")
    # Extract zip from address if present
    zip_code = ""
    zip_m = re.search(r"\b(\d{5})\b", address_raw)
    if zip_m:
        zip_code = zip_m.group(1)

    return PermitRecord(
        permit_number       = permit_num,
        permit_type         = row.get("_permit_type") or pick("Record Type", "Permit Type", "Description"),
        owner_name          = pick("Owner", "Owner Name", "Applicant"),
        business_name       = pick("Contractor", "Contractor Name", "CONTRACTOR_NAME"),
        address_1           = address_raw,
        city                = "Tampa",
        state               = "FL",
        zip                 = zip_code,
        project_description = pick("Description", "Project Name", "PROJECT_NAME"),
        issued_date         = issued,
        status              = pick("Status", "PERMIT_STATUS"),
        project_value       = parse_money(pick("Valuation", "Job Value", "FINAL_VALUATION")),
        raw_payload         = row,
    )


def scrape_pasco(start: date, end: date, visible: bool = False) -> List[PermitRecord]:
    """Scrape all Pasco permits for the date range."""
    start_str = start.strftime("%m/%d/%Y")
    end_str   = end.strftime("%m/%d/%Y")
    # Split period in half to handle 59-row cap
    mid       = start + (end - start) / 2
    mid_str   = mid.strftime("%m/%d/%Y")

    print(f"\n[Pasco] Scraping {start_str} → {end_str}")
    print(f"  Period split at: {mid_str}")

    driver = make_driver(visible=visible)
    all_records: List[PermitRecord] = []
    seen: set = set()

    try:
        for permit_type in SEARCH_TYPES:
            print(f"  {permit_type[:50]}...", end="", flush=True)
            rows1 = search_one_type(driver, permit_type, start_str, mid_str)
            rows2 = search_one_type(driver, permit_type, mid_str, end_str)
            all_rows = rows1 + rows2
            count = 0
            for row in all_rows:
                rec = row_to_permit(row)
                if rec and rec.permit_number not in seen:
                    seen.add(rec.permit_number)
                    all_records.append(rec)
                    count += 1
            print(f" {count} permits")
            time.sleep(0.5)

    except Exception as e:
        print(f"\n  [Pasco] Error: {e}")
    finally:
        driver.quit()

    print(f"\n  Total unique: {len(all_records)}")
    return all_records


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
                        issued_date         = EXCLUDED.issued_date
                    RETURNING id, (xmax = 0) AS is_insert
                """, (
                    county_id, raw_id,
                    rec.owner_name, rec.business_name,
                    rec.address_1, rec.city, "FL", rec.zip,
                    rec.permit_number, rec.permit_type, rec.project_description,
                    rec.issued_date,
                    (rec.permit_type or "").lower()[:100] if rec.permit_type else None,
                    f"pasco::{rec.permit_number}",
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
    parser = argparse.ArgumentParser(description="Pasco County permit scraper")
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--visible",   action="store_true")
    parser.add_argument("--no-db",     action="store_true")
    args = parser.parse_args()

    end   = date.today()
    start = end - timedelta(days=args.days_back)

    records = scrape_pasco(start, end, visible=args.visible)

    if records:
        snap = RAW_DIR / f"pasco_permits_{nowstamp()}.json"
        snap.write_text(
            json.dumps([asdict(r) for r in records], default=str, indent=2),
            encoding="utf-8"
        )
        print(f"Saved: {snap}")
        print("\nSample:")
        for r in records[:5]:
            print(f"  {r.permit_number} | {r.owner_name or r.business_name} | {r.address_1} | {r.issued_date}")

    stats = {"raw": 0, "normalized": 0, "skipped": 0}
    if not args.no_db and records:
        stats = import_records(records)

    print(f"\n--- Pasco summary ---")
    print(f"  Records scraped    : {len(records)}")
    print(f"  raw inserted       : {stats['raw']}")
    print(f"  normalized inserted: {stats['normalized']}")
    print(f"  skipped            : {stats['skipped']}")


if __name__ == "__main__":
    main()