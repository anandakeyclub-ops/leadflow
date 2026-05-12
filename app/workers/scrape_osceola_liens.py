"""
scrape_osceola_liens.py
=======================
Osceola County lien scraper.
Portal: https://officialrecords.osceolaclerk.org/browserview/
System: NewVision SPA — uses JSON API endpoints

Doc types confirmed from inspector:
- FTL = Federal Tax Lien
- TAX = State Tax Lien (Florida Dept of Revenue)

Columns: Name (debtor), Cross Party (creditor), Date, Type, Instr#, Book, Page

Strategy: POST to NewVision API with doc type codes + date range

Usage:
  python -m app.workers.scrape_osceola_liens --days-back 180
  python -m app.workers.scrape_osceola_liens --days-back 7 --no-db --visible
"""
from __future__ import annotations

import argparse, json, re, time, base64
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
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

COUNTY_NAME  = "Osceola"
SOURCE_NAME  = "osceola_liens"
HASH_PREFIX  = "osceola"
SEARCH_URL   = "https://officialrecords.osceolaclerk.org/browserview/"
DOC_TYPES    = "FTL,TAX"   # Federal Tax Lien + State Tax Lien

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR  = BASE_DIR / "data" / "raw" / "osceola" / "liens"
DBG_DIR  = RAW_DIR / "debug"
PDF_DIR  = RAW_DIR / "pdfs"
for d in [RAW_DIR, DBG_DIR, PDF_DIR]:
    d.mkdir(parents=True, exist_ok=True)

IRS_KEYWORDS = {
    "INTERNAL REVENUE", "IRS", "FLORIDA DEPT OF REVENUE",
    "FLORIDA DEPARTMENT OF REVENUE", "DEPARTMENT OF JUSTICE",
    "UNITED STATES", "STATE OF FLORIDA",
}

def nowstamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def make_driver(visible=False):
    opts = Options()
    if not visible:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1440,900")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    if HAS_WDM:
        drv = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    else:
        drv = webdriver.Chrome(options=opts)
    return drv

def save_debug(driver, label):
    try:
        driver.save_screenshot(str(DBG_DIR / f"{nowstamp()}_{label}.png"))
    except Exception:
        pass

def scrape(driver, start: date, end: date) -> List[dict]:
    CHUNK_DAYS = 30
    all_rows = []
    seen = set()

    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end)
        print(f"  Chunk: {chunk_start} → {chunk_end}")
        rows = _scrape_chunk(driver, chunk_start, chunk_end)
        for row in rows:
            key = str(row.get("Instr#") or row.get("col_5") or str(row)[:40])
            if key not in seen:
                seen.add(key)
                all_rows.append(row)
        print(f"  Chunk total: {len(rows)} rows | Running total: {len(all_rows)}")
        chunk_start = chunk_end + timedelta(days=1)

    return all_rows


