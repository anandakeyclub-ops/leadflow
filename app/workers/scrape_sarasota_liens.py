"""
scrape_sarasota_liens.py
========================
Sarasota County IRS federal + state tax lien scraper for LeadFlow.

Portal: https://secure.sarasotaclerk.com/OfficialRecords.aspx
Platform: ClerkNet (custom Sarasota portal)

No reCAPTCHA. No login required for public search.
Search by document type with date range.

Usage:
  python -m app.workers.scrape_sarasota_liens --days-back 180 --no-db
  python -m app.workers.scrape_sarasota_liens --days-back 180
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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
COUNTY_NAME = "Sarasota"
SOURCE_NAME = "sarasota_liens"

HOME_URL   = "https://secure.sarasotaclerk.com/OfficialRecords.aspx"

# Document type codes for Sarasota ClerkNet
# These will be discovered on first run — update after inspecting portal
DOC_TYPES = [
    ("FTL", "FEDERAL TAX LIEN",  "federal_tax_lien"),
    ("STL", "STATE TAX LIEN",    "state_tax_lien"),
]

CHUNK_DAYS = 30

IRS_NAMES = {
    "INTERNAL REV", "INTERNAL REVENUE", "IRS", "UNITED STATES",
    "US TREASURY", "U S TREASURY", "DEPT OF TREASURY",
    "FLORIDA DEPT", "FL DEPT", "DEPARTMENT OF REVENUE",
    "DEPT OF REVENUE", "STATE OF FLORIDA",
}

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR  = BASE_DIR / "data" / "raw" / "sarasota" / "liens"
PDF_DIR  = RAW_DIR / "pdfs"
DBG_DIR  = RAW_DIR / "debug"
for d in [RAW_DIR, PDF_DIR, DBG_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class LienRecord:
    instrument_number: str
    debtor_name:       Optional[str]  = None
    creditor_name:     Optional[str]  = None
    doc_type:          Optional[str]  = None
    lien_type:         str            = "federal_tax_lien"
    filed_date:        Optional[date] = None
    pdf_path:          Optional[str]  = None
    raw_payload:       Dict           = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()

def parse_dt(v: Any) -> Optional[date]:
    s = clean(v).split("T")[0].split(" ")[0]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    return None

def nowstamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def save_debug(driver, label: str):
    try:
        ts = nowstamp()
        driver.save_screenshot(str(DBG_DIR / f"{ts}_{label}.png"))
        (DBG_DIR / f"{ts}_{label}.html").write_text(
            driver.page_source, encoding="utf-8", errors="ignore")
    except Exception:
        pass


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
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    )
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_experimental_option("prefs", {
        "download.default_directory":         str(PDF_DIR),
        "download.prompt_for_download":       False,
        "plugins.always_open_pdf_externally": True,
    })
    if HAS_WDM:
        drv = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), options=opts)
    else:
        drv = webdriver.Chrome(options=opts)
    drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            window.chrome = {runtime: {}};
        """
    })
    return drv


