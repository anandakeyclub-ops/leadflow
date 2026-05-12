"""
scrape_polk_liens.py
=====================
Polk County official records lien scraper.
Portal: https://apps.polkcountyclerk.net/browserviewor/

AngularJS SPA — Document Type search.
Strategy:
  1. Click "Document Type" tab
  2. Type doc code into filter input (ng-model=searchDocTypesFilter)
  3. Click the matching checkbox row from ng-repeat docTypes table
  4. Repeat for each lien doc type
  5. Click "30 Days" button (or set custom date range via fromDate/toDate)
  6. Click Search button
  7. Parse results table, paginate with btNext

Doc types confirmed from final.html:
  LIEN    - LIEN
  JDG     - JUDGMENT
  FIN JDG - FINAL JUDGMENT
  CCJ     - CERTIFIED COPY OF COURT JUDGMENT
  TX LN   - TAX LIEN
  CE LN   - CODE ENFORCEMENT BOARD LIEN

Usage:
  python -m app.workers.scrape_polk_liens --days-back 30 --visible
  python -m app.workers.scrape_polk_liens --days-back 30
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

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
SOURCE_NAME = "polk_browserviewor"
POLK_URL    = "https://apps.polkcountyclerk.net/browserviewor/"

BASE_DIR    = Path(__file__).resolve().parents[2]
RAW_DIR     = BASE_DIR / "data" / "raw" / "polk" / "liens"
PDF_DIR     = RAW_DIR / "pdfs"
DEBUG_DIR   = RAW_DIR / "debug"
for d in [RAW_DIR, PDF_DIR, DEBUG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Targeting IRS federal liens and state tax liens only
LIEN_DOC_TYPES = [
    ("LIEN",  "LIEN"),    # IRS federal tax liens (filtered by creditor post-scrape)
    ("TX LN", "TAX LIEN"), # Florida / state tax liens
]

# IRS creditor patterns for post-scrape filtering of LIEN doc type
IRS_PATTERNS = (
    "INTERNAL REVENUE", "INTERNAL REV", "IRS",
    "UNITED STATES", "US TREASURY", "U S TREASURY",
    "DEPT OF TREASURY", "DEPARTMENT OF TREASURY",
)

BUSINESS_MARKERS = {
    "LLC", "INC", "CORP", "LTD", "LP", "LLP", "BANK", "MORTGAGE",
    "FEDERAL", "STATE", "COUNTY", "CITY", "FLORIDA", "INTERNAL",
    "REVENUE", "BOARD",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class LienRecord:
    instrument_number: str
    debtor_name:       Optional[str]  = None
    creditor_name:     Optional[str]  = None
    doc_type:          Optional[str]  = None
    filed_date:        Optional[date] = None
    book:              Optional[str]  = None
    page:              Optional[str]  = None
    pdf_path:          Optional[str]  = None
    raw_payload:       Dict           = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()

def parse_date(v: Any) -> Optional[date]:
    s = clean(v).split("T")[0].strip()
    s = re.sub(r"\s+\d{1,2}:\d{2}.*$", "", s).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

def title_name(v: str) -> str:
    return clean(v).title()

def is_business(name: str) -> bool:
    upper = name.upper()
    return any(m in upper for m in BUSINESS_MARKERS)

def nowstamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


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
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        driver.save_screenshot(str(DEBUG_DIR / f"{ts}_{label}.png"))
        (DEBUG_DIR / f"{ts}_{label}.html").write_text(
            driver.page_source, encoding="utf-8", errors="ignore"
        )
    except Exception:
        pass


def wait_angular(driver, timeout: int = 8) -> None:
    try:
        driver.execute_async_script("""
            var cb = arguments[arguments.length - 1];
            var el = document.querySelector('[ng-app]');
            if (window.angular && el) {
                try {
                    window.angular.element(el).injector()
                        .get('$browser').notifyWhenNoOutstandingRequests(cb);
                    return;
                } catch(e) {}
            }
            cb();
        """)
    except Exception:
        time.sleep(2)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def load_portal(driver) -> bool:
    driver.get(POLK_URL)
    time.sleep(7)
    wait_angular(driver)
    ok = "browserviewor" in driver.current_url.lower()
    print(f"  Portal loaded: {ok} ({driver.current_url})")
    return ok


def click_document_type_tab(driver) -> bool:
    for xpath in [
        "//a[normalize-space(text())='Document Type']",
        "//li//a[contains(text(),'Document Type')]",
        "//a[contains(@href,'doctype') or contains(@href,'DocType')]",
    ]:
        try:
            el = driver.find_element(By.XPATH, xpath)
            driver.execute_script("arguments[0].click();", el)
            time.sleep(2)
            wait_angular(driver)
            print("  Clicked Document Type tab")
            return True
        except Exception:
            continue
    # Try clicking by text match on all links
    try:
        links = driver.find_elements(By.TAG_NAME, "a")
        for link in links:
            if "document type" in link.text.lower():
                driver.execute_script("arguments[0].click();", link)
                time.sleep(2)
                print(f"  Clicked: {link.text!r}")
                return True
    except Exception:
        pass
    print("  Document Type tab not found")
    return False



def select_doc_type(driver, code: str, description: str) -> bool:
    """Filter doc types and click matching checkbox in the active Document Type panel."""
    
    # Find the filter input inside the active Document Type tab panel
    # There are multiple searchDocTypesFilter inputs - get the one in the active/visible panel
    filter_input = None
    try:
        # The Document Type panel's filter input - find visible one
        inputs = driver.find_elements(By.XPATH, 
            "//input[@ng-model='documentService.SearchCriteria.searchDocTypesFilter']")
        for inp in inputs:
            if inp.is_displayed():
                filter_input = inp
                break
    except Exception:
        pass

    if not filter_input:
        print(f"  Filter input not found for {code}")
        return False

    # Clear and type the code
    filter_input.clear()
    filter_input.send_keys(code)
    time.sleep(1.5)

    # Find the matching row — look for visible checkbox next to a td with exact code text
    # The row structure is: <tr ng-repeat="docType..."><td><input checked></td><td>CODE</td><td>DESC</td></tr>
    found = False
    try:
        # Get all visible rows matching the filter
        rows = driver.find_elements(By.XPATH,
            "//tr[@ng-repeat and .//input[@type='checkbox']]")
        for row in rows:
            try:
                cells = row.find_elements(By.TAG_NAME, "td")
                # Cell 1 = checkbox, Cell 2 = code, Cell 3 = description
                if len(cells) >= 2:
                    row_code = cells[1].text.strip() if len(cells) > 1 else ""
                    if row_code == code and row.is_displayed():
                        cb = cells[0].find_element(By.TAG_NAME, "input")
                        driver.execute_script("arguments[0].click();", cb)
                        time.sleep(0.5)
                        found = True
                        print(f"  ✓ {code}: {description}")
                        break
            except Exception:
                continue
    except Exception as e:
        print(f"  Row search error: {e}")

    # Clear filter for next selection
    try:
        filter_input.clear()
        time.sleep(0.3)
    except Exception:
        pass

    if not found:
        print(f"  ✗ Could not select: {code}")
    return found



def set_date_range(driver, start: date, end: date) -> None:
    start_str = f"{start.month}/{start.day}/{start.year}"
    end_str   = f"{end.month}/{end.day}/{end.year}"

    for ng_model, value in [
        ("documentService.SearchCriteria.fromDate", start_str),
        ("documentService.SearchCriteria.toDate",   end_str),
    ]:
        for xpath in [
            f"//input[@ng-model='{ng_model}']",
        ]:
            try:
                el = driver.find_element(By.XPATH, xpath)
                driver.execute_script("arguments[0].value = arguments[1];", el, value)
                # Trigger Angular
                driver.execute_script("""
                    try {
                        angular.element(arguments[0]).triggerHandler('input');
                        angular.element(arguments[0]).triggerHandler('change');
                    } catch(e) {}
                """, el)
                break
            except Exception:
                continue

    print(f"  Date range: {start_str} → {end_str}")


def click_days_button(driver, days: int) -> bool:
    """Set date range by directly updating Angular scope model."""
    from datetime import date, timedelta
    end_dt   = date.today()
    start_dt = end_dt - timedelta(days=days)
    start_str = f"{start_dt.month:02d}/{start_dt.day:02d}/{start_dt.year}"
    end_str   = f"{end_dt.month:02d}/{end_dt.day:02d}/{end_dt.year}"

    # Find visible date inputs
    inputs = driver.find_elements(By.XPATH, "//input[@placeholder='MM/DD/YYYY']")
    visible = [i for i in inputs if i.is_displayed()]
    print(f"  Visible date inputs: {len(visible)}")

    if len(visible) >= 2:
        for inp, val, label in zip(visible[:2], [start_str, end_str], ["From", "To"]):
            try:
                # Get the Angular ng-model for this input
                ng_model = driver.execute_script(
                    "return arguments[0].getAttribute('ng-model');", inp)

                # Set value via native setter (works with Angular)
                driver.execute_script("""
                    var el = arguments[0];
                    var val = arguments[1];
                    var setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    setter.call(el, val);
                    el.dispatchEvent(new Event('input', {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                    el.dispatchEvent(new Event('blur', {bubbles:true}));
                    try {
                        var scope = angular.element(el).scope();
                        if (scope && arguments[2]) {
                            // Set via scope path
                            var parts = arguments[2].split('.');
                            var obj = scope;
                            for (var i = 0; i < parts.length - 1; i++) obj = obj[parts[i]];
                            obj[parts[parts.length-1]] = val;
                            scope.$apply();
                        }
                    } catch(e) {}
                """, inp, val, ng_model or "")
                time.sleep(0.5)
                print(f"  Set {label}: {val} (ng-model={ng_model})")
            except Exception as e:
                print(f"  {label} date error: {e}")
    else:
        print(f"  Not enough visible date inputs ({len(visible)})")
        return False

    time.sleep(1)
    return True


def click_search(driver) -> bool:
    """Click Search and wait for results to load."""
    # First verify Angular has the date values
    date_check = driver.execute_script("""
        try {
            var inputs = document.querySelectorAll('input[placeholder="MM/DD/YYYY"]');
            var visible = Array.from(inputs).filter(i => i.offsetParent !== null);
            return visible.map(i => i.value);
        } catch(e) { return []; }
    """)
    print(f"  Date values before search: {date_check}")

    try:
        btns = driver.find_elements(By.XPATH, "//button[normalize-space(text())='Search']")
        visible_btns = [b for b in btns if b.is_displayed() and b.is_enabled()]
        if visible_btns:
            driver.execute_script("arguments[0].click();", visible_btns[0])
            time.sleep(8)
            wait_angular(driver)
            print("  Clicked Search")
            return True
    except Exception as e:
        print(f"  Search error: {e}")
    print("  Search button not found")
    return False


# ---------------------------------------------------------------------------
# Results parsing
# ---------------------------------------------------------------------------

def get_result_count(driver) -> int:
    for pattern in [
        r"(\d[\d,]+)\s+total\s+records",
        r"of\s+([\d,]+)\s+\(",
        r"through\s+\d+\s+of\s+([\d,]+)",
    ]:
        m = re.search(pattern, driver.page_source, re.IGNORECASE)
        if m:
            return int(m.group(1).replace(",", ""))
    return -1


def parse_results_table(driver) -> List[LienRecord]:
    """
    Parse Polk AG Grid results.
    Grid uses div.ag-row > div.ag-cell[colid=...] structure.
    colids: doc_id, party_code, party_name, rec_date, doc_type,
            book, page, legal_1, file_num, doc_status, correction_flag
    AG Grid virtual-scrolls — only visible rows in DOM at once.
    We scroll down to load all rows.
    """
    skip_types = {"S LIEN", "S JDG", "REL LIEN", "LIEN DIS", "JDG DIS",
                  "S TX LN", "REL TX LN", "S AM LN", "S CE LN", "S FF LN"}

    from bs4 import BeautifulSoup

    all_records: List[LienRecord] = []
    seen_instruments: set = set()

    # AG Grid virtual scroll — container is .ag-body-viewport
    # Total height = 25025px for 1001 rows (25px each)
    # Scroll in steps of ~20 rows (500px) and parse each batch

    grid_info = driver.execute_script("""
        var vp = document.querySelector('.ag-body-viewport');
        var container = document.querySelector('.ag-body-container');
        return {
            vpHeight:        vp ? vp.clientHeight : 0,
            containerHeight: container ? container.style.height : '0px',
            scrollHeight:    vp ? vp.scrollHeight : 0
        };
    """)
    print(f"  AG Grid: viewport={grid_info}")

    scroll_step  = 400  # px (~16 rows per step)
    scroll_pos   = 0
    scroll_num   = 0

    # Get actual total height from AG Grid container
    total_height = driver.execute_script("""
        var c = document.querySelector('.ag-body-container');
        if (c) {
            var h = c.style.height || c.offsetHeight + 'px';
            return parseInt(h);
        }
        var vp = document.querySelector('.ag-body-viewport');
        return vp ? vp.scrollHeight : 25025;
    """) or 25025
    print(f"  AG Grid total height: {total_height}px (~{total_height//25} rows)")

    while scroll_pos <= total_height:
        # Parse visible rows from live DOM via JS
        rows_data = driver.execute_script("""
            var results = [];
            var rows = document.querySelectorAll('.ag-row');
            for (var r = 0; r < rows.length; r++) {
                var cells = rows[r].querySelectorAll('.ag-cell[colid]');
                var data = {};
                for (var c = 0; c < cells.length; c++) {
                    data[cells[c].getAttribute('colid')] = cells[c].innerText.trim();
                }
                if (data.party_name) results.push(data);
            }
            return results;
        """)

        for data in (rows_data or []):
            name     = data.get("party_name", "").strip()
            rec_date = parse_date(data.get("rec_date", ""))
            doc_type = data.get("doc_type", "").strip()
            book     = data.get("book", "").strip()
            page     = data.get("page", "").strip()
            file_num = data.get("file_num", "").strip()

            if not name or not doc_type:
                continue
            if doc_type in skip_types:
                continue
            # For LIEN doc type, we'll filter IRS vs other in post-processing
            # TX LN records are all state tax liens — keep all

            instrument = file_num or f"polk::{book}:{page}:{rec_date}"
            if instrument in seen_instruments:
                continue
            seen_instruments.add(instrument)

            # Determine lien type from doc_type code
            if doc_type == "TX LN":
                lien_type_tag = "state_tax_lien"
            else:
                lien_type_tag = "lien"  # will be re-classified after creditor lookup

            all_records.append(LienRecord(
                instrument_number = clean(instrument),
                debtor_name       = title_name(name),
                creditor_name     = None,
                doc_type          = doc_type,
                filed_date        = rec_date,
                book              = book or None,
                page              = page or None,
                raw_payload       = {**data, "_lien_type": lien_type_tag},
            ))

        scroll_pos += scroll_step
        scroll_num += 1
        driver.execute_script("""
            var vp = document.querySelector('.ag-body-viewport');
            if (vp) vp.scrollTop = arguments[0];
        """, scroll_pos)
        time.sleep(0.8)  # Wait for AG Grid to render new rows

        if scroll_num % 10 == 0:
            print(f"  ... scroll {scroll_num} pos={scroll_pos}px records={len(all_records)}")

    print(f"  AG Grid parsed: {len(all_records)} unique records in {scroll_num} scroll steps")
    return all_records


def click_next_page(driver) -> bool:
    """Polk uses a scrollable results panel — all rows load at once, no pagination."""
    # The portal loads ALL results into a scrollable tbody, not paginated
    # So there is no next page — return False immediately
    return False


# ---------------------------------------------------------------------------
# Main scrape
# ---------------------------------------------------------------------------

def scrape_polk_liens(start: date, end: date, visible: bool = False) -> List[LienRecord]:
    print(f"\n[Polk Liens] Scraping {start} → {end}")

    driver = make_driver(visible=visible)
    all_records: List[LienRecord] = []
    seen: set = set()

    try:
        if not load_portal(driver):
            print("  Could not load portal")
            save_debug(driver, "01_no_portal")
            return all_records

        if not click_document_type_tab(driver):
            save_debug(driver, "02_no_tab")
            return all_records

        save_debug(driver, "02_tab_clicked")
        time.sleep(2)

        # Select each lien doc type
        selected = 0
        for code, desc in LIEN_DOC_TYPES:
            if select_doc_type(driver, code, desc):
                selected += 1

        print(f"  Selected {selected}/{len(LIEN_DOC_TYPES)} doc types")
        save_debug(driver, "03_types_selected")

        # Set date range directly via input fields
        days_back = (end - start).days
        if not click_days_button(driver, days_back):
            print("  Date setting failed — trying set_date_range fallback")
            set_date_range(driver, start, end)

        save_debug(driver, "04_dates")
        time.sleep(1)

        if not click_search(driver):
            save_debug(driver, "05_no_search")
            return all_records

        # Click the Results tab to load the results view
        time.sleep(3)
        try:
            results_tab = driver.find_element(By.XPATH,
                "//a[normalize-space(text())='Results'] | //li//a[contains(text(),'Results')]")
            driver.execute_script("arguments[0].click();", results_tab)
            print("  Clicked Results tab")
            time.sleep(6)  # Wait longer for Angular to render results
            wait_angular(driver)
            time.sleep(2)  # Extra buffer
        except Exception as e:
            print(f"  Results tab click failed: {e}")

        save_debug(driver, "05_results")
        count = get_result_count(driver)
        print(f"  Total results: {count}")

        # Debug: dump table info to understand DOM structure
        table_info = driver.execute_script("""
            var info = [];
            var tables = document.querySelectorAll('table');
            for (var t = 0; t < tables.length; t++) {
                var rows = tables[t].querySelectorAll('tr');
                var headers = Array.from(tables[t].querySelectorAll('th')).map(h=>h.innerText.trim());
                var firstRow = rows[1] ? Array.from(rows[1].querySelectorAll('td')).map(c=>c.innerText.trim().substring(0,20)) : [];
                info.push({
                    index: t,
                    rows: rows.length,
                    headers: headers,
                    firstRow: firstRow,
                    id: tables[t].id || '',
                    className: tables[t].className.substring(0,50)
                });
            }
            // Also check for divs that might contain results
            var resultDiv = document.querySelector('.result-list, .results, [ng-repeat*="result"], [ng-repeat*="Record"]');
            info.push({divCheck: resultDiv ? resultDiv.innerHTML.substring(0,200) : 'none'});
            return info;
        """)
        for ti in table_info:
            print(f"  Table info: {ti}")

        # Paginate - Polk shows ~35 rows per page, navigates with btNext
        page_num = 0
        consecutive_empty = 0
        while True:
            page_num += 1
            recs = parse_results_table(driver)
            new_recs = [r for r in recs if r.instrument_number not in seen]
            for r in new_recs:
                seen.add(r.instrument_number)
                all_records.append(r)
            print(f"  Page {page_num}: {len(recs)} rows, {len(new_recs)} new, {len(all_records)} total")

            if len(recs) == 0:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    break
            else:
                consecutive_empty = 0

            if not click_next_page(driver):
                print("  No more pages")
                break
            if page_num > 500:
                break

    except Exception as e:
        print(f"  [Polk] Error: {e}")
        import traceback
        traceback.print_exc()
        save_debug(driver, "error")
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


def import_records(records: List[LienRecord]) -> Dict[str, int]:
    if not records or not get_connection:
        return {"raw": 0, "normalized": 0, "skipped": 0}

    conn = get_connection()
    conn.autocommit = False
    stats = {"raw": 0, "normalized": 0, "skipped": 0}

    try:
        with conn.cursor() as cur:
            county_id = get_county_id(cur)
            for rec in records:
                if not rec.instrument_number or not rec.debtor_name:
                    stats["skipped"] += 1
                    continue

                source_record_id = f"{SOURCE_NAME}::{rec.instrument_number}"
                payload = json.dumps({
                    "instrument": rec.instrument_number,
                    "debtor":     rec.debtor_name,
                    "creditor":   rec.creditor_name,
                    "doc_type":   rec.doc_type,
                    "filed_date": str(rec.filed_date) if rec.filed_date else None,
                }, default=str)

                raw_id = None
                try:
                    cur.execute("""
                        INSERT INTO raw_liens
                            (county_id, source_file, source_record_id, raw_payload, filed_date)
                        VALUES (%s,%s,%s,%s::jsonb,%s)
                        ON CONFLICT (county_id, source_record_id) DO UPDATE SET
                            raw_payload = EXCLUDED.raw_payload
                        RETURNING id, (xmax = 0) AS is_insert
                    """, (county_id, SOURCE_NAME, source_record_id, payload, rec.filed_date))
                    rl = cur.fetchone()
                    raw_id = rl[0]
                    if rl[1]:
                        stats["raw"] += 1
                except Exception as e:
                    conn.rollback()  # Reset transaction after failed insert
                    # raw_liens table missing — continue without raw_id

                n_hash = f"polk::{rec.instrument_number}::{(rec.debtor_name or '')[:40]}"
                cur.execute("""
                    INSERT INTO normalized_liens (
                        county_id, raw_lien_id, debtor_name, business_name,
                        address_1, filing_type, lien_type,
                        filed_date, normalized_hash
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (normalized_hash) DO UPDATE SET
                        debtor_name = EXCLUDED.debtor_name,
                        filing_type = EXCLUDED.filing_type,
                        filed_date  = COALESCE(EXCLUDED.filed_date, normalized_liens.filed_date)
                    RETURNING id, (xmax = 0) AS is_insert
                """, (
                    county_id, raw_id,
                    rec.debtor_name,
                    rec.creditor_name if rec.creditor_name and is_business(rec.creditor_name) else None,
                    None,
                    rec.doc_type,
                    rec.raw_payload.get("_lien_type", "federal_tax_lien") if rec.doc_type == "TX LN" else "federal_tax_lien",
                    rec.filed_date,
                    n_hash,
                ))
                nl = cur.fetchone()
                if nl and nl[1]:
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
# PDF download
# ---------------------------------------------------------------------------
# Polk document viewer — instrument number maps to viewer URL
POLK_DOC_VIEWER = "https://apps.polkcountyclerk.net/browserviewor/"


def download_pdf_polk(driver, rec: LienRecord) -> Optional[str]:
    """
    Download the lien document PDF from the Polk County portal.
    Uses the instrument/file number to navigate to the document viewer,
    then prints to PDF via Chrome CDP.
    """
    instrument = re.sub(r"[^\w\-]", "_", rec.instrument_number)[:60]
    pdf_path   = PDF_DIR / f"polk_{instrument}.pdf"

    if pdf_path.exists() and pdf_path.stat().st_size > 500:
        return str(pdf_path)

    # Polk viewer URL patterns using instrument/file number
    urls = [
        f"{POLK_DOC_VIEWER}#/search/results/detail/{rec.instrument_number}",
        f"https://apps.polkcountyclerk.net/browserviewor/api/Document/{rec.instrument_number}",
        f"https://apps.polkcountyclerk.net/OfficialRecords/Document/{rec.instrument_number}",
    ]

    for url in urls:
        try:
            result = driver.execute_async_script("""
                var done = arguments[arguments.length - 1];
                fetch(arguments[0], {method:"GET", credentials:"include"})
                .then(function(r) {
                    if (!r.ok) { done({status:r.status, data:null}); return; }
                    var ct = r.headers.get("content-type") || "";
                    return r.arrayBuffer().then(function(buf) {
                        var bytes = new Uint8Array(buf);
                        var bin = "";
                        for (var i = 0; i < bytes.byteLength; i++) bin += String.fromCharCode(bytes[i]);
                        done({status:r.status, data:btoa(bin), ct:ct});
                    });
                })
                .catch(function(e) { done({status:0, data:null, error:e.toString()}); });
            """, url)

            if not result or not result.get("data"):
                continue

            raw_bytes = base64.b64decode(result["data"])
            ct = result.get("ct", "")

            if raw_bytes[:4] == b"%PDF" or "pdf" in ct.lower():
                pdf_path.write_bytes(raw_bytes)
                return str(pdf_path)

            if "html" in ct.lower() and len(raw_bytes) > 500:
                driver.get(url)
                time.sleep(4)
                pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                    "printBackground": True,
                    "paperWidth": 8.5,
                    "paperHeight": 11,
                })
                if pdf_data and pdf_data.get("data"):
                    pdf_path.write_bytes(base64.b64decode(pdf_data["data"]))
                    return str(pdf_path)

        except Exception:
            continue

    return None


def download_pdfs_polk(
    driver, records: List[LienRecord], limit: Optional[int] = None
) -> dict:
    stats = {"attempted": 0, "saved": 0, "failed": 0}
    targets = records[:limit] if limit else records
    for i, rec in enumerate(targets, 1):
        print(f"  [pdf {i}/{len(targets)}] {rec.instrument_number} | {rec.debtor_name}")
        stats["attempted"] += 1
        path = download_pdf_polk(driver, rec)
        if path:
            rec.pdf_path = path
            stats["saved"] += 1
            print(f"    saved: {Path(path).name}")
        else:
            stats["failed"] += 1
            print(f"    failed")
        time.sleep(1.5)
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Polk County IRS federal + state tax lien scraper")
    parser.add_argument("--days-back",  type=int, default=30)
    parser.add_argument("--visible",    action="store_true")
    parser.add_argument("--no-db",      action="store_true")
    parser.add_argument("--no-pdf",     action="store_true", help="Skip PDF download")
    parser.add_argument("--pdf-limit",  type=int, default=None, help="Max PDFs to download")
    args = parser.parse_args()

    end   = date.today()
    start = end - timedelta(days=args.days_back)

    records = scrape_polk_liens(start, end, visible=args.visible)

    if records:
        snap = RAW_DIR / f"polk_ftl_{nowstamp()}.json"
        snap.write_text(
            json.dumps([{
                "instrument": r.instrument_number,
                "debtor":     r.debtor_name,
                "doc_type":   r.doc_type,
                "filed_date": str(r.filed_date),
                "pdf_path":   r.pdf_path,
            } for r in records], indent=2),
            encoding="utf-8"
        )
        print(f"Saved: {snap}")
        print("\nSample:")
        for r in records[:5]:
            print(f"  {r.instrument_number} | {r.debtor_name} | {r.doc_type} | {r.filed_date}")

    # Download PDFs using a fresh driver
    pdf_stats = {"attempted": 0, "saved": 0, "failed": 0}
    if not args.no_pdf and records:
        print(f"\nDownloading PDFs to {PDF_DIR} …")
        pdf_driver = make_driver(visible=args.visible)
        try:
            pdf_driver.get(POLK_DOC_VIEWER)
            time.sleep(3)
            pdf_stats = download_pdfs_polk(pdf_driver, records, limit=args.pdf_limit)
        finally:
            pdf_driver.quit()
        print(f"  PDFs: {pdf_stats['saved']}/{pdf_stats['attempted']} saved, {pdf_stats['failed']} failed")

    stats = {"raw": 0, "normalized": 0, "skipped": 0}
    if not args.no_db and records:
        stats = import_records(records)

    print(f"\n--- Polk IRS/state tax lien summary ---")
    print(f"  Records scraped    : {len(records)}")
    print(f"  raw inserted       : {stats['raw']}")
    print(f"  normalized inserted: {stats['normalized']}")
    print(f"  skipped            : {stats['skipped']}")
    if not args.no_pdf:
        print(f"  pdf saved          : {pdf_stats['saved']}/{pdf_stats['attempted']}")


if __name__ == "__main__":
    main()