def _scrape_chunk(driver, start: date, end: date) -> List[dict]:
    start_str = start.strftime("%m/%d/%Y")
    end_str   = end.strftime("%m/%d/%Y")  # chunk dates

    print(f"  Loading: {SEARCH_URL}")
    driver.get(SEARCH_URL)
    time.sleep(5)

    # Click Document Type tab
    driver.execute_script("""
        document.querySelectorAll('li, a, span, div, td').forEach(function(el) {
            if (el.children.length === 0 &&
                el.textContent.trim() === 'Document Type' &&
                el.offsetParent !== null) { el.click(); }
        });
    """)
    time.sleep(2)

    # Use Angular scope directly - confirmed scope key: docSearchForm
    set_result = driver.execute_script("""
        var docTypes = arguments[0];
        var fromDate = arguments[1];
        var toDate   = arguments[2];

        // Find any Angular element to get injector
        var el = document.querySelector('[ng-app], [data-ng-app]') ||
                 document.querySelector('.ng-scope');
        if (!el) return {error: 'no ng-app found'};

        try {
            var scope = angular.element(el).scope();
            var $rootScope = angular.element(el).injector().get('$rootScope');

            // docSearchForm confirmed from scope dump
            if (scope.docSearchForm) {
                scope.docSearchForm.docType = docTypes;
                scope.docSearchForm.dateFrom = fromDate;
                scope.docSearchForm.dateTo   = toDate;
                scope.docSearchForm.fromDate = fromDate;
                scope.docSearchForm.toDate   = toDate;
            }

            // Also try setting on root scope
            $rootScope.docSearchForm = $rootScope.docSearchForm || {};
            $rootScope.docSearchForm.docType  = docTypes;
            $rootScope.docSearchForm.dateFrom = fromDate;
            $rootScope.docSearchForm.dateTo   = toDate;

            $rootScope.$apply();
            return {ok: true, form: JSON.stringify(scope.docSearchForm||{}).substring(0,200)};
        } catch(e) {
            return {error: String(e)};
        }
    """, DOC_TYPES, start_str, end_str)
    print(f"  Angular set: {set_result}")

    # Fill date range inputs directly after Angular set
    from selenium.webdriver.common.keys import Keys
    for ph, val in [("From Date", start_str), ("To Date", end_str),
                    ("Start Date", start_str), ("End Date", end_str)]:
        try:
            els = driver.find_elements(By.XPATH,
                f"//input[@placeholder='{ph}' or contains(@placeholder,'{ph.split()[0]}')]")
            for el in els:
                if el.is_displayed():
                    el.clear()
                    el.send_keys(val)
                    el.send_keys(Keys.TAB)
                    print(f"  Set date field '{ph}': {val}")
                    break
        except Exception:
            pass

    # Also set via Angular ng-model on date inputs
    driver.execute_script("""
        var fromVal = arguments[0], toVal = arguments[1];
        document.querySelectorAll('input').forEach(function(inp) {
            var ph = (inp.placeholder || '').toLowerCase();
            var ngModel = inp.getAttribute('ng-model') || '';
            if (ph.indexOf('from') >= 0 || ngModel.indexOf('from') >= 0 ||
                ngModel.indexOf('start') >= 0) {
                Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value')
                    .set.call(inp, fromVal);
                inp.dispatchEvent(new Event('input',{bubbles:true}));
                inp.dispatchEvent(new Event('change',{bubbles:true}));
            }
            if (ph.indexOf('to') >= 0 || ngModel.indexOf('to') >= 0 ||
                ngModel.indexOf('end') >= 0) {
                Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value')
                    .set.call(inp, toVal);
                inp.dispatchEvent(new Event('input',{bubbles:true}));
                inp.dispatchEvent(new Event('change',{bubbles:true}));
            }
        });
    """, start_str, end_str)

    # Also type into the visible input field using JS to fire Angular events
    driver.execute_script("""
        var inp = document.querySelector("input[placeholder='Document Types']");
        if (inp) {
            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(inp, arguments[0]);
            inp.dispatchEvent(new Event('input', {bubbles:true}));
            inp.dispatchEvent(new Event('change', {bubbles:true}));
            inp.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true}));
        }
        // Set date inputs too
        document.querySelectorAll('input').forEach(function(el) {
            var ph = (el.placeholder||'').toLowerCase();
            if (ph.indexOf('from') >= 0 || ph.indexOf('start') >= 0) {
                var setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, arguments[1]);
                el.dispatchEvent(new Event('input',{bubbles:true}));
                el.dispatchEvent(new Event('change',{bubbles:true}));
            }
            if (ph.indexOf('to') >= 0 || ph.indexOf('end') >= 0) {
                var setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, arguments[2]);
                el.dispatchEvent(new Event('input',{bubbles:true}));
                el.dispatchEvent(new Event('change',{bubbles:true}));
            }
        });
    """, DOC_TYPES, start_str, end_str)
    time.sleep(1)

    # Verify what the input shows now
    current_val = driver.execute_script("""
        var inp = document.querySelector("input[placeholder='Document Types']");
        return inp ? inp.value : 'not found';
    """)
    print(f"  Doc type input value: {current_val!r}")

    # Click Search
    driver.execute_script("""
        document.querySelectorAll('button, input[type="submit"]').forEach(function(btn) {
            if ((btn.textContent||btn.value||'').trim().toLowerCase() === 'search') btn.click();
        });
    """)
    time.sleep(8)
    save_debug(driver, "after_search")

    # Check if Results tab appeared and click it
    driver.execute_script("""
        document.querySelectorAll('li, a, span, div').forEach(function(el) {
            if (el.children.length === 0 && el.textContent.trim() === 'Results' &&
                el.offsetParent !== null) el.click();
        });
    """)
    time.sleep(2)

    # Parse AG Grid results - uses div rows, not table rows
    rows = driver.execute_script("""
        var results = [];

        // AG Grid row selector - confirmed class 'ag-cell'
        var agRows = document.querySelectorAll('.ag-row');
        if (agRows.length > 0) {
            // Get column headers
            var headerCells = document.querySelectorAll('.ag-header-cell');
            var colIds = Array.from(headerCells).map(function(c) {
                return c.getAttribute('col-id') || c.innerText.trim();
            });
            agRows.forEach(function(row) {
                var cells = row.querySelectorAll('.ag-cell');
                if (cells.length < 2) return;
                var rec = {};
                cells.forEach(function(cell, i) {
                    var colId = cell.getAttribute('col-id') || colIds[i] || 'col_' + i;
                    rec[colId] = (cell.innerText || '').trim();
                });
                var link = row.querySelector('a');
                if (link) rec['_url'] = link.href;
                results.push(rec);
            });
            return {rows: results, source: 'ag-grid', count: agRows.length, colIds: colIds};
        }

        // Fallback: table-striped with actual data
        var best = null, bestN = 0;
        document.querySelectorAll('table.table-striped').forEach(function(t) {
            var txt = t.innerText;
            if (/\\d{10}/.test(txt) || txt.indexOf('FTL') >= 0 || txt.indexOf('TAX') >= 0) {
                var n = t.querySelectorAll('tr').length;
                if (n > bestN) { bestN = n; best = t; }
            }
        });
        if (best) {
            var headers = [];
            var hrow = best.querySelector('thead tr');
            if (hrow) hrow.querySelectorAll('th').forEach(function(c){
                headers.push((c.innerText||'').trim());
            });
            best.querySelectorAll('tbody tr').forEach(function(row){
                var cells = row.querySelectorAll('td');
                if (cells.length < 2) return;
                var rec = {};
                cells.forEach(function(c,i){
                    rec[headers[i]||'col_'+i]=(c.innerText||'').trim();
                });
                var lnk = row.querySelector('a');
                if (lnk) rec['_url'] = lnk.href;
                results.push(rec);
            });
            return {rows: results, source: 'table'};
        }

        return {body: document.body.innerText.substring(0,300)};
    """)

    if isinstance(rows, dict) and 'body' in rows:
        # Results tab IS loaded - dump HTML to find table structure
        html_dump = driver.execute_script("""
            // Find the results section
            var body = document.body.innerHTML;
            // Look for table or grid with data
            var tables = document.querySelectorAll('table');
            var info = {tableCount: tables.length, tables: []};
            tables.forEach(function(t) {
                info.tables.push({
                    cls: t.className.substring(0,40),
                    rows: t.querySelectorAll('tr').length,
                    sample: t.innerText.substring(0,200)
                });
            });
            // Also look for ng-repeat rows
            var ngRows = document.querySelectorAll('[ng-repeat]');
            info.ngRows = ngRows.length;
            info.ngSample = ngRows.length > 0 ? ngRows[0].innerText.substring(0,100) : '';
            // Look for any element with instrument numbers
            var instrEls = [];
            document.querySelectorAll('*').forEach(function(el) {
                if (el.children.length === 0 && /^\\d{10}$/.test(el.textContent.trim())) {
                    instrEls.push({tag:el.tagName, cls:el.className.substring(0,30),
                                   parent:el.parentElement.tagName});
                }
            });
            info.instrEls = instrEls.slice(0,5);
            return info;
        """)
        print(f"  HTML analysis: {html_dump}")
        return []

    data = rows.get('rows', []) if isinstance(rows, dict) else []
    src_label = rows.get('source', '?') if isinstance(rows, dict) else '?'
    col_ids = rows.get('colIds', []) if isinstance(rows, dict) else []
    print(f"  Results: {len(data)} rows (source: {src_label})")
    print(f"  Col IDs from header: {col_ids}")
    if data:
        print(f"  Keys: {list(data[0].keys())[:8]}")
        print(f"  Sample: {dict(list(data[0].items())[:6])}")
    return data

    print(f"  Loading: {SEARCH_URL}")

    # Enable network request interception to find API endpoint
    driver.execute_cdp_cmd("Network.enable", {})
    api_requests = []
    driver.execute_script("""
        window._apiCalls = [];
        var origOpen = XMLHttpRequest.prototype.open;
        var origSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.open = function(method, url) {
            this._url = url; this._method = method;
            return origOpen.apply(this, arguments);
        };
        XMLHttpRequest.prototype.send = function(body) {
            var self = this;
            var origOnLoad = this.onload;
            this.onload = function() {
                window._apiCalls.push({
                    url: self._url, method: self._method,
                    body: body ? body.substring(0,200) : '',
                    response: self.responseText ? self.responseText.substring(0,500) : ''
                });
                if (origOnLoad) origOnLoad.apply(this, arguments);
            };
            return origSend.apply(this, arguments);
        };
        // Also intercept fetch
        var origFetch = window.fetch;
        window.fetch = function(url, opts) {
            window._apiCalls.push({url: url, method: opts ? opts.method : 'GET',
                                   body: opts && opts.body ? String(opts.body).substring(0,200) : ''});
            return origFetch.apply(this, arguments);
        };
    """)

    driver.get(SEARCH_URL)
    time.sleep(4)

    # Click Document Type tab by exact text
    driver.execute_script("""
        var found = false;
        document.querySelectorAll('li, a, span, div, td').forEach(function(el) {
            if (el.children.length === 0 &&
                el.textContent.trim() === 'Document Type' &&
                el.offsetParent !== null) {
                el.click();
                found = true;
            }
        });
        return found;
    """)
    time.sleep(2)

    # Dump all visible inputs to find the right one
    inputs = driver.execute_script("""
        var r = [];
        document.querySelectorAll('input,select,textarea').forEach(function(el) {
            if (el.offsetParent !== null || el.offsetHeight > 0) {
                r.push({tag:el.tagName, id:el.id, name:el.name,
                        type:el.getAttribute('type'), ph:el.placeholder,
                        val:el.value, cls:el.className.substring(0,30)});
            }
        });
        return r;
    """)
    print(f"  Visible inputs after tab click ({len(inputs)}):")
    for inp in inputs[:15]:
        print(f"    {inp}")

    # Find the Document Type code input specifically
    # From screenshot: it's a text input where you type "FTL,TAX"
    # Set it by trying all text inputs and checking which one accepts our value
    set_result = driver.execute_script("""
        var docTypes = arguments[0];
        var inputs = document.querySelectorAll('input[type="text"], input:not([type])');
        var results = [];
        inputs.forEach(function(inp) {
            if (inp.offsetParent === null && inp.offsetHeight === 0) return;
            var ph = (inp.placeholder || '').toLowerCase();
            var cls = (inp.className || '').toLowerCase();
            // Look for the doc type input by placeholder or class
            if (ph.indexOf('mtg') >= 0 || ph.indexOf('code') >= 0 ||
                ph.indexOf('type') >= 0 || cls.indexOf('doctype') >= 0 ||
                ph.indexOf('comma') >= 0 || ph.indexOf('document') >= 0) {
                inp.value = docTypes;
                inp.dispatchEvent(new Event('input',{bubbles:true}));
                inp.dispatchEvent(new Event('change',{bubbles:true}));
                results.push({id:inp.id, ph:inp.placeholder, cls:inp.className.substring(0,30)});
            }
        });
        // If nothing matched, try the first visible text input
        if (!results.length) {
            for (var inp of inputs) {
                if (inp.offsetParent !== null || inp.offsetHeight > 0) {
                    inp.value = docTypes;
                    inp.dispatchEvent(new Event('input',{bubbles:true}));
                    results.push({id:inp.id, ph:inp.placeholder, fallback:true});
                    break;
                }
            }
        }
        return results;
    """, DOC_TYPES)
    print(f"  Doc type set: {set_result}")

    # Set date range
    driver.execute_script("""
        var inputs = document.querySelectorAll('input');
        var fromSet = false, toSet = false;
        inputs.forEach(function(inp) {
            if (inp.offsetParent === null && inp.offsetHeight === 0) return;
            var id = (inp.id||'').toLowerCase();
            var nm = (inp.name||'').toLowerCase();
            var ph = (inp.placeholder||'').toLowerCase();
            var lbl = '';
            // Check associated label
            if (inp.id) {
                var l = document.querySelector('label[for="'+inp.id+'"]');
                if (l) lbl = l.textContent.toLowerCase();
            }
            if (!fromSet && (id.indexOf('from')>=0||nm.indexOf('from')>=0||ph.indexOf('from')>=0||lbl.indexOf('from')>=0)) {
                inp.value = arguments[0];
                inp.dispatchEvent(new Event('change',{bubbles:true}));
                fromSet = true;
            } else if (!toSet && (id.indexOf('to')>=0||nm.indexOf('to')>=0||ph.indexOf('to')>=0||lbl.indexOf('to')>=0)) {
                inp.value = arguments[1];
                inp.dispatchEvent(new Event('change',{bubbles:true}));
                toSet = true;
            }
        });
        return {from:fromSet, to:toSet};
    """, start_str, end_str)
    print(f"  Dates: {start_str} → {end_str}")

    save_debug(driver, "before_search")

    # Click Search - try multiple selectors
    clicked = driver.execute_script("""
        var btns = ['button', 'input[type="button"]', 'input[type="submit"]',
                    'a', 'span', 'div'];
        for (var sel of btns) {
            var els = document.querySelectorAll(sel);
            for (var el of els) {
                var txt = (el.textContent || el.value || '').trim().toLowerCase();
                if (txt === 'search') {
                    el.click();
                    return {clicked: true, tag: sel, text: txt};
                }
            }
        }
        return {clicked: false};
    """)
    print(f"  Search click: {clicked}")
    time.sleep(8)
    save_debug(driver, "after_search")

    # Dump API calls made
    api_calls = driver.execute_script("return window._apiCalls || [];")
    print(f"  API calls ({len(api_calls)}):")
    for c in api_calls[-5:]:
        print(f"    {c.get('method','?')} {c.get('url','?')[:100]}")
        if c.get('response'):
            print(f"      response: {c['response'][:100]!r}")

    # Parse results table
    rows = driver.execute_script("""
        var results = [];
        // Find table with instrument numbers (10+ digit numbers)
        var best = null;
        document.querySelectorAll('table').forEach(function(t) {
            if (/\\d{10}/.test(t.innerText) && t.querySelectorAll('tr').length > 2) {
                best = t;
            }
        });
        if (!best) return {rows:[], body:document.body.innerText.substring(0,500)};

        var headers = [];
        var hrow = best.querySelector('thead tr, tr');
        if (hrow) hrow.querySelectorAll('th,td').forEach(function(c){
            headers.push((c.innerText||'').trim());
        });

        best.querySelectorAll('tbody tr, tr:not(:first-child)').forEach(function(row) {
            var cells = row.querySelectorAll('td');
            if (cells.length < 3) return;
            var rec = {};
            cells.forEach(function(c,i){
                rec[headers[i]||'col_'+i]=(c.innerText||'').trim();
            });
            var link = row.querySelector('a');
            if (link) rec['_url'] = link.href;
            results.push(rec);
        });
        return {rows:results, headers:headers};
    """)

    if isinstance(rows, dict) and 'body' in rows:
        print(f"  No results table. Body: {rows['body'][:300]!r}")
        return []

    data = rows.get('rows', []) if isinstance(rows, dict) else []
    hdrs = rows.get('headers', []) if isinstance(rows, dict) else []
    print(f"  Results: {len(data)} rows | headers: {hdrs[:8]}")
    if data:
        print(f"  Sample: {dict(list(data[0].items())[:6])}")
    return data