# ---------------------------------------------------------------------------
# Session setup
# ---------------------------------------------------------------------------
def setup_session(driver) -> bool:
    driver.get(HOME_URL)
    time.sleep(4)
    print(f"  Loaded: {driver.current_url}")

    # Dump all form fields on first load to discover doc type selectors
    fields = driver.execute_script("""
        var r = [];
        document.querySelectorAll('select,input,button').forEach(function(el) {
            if (el.getBoundingClientRect().width > 0) {
                var opts = [];
                if (el.tagName === 'SELECT') {
                    Array.from(el.options).forEach(function(o) {
                        opts.push({val: o.value, text: o.text.trim()});
                    });
                }
                r.push({tag: el.tagName, id: el.id, name: el.name,
                        type: el.type, value: el.value,
                        cls: el.className.substring(0,40), options: opts});
            }
        });
        return r;
    """)
    print(f"  Page fields ({len(fields)}):")
    for f in fields[:30]:
        opts_str = ""
        if f.get('options'):
            lien_opts = [o for o in f['options']
                         if any(k in o['text'].upper()
                                for k in ['LIEN', 'TAX', 'FTL', 'STL'])]
            if lien_opts:
                opts_str = f" LIEN OPTIONS: {lien_opts}"
        print(f"    <{f['tag']}> id={f['id']!r} name={f['name']!r} "
              f"type={f['type']!r} val={f['value']!r}{opts_str}")
    return True


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def search_chunk(driver, doc_code: str, doc_label: str,
                 lien_type: str, start: date, end: date) -> List[dict]:
    """
    Sarasota ClerkNet search.
    From screenshots:
    - Date Filed From / Date Filed To: Telerik RadDatePicker inputs
    - Document Type: scrollable checkbox listbox  
    - Search button: input[value='Search']
    """
    start_str = f"{start.month}/{start.day}/{start.year}"
    end_str   = f"{end.month}/{end.day}/{end.year}"
    print(f"  Searching {doc_label}: {start_str} → {end_str}")

    driver.get(HOME_URL)
    time.sleep(4)

    # Step 0: Uncheck ALL doc type checkboxes first
    driver.execute_script("""
        document.querySelectorAll('input[type="checkbox"]').forEach(function(cb) {
            if (cb.id.indexOf('cbDocType') !== -1 && cb.checked) {
                cb.checked = false;
                cb.dispatchEvent(new Event('change', {bubbles: true}));
            }
        });
    """)
    time.sleep(0.5)

    # Step 1: Find and check the correct checkbox
    # Use exact match to avoid 'STATE TAX LIEN' matching 'ESTATE TAX LIEN'
    checked = driver.execute_script("""
        var label = arguments[0].toUpperCase();
        var checkboxes = document.querySelectorAll('input[type="checkbox"]');
        var found = [];
        // First try exact match
        for (var i = 0; i < checkboxes.length; i++) {
            var cb = checkboxes[i];
            var labelEl = document.querySelector('label[for="' + cb.id + '"]');
            var text = (labelEl ? labelEl.innerText.trim() : (cb.value || '')).toUpperCase();
            if (text === label) {
                cb.checked = true;
                cb.dispatchEvent(new Event('change', {bubbles: true}));
                found.push({id: cb.id, value: cb.value, text: text});
            }
        }
        // If no exact match, try contains but exclude RELEASE/ESTATE prefixes
        if (found.length === 0) {
            for (var i = 0; i < checkboxes.length; i++) {
                var cb = checkboxes[i];
                var labelEl = document.querySelector('label[for="' + cb.id + '"]');
                var text = (labelEl ? labelEl.innerText.trim() : (cb.value || '')).toUpperCase();
                if (text.indexOf(label) !== -1
                    && text.indexOf('RELEASE') < 0
                    && text.indexOf('ESTATE') < 0
                    && !text.startsWith('RE')) {
                    cb.checked = true;
                    cb.dispatchEvent(new Event('change', {bubbles: true}));
                    found.push({id: cb.id, value: cb.value, text: text});
                }
            }
        }
        return found;
    """, doc_label)

    if checked:
        print(f"  Checked: {checked}")
    else:
        # Try finding by scrolling the listbox
        print(f"  Checkbox for '{doc_label}' not found by JS — trying XPath")
        for xpath in [
            f"//label[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{doc_label}')]//preceding-sibling::input",
            f"//label[contains(text(),'{doc_label}')]",
            f"//span[contains(text(),'{doc_label}')]//ancestor::*//input[@type='checkbox']",
        ]:
            try:
                el = driver.find_element(By.XPATH, xpath)
                driver.execute_script("arguments[0].checked=true;", el)
                driver.execute_script("arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", el)
                print(f"  Checked via XPath")
                break
            except Exception:
                continue

    # Step 2: Set date fields
    # Telerik RadDatePicker requires setting the _dateInput subfield
    # Confirmed IDs: ctl00_cphBody_rdAppFrom, ctl00_cphBody_rdAppTo
    date_set = 0
    for fid, value in [
        ("ctl00_cphBody_rdAppFrom", start_str),
        ("ctl00_cphBody_rdAppTo",   end_str),
    ]:
        date_input_id = fid + "_dateInput"
        set_ok = driver.execute_script("""
            var fid = arguments[0];
            var val = arguments[1];
            var dateInputId = fid + '_dateInput';

            // Set both the main input and the dateInput sub-field
            var mainEl = document.getElementById(fid);
            var dateEl = document.getElementById(dateInputId);

            function setVal(el) {
                if (!el) return false;
                el.value = val;
                ['input','change','blur','keyup'].forEach(function(evt) {
                    el.dispatchEvent(new Event(evt, {bubbles: true}));
                });
                // Telerik-specific: trigger the clientState update
                var stateEl = document.getElementById(fid + '_ClientState');
                if (stateEl) {
                    // Telerik stores date as JSON in ClientState
                    var parts = val.split('/');
                    if (parts.length === 3) {
                        var d = new Date(parts[2], parts[0]-1, parts[1]);
                        stateEl.value = JSON.stringify({
                            "enabled":true, "emptyMessage":"",
                            "validationText": val,
                            "valueAsString": val,
                            "minDateStr":"1900-01-01-0-0-0-0",
                            "maxDateStr":"2100-12-31-0-0-0-0",
                            "lastSetTextBoxValue": val
                        });
                    }
                }
                return true;
            }
            return setVal(dateEl) || setVal(mainEl);
        """, fid, value)

        actual = driver.execute_script(
            f"var el=document.getElementById('{date_input_id}') || "
            f"document.getElementById('{fid}'); return el ? el.value : '';")
        print(f"  Date {'From' if 'From' in fid else 'To'}: {actual} (id={date_input_id!r})")
        if actual:
            date_set += 1

    print(f"  Dates set: {date_set}/2  {start_str} → {end_str}")

    # Step 3: Click Search button
    submitted = False
    for by, sel in [
        (By.XPATH,       "//input[@value='Search']"),
        (By.XPATH,       "//input[@value='search']"),
        (By.CSS_SELECTOR,"input[value='Search']"),
        (By.CSS_SELECTOR,"input[type='submit']"),
        (By.CSS_SELECTOR,"button[type='submit']"),
        (By.ID,          "btnSearch"),
        (By.XPATH,       "//button[contains(text(),'Search')]"),
    ]:
        try:
            btn = driver.find_element(by, sel)
            if btn.is_displayed() and btn.is_enabled():
                driver.execute_script("arguments[0].click();", btn)
                submitted = True
                print(f"  Clicked Search via {sel}")
                break
        except Exception:
            continue

    if not submitted:
        print("  Search button not found — all visible inputs:")
        for el in driver.find_elements(By.CSS_SELECTOR, "input,button"):
            try:
                if el.is_displayed():
                    print(f"    {el.tag_name} value={el.get_attribute('value')!r} "
                          f"type={el.get_attribute('type')!r} id={el.get_attribute('id')!r}")
            except Exception:
                pass
        save_debug(driver, f"no_submit_{doc_code}_{start.strftime('%Y%m%d')}")
        return []

    time.sleep(6)
    save_debug(driver, f"results_{doc_code}_{start.strftime('%Y%m%d')}")
    return _parse_results(driver, lien_type)


