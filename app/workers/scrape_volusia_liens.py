"""
scrape_volusia_liens.py — v5
Uses CDP network interception to capture the results URL from window.open()
"""
from __future__ import annotations
import argparse, json, os, re, time, threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

try:
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_WDM = True
except ImportError:
    HAS_WDM = False

try:
    from app.core.db import get_connection
except ImportError:
    get_connection = None

COUNTY_NAME = "Volusia"
SOURCE_NAME = "volusia_liens"
HASH_PREFIX = "volusia"
SEARCH_URL  = "https://app02.clerk.org/or_m/inquiry.aspx"

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR  = BASE_DIR / "data" / "raw" / "volusia" / "liens"
DBG_DIR  = RAW_DIR / "debug"
for d in [RAW_DIR, DBG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

IRS_KEYWORDS = {
    "INTERNAL REVENUE", "IRS", "UNITED STATES", "US TREASURY",
    "FLORIDA DEPT", "FL DEPT", "DEPARTMENT OF REVENUE", "STATE OF FLORIDA",
    "FLORIDA DEPARTMENT",
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
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--disable-extensions")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("prefs", {
        "profile.default_content_setting_values.popups": 1,
        "profile.default_content_settings.popups": 1,
    })
    opts.add_experimental_option("useAutomationExtension", False)
    # Enable logging to capture network requests
    caps = DesiredCapabilities.CHROME.copy()
    caps['goog:loggingPrefs'] = {'performance': 'ALL'}
    opts.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
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

def scrape_requests(start: date, end: date) -> List[dict]:
    """
    Use requests to POST directly to Volusia search form.
    Bypasses the popup/new-window issue entirely.
    """
    import requests
    from bs4 import BeautifulSoup

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        "Referer": SEARCH_URL,
    })

    start_str = start.strftime("%m/%d/%Y")
    end_str   = end.strftime("%m/%d/%Y")

    # Step 1: GET the search page to get ASP.NET viewstate tokens
    r = session.get(SEARCH_URL, timeout=15)
    soup = BeautifulSoup(r.text, "lxml")

    # Extract hidden form fields
    form_data = {}
    for inp in soup.find_all("input", type="hidden"):
        if inp.get("name") and inp.get("value"):
            form_data[inp["name"]] = inp["value"]

    print(f"  Form fields: {list(form_data.keys())[:8]}")

    # Step 2: POST with search criteria
    form_data.update({
        "__EVENTTARGET":   "ctl00$ContentPlaceHolder1$search",
        "__EVENTARGUMENT": "",
        "ctl00$ContentPlaceHolder1$fromDate":  start_str,
        "ctl00$ContentPlaceHolder1$toDate":    end_str,
        "ctl00$ContentPlaceHolder1$doctype":   "LIEN",
        "ctl00$ContentPlaceHolder1$rows":      "500",
    })

    r2 = session.post(SEARCH_URL, data=form_data, timeout=30,
                      allow_redirects=True)
    print(f"  POST status: {r2.status_code} URL: {r2.url}")
    print(f"  Response size: {len(r2.text)} chars")

    soup2 = BeautifulSoup(r2.text, "lxml")

    # Find results table
    all_rows = []
    tables = soup2.find_all("table")
    best_table = max(tables, key=lambda t: len(t.find_all("tr")), default=None)

    if not best_table:
        print(f"  No table found. Body: {soup2.get_text()[:200]!r}")
        return []

    headers = []
    header_row = best_table.find("tr")
    if header_row:
        headers = [th.get_text(strip=True) for th in
                   header_row.find_all(["th", "td"])]
    print(f"  Headers: {headers[:8]}")

    for row in best_table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        rec = {}
        for i, cell in enumerate(cells):
            key = headers[i] if i < len(headers) else f"col{i}"
            rec[key] = cell.get_text(strip=True)
        if any(v for v in rec.values()):
            all_rows.append(rec)

    print(f"  Rows found: {len(all_rows)}")
    if all_rows:
        print(f"  Sample: {dict(list(all_rows[0].items())[:4])}")
    return all_rows


