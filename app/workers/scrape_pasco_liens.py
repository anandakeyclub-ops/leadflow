"""
scrape_pasco_liens.py
=====================
Pasco County lien scraper.
Portal: https://app.pascoclerk.com/appdot-public-online-services-forms-or-search.asp

From inspector 2026-05-10:
- Name search with docset=LIEN + fromdate/todate date inputs
- No CAPTCHA, no login, no disclaimer
- Fields: fromdate (YYYY-MM-DD), todate (YYYY-MM-DD), docset=LIEN, name='', namedir=A
- Submit: Search by Name button

Strategy: search with empty name to get ALL liens in date range

Usage:
  python -m app.workers.scrape_pasco_liens --days-back 180
  python -m app.workers.scrape_pasco_liens --days-back 7 --no-db --visible
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
from selenium.webdriver.support.ui import Select

try:
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_WDM = True
except ImportError:
    HAS_WDM = False

try:
    from app.core.db import get_connection
except ImportError:
    get_connection = None

COUNTY_NAME = "Pasco"
SOURCE_NAME = "pasco_liens"
HASH_PREFIX = "pasco"
SEARCH_URL  = "https://app.pascoclerk.com/appdot-public-online-services-forms-or-search.asp"

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR  = BASE_DIR / "data" / "raw" / "pasco" / "liens"
DBG_DIR  = RAW_DIR / "debug"
PDF_DIR  = RAW_DIR / "pdfs"
for d in [RAW_DIR, DBG_DIR, PDF_DIR]:
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
    SEARCH_TERMS = [
        "INTERNAL REVENUE",
        "FLORIDA DEPARTMENT OF REVENUE",
        "STATE OF FLORIDA",
        "UNITED STATES",
        "DEPARTMENT OF REVENUE",
    ]
    CHUNK_DAYS = 30
    all_rows = []
    seen_instruments = set()

    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end)
        cs = chunk_start.strftime("%Y-%m-%d")
        ce = chunk_end.strftime("%Y-%m-%d")
        print(f"\n  Chunk: {cs} → {ce}")

        for creditor in SEARCH_TERMS:
            driver.get(SEARCH_URL)
            time.sleep(2)

            # Set dates
            for fid, val in [("fromdate", cs), ("todate", ce)]:
                el = driver.find_element(By.ID, fid)
                driver.execute_script("arguments[0].value=arguments[1];", el, val)

            # Set docset = LIEN
            driver.execute_script("""
                document.querySelectorAll("select[name='docset']").forEach(function(sel) {
                    Array.from(sel.options).forEach(function(o) {
                        if (o.text.trim() === 'LIEN') sel.value = o.value;
                    });
                });
            """)

            # Set creditor name
            name_el = driver.find_element(By.ID, "name")
            driver.execute_script("arguments[0].value=arguments[1];", name_el, creditor)

            # Submit
            btn = driver.find_element(By.XPATH, "//input[@value='Search by Name']")
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(4)

            # Parse results
            page_rows = driver.execute_script("""
                var results = [];
                var tables = document.querySelectorAll('table');
                var best = null, bestN = 0;
                tables.forEach(function(t) {
                    var n = t.querySelectorAll('tr').length;
                    if (n > bestN && t.innerText.indexOf('Instrument') >= 0) {
                        bestN = n; best = t;
                    }
                });
                if (!best) return [];
                var headers = [];
                var hrow = best.querySelector('tr');
                if (hrow) hrow.querySelectorAll('th,td').forEach(function(c){
                    headers.push((c.innerText||'').trim());
                });
                var rows = best.querySelectorAll('tr:not(:first-child)');
                rows.forEach(function(row) {
                    var cells = row.querySelectorAll('td');
                    if (cells.length < 2) return;
                    var rec = {};
                    cells.forEach(function(c,i){
                        rec[headers[i]||'col_'+i]=(c.innerText||c.textContent||'').trim();
                    });
                    var link = row.querySelector('a[href]');
                    if (link) rec['_url'] = link.href;
                    results.push(rec);
                });
                return results;
            """)

            new_rows = [r for r in page_rows
                        if r.get('Instrument') and r['Instrument'] not in seen_instruments]
            if page_rows and not new_rows:
                for row in page_rows:
                    instr = next((v for k,v in row.items()
                                  if k not in ('_url',) and v and
                                  __import__('re').match(r'\d{8,}', str(v))), None)
                    if instr and instr not in seen_instruments:
                        seen_instruments.add(instr)
                        new_rows.append(row)
            else:
                for r in new_rows:
                    seen_instruments.add(r['Instrument'])

            all_rows.extend(new_rows)
            print(f"  '{creditor}': {len(new_rows)} new liens ({len(page_rows)} total)")
            if new_rows and len(all_rows) <= len(new_rows):
                print(f"  Columns: {list(new_rows[0].keys())[:8]}")
                print(f"  Sample: {dict(list(new_rows[0].items())[:4])}")

        chunk_start = chunk_end + timedelta(days=1)

    return all_rows


def parse_record(row: dict) -> Optional[dict]:
    # Confirmed Pasco columns: Book, Cross-Party Name, Date, Document,
    # Instrument, Legal, Name, Page, Time
    # Name = creditor (IRS/FLDOR), Cross-Party Name = debtor
    instrument = str(row.get("Instrument") or "").strip()
    creditor   = str(row.get("Name") or "").strip()
    debtor     = str(row.get("Cross-Party Name") or "").strip()
    rec_date   = str(row.get("Date") or "").strip()

    if not instrument:
        return None
    if not debtor:
        debtor = creditor  # fallback

    if not debtor or len(debtor.strip()) < 2:
        return None

    filed = None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            filed = datetime.strptime(rec_date.split()[0], fmt).date()
            break
        except Exception:
            pass

    return {
        "instrument_number": instrument,
        "debtor_name":       debtor.strip().title(),
        "creditor_name":     creditor.strip().title() if creditor else None,
        "lien_type":         "federal_tax_lien",
        "filed_date":        filed,
        "doc_url":           row.get("_url", ""),
        "raw_payload":       row,
    }


def download_pdf(driver, rec: dict) -> Optional[str]:
    import base64
    url = rec.get("doc_url", "")
    instr = rec.get("instrument_number", "")
    if not url and not instr:
        return None
    safe     = re.sub(r"[^\w\-]", "_", instr)[:60]
    pdf_path = PDF_DIR / f"pasco_{safe}.pdf"
    if pdf_path.exists() and pdf_path.stat().st_size > 500:
        return str(pdf_path)
    try:
        target = url or f"https://app.pascoclerk.com/appdot-public-online-services-or-img.asp?instrnum={instr}"
        driver.get(target)
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
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
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
    print(f"\n[Pasco Liens] {start} → {end}")

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
        print(f"  Raw row keys: {list(raw_rows[0].keys())}")
        print(f"  Raw row sample: {raw_rows[0]}")
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
        snap = RAW_DIR / f"pasco_liens_{nowstamp()}.json"
        snap.write_text(json.dumps([{"i":r["instrument_number"],"d":r["debtor_name"],"f":str(r["filed_date"])} for r in records],indent=2,default=str),encoding="utf-8")
        print(f"  Saved: {snap.name}")
        for r in records[:5]:
            print(f"    {r['instrument_number']} | {r['debtor_name']} | {r['filed_date']}")

    stats = {"inserted":0,"skipped":0}
    if not args.no_db and records:
        try:
            stats = import_records(records)
        except Exception as e:
            print(f"  DB error: {e}")
            print(f"  Records saved to JSON — run with --no-db to skip DB")

    print(f"\n--- Pasco Summary ---")
    print(f"  Scraped  : {len(records)}")
    print(f"  Inserted : {stats.get('inserted',0)}")
    print(f"  Skipped  : {stats.get('skipped',0)}")

if __name__ == "__main__":
    main()