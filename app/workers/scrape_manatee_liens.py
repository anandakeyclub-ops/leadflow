"""
scrape_manatee_liens.py
=======================
Manatee County lien scraper.
Portal: records.manateeclerk.com/OfficialRecords/Search/InstrumentType

Confirmed from inspector 2026-05-10:
- URL pattern: /OfficialRecords/Search/InstrumentType/16/{start}/{end}/{page}/50
- Type 16 = LIEN
- Columns: Instrument, From, To, Type, Book, Page, Consideration, Description, Date, Pages
- 'From' = grantor/creditor (IRS), 'To' = debtor
- 20245 total liens in default 10-year window
- No login required, no CAPTCHA

Usage:
  python -m app.workers.scrape_manatee_liens --days-back 180
  python -m app.workers.scrape_manatee_liens --days-back 7 --no-db --visible
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

COUNTY_NAME  = "Manatee"
SOURCE_NAME  = "manatee_liens"
HASH_PREFIX  = "manatee"
BASE_URL     = "https://records.manateeclerk.com/OfficialRecords/Search/InstrumentType"
LIEN_TYPE_ID = 16   # confirmed: Type 16 = LIEN
PAGE_SIZE    = 50

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR  = BASE_DIR / "data" / "raw" / "manatee" / "liens"
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

def scrape_page(driver, url: str) -> List[dict]:
    driver.get(url)
    time.sleep(3)
    return driver.execute_script("""
        var results = [];
        // Find the results table
        var tables = document.querySelectorAll('table');
        var best = null, bestN = 0;
        tables.forEach(function(t) {
            var n = t.querySelectorAll('tbody tr, tr').length;
            if (n > bestN && t.innerText.indexOf('Instrument') >= 0) {
                bestN = n; best = t;
            }
        });
        if (!best) return results;

        // Get headers
        var headers = [];
        var hrow = best.querySelector('thead tr, tr');
        if (hrow) hrow.querySelectorAll('th,td').forEach(function(c) {
            headers.push((c.innerText||'').trim());
        });

        // Get data rows
        var rows = best.querySelectorAll('tbody tr');
        if (!rows.length) rows = best.querySelectorAll('tr:not(:first-child)');
        rows.forEach(function(row) {
            var cells = row.querySelectorAll('td');
            if (cells.length < 3) return;
            var rec = {};
            cells.forEach(function(c, i) {
                rec[headers[i] || 'col_' + i] = (c.innerText || c.textContent || '').trim();
            });
            // Get document link
            var link = row.querySelector('a[href*="Document"], a[href*="Instrument"]');
            if (link) rec['_doc_url'] = link.href;
            // Get view icon link
            var icon = row.querySelector('a');
            if (icon && icon.href) rec['_view_url'] = icon.href;
            if (rec['Instrument'] || rec['col_1']) results.push(rec);
        });
        return results;
    """)

def get_total_pages(driver) -> int:
    try:
        info = driver.execute_script("""
            var text = document.body.innerText;
            var m = text.match(/Showing\\s+\\d+\\s+to\\s+\\d+\\s+of\\s+(\\d+)/i);
            return m ? parseInt(m[1]) : 0;
        """)
        return info
    except Exception:
        return 0

def scrape(driver, start: date, end: date) -> List[dict]:
    start_str = start.strftime("%Y-%m-%d")
    end_str   = end.strftime("%Y-%m-%d")

    # First page to get total count
    url1 = f"{BASE_URL}/{LIEN_TYPE_ID}/{start_str}/{end_str}/1/{PAGE_SIZE}"
    print(f"  URL: {url1}")
    rows1 = scrape_page(driver, url1)
    save_debug(driver, "page1")

    if not rows1:
        body = driver.execute_script("return document.body.innerText.substring(0,300);")
        print(f"  No rows on page 1. Body: {body!r}")
        return []

    print(f"  Page 1: {len(rows1)} rows")
    if rows1:
        print(f"  Headers: {list(rows1[0].keys())}")
        print(f"  Full first row: {rows1[0]}")

    total = get_total_pages(driver)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 1
    print(f"  Total records: {total} | Pages: {total_pages}")

    all_rows = list(rows1)
    for page in range(2, min(total_pages + 1, 201)):  # cap at 200 pages = 10,000 records
        url = f"{BASE_URL}/{LIEN_TYPE_ID}/{start_str}/{end_str}/{page}/{PAGE_SIZE}"
        rows = scrape_page(driver, url)
        print(f"  Page {page}/{total_pages}: {len(rows)} rows")
        all_rows.extend(rows)
        if not rows:
            break
        time.sleep(1)

    return all_rows

def parse_record(row: dict) -> Optional[dict]:
    # Confirmed columns: Book, Consideration, Date, Description, From, Instrument, Page, Pages
    # From screenshot also shows: To column (may be named differently)
    instrument = str(row.get("Instrument") or row.get("col_1") or "").strip()
    from_name  = str(row.get("From") or row.get("col_5") or "").strip()
    to_name    = str(row.get("To") or row.get("col_2") or "").strip()
    rec_date   = str(row.get("Date") or row.get("col_3") or "").strip()

    if not instrument:
        return None

    fr_upper = from_name.upper()
    to_upper = to_name.upper()

    if any(k in fr_upper for k in IRS_KEYWORDS):
        debtor, creditor = to_name, from_name
    elif any(k in to_upper for k in IRS_KEYWORDS):
        debtor, creditor = from_name, to_name
    elif to_name and to_name != from_name:
        # From = creditor/filer, To = debtor
        debtor, creditor = to_name, from_name
    else:
        # Only From available — treat as debtor (skip known filers)
        skip_names = {"SIMPLIFILE", "CSC", "CT CORPORATION"}
        if from_name.upper() in skip_names:
            return None
        debtor, creditor = from_name, None

    filed = None
    for fmt in ("%m-%d-%Y", "%Y-%m-%d", "%m/%d/%Y"):
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
        "doc_url":           row.get("_doc_url") or row.get("_view_url") or "",
        "raw_payload":       row,
    }

def download_pdf(driver, rec: dict) -> Optional[str]:
    import base64
    instr = rec.get("instrument_number", "")
    if not instr:
        return None
    safe     = re.sub(r"[^\w\-]", "_", instr)[:60]
    pdf_path = PDF_DIR / f"manatee_{safe}.pdf"
    if pdf_path.exists() and pdf_path.stat().st_size > 500:
        return str(pdf_path)

    # Try view URL from results
    view_url = rec.get("doc_url", "")
    urls = [view_url] if view_url else []
    urls.append(f"https://records.manateeclerk.com/OfficialRecords/ViewDocument/{instr}")

    for url in urls:
        if not url:
            continue
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
            continue
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
    conn = get_connection()
    conn.autocommit = False
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    try:
        with conn.cursor() as cur:
            county_id = get_county_id(cur)
            for rec in records:
                if not rec.get("debtor_name"):
                    stats["skipped"] += 1; continue
                source_id = f"{SOURCE_NAME}::{rec['instrument_number']}"
                payload   = json.dumps(rec["raw_payload"], default=str)
                raw_id = None
                try:
                    cur.execute("""
                        INSERT INTO raw_liens(county_id,source_file,source_record_id,raw_payload,filed_date)
                        VALUES(%s,%s,%s,%s::jsonb,%s)
                        ON CONFLICT(county_id,source_record_id) DO UPDATE SET raw_payload=EXCLUDED.raw_payload
                        RETURNING id
                    """, (county_id, SOURCE_NAME, source_id, payload, rec["filed_date"]))
                    r = cur.fetchone(); raw_id = r[0] if r else None
                except Exception:
                    conn.rollback(); stats["skipped"] += 1; continue
                n_hash = f"{HASH_PREFIX}::{rec['instrument_number']}::{rec['debtor_name'][:40]}"
                pdf_val = (rec.get("pdf_path") or "")[:250] or None
                try:
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
    print(f"\n[Manatee Liens] {start} → {end}")

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
    for row in raw_rows:
        rec = parse_record(row)
        if rec and rec.get("instrument_number") not in seen:
            seen.add(rec.get("instrument_number", ""))
            records.append(rec)

    print(f"\n  Total scraped: {len(records)}")

    if not args.no_pdf and records:
        print(f"  Downloading PDFs...")
        driver2 = make_driver(visible=args.visible)
        try:
            pdf_count = 0
            for i, rec in enumerate(records):
                path = download_pdf(driver2, rec)
                if path:
                    rec["pdf_path"] = path
                    pdf_count += 1
                if (i + 1) % 20 == 0:
                    print(f"    {pdf_count}/{i+1}")
            print(f"  PDFs: {pdf_count}/{len(records)}")
        except Exception as e:
            print(f"  PDF error: {e}")
        finally:
            driver2.quit()

    if records:
        snap = RAW_DIR / f"manatee_liens_{nowstamp()}.json"
        snap.write_text(json.dumps([{
            "i": r["instrument_number"], "d": r["debtor_name"],
            "f": str(r["filed_date"])
        } for r in records], indent=2, default=str), encoding="utf-8")
        print(f"  Saved: {snap.name}")
        print("  Sample:")
        for r in records[:5]:
            print(f"    {r['instrument_number']} | {r['debtor_name']} | {r['filed_date']}")

    stats = {"inserted": 0, "skipped": 0}
    if not args.no_db and records:
        stats = import_records(records)

    print(f"\n--- Manatee Summary ---")
    print(f"  Scraped  : {len(records)}")
    print(f"  Inserted : {stats.get('inserted', 0)}")
    print(f"  Skipped  : {stats.get('skipped', 0)}")

if __name__ == "__main__":
    main()