def scrape(driver, start: date, end: date) -> List[dict]:
    # Try requests approach first (avoids popup blocker)
    try:
        rows = scrape_requests(start, end)
        if rows:
            return rows
        print("  Requests approach got 0 rows — trying browser")
    except Exception as e:
        print(f"  Requests error: {e} — trying browser")

    start_str = start.strftime("%m/%d/%Y")
    end_str   = end.strftime("%m/%d/%Y")

    driver.get(SEARCH_URL)
    time.sleep(3)

    # Open Advanced checkbox
    try:
        cb = driver.find_element(By.ID, "keepOpen")
        if not cb.is_selected():
            driver.execute_script("arguments[0].click();", cb)
        time.sleep(1)
    except Exception:
        pass

    # Set dates - confirmed IDs: fromDateTxt, toDateTxt
    for fid, val in [("fromDateTxt", start_str), ("toDateTxt", end_str)]:
        el = driver.find_element(By.ID, fid)
        driver.execute_script("arguments[0].value=arguments[1];", el, val)
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", el)

    # Set doc type - confirmed id='doctype', value='LIEN'
    from selenium.webdriver.support.ui import Select as _Sel
    sel_el = driver.find_element(By.ID, "doctype")
    sel_obj = _Sel(sel_el)
    for opt in sel_obj.options:
        if 'LIEN' in opt.text.upper() and 'LIS' not in opt.text.upper():
            sel_obj.select_by_visible_text(opt.text)
            print(f"  Doc type: {opt.text!r}")
            break

    # Set max records per page to highest available to avoid pagination
    # (Volusia results expire on page navigation)
    try:
        rows_sel = driver.find_element(By.ID, "rows")
        rows_obj = _Sel(rows_sel)
        # Try to select maximum rows (999, 500, 200, 100)
        for max_val in ["999", "500", "250", "200", "100"]:
            try:
                rows_obj.select_by_value(max_val)
                print(f"  Max rows set to: {max_val}")
                break
            except Exception:
                continue
    except Exception:
        pass

    # Also try setting rows via name attribute
    try:
        rows_sel2 = driver.find_element(By.NAME, "rows")
        rows_obj2 = _Sel(rows_sel2)
        for max_val in ["999", "500", "200", "100"]:
            try:
                rows_obj2.select_by_value(max_val)
                break
            except Exception:
                continue
    except Exception:
        pass

    # Verify values
    vals = driver.execute_script("""
        return {
            from: document.getElementById('fromDateTxt').value,
            to:   document.getElementById('toDateTxt').value,
            doc:  document.getElementById('doctype').value
        };
    """)
    print(f"  Values: from={vals['from']} to={vals['to']} doc={vals['doc']}")

    # Intercept window.open and force same-window navigation BEFORE submit
    result = driver.execute_script("""
        // Override window.open to redirect in same window
        var _origOpen = window.open;
        window.open = function(url, name, features) {
            if (url && url !== 'about:blank') {
                window.location.href = url;
                return window;
            }
            return _origOpen.apply(this, arguments);
        };

        // Force all forms to target _self
        Array.from(document.forms).forEach(function(f) {
            f.target = '_self';
        });

        // Set ASP.NET event fields
        var et = document.getElementById('__EVENTTARGET');
        var ea = document.getElementById('__EVENTARGUMENT');
        if (et) et.value = 'ctl00$ContentPlaceHolder1$search';
        if (ea) ea.value = '';

        var form = document.forms[0];
        return {
            formAction: form ? form.action : 'none',
            formTarget: form ? (form.target || 'none') : 'none',
            fields: form ? form.elements.length : 0
        };
    """)
    print(f"  Form info: {result}")

    # Try clicking the Search button directly instead of form.submit()
    submitted = False
    for by, sel in [
        (By.CSS_SELECTOR, "input[type='submit']"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.XPATH, "//input[@value='Search' or @value='Submit']"),
        (By.XPATH, "//button[contains(text(),'Search')]"),
    ]:
        try:
            btn = driver.find_element(by, sel)
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                submitted = True
                print(f"  Clicked submit via {sel}")
                break
        except Exception:
            continue

    if not submitted:
        driver.execute_script("document.forms[0].submit();")
        print("  Submitted via form.submit()")

    # Wait and check for new windows
    time.sleep(3)
    handles_after = set(driver.window_handles)
    if len(handles_after) > 1:
        new_handle = [h for h in handles_after if h != driver.current_window_handle]
        if new_handle:
            driver.switch_to.window(new_handle[0])
            print(f"  Switched to new window")
    
    time.sleep(3)
    print(f"  After submit URL: {driver.current_url}")
    save_debug(driver, "after_submit")

    # Check if we got results or error
    page_info = driver.execute_script("""
        var body = document.body ? document.body.innerText : '';
        var tables = [];
        document.querySelectorAll('table').forEach(function(t) {
            var rows = t.querySelectorAll('tr');
            if (rows.length > 2) tables.push({
                rows: rows.length,
                sample: rows.length > 1 ? rows[1].innerText.trim().substring(0,80) : ''
            });
        });
        return {body_start: body.substring(0,300), tables: tables};
    """)
    print(f"  Body: {page_info['body_start'][:200]!r}")
    print(f"  Tables: {page_info['tables'][:3]}")

    all_rows = []
    page = 1
    while True:
        rows = driver.execute_script("""
            var results = [];
            var tables = document.querySelectorAll('table');
            var best = null; var bestN = 0;
            tables.forEach(function(t) {
                var n = t.querySelectorAll('tbody tr, tr').length;
                if (n > bestN) { bestN = n; best = t; }
            });
            if (!best) return [];
            var headers = [];
            var hrow = best.querySelector('tr');
            if (hrow) hrow.querySelectorAll('th,td').forEach(function(c){
                headers.push((c.innerText||'').trim());
            });
            var rows = best.querySelectorAll('tbody tr');
            if (!rows.length) rows = best.querySelectorAll('tr:not(:first-child)');
            rows.forEach(function(row) {
                var cells = row.querySelectorAll('td');
                if (cells.length < 3) return;
                var rec = {};
                cells.forEach(function(c,i){
                    rec[headers[i]||'col'+i]=(c.innerText||c.textContent||'').trim();
                });
                results.push(rec);
            });
            return results;
        """)
        print(f"  Page {page}: {len(rows)} rows")
        if rows:
            print(f"  Headers: {list(rows[0].keys())[:6]}")
            print(f"  Sample: {dict(list(rows[0].items())[:4])}")
        all_rows.extend(rows)
        if not rows: break
        try:
            nxt = driver.find_element(By.XPATH,
                "//a[normalize-space(text())='next' or normalize-space(text())='Next']")
            if 'disabled' in (nxt.get_attribute('class') or '').lower(): break
            # Check if page expired before clicking next
            page_text = driver.find_element(By.TAG_NAME, "body").text
            if "expired" in page_text.lower() or "session" in page_text.lower():
                print(f"  Page expired — stopping pagination")
                break
            driver.execute_script("arguments[0].click();", nxt)
            time.sleep(3); page += 1
            # Check again after click
            page_text2 = driver.find_element(By.TAG_NAME, "body").text
            if "expired" in page_text2.lower() or len(page_text2) < 200:
                print(f"  Page expired after click — stopping")
                break
        except Exception: break
    return all_rows