def parse_record(row: dict) -> Optional[dict]:
    # Confirmed columns from AG Grid: Name, Cross Party, Date, Type, Instr#, Book, Page
    # Name = debtor, Cross Party = creditor (IRS/FLDOR)
    debtor   = str(row.get("Name") or "").strip()
    creditor = str(row.get("Cross Party") or "").strip()
    rec_date = str(row.get("Date") or "").strip()
    doc_type = str(row.get("Type") or "").strip()
    instr    = str(row.get("Instr#") or "").strip()

    if not instr and not debtor:
        return None

    # If creditor is the debtor (asterisk rows show FLDOR as Name)
    if any(k in debtor.upper() for k in IRS_KEYWORDS):
        debtor, creditor = creditor, debtor

    if not debtor or len(debtor.strip()) < 2:
        return None

    filed = None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            filed = datetime.strptime(rec_date.split()[0], fmt).date()
            break
        except Exception:
            pass

    lien_type = "state_tax_lien" if doc_type == "TAX" else "federal_tax_lien"

    return {
        "instrument_number": instr,
        "debtor_name":       debtor.strip().title(),
        "creditor_name":     creditor.strip().title() if creditor else None,
        "lien_type":         lien_type,
        "filed_date":        filed,
        "doc_url":           row.get("_url", ""),
        "raw_payload":       row,
    }