def _parse_results(driver, lien_type: str) -> List[dict]:
    """
    Parse Telerik RadGrid results from Sarasota ClerkNet.
    RadGrid uses specific row classes: rgRow, rgAltRow, rgSelectedRow
    """
    rows = driver.execute_script("""
        var results = [];
        var lien_type = arguments[0];

        // Find the actual data grid — look for table with rgRow/rgAltRow
        var grid = null;
        document.querySelectorAll('table').forEach(function(t) {
            if (t.querySelector('tr.rgRow, tr.rgAltRow')) grid = t;
        });
        if (!grid) {
            // Fallback: find largest table with >3 cols
            var best = null, bestCols = 0;
            document.querySelectorAll('table').forEach(function(t) {
                var firstRow = t.querySelector('tbody tr');
                if (firstRow) {
                    var cols = firstRow.querySelectorAll('td').length;
                    if (cols > bestCols) { bestCols = cols; best = t; }
                }
            });
            grid = best;
        }
        if (!grid) return results;

        // Get headers from rgHeader cells (not all th/td)
        var headerCells = grid.querySelectorAll('th.rgHeader');
        if (!headerCells.length) headerCells = grid.querySelectorAll('thead th, thead td');
        var headers = Array.from(headerCells)
            .map(function(h) { return h.innerText.trim(); })
            .filter(function(h) { return h && h.length > 0 && h.length < 60; });

        // Data rows
        var trs = grid.querySelectorAll('tr.rgRow, tr.rgAltRow, tr.rgSelectedRow');
        if (!trs.length) trs = grid.querySelectorAll('tbody tr');

        for (var i = 0; i < trs.length; i++) {
            var cells = trs[i].querySelectorAll('td');
            if (cells.length < 2) continue;
            var row = {'_lien_type': lien_type};
            for (var j = 0; j < cells.length; j++) {
                var text = (cells[j].innerText || cells[j].textContent || '').trim();
                if (!text) {
                    var a = cells[j].querySelector('a');
                    if (a) text = (a.innerText || a.textContent || '').trim();
                }
                row[headers[j] || 'col_' + j] = text;
                var link = cells[j].querySelector('a');
                if (link && link.href) row['_link_' + j] = link.href;
            }
            var vals = Object.values(row).filter(function(v){return typeof v==='string';}).join('');
            if (vals.replace(lien_type,'').length > 3) results.push(row);
        }
        return results;
    """, lien_type)

    if rows:
        print(f"    {len(rows)} rows")
        if not getattr(_parse_results, "_printed", False):
            _parse_results._printed = True
            print(f"    Columns: {[k for k in rows[0] if not k.startswith('_')]}")
            print(f"    First row data: {dict((k,v) for k,v in rows[0].items() if not k.startswith('_'))}")
    else:
        body = driver.find_element(By.TAG_NAME, "body").text[:200]
        # Check if there's a "no records" message
        if any(k in body.lower() for k in ['no record', 'no result', '0 record']):
            print(f"    No records found (genuine empty result)")
        else:
            print(f"    No rows parsed — page text: {body[:100]!r}")

    return rows or []