def parse_record(row):
    grantor  = str(row.get("Direct Name",row.get("col1",row.get("col2","")))).strip()
    grantee  = str(row.get("Indirect Name",row.get("col2",row.get("col3","")))).strip()
    rec_date = str(row.get("Record Date",row.get("col3",row.get("col4","")))).strip()
    instr    = str(row.get("Instrument #",row.get("col6",row.get("col7","")))).strip()
    if not grantor and not grantee: return None
    g_up,gr_up = grantee.upper(),grantor.upper()
    if any(k in g_up for k in IRS_KEYWORDS): debtor,creditor=grantor,grantee
    elif any(k in gr_up for k in IRS_KEYWORDS): debtor,creditor=grantee,grantor
    else: debtor,creditor=grantor,grantee
    if not debtor or len(debtor.strip())<2: return None
    filed=None
    for fmt in ("%m/%d/%Y","%Y-%m-%d"):
        try: filed=datetime.strptime(rec_date.split()[0],fmt).date(); break
        except: pass
    return {"instrument_number":instr,"debtor_name":debtor.title(),
            "creditor_name":creditor.title() if creditor else None,
            "lien_type":"federal_tax_lien","filed_date":filed,"raw_payload":row}


def get_county_id(cur):
    cur.execute("SELECT id FROM counties WHERE county_name=%s",(COUNTY_NAME,))
    r=cur.fetchone()
    if r: return r[0]
    cur.execute("INSERT INTO counties(county_name,state,active,created_at) VALUES(%s,'FL',true,NOW()) RETURNING id",(COUNTY_NAME,))
    return cur.fetchone()[0]


