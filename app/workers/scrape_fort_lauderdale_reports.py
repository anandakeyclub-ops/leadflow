"""
scrape_fort_lauderdale_reports.py
==================================
Fort Lauderdale permit scraper using LauderBuild.

LauderBuild (https://aca-prod.accela.com/FTL) is NOT standard Accela.
It has a custom skin with:
  - A "Reports (8)" dropdown in the top right showing pre-built permit reports
  - A General Search form that searches by record number, type, description
  - NO date range search on the public-facing form

Strategy:
  1. Click "Reports" dropdown → click each report link → extract permit rows
  2. Use General Search with wildcard "%" to get recent records
  3. Parse results from both approaches

Usage:
  python -m app.workers.scrape_fort_lauderdale_reports --visible
  python -m app.workers.scrape_fort_lauderdale_reports --visible --no-db
  python -m app.workers.scrape_fort_lauderdale_reports --visible --reports-only
  python -m app.workers.scrape_fort_lauderdale_reports --visible --search-only
"""
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait, Select

try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    ChromeDriverManager = None

try:
    from app.core.db import get_connection
except Exception:
    get_connection = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FTL_URL     = "https://aca-prod.accela.com/FTL/Cap/CapHome.aspx?module=Permits&TabName=Permits"
SOURCE_NAME = "fort_lauderdale_lauderbuild"
COUNTY_NAME = "Broward"

PROJECT_ROOT    = Path.cwd()
DEBUG_ROOT      = PROJECT_ROOT / "data" / "debug" / "broward_permits" / "fort_lauderdale"
RAW_EXPORT_ROOT = PROJECT_ROOT / "data" / "raw" / "broward" / "permits"
for d in [DEBUG_ROOT, RAW_EXPORT_ROOT]:
    d.mkdir(parents=True, exist_ok=True)

# FTL permit formats: BLD-ROOF-0001234, BLD-RES-0001234, FTL-26-001234
PERMIT_RE = re.compile(
    r"\b(?:"
    r"[A-Z]{2,5}-[A-Z]{2,6}-\d{5,8}"   # BLD-ROOF-0001234
    r"|[A-Z]{2,4}-\d{2}-\d{4,7}"        # FTL-26-001234
    r"|PM[-\s]?\d{5,10}"                 # PM-1234567 (pre-2019)
    r"|[A-Z]{1,4}\d{2}[-\s]\d{4,7}"     # B26-001234
    r")\b",
    re.I,
)
DATE_RE = re.compile(r"\b(?:\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})\b")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class PermitRecord:
    source_name:         str            = SOURCE_NAME
    jurisdiction:        str            = "Fort Lauderdale"
    permit_number:       str            = ""
    permit_type:         Optional[str]  = None
    project_description: Optional[str] = None
    issued_date:         Optional[date] = None
    owner_name:          Optional[str]  = None
    business_name:       Optional[str]  = None
    address_1:           Optional[str]  = None
    city:                str            = "Fort Lauderdale"
    state:               str            = "FL"
    zip:                 Optional[str]  = None
    status:              Optional[str]  = None
    raw_payload:         Dict           = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()

def norm_permit(v: str) -> str:
    return re.sub(r"[\s]", "", v).upper()

def parse_date(v: Any) -> Optional[date]:
    s = clean(v)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

def nowstamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def safe_name(v: str) -> str:
    return re.sub(r"[^\w]", "_", v)[:40]


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

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


def save_debug(driver, label: str) -> None:
    try:
        (DEBUG_ROOT / f"{label}.html").write_text(
            driver.page_source, encoding="utf-8", errors="ignore"
        )
        driver.save_screenshot(str(DEBUG_ROOT / f"{label}.png"))
        print(f"  [debug] {label}.png")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Approach A: LauderBuild Reports dropdown
# Screenshot shows "Reports (8)" in top right — click it and extract each report
# ---------------------------------------------------------------------------

