"""
scrape_polk_permits.py
======================
Polk County building permits from Accela Citizen Access (POLKCO tenant).
Portal: https://aca-prod.accela.com/POLKCO/

Adapted from download_polk_weekly.py — same Accela strategy,
writes directly to normalized_permits instead of CSV.

Usage:
  python -m app.workers.scrape_polk_permits --days-back 30
  python -m app.workers.scrape_polk_permits --days-back 90 --visible
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

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
COUNTY_NAME = "Polk"
BASE_URL    = "https://aca-prod.accela.com/POLKCO"
MODULE_URL  = f"{BASE_URL}/Cap/CapHome.aspx?module=Building"

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR  = BASE_DIR / "data" / "raw" / "polk" / "permits"
RAW_DIR.mkdir(parents=True, exist_ok=True)

RECORD_TYPES = [
    "Re-Roof Permit",
    "Electric Permit",
    "Mechanical Permit",
    "Plumbing Permit",
    "Pool Permit",
    "Residential New Permit - Ex: New House",
    "Residential Renovation/Addition Permit-Ex: Screen Room (solid roof)",
    "Residential Accessory Permit - Ex: Shed, Detached Carport, Guest Houses, Screen/Pool Enclosures",
    "Commercial New Permit - Ex: Shell Bldg., Dumpster Enclosure, Office Trailer, etc.",
    "Commercial Renovation Permit - Ex: Tenant Buildout, Window Changeout, Remodel, Addition, etc.",
    "Window and Door Permit",
    "Fence or Wall Permit",
    "Gas Permit",
    "Demolition Permit",
    "Mobile Home Permit",
    "Commercial Fire Permit - Ex: Fire Sprinkler, Suppression, Underground",
    "Commercial Multi-Family Permit - Ex: Apts or Condos, Triplex or Quadplex",
    "Commercial Sign Permit",
]

COLUMN_MAP = {
    "Record #":        "permit_number",
    "Record Number":   "permit_number",
    "Permit #":        "permit_number",
    "Permit Number":   "permit_number",
    "Record Type":     "permit_type",
    "Permit Type":     "permit_type",
    "Description":     "project_description",
    "Address":         "address_1",
    "Site Address":    "address_1",
    "Date":            "issued_date",
    "Issued Date":     "issued_date",
    "Issue Date":      "issued_date",
    "Applied Date":    "issued_date",
    "Start Date":      "issued_date",
    "Status":          "status",
    "Valuation":       "valuation",
    "Job Value":       "valuation",
    "Contractor":      "owner_name",
    "Contractor Name": "owner_name",
    "Owner":           "owner_name",
    "Project Name":    "project_description",
}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def make_driver(visible: bool = False) -> webdriver.Chrome:
    opts = Options()
    if not visible:
        opts.add_argument("--headless=new")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    if ChromeDriverManager:
        return webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), options=opts
        )
    return webdriver.Chrome(options=opts)


# ---------------------------------------------------------------------------
# Accela helpers (same as Hillsborough/Pinellas)
# ---------------------------------------------------------------------------

def fill_date_field(driver, field_id: str, date_str: str) -> bool:
    try:
        field = driver.find_element(By.ID, field_id)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", field)
        field.click()
        field.send_keys(Keys.CONTROL + "a")
        field.send_keys(Keys.DELETE)
        field.send_keys(date_str)
        field.send_keys(Keys.TAB)
        return True
    except Exception:
        return False


def select_record_type(driver, rec_type: str) -> bool:
    dropdown_ids = [
        "ctl00_PlaceHolderMain_generalSearchForm_ddlGSPermitType",
        "ctl00_PlaceHolderMain_generalSearchForm_ddlGSRecordType",
        "ctl00_PlaceHolderMain_generalSearchForm_ddl_PermitType",
    ]
    for dd_id in dropdown_ids:
        try:
            sel = Select(driver.find_element(By.ID, dd_id))
            try:
                sel.select_by_visible_text(rec_type)
                return True
            except Exception:
                pass
            # Partial match
            key = rec_type.split(" - ")[0].split(" Permit")[0].strip().lower()
            match = next((o.text for o in sel.options if key in o.text.lower()), None)
            if match:
                sel.select_by_visible_text(match)
                return True
        except Exception:
            continue
    return False


def scrape_page(driver) -> List[dict]:
    soup = BeautifulSoup(driver.page_source, "lxml")
    for tbl in soup.find_all("table"):
        headers_lower = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if any(h in headers_lower for h in ["permit #", "record #", "address", "status", "date"]):
            col_names = [th.get_text(strip=True) for th in tbl.find_all("th")]
            rows = []
            tbody = tbl.find("tbody") or tbl
            for tr in tbody.find_all("tr"):
                cells = tr.find_all("td")
                if len(cells) < 2:
                    continue
                values = [td.get_text(" ", strip=True) for td in cells]
                if any(v.strip() for v in values):
                    rows.append(dict(zip(col_names, values)))
            return rows
    return []


def click_next(driver) -> bool:
    for xpath in [
        "//a[contains(@id,'lbtnNextPage')]",
        "//a[contains(@title,'Next page')]",
        "//a[text()='Next']",
    ]:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            if btn.is_displayed() and btn.is_enabled():
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(3)
                return True
        except Exception:
            continue
    return False


def search_record_type(driver, rec_type: str, start_str: str, end_str: str) -> List[dict]:
    driver.get(MODULE_URL)
    time.sleep(3)

    for xpath in [
        "//a[contains(text(),'Search Permit')]",
        "//a[contains(text(),'Search Applications')]",
        "//a[contains(text(),'Search')]",
    ]:
        try:
            driver.find_element(By.XPATH, xpath).click()
            time.sleep(2)
            break
        except Exception:
            continue

    if not select_record_type(driver, rec_type):
        return []

    time.sleep(0.5)
    driver.execute_script("window.scrollTo(0, 600);")
    time.sleep(0.3)

    for field_id, value in [
        ("ctl00_PlaceHolderMain_generalSearchForm_txtGSStartDate", start_str),
        ("ctl00_PlaceHolderMain_generalSearchForm_txtGSEndDate",   end_str),
    ]:
        fill_date_field(driver, field_id, value)

    try:
        btn = driver.find_element(By.ID, "ctl00_PlaceHolderMain_btnNewSearch")
        driver.execute_script("arguments[0].click();", btn)
    except Exception:
        try:
            driver.find_element(
                By.XPATH, "//input[@value='Search' or @value='Submit']"
            ).click()
        except Exception:
            return []

    time.sleep(4)

    all_rows: List[dict] = []
    page = 1
    while True:
        page_rows = scrape_page(driver)
        for row in page_rows:
            row["_record_type"] = rec_type
        all_rows.extend(page_rows)
        if not page_rows or page >= 15:
            break
        if not click_next(driver):
            break
        page += 1

    return all_rows


# ---------------------------------------------------------------------------
# Normalize
# ---------------------------------------------------------------------------

def clean(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()

def parse_date(v) -> Optional[date]:
    s = clean(v)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

def parse_address(addr: str):
    """Split '123 Main St, Lakeland, FL 33801' into components."""
    parts = [p.strip() for p in addr.split(",")]
    address_1 = parts[0] if parts else addr
    city = parts[1] if len(parts) > 1 else None
    state = zip_code = None
    if len(parts) > 2:
        state_zip = parts[2].strip().split()
        state    = state_zip[0] if state_zip else None
        zip_code = state_zip[1] if len(state_zip) > 1 else None
    return address_1, city, state, zip_code

def make_hash(county_id: int, permit_number: str, owner: str) -> str:
    return f"polk::{permit_number}::{owner[:30]}"

def normalize_row(row: dict, rec_type: str) -> Optional[dict]:
    mapped = {}
    for raw_col, val in row.items():
        norm_col = COLUMN_MAP.get(raw_col.strip())
        if norm_col and norm_col not in mapped:
            mapped[norm_col] = clean(val)

    permit_number = mapped.get("permit_number", "")
    if not permit_number or permit_number.lower() in ("none", ""):
        return None

    # Garbage filter — same as Hillsborough
    if len(permit_number) > 50 or not re.search(r"\d", permit_number):
        return None
    # Filter pagination headers like "Showing 1-10 of 100+"
    if "showing" in permit_number.lower() or "of " in permit_number.lower():
        return None

    raw_addr   = mapped.get("address_1", "")
    address_1, city, state, zip_code = parse_address(raw_addr)

    permit_type = mapped.get("permit_type") or rec_type.split(" - ")[0]
    owner_name  = mapped.get("owner_name", "")
    issued_date = parse_date(mapped.get("issued_date", ""))

    # Determine trade
    pt_lower = permit_type.lower()
    if any(k in pt_lower for k in ["electric", "elec"]):
        trade = "electrical"
    elif any(k in pt_lower for k in ["plumb", "plbg"]):
        trade = "plumbing"
    elif any(k in pt_lower for k in ["mechanical", "mech", "hvac", "a/c", "ac"]):
        trade = "mechanical"
    elif any(k in pt_lower for k in ["roof"]):
        trade = "roofing"
    elif any(k in pt_lower for k in ["pool"]):
        trade = "pool"
    elif any(k in pt_lower for k in ["new", "residential new", "commercial new"]):
        trade = "new_construction"
    else:
        trade = "general"

    return {
        "permit_number":      permit_number,
        "permit_type":        permit_type,
        "owner_name":         owner_name or None,
        "address_1":          address_1 or None,
        "city":               city or None,
        "state":              state or "FL",
        "zip":                zip_code or None,
        "issued_date":        issued_date,
        "trade":              trade,
        "project_description": mapped.get("project_description") or permit_type,
    }


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
        "VALUES (%s, 'FL', true, NOW()) RETURNING id", (COUNTY_NAME,)
    )
    return cur.fetchone()[0]


def import_permits(permits: List[dict]) -> Dict[str, int]:
    if not permits or not get_connection:
        return {"inserted": 0, "skipped": 0}

    conn = get_connection()
    conn.autocommit = False
    stats = {"inserted": 0, "skipped": 0}

    try:
        with conn.cursor() as cur:
            county_id = get_county_id(cur)
            for p in permits:
                if not p:
                    stats["skipped"] += 1
                    continue
                n_hash = make_hash(county_id, p["permit_number"], p.get("owner_name") or "")
                cur.execute("""
                    INSERT INTO normalized_permits (
                        county_id, raw_permit_id, owner_name, business_name,
                        address_1, city, state, zip,
                        permit_number, permit_type, project_description,
                        issued_date, trade, normalized_hash
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (normalized_hash) DO UPDATE SET
                        owner_name   = COALESCE(EXCLUDED.owner_name, normalized_permits.owner_name),
                        issued_date  = COALESCE(EXCLUDED.issued_date, normalized_permits.issued_date)
                    RETURNING id, (xmax = 0) AS is_insert
                """, (
                    county_id, None,
                    p.get("owner_name"), None,
                    p.get("address_1"), p.get("city"), p.get("state", "FL"), p.get("zip"),
                    p["permit_number"], p.get("permit_type"), p.get("project_description"),
                    p.get("issued_date"), p.get("trade"), n_hash,
                ))
                row = cur.fetchone()
                if row and row[1]:
                    stats["inserted"] += 1
                else:
                    stats["skipped"] += 1
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
    parser = argparse.ArgumentParser(description="Polk County permit scraper")
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--visible",   action="store_true")
    parser.add_argument("--no-db",     action="store_true")
    args = parser.parse_args()

    today      = date.today()
    start_date = today - timedelta(days=args.days_back)
    start_str  = start_date.strftime("%m/%d/%Y")
    end_str    = today.strftime("%m/%d/%Y")
    print(f"[Polk Permits] {start_str} → {end_str}")

    driver = make_driver(visible=args.visible)
    all_rows: List[dict] = []

    try:
        for rec_type in RECORD_TYPES:
            rows = search_record_type(driver, rec_type, start_str, end_str)
            if rows:
                label = rec_type.split(" - ")[0][:40]
                print(f"  {label}: {len(rows)}")
                all_rows.extend(rows)
            time.sleep(0.5)
    finally:
        driver.quit()

    if not all_rows:
        print("No permits found.")
        return

    # Normalize
    permits = []
    seen = set()
    for row in all_rows:
        rec_type = row.get("_record_type", "")
        p = normalize_row(row, rec_type)
        if p and p["permit_number"] not in seen:
            seen.add(p["permit_number"])
            permits.append(p)

    print(f"\nTotal unique permits: {len(permits)}")

    # Save snapshot
    snap = RAW_DIR / f"polk_permits_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    snap.write_text(
        json.dumps([{k: str(v) for k, v in p.items()} for p in permits], indent=2),
        encoding="utf-8"
    )
    print(f"Saved: {snap}")

    if permits:
        print("\nSample:")
        for p in permits[:5]:
            print(f"  {p['permit_number']} | {p['owner_name']} | {p['permit_type']} | {p['issued_date']}")

    stats = {"inserted": 0, "skipped": 0}
    if not args.no_db:
        stats = import_permits(permits)

    print(f"\n--- Polk permit summary ---")
    print(f"  Scraped : {len(permits)}")
    print(f"  Inserted: {stats['inserted']}")
    print(f"  Skipped : {stats['skipped']}")


if __name__ == "__main__":
    main()