def download_pdf(driver, rec: dict) -> Optional[str]:
    url  = rec.get("doc_url", "")
    instr = rec.get("instrument_number", "")
    if not url and not instr:
        return None
    safe     = re.sub(r"[^\w\-]", "_", instr)[:60]
    pdf_path = PDF_DIR / f"osceola_{safe}.pdf"
    if pdf_path.exists() and pdf_path.stat().st_size > 500:
        return str(pdf_path)
    try:
        driver.get(url or SEARCH_URL)
        time.sleep(3)
        pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
            "printBackground": True, "paperWidth": 8.5, "paperHeight": 11,
        })
        if pdf_data and pdf_data.get("data"):
            pdf_path.write_bytes(base64.b64decode(pdf_data["data"]))
            if pdf_path.stat().st_size > 2000:
                return str(pdf_path)
            pdf_path.unlink(missing_ok=True)
    except Exception:
        pass
    return None


def get_county_id(cur):
    cur.execute("SELECT id FROM counties WHERE county_name=%s", (COUNTY_NAME,))
    r = cur.fetchone()
    if r: return r[0]
    cur.execute("INSERT INTO counties(county_name,state,active,created_at) VALUES(%s,'FL',true,NOW()) RETURNING id", (COUNTY_NAME,))
    return cur.fetchone()[0]