def scrape_via_reports(driver, debug: bool = False) -> List[PermitRecord]:
    print("\n[FTL] Approach A: Reports dropdown")
    records = []

    driver.get(FTL_URL)
    time.sleep(5)

    if debug:
        save_debug(driver, "A_01_home")

    # Click the "Reports" dropdown/link in the top navigation
    # Screenshot shows "Reports (8)" text — find and click it
    report_menu_clicked = False
    for xpath in [
        "//a[contains(text(),'Reports')]",
        "//span[contains(text(),'Reports')]",
        "//li[contains(text(),'Reports')]",
        "//div[contains(text(),'Reports')]",
        "//*[contains(@class,'report') and contains(text(),'Report')]",
    ]:
        try:
            el = driver.find_element(By.XPATH, xpath)
            driver.execute_script("arguments[0].click();", el)
            time.sleep(2)
            print(f"  [A] Clicked Reports via: {xpath}")
            report_menu_clicked = True
            break
        except Exception:
            continue

    if debug:
        save_debug(driver, "A_02_after_reports_click")

    # Find report links that appeared after clicking
    # LauderBuild report links have id starting with 'report' e.g. report33518
    report_links = []
    for a in driver.find_elements(By.XPATH, "//a[starts-with(@id,'report')]"):
        rid  = a.get_attribute("id") or ""
        text = clean(a.text)
        href = a.get_attribute("href") or ""
        if rid.startswith("report"):
            report_links.append((rid, text, href))

    # Also check for links in dropdown menus
    if not report_links:
        for a in driver.find_elements(By.XPATH, "//ul//a | //div[contains(@class,'dropdown')]//a"):
            text = clean(a.text)
            rid  = a.get_attribute("id") or ""
            href = a.get_attribute("href") or ""
            if "permit" in text.lower() or "report" in text.lower() or rid.startswith("report"):
                report_links.append((rid or f"link_{len(report_links)}", text, href))

    print(f"  [A] Found {len(report_links)} report links")
    for rid, text, href in report_links:
        print(f"    {rid}: '{text[:60]}' href={href[:60]}")

    if not report_links:
        print("  [A] No report links found")
        return records

    for rid, rtext, rhref in report_links[:10]:  # max 10 reports
        try:
            driver.get(FTL_URL)
            time.sleep(3)

            # Re-click Reports menu
            for xpath in [
                "//a[contains(text(),'Reports')]",
                "//span[contains(text(),'Reports')]",
            ]:
                try:
                    driver.find_element(By.XPATH, xpath).click()
                    time.sleep(1.5)
                    break
                except Exception:
                    continue

            # Click specific report
            try:
                if rid.startswith("report"):
                    el = WebDriverWait(driver, 8).until(
                        EC.presence_of_element_located((By.ID, rid))
                    )
                else:
                    el = driver.find_element(By.XPATH, f"//a[contains(text(),'{rtext[:30]}')]")
                driver.execute_script("arguments[0].scrollIntoView({{block:'center'}});", el)
                driver.execute_script("arguments[0].click();", el)
                time.sleep(5)
            except Exception as e:
                print(f"  [A] Could not click {rid}: {e}")
                continue

            # Handle new window
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])
                time.sleep(2)

            if debug:
                save_debug(driver, f"A_report_{safe_name(rid)}")

            recs = parse_html_for_permits(driver.page_source, rid, driver.current_url)
            print(f"  [A] {rid} '{rtext[:40]}': {len(recs)} permits")
            records.extend(recs)

            if len(driver.window_handles) > 1:
                driver.close()
                driver.switch_to.window(driver.window_handles[0])

        except Exception as e:
            print(f"  [A] Error on {rid}: {e}")
            if len(driver.window_handles) > 1:
                try:
                    driver.close()
                    driver.switch_to.window(driver.window_handles[0])
                except Exception:
                    pass

    return records


# ---------------------------------------------------------------------------
# Approach B: General Search with wildcard
# LauderBuild's General Search accepts % as wildcard in Permit Description
# Search for "%" returns all recent records
# ---------------------------------------------------------------------------