def parse_row(row: dict) -> Optional[LienRecord]:
    instrument = clean(
        row.get("Instrument") or row.get("Instrument #") or
        row.get("Doc #") or row.get("col_0") or ""
    )
    if not instrument:
        for k, v in row.items():
            if "_link_" in k and v:
                m = re.search(r"[/=](\d{8,})", v)
                if m:
                    instrument = m.group(1)
                    break
    if not instrument:
        return None

    # Sarasota confirmed: Name column = "INTERNAL REVENUE SERVICE\nSIENS JOYCE A"
    # Line 1 = creditor/IRS, Line 2 = debtor — split on newline
    name_field = row.get("Name", "")
    name_parts = [p.strip() for p in name_field.split("\n") if p.strip()]
    if len(name_parts) >= 2:
        grantor = clean(name_parts[0])
        grantee = clean(name_parts[1])
    else:
        grantor = clean(name_parts[0] if name_parts else "")
        grantee = ""

    if grantee and any(n in grantee.upper() for n in IRS_NAMES):
        debtor_raw, creditor_raw = grantor, grantee
    elif grantor and any(n in grantor.upper() for n in IRS_NAMES):
        debtor_raw, creditor_raw = grantee, grantor
    elif grantor and not grantee:
        # Sarasota: only Name column — it IS the debtor
        debtor_raw, creditor_raw = grantor, None
    else:
        debtor_raw, creditor_raw = grantor, grantee

    if not debtor_raw:
        return None

    lien_type = row.get("_lien_type", "federal_tax_lien")
    filed_date = parse_dt(
        row.get("Date") or row.get("Record Date") or
        row.get("Filed Date") or row.get("col_3") or ""
    )

    return LienRecord(
        instrument_number = instrument[:200],
        debtor_name       = debtor_raw.title()[:250],
        creditor_name     = creditor_raw.title()[:250] if creditor_raw else None,
        lien_type         = lien_type,
        filed_date        = filed_date,
        raw_payload       = row,
    )


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------
def download_pdf(driver, rec: LienRecord) -> Optional[str]:
    safe     = re.sub(r"[^\w\-]", "_", rec.instrument_number)[:60]
    pdf_path = PDF_DIR / f"sar_{safe}.pdf"
    if pdf_path.exists() and pdf_path.stat().st_size > 1000:
        return str(pdf_path)

    for url in [
        f"https://secure.sarasotaclerk.com/OfficialRecords.aspx?InstrumentNumber={rec.instrument_number}",
        f"https://secure.sarasotaclerk.com/Document/{rec.instrument_number}",
    ]:
        try:
            driver.get(url)
            time.sleep(4)
            if len(driver.find_element(By.TAG_NAME, "body").text) < 100:
                continue
            pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                "printBackground": True, "paperWidth": 8.5, "paperHeight": 11,
            })
            if pdf_data and pdf_data.get("data"):
                pdf_path.write_bytes(base64.b64decode(pdf_data["data"]))
                if pdf_path.stat().st_size > 5000:
                    return str(pdf_path)
                pdf_path.unlink(missing_ok=True)
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Main scrape loop
# ---------------------------------------------------------------------------
def scrape(start: date, end: date, visible: bool = False) -> List[LienRecord]:
    print(f"\n[Sarasota Liens] {start} → {end}")
    driver      = make_driver(visible=visible)
    all_records: List[LienRecord] = []
    seen:        set = set()

    try:
        setup_session(driver)

        current = start
        while current <= end:
            chunk_end = min(current + timedelta(days=CHUNK_DAYS - 1), end)

            for doc_code, doc_label, lien_type in DOC_TYPES:
                rows = search_chunk(driver, doc_code, doc_label,
                                    lien_type, current, chunk_end)
                added = 0
                for row in rows:
                    rec = parse_row(row)
                    if rec and rec.instrument_number not in seen:
                        seen.add(rec.instrument_number)
                        all_records.append(rec)
                        added += 1
                print(f"    +{added} {lien_type} (total: {len(all_records)})")
                time.sleep(2)

            current = chunk_end + timedelta(days=1)

    except KeyboardInterrupt:
        print("\n  Interrupted")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()
        save_debug(driver, "error")
    finally:
        driver.quit()

    print(f"\n  Total: {len(all_records)}")
    return all_records


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