def import_records(records):
    if not records or not get_connection:
        return {"inserted": 0, "skipped": 0}
    conn = get_connection(); conn.autocommit = False
    stats = {"inserted": 0, "skipped": 0}
    try:
        with conn.cursor() as cur:
            cid = get_county_id(cur)
            for rec in records:
                if not rec.get("debtor_name"):
                    stats["skipped"] += 1; continue
                sid     = f"{SOURCE_NAME}::{rec['instrument_number']}"
                payload = json.dumps(rec["raw_payload"], default=str)
                raw_id  = None
                try:
                    cur.execute("INSERT INTO raw_liens(county_id,source_file,source_record_id,raw_payload,filed_date) VALUES(%s,%s,%s,%s::jsonb,%s) ON CONFLICT(county_id,source_record_id) DO UPDATE SET raw_payload=EXCLUDED.raw_payload RETURNING id",
                                (cid, SOURCE_NAME, sid, payload, rec["filed_date"]))
                    r = cur.fetchone(); raw_id = r[0] if r else None
                except Exception:
                    conn.rollback(); stats["skipped"] += 1; continue
                nh = f"{HASH_PREFIX}::{rec['instrument_number']}::{rec['debtor_name'][:40]}"
                pdf_val = (rec.get("pdf_path") or "")[:250] or None
                try:
                    cur.execute("INSERT INTO normalized_liens(county_id,raw_lien_id,debtor_name,lien_type,filed_date,normalized_hash,pdf_path) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(normalized_hash) DO UPDATE SET debtor_name=EXCLUDED.debtor_name,filed_date=COALESCE(EXCLUDED.filed_date,normalized_liens.filed_date),pdf_path=COALESCE(EXCLUDED.pdf_path,normalized_liens.pdf_path)",
                                (cid, raw_id, rec["debtor_name"], rec["lien_type"], rec["filed_date"], nh, pdf_val))
                    stats["inserted"] += 1
                except Exception as e:
                    conn.rollback(); print(f"  err:{e}"); stats["skipped"] += 1; continue
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-back", type=int, default=180)
    parser.add_argument("--no-db",  action="store_true")
    parser.add_argument("--no-pdf", action="store_true")
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()

    end   = date.today()
    start = end - timedelta(days=args.days_back)
    print(f"\n[Osceola Liens] {start} → {end}")

    driver = make_driver(visible=args.visible)
    raw_rows = []
    try:
        raw_rows = scrape(driver, start, end)
    except Exception as e:
        print(f"  ERROR: {e}"); import traceback; traceback.print_exc()
        save_debug(driver, "error")
    finally:
        driver.quit()

    records = []
    seen = set()
    if raw_rows:
        print(f"  Raw keys: {list(raw_rows[0].keys())}")
        print(f"  Raw sample: {raw_rows[0]}")
    for row in raw_rows:
        rec = parse_record(row)
        if rec and rec.get("instrument_number") not in seen:
            seen.add(rec.get("instrument_number", ""))
            records.append(rec)

    print(f"\n  Total scraped: {len(records)}")

    if not args.no_pdf and records:
        print(f"  Downloading PDFs...")
        d2 = make_driver(visible=args.visible)
        try:
            pdf_count = 0
            for i, rec in enumerate(records):
                path = download_pdf(d2, rec)
                if path:
                    rec["pdf_path"] = path; pdf_count += 1
                if (i+1) % 20 == 0:
                    print(f"    {pdf_count}/{i+1}")
            print(f"  PDFs: {pdf_count}/{len(records)}")
        except Exception as e:
            print(f"  PDF error: {e}")
        finally:
            d2.quit()

    if records:
        snap = RAW_DIR / f"osceola_liens_{nowstamp()}.json"
        snap.write_text(json.dumps([{"i":r["instrument_number"],"d":r["debtor_name"],"f":str(r["filed_date"]),"t":r["lien_type"]} for r in records],indent=2,default=str),encoding="utf-8")
        print(f"  Saved: {snap.name}")
        for r in records[:5]:
            print(f"    {r['instrument_number']} | {r['debtor_name']} | {r['lien_type']} | {r['filed_date']}")

    stats = {"inserted":0,"skipped":0}
    if not args.no_db and records:
        stats = import_records(records)

    print(f"\n--- Osceola Summary ---")
    print(f"  Scraped  : {len(records)}")
    print(f"  Inserted : {stats.get('inserted',0)}")
    print(f"  Skipped  : {stats.get('skipped',0)}")

if __name__ == "__main__":
    main()