def scrape_via_wildcard_search(driver, debug: bool = False) -> List[PermitRecord]:
    print("\n[FTL] Approach B: Wildcard general search")
    records = []

    driver.get(FTL_URL)
    time.sleep(5)

    if debug:
        save_debug(driver, "B_01_home")

    # Fill Permit Description with "%" wildcard
    # Screenshot shows: Record Number | Record Type | Permit Description fields
    for xpath in [
        "//input[contains(@id,'Description') or contains(@name,'Description')]",
        "//input[contains(@id,'Permit') or contains(@placeholder,'Description')]",
        "//input[@type='text'][3]",  # Third text input is typically Description
    ]:
        try:
            el = driver.find_element(By.XPATH, xpath)
            el.clear()
            el.send_keys("%")
            print(f"  [B] Filled description with % via {xpath}")
            break
        except Exception:
            continue

    # Check "Search All Records" checkbox if present
    try:
        cb = driver.find_element(By.XPATH, "//input[@type='checkbox'][contains(@id,'SearchAll') or contains(@id,'All')]")
        if not cb.is_selected():
            driver.execute_script("arguments[0].click();", cb)
            print("  [B] Checked 'Search All Records'")
    except Exception:
        pass

    if debug:
        save_debug(driver, "B_02_form_filled")

    # Submit
    for xpath in [
        "//input[@value='Search']",
        "//button[contains(text(),'Search')]",
        "//a[normalize-space()='Search']",
        "//input[@type='submit']",
    ]:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(5)
            print(f"  [B] Search submitted")
            break
        except Exception:
            continue

    if debug:
        save_debug(driver, "B_03_results")

    # Paginate and extract
    page = 1
    seen_sigs = set()
    while page <= 20:
        html = driver.page_source
        sig  = hash(html[:3000])
        if sig in seen_sigs:
            break
        seen_sigs.add(sig)

        recs = parse_html_for_permits(html, f"wildcard_p{page}", driver.current_url)
        print(f"  [B] Page {page}: {len(recs)} permits")
        records.extend(recs)

        if not recs and page > 1:
            break

        # Next page
        try:
            nxt = driver.find_element(
                By.XPATH,
                "//a[contains(text(),'Next') and not(contains(@class,'disabled'))]"
            )
            driver.execute_script("arguments[0].click();", nxt)
            time.sleep(3)
            page += 1
        except Exception:
            break

    return records


# ---------------------------------------------------------------------------
# HTML parsing — extract permit records from any LauderBuild page
# ---------------------------------------------------------------------------

def parse_html_for_permits(html: str, source_id: str,
                            url: str) -> List[PermitRecord]:
    records = []
    seen    = set()

    if BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")

        # Parse result tables
        for table in soup.find_all("table"):
            rows    = table.find_all("tr")
            headers = []
            for tr in rows:
                cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th","td"])]
                if not cells or not any(cells):
                    continue
                if tr.find_all("th"):
                    headers = [c.lower().replace(" ","_") or f"col_{i}" for i, c in enumerate(cells)]
                    continue

                row_text = " | ".join(cells)
                m = PERMIT_RE.search(row_text)
                if not m:
                    continue
                pn = norm_permit(m.group(0))
                if pn in seen:
                    continue
                seen.add(pn)

                data = {}
                for i, c in enumerate(cells):
                    key = headers[i] if i < len(headers) else f"cell_{i}"
                    data[key] = c

                def pick(*keys):
                    for k in keys:
                        for dk, dv in data.items():
                            if k in dk.lower() and dv:
                                return dv
                    return ""

                addr_m = re.search(
                    r"\d+\s+(?:\w+\s+){1,4}(?:Ave|Blvd|St|Dr|Ln|Rd|Way|Ct|Pl|Ter|Cir)\b",
                    row_text, re.I
                )
                records.append(PermitRecord(
                    permit_number=m.group(0),
                    permit_type=pick("type","record_type","permit_type"),
                    project_description=pick("description","project","work","scope") or row_text[:300],
                    address_1=pick("address","location","site") or (addr_m.group(0) if addr_m else None),
                    owner_name=pick("owner","applicant","name"),
                    business_name=pick("contractor","business","licensee"),
                    issued_date=parse_date(pick("issued","date","applied","created")),
                    status=pick("status"),
                    raw_payload={"source_id": source_id, "url": url, "row": data},
                ))

        # Also scan all permit-number links
        for a in soup.find_all("a"):
            text = clean(a.get_text(" ", strip=True))
            href = a.get("href") or ""
            m    = PERMIT_RE.search(f"{text} {href}")
            if not m:
                continue
            pn = norm_permit(m.group(0))
            if pn in seen:
                continue
            seen.add(pn)
            parent = a.find_parent("tr")
            ctx    = clean(parent.get_text(" ", strip=True)) if parent else text
            addr_m = re.search(
                r"\d+\s+(?:\w+\s+){1,4}(?:Ave|Blvd|St|Dr|Ln|Rd|Way|Ct|Pl|Ter|Cir)\b",
                ctx, re.I
            )
            records.append(PermitRecord(
                permit_number=m.group(0),
                project_description=ctx[:300],
                address_1=addr_m.group(0) if addr_m else None,
                issued_date=parse_date(DATE_RE.search(ctx).group(0) if DATE_RE.search(ctx) else ""),
                raw_payload={"source_id": source_id, "url": url, "text": f"{text} {href}"[:200]},
            ))

    return records