def import_records(records):
    if not records or not get_connection: return {"inserted":0,"skipped":0}
    conn=get_connection(); conn.autocommit=False
    stats={"inserted":0,"updated":0,"skipped":0}
    try:
        with conn.cursor() as cur:
            cid=get_county_id(cur)
            for rec in records:
                if not rec.get("debtor_name"): stats["skipped"]+=1; continue
                sid=f"{SOURCE_NAME}::{rec['instrument_number']}"
                payload=json.dumps(rec["raw_payload"],default=str)
                raw_id=None
                try:
                    cur.execute("INSERT INTO raw_liens(county_id,source_file,source_record_id,raw_payload,filed_date) VALUES(%s,%s,%s,%s::jsonb,%s) ON CONFLICT(county_id,source_record_id) DO UPDATE SET raw_payload=EXCLUDED.raw_payload RETURNING id",(cid,SOURCE_NAME,sid,payload,rec["filed_date"]))
                    r=cur.fetchone(); raw_id=r[0] if r else None
                except: conn.rollback(); stats["skipped"]+=1; continue
                nh=f"{HASH_PREFIX}::{rec['instrument_number']}::{rec['debtor_name'][:40]}"
                try:
                    cur.execute("INSERT INTO normalized_liens(county_id,raw_lien_id,debtor_name,lien_type,filed_date,normalized_hash) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(normalized_hash) DO UPDATE SET debtor_name=EXCLUDED.debtor_name,filed_date=COALESCE(EXCLUDED.filed_date,normalized_liens.filed_date)",(cid,raw_id,rec["debtor_name"],rec["lien_type"],rec["filed_date"],nh))
                    stats["inserted"]+=1
                except Exception as e: conn.rollback(); print(f"  err:{e}"); stats["skipped"]+=1; continue
        conn.commit()
    except: conn.rollback(); raise
    finally: conn.close()
    return stats


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--days-back",type=int,default=180)
    parser.add_argument("--no-db",action="store_true")
    parser.add_argument("--no-pdf",action="store_true")
    parser.add_argument("--visible",action="store_true")
    args=parser.parse_args()
    end=date.today(); start=end-timedelta(days=args.days_back)
    print(f"\n[Volusia IRS Liens] {start} → {end}")
    driver=make_driver(visible=args.visible)
    raw_rows=[]
    try: raw_rows=scrape(driver,start,end)
    except Exception as e: print(f"  ERROR:{e}"); import traceback; traceback.print_exc()
    finally: driver.quit()
    records=[]; seen=set()
    for row in raw_rows:
        rec=parse_record(row)
        if rec and rec.get("instrument_number") not in seen:
            seen.add(rec.get("instrument_number",""))
            records.append(rec)
    print(f"\n  Total: {len(records)}")
    if records:
        snap=RAW_DIR/f"volusia_liens_{nowstamp()}.json"
        snap.write_text(json.dumps([{"i":r["instrument_number"],"d":r["debtor_name"],"f":str(r["filed_date"])} for r in records],indent=2,default=str))
        print(f"  Saved: {snap.name}")
        for r in records[:5]: print(f"    {r['instrument_number']} | {r['debtor_name']} | {r['filed_date']}")
    stats={"inserted":0,"skipped":0}
    if not args.no_db and records: stats=import_records(records)
    print(f"\n--- Volusia Summary ---")
    print(f"  Scraped  : {len(records)}")
    print(f"  Inserted : {stats.get('inserted',0)}")
    print(f"  Skipped  : {stats.get('skipped',0)}")

if __name__=="__main__":
    main()