def import_records(records: List[LienRecord]) -> Dict[str, int]:
    if not records or not get_connection:
        return {"inserted": 0, "skipped": 0}
    conn  = get_connection()
    conn.autocommit = False
    stats = {"inserted": 0, "skipped": 0}
    try:
        with conn.cursor() as cur:
            county_id = get_county_id(cur)
            for rec in records:
                if not rec.instrument_number or not rec.debtor_name:
                    stats["skipped"] += 1
                    continue
                source_id = f"{SOURCE_NAME}::{rec.instrument_number}"
                payload   = json.dumps({
                    "instrument": rec.instrument_number,
                    "debtor":     rec.debtor_name,
                    "lien_type":  rec.lien_type,
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
                    """, (county_id, SOURCE_NAME, source_id, payload, rec.filed_date))
                    rl = cur.fetchone()
                    if rl and rl[1]:
                        raw_id = rl[0]
                        stats["inserted"] += 1
                except Exception:
                    conn.rollback()
                    stats["skipped"] += 1
                    continue

                n_hash = f"sar::{rec.instrument_number}::{(rec.debtor_name or '')[:40]}"
                try:
                    cur.execute("""
                        INSERT INTO normalized_liens (
                            county_id, raw_lien_id, debtor_name,
                            lien_type, filed_date, normalized_hash
                        ) VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (normalized_hash) DO UPDATE SET
                            debtor_name = EXCLUDED.debtor_name,
                            filed_date  = COALESCE(EXCLUDED.filed_date, normalized_liens.filed_date)
                    """, (county_id, raw_id, rec.debtor_name,
                          rec.lien_type, rec.filed_date, n_hash))
                except Exception as e:
                    conn.rollback()
                    stats["skipped"] += 1
                    continue
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Sarasota County lien scraper")
    parser.add_argument("--days-back", type=int, default=14)
    parser.add_argument("--no-db",     action="store_true")
    parser.add_argument("--visible",   action="store_true")
    parser.add_argument("--no-pdf",    action="store_true")
    args = parser.parse_args()

    end   = date.today()
    start = end - timedelta(days=args.days_back)

    records = scrape(start, end, visible=args.visible)

    # Download PDFs
    if records and not args.no_pdf:
        print(f"\n  Downloading PDFs for {len(records)} liens...")
        driver = make_driver(visible=args.visible)
        try:
            setup_session(driver)
            pdf_count = 0
            for i, rec in enumerate(records):
                path = download_pdf(driver, rec)
                if path:
                    rec.pdf_path = path
                    pdf_count += 1
                if (i + 1) % 10 == 0:
                    print(f"    {pdf_count}/{i+1} PDFs")
            print(f"  PDFs downloaded: {pdf_count}/{len(records)}")
        except Exception as e:
            print(f"  PDF error: {e}")
        finally:
            driver.quit()

    if records:
        snap = RAW_DIR / f"sar_liens_{nowstamp()}.json"
        snap.write_text(json.dumps([{
            "instrument": r.instrument_number,
            "debtor":     r.debtor_name,
            "lien_type":  r.lien_type,
            "filed_date": str(r.filed_date),
        } for r in records], indent=2, default=str), encoding="utf-8")
        print(f"\nSaved: {snap}")
        for r in records[:5]:
            print(f"  {r.instrument_number} | {r.debtor_name} | {r.lien_type} | {r.filed_date}")

    stats = {"inserted": 0, "skipped": 0}
    if not args.no_db and records:
        stats = import_records(records)

    federal = sum(1 for r in records if r.lien_type == "federal_tax_lien")
    state   = sum(1 for r in records if r.lien_type == "state_tax_lien")
    print(f"\n--- Sarasota Lien Summary ---")
    print(f"  Scraped  : {len(records)} ({federal} federal, {state} state)")
    print(f"  Inserted : {stats['inserted']}")
    print(f"  Skipped  : {stats['skipped']}")


if __name__ == "__main__":
    main()