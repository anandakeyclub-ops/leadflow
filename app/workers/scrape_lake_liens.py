"""
scrape_lake_liens.py
====================
Lake County IRS/state tax lien scraper.
Portal: https://officialrecords.lakecountyclerk.org/ (Acclaim/Harris)

From inspector 2026-05-10:
- Accept disclaimer via id='btnButton'
- Navigate to /search/SearchTypeDocType
- Select Doc Type = LN (Lien)
- Set date range
- Results table: Direct Name, Indirect Name, Record Date, Doc Type, Instrument #
- 1,263 records found (LN doc type, all dates)
- Paginated: 50 per page, 26 pages
- Export to CSV button available

Usage:
  python -m app.workers.scrape_lake_liens --days-back 180
  python -m app.workers.scrape_lake_liens --days-back 7 --no-db --visible
"""
from __future__ import annotations

import argparse, json, os, re, time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

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

COUNTY_NAME  = "Lake"
SOURCE_NAME  = "lake_liens"
HASH_PREFIX  = "lake"
HOME_URL     = "https://officialrecords.lakecountyclerk.org/"
SEARCH_URL   = "https://officialrecords.lakecountyclerk.org/search/SearchTypeDocType"

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR  = BASE_DIR / "data" / "raw" / "lake" / "liens"
DBG_DIR  = RAW_DIR / "debug"
PDF_DIR  = RAW_DIR / "pdfs"
for d in [RAW_DIR, DBG_DIR, PDF_DIR]:
    d.mkdir(parents=True, exist_ok=True)