# ---------------------------------------------------------------------------
# Database import
# ---------------------------------------------------------------------------

def get_county_id(cur) -> int:
    cur.execute("SELECT id FROM counties WHERE county_name = 'Broward'")
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO counties (county_name, state_code, created_at, updated_at) "
        "VALUES ('Broward', 'FL', NOW(), NOW()) RETURNING id"
    )
    return cur.fetchone()[0]


def insert_records(records: List[PermitRecord]) -> Dict[str, int]:
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

                source_record_id = f"{rec.source_name}::{rec.permit_number}"
                payload = json.dumps(asdict(rec), default=str)

                cur.execute("""
                    INSERT INTO raw_permits
                        (county_id, source_file, source_record_id, raw_payload, issued_date)
                    VALUES (%s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (county_id, source_record_id) DO UPDATE SET
                        raw_payload = EXCLUDED.raw_payload,
                        issued_date = EXCLUDED.issued_date
                    RETURNING id, (xmax = 0) AS is_insert
                """, (county_id, rec.source_name, source_record_id, payload, rec.issued_date))
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
                    rec.address_1, rec.city, rec.state, rec.zip,
                    rec.permit_number, rec.permit_type, rec.project_description,
                    rec.issued_date,
                    (rec.permit_type or "").lower()[:100] if rec.permit_type else None,
                    f"ftl::{rec.permit_number}",
                ))
                np = cur.fetchone()
                if np and np[1]:
                    stats["normalized"] += 1

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"  [db] ERROR: {e}")
        raise
    finally:
        conn.close()

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Fort Lauderdale LauderBuild permit scraper")
    parser.add_argument("--visible",      action="store_true", help="Show browser")
    parser.add_argument("--debug",        action="store_true", help="Save debug screenshots")
    parser.add_argument("--no-db",        action="store_true", help="Skip DB import")
    parser.add_argument("--reports-only", action="store_true", help="Only use report links (approach A)")
    parser.add_argument("--search-only",  action="store_true", help="Only use wildcard search (approach B)")
    args = parser.parse_args()

    print(f"Fort Lauderdale LauderBuild scraper | visible={args.visible}")

    driver = make_driver(visible=args.visible)
    all_records: List[PermitRecord] = []

    try:
        if not args.search_only:
            recs = scrape_via_reports(driver, debug=args.debug)
            print(f"[A] Reports total: {len(recs)}")
            all_records.extend(recs)

        if not args.reports_only:
            recs = scrape_via_wildcard_search(driver, debug=args.debug)
            print(f"[B] Wildcard total: {len(recs)}")
            all_records.extend(recs)

    except Exception as e:
        print(f"[error] {e}")
        save_debug(driver, "error")
    finally:
        driver.quit()

    # Deduplicate
    seen: Dict[str, PermitRecord] = {}
    for rec in all_records:
        key = norm_permit(rec.permit_number)
        if key and key not in seen:
            seen[key] = rec
    records = list(seen.values())

    print(f"\nTotal unique permits: {len(records)}")

    if records:
        out = RAW_EXPORT_ROOT / f"fort_lauderdale_{nowstamp()}.json"
        out.write_text(
            json.dumps([asdict(r) for r in records], default=str, indent=2),
            encoding="utf-8"
        )
        print(f"Saved: {out}")

    stats = {"raw": 0, "normalized": 0, "skipped": 0}
    if not args.no_db and records:
        stats = insert_records(records)

    print(f"\n--- Fort Lauderdale summary ---")
    print(f"  records extracted  : {len(records)}")
    print(f"  raw inserted       : {stats['raw']}")
    print(f"  normalized inserted: {stats['normalized']}")
    print(f"  skipped            : {stats['skipped']}")
    print(f"  debug dir          : {DEBUG_ROOT}")
    if len(records) == 0:
        print("\n  [tip] Run with --visible --debug to inspect the page")
        print("  [tip] Check debug screenshots in data/debug/broward_permits/fort_lauderdale/")


if __name__ == "__main__":
    main()