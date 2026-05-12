"""
scrape_pinellas_permits.py
===========================
Pinellas County building permits via Accela Citizen Access.
Portal: https://aca-prod.accela.com/PINELLAS/Cap/CapHome.aspx?module=Building

Adapted from permit_bot download_pinellas_weekly.py for leadflow DB import.

Usage:
  python -m app.workers.scrape_pinellas_permits --days-back 30 --visible
  python -m app.workers.scrape_pinellas_permits --days-back 7 --no-db
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
COUNTY_NAME = "Pinellas"
SOURCE_NAME = "pinellas_accela"
MODULE_URL  = "https://aca-prod.accela.com/PINELLAS/Cap/CapHome.aspx?module=Building&TabName=Building"

INCLUDE_KEYWORDS = [
    "roof", "reroof", "re-roof", "mechanical", "hvac", "air conditioning",
    "electrical", "electric", "generator", "plumbing", "gas", "pool", "spa",
    "solar", "residential", "commercial", "building", "addition", "alteration",
    "remodel", "renovation", "repair", "window", "door", "demolition",
    "accessory", "screen", "enclosure", "aluminum", "siding",
]
SKIP_KEYWORDS = [
    "planning", "zoning", "tree", "right of way", "fire", "alarm", "sprinkler",
    "low voltage", "temporary", "extension", "revision", "void", "cancel",
    "change of contractor", "addressing", "certificate", "inspection", "administrative",
]

DATE_FIELD_START_IDS = [
    "ctl00_PlaceHolderMain_generalSearchForm_txtGSStartDate",
    "ctl00_PlaceHolderMain_generalSearchForm_txtGSDateFrom",
    "ctl00_PlaceHolderMain_generalSearchForm_txtStartDate",
]
DATE_FIELD_END_IDS = [
    "ctl00_PlaceHolderMain_generalSearchForm_txtGSEndDate",
    "ctl00_PlaceHolderMain_generalSearchForm_txtGSDateTo",
    "ctl00_PlaceHolderMain_generalSearchForm_txtEndDate",
]
TYPE_DROPDOWN_IDS = [
    "ctl00_PlaceHolderMain_generalSearchForm_ddlGSPermitType",
    "ctl00_PlaceHolderMain_generalSearchForm_ddlGSRecordType",
    "ctl00_PlaceHolderMain_generalSearchForm_ddl_PermitType",
]

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR  = BASE_DIR / "data" / "raw" / "pinellas" / "permits"
RAW_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class PermitRecord:
    permit_number:       str
    permit_type:         Optional[str]   = None
    owner_name:          Optional[str]   = None
    business_name:       Optional[str]   = None
    address_1:           Optional[str]   = None
    city:                str             = "Clearwater"
    state:               str             = "FL"
    zip:                 Optional[str]   = None
    project_description: Optional[str]  = None
    issued_date:         Optional[date]  = None
    status:              Optional[str]   = None
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

def keep_type(name: str) -> bool:
    low = name.lower()
    if any(k in low for k in SKIP_KEYWORDS):
        return False
    return any(k in low for k in INCLUDE_KEYWORDS)


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


def click_search(driver) -> None:
    for xpath in [
        "//a[contains(text(),'Search Applications')]",
        "//a[contains(text(),'Search Permit')]",
        "//a[contains(text(),'Search Records')]",
        "//a[contains(text(),'Search')]",
    ]:
        try:
            el = driver.find_element(By.XPATH, xpath)
            if el.is_displayed():
                driver.execute_script("arguments[0].click();", el)
                time.sleep(2)
                return
        except Exception:
            continue


def fill_date(driver, field_ids: List[str], value: str) -> None:
    for fid in field_ids:
        try:
            el = driver.find_element(By.ID, fid)
            if not el.is_displayed():
                continue
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            el.click()
            el.send_keys(Keys.CONTROL + "a")
            el.send_keys(Keys.DELETE)
            el.send_keys(value)
            el.send_keys(Keys.TAB)
            return
        except Exception:
            continue


def find_dropdown(driver):
    for dd_id in TYPE_DROPDOWN_IDS:
        try:
            el = driver.find_element(By.ID, dd_id)
            if el.is_displayed():
                return dd_id, Select(el)
        except Exception:
            continue
    return None, None


def discover_types(driver) -> List[str]:
    driver.get(MODULE_URL)
    time.sleep(3)
    click_search(driver)
    time.sleep(3)
    _, sel = find_dropdown(driver)
    if not sel:
        return []
    types = []
    for opt in sel.options:
        text = opt.text.strip()
        if not text or text.lower() in {"--select--", "select one", "all"}:
            continue
        if keep_type(text):
            types.append(text)
    print(f"  Discovered {len(types)} relevant permit types")
    return types


def search_one_type(driver, permit_type: str, start_str: str, end_str: str) -> List[dict]:
    driver.get(MODULE_URL)
    time.sleep(3)
    click_search(driver)
    time.sleep(2)

    _, sel = find_dropdown(driver)
    if sel and permit_type:
        try:
            sel.select_by_visible_text(permit_type)
            time.sleep(3)
        except Exception as e:
            print(f"    Type select failed: {e}")
            return []

    fill_date(driver, DATE_FIELD_START_IDS, start_str)
    fill_date(driver, DATE_FIELD_END_IDS,   end_str)

    for btn_id in [
        "ctl00_PlaceHolderMain_btnNewSearch",
        "ctl00_PlaceHolderMain_btnSearch",
        "ctl00_PlaceHolderMain_btnSubmit",
    ]:
        try:
            btn = driver.find_element(By.ID, btn_id)
            if btn.is_displayed() and btn.is_enabled():
                driver.execute_script("arguments[0].click();", btn)
                break
        except Exception:
            continue
    time.sleep(5)

    # Paginate
    all_rows: List[dict] = []
    for page in range(1, 15):
        soup = BeautifulSoup(driver.page_source, "lxml")
        found = False
        for tbl in soup.find_all("table"):
            ths = [th.get_text(" ", strip=True) for th in tbl.find_all("th")]
            if any(h in ths for h in ["Record #", "Record Number", "Permit #", "Address", "Status"]):
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
        next_btn = None
        for xpath in ["//a[contains(@id,'lbtnNextPage')]", "//a[contains(@title,'Next page')]"]:
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
    def pick(*keys) -> str:
        for k in keys:
            for rk, rv in row.items():
                if k.lower() in rk.lower() and rv and rv.strip():
                    return clean(rv)
        return ""

    permit_num = pick("Record #", "Record Number", "Permit #", "Permit Number")
    if not permit_num:
        return None
    if len(permit_num) > 50 or not re.search(r"\d", permit_num):
        return None
    if any(kw in permit_num.lower() for kw in ["select", "cancel", "--", "spell"]):
        return None

    address = pick("Address", "Site Address")
    zip_code = ""
    zip_m = re.search(r"\b(\d{5})\b", address)
    if zip_m:
        zip_code = zip_m.group(1)

    return PermitRecord(
        permit_number       = permit_num,
        permit_type         = row.get("_permit_type") or pick("Record Type", "Permit Type", "Type"),
        owner_name          = pick("Owner", "Owner Name"),
        business_name       = pick("Contractor", "Contractor Name"),
        address_1           = address,
        city                = "Clearwater",
        state               = "FL",
        zip                 = zip_code,
        project_description = pick("Description", "Project Name"),
        issued_date         = parse_date(pick("Issued Date", "Date", "Applied Date", "Start Date")),
        status              = pick("Status"),
        project_value       = parse_money(pick("Valuation", "Job Value")),
        raw_payload         = row,
    )


# ---------------------------------------------------------------------------
# Main scrape
# ---------------------------------------------------------------------------

def scrape_pinellas(start: date, end: date, visible: bool = False) -> List[PermitRecord]:
    start_str = start.strftime("%m/%d/%Y")
    end_str   = end.strftime("%m/%d/%Y")
    mid       = start + (end - start) / 2
    mid_str   = mid.strftime("%m/%d/%Y")

    print(f"\n[Pinellas] Scraping {start_str} → {end_str} (split at {mid_str})")

    driver = make_driver(visible=visible)
    all_records: List[PermitRecord] = []
    seen: set = set()

    try:
        permit_types = discover_types(driver)
        if not permit_types:
            permit_types = [""]

        for pt in permit_types:
            label = pt[:50] if pt else "ALL"
            rows = (
                search_one_type(driver, pt, start_str, mid_str) +
                search_one_type(driver, pt, mid_str, end_str)
            )
            count = 0
            for row in rows:
                rec = row_to_permit(row)
                if rec and rec.permit_number not in seen:
                    seen.add(rec.permit_number)
                    all_records.append(rec)
                    count += 1
            print(f"  {label}: {count}")
            time.sleep(0.5)

    except Exception as e:
        print(f"  [Pinellas] Error: {e}")
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
                        owner_name  = EXCLUDED.owner_name,
                        address_1   = EXCLUDED.address_1,
                        permit_type = EXCLUDED.permit_type,
                        issued_date = EXCLUDED.issued_date
                    RETURNING id, (xmax = 0) AS is_insert
                """, (
                    county_id, raw_id,
                    rec.owner_name, rec.business_name,
                    rec.address_1, rec.city, "FL", rec.zip,
                    rec.permit_number, rec.permit_type, rec.project_description,
                    rec.issued_date,
                    (rec.permit_type or "").lower()[:100],
                    f"pinellas::{rec.permit_number}",
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
    parser = argparse.ArgumentParser(description="Pinellas County permit scraper")
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--visible",   action="store_true")
    parser.add_argument("--no-db",     action="store_true")
    args = parser.parse_args()

    end   = date.today()
    start = end - timedelta(days=args.days_back)

    records = scrape_pinellas(start, end, visible=args.visible)

    if records:
        snap = RAW_DIR / f"pinellas_permits_{nowstamp()}.json"
        snap.write_text(json.dumps([asdict(r) for r in records], default=str, indent=2))
        print(f"Saved: {snap}")
        print("\nSample:")
        for r in records[:5]:
            print(f"  {r.permit_number} | {r.permit_type} | {r.address_1} | {r.issued_date}")

    stats = {"raw": 0, "normalized": 0, "skipped": 0}
    if not args.no_db and records:
        stats = import_records(records)

    print(f"\n--- Pinellas summary ---")
    print(f"  Records scraped    : {len(records)}")
    print(f"  raw inserted       : {stats['raw']}")
    print(f"  normalized inserted: {stats['normalized']}")
    print(f"  skipped            : {stats['skipped']}")


if __name__ == "__main__":
    main()