IRS_KEYWORDS = {
    "INTERNAL REVENUE", "IRS", "UNITED STATES", "US TREASURY",
    "FLORIDA DEPT", "FL DEPT", "DEPARTMENT OF REVENUE", "STATE OF FLORIDA",
    "FLORIDA DEPARTMENT", "CITY OF", "COUNTY OF",
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

def accept_disclaimer(driver):
    """Accept the Acclaim disclaimer to get search access."""
    driver.get(HOME_URL)
    time.sleep(3)
    try:
        btn = driver.find_element(By.ID, "btnButton")
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(2)
        print(f"  Disclaimer accepted — {driver.current_url}")
    except Exception:
        print(f"  Disclaimer already accepted or not found")

def scrape(driver, start: date, end: date) -> List[dict]:
    start_str = start.strftime("%m/%d/%Y")
    end_str   = end.strftime("%m/%d/%Y")

    # Navigate directly to Doc Type search
    print(f"  Loading Doc Type search...")
    driver.get(SEARCH_URL)
    time.sleep(3)
    save_debug(driver, "01_doctype_search")

    # Dump inputs to find field IDs
    inputs = driver.execute_script("""
        var r = [];
        document.querySelectorAll('input,select').forEach(function(el) {
            if (el.offsetHeight > 0 || el.type === 'hidden') {
                var opts = [];
                if (el.tagName === 'SELECT') {
                    Array.from(el.options).forEach(function(o) {
                        if (o.text.trim()) opts.push(o.value + ':' + o.text.trim());
                    });
                }
                r.push({tag:el.tagName, id:el.id, name:el.name,
                    type:el.type, value:el.value, ph:el.placeholder, opts:opts});
            }
        });
        return r;
    """)
    print(f"  Inputs ({len(inputs)}):")
    for i in inputs:
        print(f"    id={i['id']!r} name={i['name']!r} val={i['value']!r}"
              f"{' OPTS='+str(i['opts'][:8]) if i['opts'] else ''}")

    # Doc type is a custom widget — id='DocTypesDisplay-input' val='All'
    # Need to click it, find LIEN option, select it
    lien_set = False
    try:
        # Click the custom doc type input to open its dropdown
        doc_input = driver.find_element(By.ID, "DocTypesDisplay-input")
        driver.execute_script("arguments[0].click();", doc_input)
        time.sleep(1)
        # Clear and type LIEN to filter
        doc_input.clear()
        doc_input.send_keys("LIEN")
        time.sleep(1)
        # Look for dropdown option containing LIEN
        for xpath in [
            "//li[contains(text(),'LIEN') and not(contains(text(),'LIS'))]",
            "//div[contains(@class,'option') and contains(text(),'LIEN')]",
            "//span[contains(text(),'LIEN') and not(contains(text(),'LIS'))]",
            "//*[contains(@class,'item') and contains(text(),'LIEN')]",
        ]:
            try:
                opt = driver.find_element(By.XPATH, xpath)
                driver.execute_script("arguments[0].click();", opt)
                lien_set = True
                print(f"  Doc Type = LIEN (via dropdown option)")
                break
            except Exception:
                continue
        if not lien_set:
            # Try clicking the 'LIEN (LN)' option directly
            # Available options confirmed: 'LIEN (LN)', 'LIENS'
            for lien_text in ['LIEN (LN)', 'LIENS', 'LIEN']:
                for xpath in [
                    f"//*[normalize-space(text())='{lien_text}']",
                    f"//*[contains(text(),'{lien_text}')]",
                ]:
                    try:
                        opt = driver.find_element(By.XPATH, xpath)
                        if opt.is_displayed():
                            driver.execute_script("arguments[0].click();", opt)
                            time.sleep(0.5)
                            actual = driver.execute_script(
                                "return document.getElementById('DocTypesDisplay-input').value;")
                            print(f"  Doc Type clicked {lien_text!r} → input={actual!r}")
                            lien_set = True
                            break
                    except Exception:
                        continue
                if lien_set:
                    break
    except Exception as e:
        print(f"  Doc type error: {e}")

    # Dump all options available in the doc type widget
    try:
        all_opts = driver.execute_script("""
            var opts = [];
            document.querySelectorAll('li,option,[role=option]').forEach(function(el) {
                var t = el.innerText.trim();
                if (t && t.length < 30) opts.push(t);
            });
            return opts.filter(function(v,i,a){return a.indexOf(v)===i;}).slice(0,30);
        """)
        print(f"  Available doc types: {all_opts}")
    except Exception:
        pass

    # Set date range
    date_from_set = date_to_set = False
    for fid in ["StartDate", "DateFrom", "startDate", "dateFrom",
                "BeginDate", "beginDate", "fromDate", "RecordDateFrom"]:
        try:
            el = driver.find_element(By.ID, fid)
            driver.execute_script("arguments[0].value = arguments[1];", el, start_str)
            date_from_set = True
            print(f"  Date From = {start_str} (id={fid})")
            break
        except Exception:
            continue

    for fid in ["EndDate", "DateTo", "endDate", "dateTo",
                "ToDate", "toDate", "RecordDateTo"]:
        try:
            el = driver.find_element(By.ID, fid)
            driver.execute_script("arguments[0].value = arguments[1];", el, end_str)
            date_to_set = True
            print(f"  Date To   = {end_str} (id={fid})")
            break
        except Exception:
            continue

    print(f"  lien={lien_set} date_from={date_from_set} date_to={date_to_set}")

    # Submit search
    submitted = False
    for by, sel in [
        (By.XPATH, "//button[contains(text(),'Search') or contains(text(),'Submit')]"),
        (By.XPATH, "//input[@type='submit']"),
        (By.CSS_SELECTOR, "button[type='submit']"),
    ]:
        try:
            btn = driver.find_element(by, sel)
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                submitted = True
                print(f"  Submitted")
                break
        except Exception:
            continue

    if not submitted:
        print("  WARNING: Could not submit")
        return []

    time.sleep(5)
    save_debug(driver, "02_results")

    # Dump page after submit to see results
    page_info = driver.execute_script("""
        var tables = [];
        document.querySelectorAll('table').forEach(function(t) {
            var rows = t.querySelectorAll('tr');
            if (rows.length > 1) {
                tables.push({id:t.id, cls:t.className.substring(0,30), rows:rows.length,
                    sample:rows[1]?rows[1].innerText.trim().substring(0,80):''});
            }
        });
        return {url:window.location.href, tables:tables,
                body:document.body.innerText.substring(0,400)};
    """)
    print(f"  URL: {page_info['url']}")
    print(f"  Tables: {page_info['tables']}")
    print(f"  Body: {page_info['body'][:300]!r}")

    # Try CSV export
    import os
    download_start = __import__('time').time()
    try:
        csv_btn = driver.find_element(By.XPATH,
            "//a[contains(text(),'Export to CSV') or contains(text(),'CSV') or contains(text(),'csv')]"
            " | //button[contains(text(),'CSV')]"
            " | //input[contains(@value,'CSV') or contains(@value,'Export')]")
        driver.execute_script("arguments[0].click();", csv_btn)
        time.sleep(8)
        print("  Clicked CSV export")
        # Check downloads
        import glob, shutil as _shutil
        search_dirs = [
            str(RAW_DIR / "*.csv"),
            str(Path.home() / "Downloads" / "*.csv"),
            "C:/Users/*/Downloads/*.csv",
        ]
        for pattern in search_dirs:
            for f in glob.glob(pattern):
                if os.path.getmtime(f) > download_start - 10:
                    print(f"  CSV downloaded: {f}")
                    import pandas as pd
                    df = pd.read_csv(f, dtype=str).fillna("")
                    print(f"  CSV columns: {list(df.columns)[:8]}")
                    # Filter to LN doc type only
                    if 'DocType' in df.columns:
                        df = df[df['DocType'].str.strip().isin(['LN','LIEN','LN '])]
                        print(f"  After LN filter: {len(df)} rows")
                    # Save to RAW_DIR
                    dest = RAW_DIR / f"lake_liens_{nowstamp()}.csv"
                    _shutil.copy2(f, dest)
                    return df.to_dict("records")
    except Exception as e:
        print(f"  CSV export: {e}")

    # Parse HTML table pages
    all_rows = []
    page = 1

    while True:
        rows = driver.execute_script("""
            var results = [];
            // Find the results table — largest table or one with 'Instrument' header
            var tables = document.querySelectorAll('table');
            var best = null;
            tables.forEach(function(t) {
                if (t.innerText.indexOf('Instrument') >= 0 ||
                    t.innerText.indexOf('Direct Name') >= 0) {
                    best = t;
                }
            });
            if (!best) {
                // Fallback: largest table
                var maxRows = 0;
                tables.forEach(function(t) {
                    var n = t.querySelectorAll('tbody tr').length;
                    if (n > maxRows) { maxRows = n; best = t; }
                });
            }
            if (!best) return results;

            // Get headers
            var headers = [];
            var hrow = best.querySelector('thead tr, tr:first-child');
            if (hrow) {
                hrow.querySelectorAll('th,td').forEach(function(c) {
                    headers.push(c.innerText.trim());
                });
            }

            // Get data rows
            var rows = best.querySelectorAll('tbody tr');
            if (!rows.length) rows = best.querySelectorAll('tr:not(:first-child)');
            rows.forEach(function(row) {
                var cells = row.querySelectorAll('td');
                if (cells.length < 3) return;
                var rec = {};
                cells.forEach(function(c, i) {
                    var key = headers[i] || ('col' + i);
                    rec[key] = c.innerText.trim();
                });
                // Get doc link
                var link = row.querySelector('a[href]');
                if (link) rec['_url'] = link.href;
                if (rec['Direct Name'] || rec['Indirect Name'] ||
                    rec['col2'] || rec['col3']) {
                    results.push(rec);
                }
            });
            return results;
        """)

        if page == 1:
            print(f"  Page {page}: {len(rows)} rows")
            if rows:
                print(f"  Columns: {list(rows[0].keys())[:8]}")
                print(f"  Sample: {dict(list(rows[0].items())[:4])}")
        else:
            print(f"  Page {page}: {len(rows)} rows")

        all_rows.extend(rows)
        if not rows:
            break

        # Next page
        try:
            nxt = driver.find_element(By.XPATH,
                "//a[normalize-space(text())='next' or normalize-space(text())='Next' "
                "or normalize-space(text())='>']")
            if 'disabled' in (nxt.get_attribute('class') or ''):
                break
            driver.execute_script("arguments[0].click();", nxt)
            time.sleep(2)
            page += 1
            if page > 100:
                break
        except Exception:
            break

    return all_rows


def parse_record(row: dict) -> Optional[dict]:
    # Confirmed column names from inspector:
    # Direct Name, Indirect Name, Record Date, Doc Type, Instrument #
    # Confirmed CSV columns: DirectName, IndirectName, RecordDate, DocType,
    # BookType, BookPage, InstrumentNumber
    grantor   = str(row.get("DirectName", row.get("Direct Name", ""))).strip()
    grantee   = str(row.get("IndirectName", row.get("Indirect Name", ""))).strip()
    rec_date  = str(row.get("RecordDate", row.get("Record Date", ""))).strip()
    instr_num = str(row.get("InstrumentNumber", row.get("Instrument #", ""))).strip()

    if not grantor and not grantee:
        return None

    g_upper  = grantee.upper()
    gr_upper = grantor.upper()
    if any(k in g_upper for k in IRS_KEYWORDS):
        debtor, creditor = grantor, grantee
    elif any(k in gr_upper for k in IRS_KEYWORDS):
        debtor, creditor = grantee, grantor
    else:
        debtor, creditor = grantor, grantee

    if not debtor or len(debtor.strip()) < 2:
        return None

    filed = None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            filed = datetime.strptime(rec_date.split()[0], fmt).date()
            break
        except Exception:
            pass

    return {
        "instrument_number": instr_num,
        "debtor_name":       debtor.title(),
        "creditor_name":     creditor.title() if creditor else None,
        "lien_type":         "federal_tax_lien",
        "filed_date":        filed,
        "raw_payload":       row,
    }


def get_county_id(cur):
    cur.execute("SELECT id FROM counties WHERE county_name = %s", (COUNTY_NAME,))
    r = cur.fetchone()
    if r: return r[0]
    cur.execute("INSERT INTO counties (county_name,state,active,created_at) VALUES(%s,'FL',true,NOW()) RETURNING id", (COUNTY_NAME,))
    return cur.fetchone()[0]


def download_pdf_lake(driver, instr_num: str) -> Optional[str]:
    """Download PDF for a Lake County lien via Acclaim portal."""
    import base64
    if not instr_num:
        return None
    safe     = re.sub(r"[^\w\-]", "_", instr_num)[:60]
    pdf_path = PDF_DIR / f"lake_{safe}.pdf"
    if pdf_path.exists() and pdf_path.stat().st_size > 500:
        return str(pdf_path)
    # Lake Acclaim URL needs bookType + instrumentNumber
    # BookType from CSV is in 'BookType' column (e.g. 'O' for Official Records)
    book_type = "O"  # Official Records is the default for liens
    url = f"https://officialrecords.lakecountyclerk.org/Details/GetDocumentByInstrumentNumber/{book_type}/{instr_num}"
    try:
        driver.get(url)
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


def import_records(records):
    if not records or not get_connection:
        return {"inserted": 0, "skipped": 0}
    conn = get_connection()
    conn.autocommit = False
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    try:
        with conn.cursor() as cur:
            county_id = get_county_id(cur)
            for rec in records:
                if not rec.get("debtor_name"):
                    stats["skipped"] += 1; continue
                instr = rec.get("instrument_number", "")
                source_id = f"{SOURCE_NAME}::{instr or rec['debtor_name'][:30]}"
                payload   = json.dumps(rec["raw_payload"], default=str)
                raw_id = None
                try:
                    cur.execute("""
                        INSERT INTO raw_liens(county_id,source_file,source_record_id,raw_payload,filed_date)
                        VALUES(%s,%s,%s,%s::jsonb,%s)
                        ON CONFLICT(county_id,source_record_id) DO UPDATE SET raw_payload=EXCLUDED.raw_payload
                        RETURNING id
                    """, (county_id, SOURCE_NAME, source_id, payload, rec["filed_date"]))
                    r = cur.fetchone()
                    if r: raw_id = r[0]
                except Exception:
                    conn.rollback(); stats["skipped"] += 1; continue
                n_hash = f"{HASH_PREFIX}::{instr}::{rec['debtor_name'][:40]}"
                try:
                    pdf_val = (rec.get("pdf_path") or "")[:250] or None
                    cur.execute("""
                        INSERT INTO normalized_liens(county_id,raw_lien_id,debtor_name,lien_type,filed_date,normalized_hash,pdf_path)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(normalized_hash) DO UPDATE SET
                            debtor_name=EXCLUDED.debtor_name,
                            filed_date=COALESCE(EXCLUDED.filed_date,normalized_liens.filed_date),
                            pdf_path=COALESCE(EXCLUDED.pdf_path,normalized_liens.pdf_path)
                    """, (county_id, raw_id, rec["debtor_name"], rec["lien_type"], rec["filed_date"], n_hash, pdf_val))
                    stats["inserted"] += 1
                except Exception as e:
                    conn.rollback(); print(f"  Insert error: {e}"); stats["skipped"] += 1; continue
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description="Lake County lien scraper")
    parser.add_argument("--days-back", type=int, default=180)
    parser.add_argument("--no-db",  action="store_true")
    parser.add_argument("--no-pdf", action="store_true")
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()

    end   = date.today()
    start = end - timedelta(days=args.days_back)
    print(f"\n[Lake IRS Liens] {start} → {end}")

    driver = make_driver(visible=args.visible)
    raw_rows = []
    try:
        accept_disclaimer(driver)
        raw_rows = scrape(driver, start, end)
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()
        save_debug(driver, "error")
    finally:
        driver.quit()

    records = []
    seen = set()
    for row in raw_rows:
        rec = parse_record(row)
        if rec and rec.get("instrument_number") not in seen:
            seen.add(rec.get("instrument_number", id(rec)))
            records.append(rec)

    print(f"\n  Total scraped: {len(records)}")
    if records:
        snap = RAW_DIR / f"lake_liens_{nowstamp()}.json"
        snap.write_text(json.dumps([{
            "instrument": r["instrument_number"],
            "debtor": r["debtor_name"],
            "filed_date": str(r["filed_date"]),
        } for r in records], indent=2, default=str), encoding="utf-8")
        print(f"  Saved: {snap.name}")
        print("\n  Sample:")
        for r in records[:5]:
            print(f"    {r['instrument_number']} | {r['debtor_name']} | {r['filed_date']}")

    # Download PDFs
    if not args.no_pdf and records:
        print(f"  Downloading PDFs for {len(records)} liens...")
        driver2 = make_driver(visible=args.visible)
        try:
            accept_disclaimer(driver2)
            pdf_count = 0
            for i, rec in enumerate(records):
                instr = rec.get("instrument_number", "")
                if instr:
                    pdf_path = download_pdf_lake(driver2, instr)
                    if pdf_path:
                        rec["pdf_path"] = pdf_path
                        pdf_count += 1
                if (i + 1) % 20 == 0:
                    print(f"    PDFs: {pdf_count}/{i+1}")
            print(f"  PDFs: {pdf_count}/{len(records)}")
        except Exception as e:
            print(f"  PDF error: {e}")
        finally:
            driver2.quit()

    stats = {"inserted": 0, "skipped": 0}
    if not args.no_db and records:
        stats = import_records(records)

    print(f"\n--- Lake Summary ---")
    print(f"  Scraped  : {len(records)}")
    print(f"  Inserted : {stats.get('inserted',0)}")
    print(f"  Skipped  : {stats.get('skipped',0)}")


if __name__ == "__main__":
    main()