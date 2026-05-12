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
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
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

def scrape(driver, start: date, end: date) -> List[dict]:
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

    # Verify values
    vals = driver.execute_script("""
        return {
            from: document.getElementById('fromDateTxt').value,
            to:   document.getElementById('toDateTxt').value,
            doc:  document.getElementById('doctype').value
        };
    """)
    print(f"  Values: from={vals['from']} to={vals['to']} doc={vals['doc']}")

    # Override form target AND submit
    result = driver.execute_script("""
        // Find the form and force target to _self
        var forms = document.querySelectorAll('form');
        var formTarget = 'unknown';
        forms.forEach(function(f) {
            formTarget = f.target || 'none';
            f.target = '_self';
            f.removeAttribute('target');
        });

        // Also override window.open as backup
        window.open = function(url, t, f) {
            window._lastOpenUrl = url;
            document.location.href = url;
            return window;
        };

        document.getElementById('__EVENTTARGET').value = 'ctl00$ContentPlaceHolder1$search';
        document.getElementById('__EVENTARGUMENT').value = '';

        var form = document.forms[0];
        return {formTarget: formTarget, formAction: form ? form.action : 'none',
                fields: form ? form.elements.length : 0};
    """)
    print(f"  Form info: {result}")

    # Submit
    driver.execute_script("document.forms[0].submit();")
    time.sleep(8)
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
            driver.execute_script("arguments[0].click();", nxt)
            time.sleep(2); page += 1
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