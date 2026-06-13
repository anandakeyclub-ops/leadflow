"""
scrape_sunbiz_liens.py
======================
Scrapes Florida federal tax lien registrations from Sunbiz
using Selenium (site blocks requests).

URL: https://dos.sunbiz.org/lienlis.html

Covers ALL 67 Florida counties in one place.

Usage:
  python -m app.workers.scrape_sunbiz_liens --search "Smith"
  python -m app.workers.scrape_sunbiz_liens --bulk          # A-Z scrape
  python -m app.workers.scrape_sunbiz_liens --bulk --no-db  # test only
"""
from __future__ import annotations

import argparse, json, re, time
from datetime import datetime, date
from pathlib import Path
from typing import Optional

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

SEARCH_URL = "https://dos.sunbiz.org/lienlis.html"
RAW_DIR    = Path("data/raw/sunbiz/liens")
RAW_DIR.mkdir(parents=True, exist_ok=True)

FL_COUNTY_CITIES = {
    "miami": "Miami-Dade", "hialeah": "Miami-Dade", "homestead": "Miami-Dade",
    "fort lauderdale": "Broward", "hollywood": "Broward", "pompano": "Broward",
    "west palm beach": "Palm Beach", "boca raton": "Palm Beach", "delray": "Palm Beach",
    "tampa": "Hillsborough", "brandon": "Hillsborough", "plant city": "Hillsborough",
    "st petersburg": "Pinellas", "clearwater": "Pinellas", "largo": "Pinellas",
    "orlando": "Orange", "kissimmee": "Osceola", "sanford": "Seminole",
    "jacksonville": "Duval", "fort myers": "Lee", "cape coral": "Lee",
    "sarasota": "Sarasota", "bradenton": "Manatee", "venice": "Sarasota",
    "naples": "Collier", "marco island": "Collier",
    "lakeland": "Polk", "winter haven": "Polk",
    "ocala": "Marion", "gainesville": "Alachua",
    "daytona": "Volusia", "deltona": "Volusia", "port orange": "Volusia",
    "pensacola": "Escambia", "tallahassee": "Leon",
    "new port richey": "Pasco", "dade city": "Pasco",
    "tavares": "Lake", "leesburg": "Lake", "eustis": "Lake",
    "stuart": "Martin", "jupiter": "Martin",
    "st augustine": "St. Johns", "ponte vedra": "St. Johns",
}

def make_driver(visible=False):
    opts = Options()
    if not visible:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1440,900")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    if HAS_WDM:
        return webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), options=opts)
    return webdriver.Chrome(options=opts)

def search_sunbiz(driver, name_prefix: str) -> list[dict]:
    """Search Sunbiz federal lien registry by debtor name prefix."""
    results = []
    try:
        driver.get(SEARCH_URL)
        time.sleep(2)

        # Find search input — Sunbiz shows "Debtor Name Search" input
        inp = None
        for css in ["input[type='text']", "input[name*='debtor']",
                    "input[name*='name']", "#debtorName"]:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, css)
                for el in els:
                    if el.is_displayed() and el.is_enabled():
                        inp = el
                        break
                if inp:
                    break
            except Exception:
                pass

        if not inp:
            # Dump all inputs for debug
            inputs = driver.execute_script("""
                return Array.from(document.querySelectorAll('input')).map(function(el) {
                    return {name:el.name, id:el.id, type:el.type,
                            ph:el.placeholder, visible:el.offsetParent!==null};
                });
            """)
            print(f"    Visible inputs: {[i for i in inputs if i['visible']][:5]}")
            return []

        # Clear and type search term
        inp.clear()
        inp.send_keys(name_prefix)
        time.sleep(0.3)

        # Set state to FL if dropdown exists
        try:
            state_sel = driver.find_element(By.CSS_SELECTOR,
                "select[name*='state'], select[name*='State'], #state")
            from selenium.webdriver.support.ui import Select
            sel = Select(state_sel)
            sel.select_by_value("FL")
        except Exception:
            pass

        # Submit form
        try:
            btn = driver.find_element(By.CSS_SELECTOR,
                "input[type='submit'], button[type='submit']")
            driver.execute_script("arguments[0].click();", btn)
        except Exception:
            inp.submit()

        time.sleep(3)

        # Extract results from all pages
        page = 1
        while True:
            rows = driver.execute_script("""
                var results = [];
                var tables = document.querySelectorAll('table');
                var best = null; var bestN = 0;
                tables.forEach(function(t) {
                    var n = t.querySelectorAll('tr').length;
                    if (n > bestN) { bestN = n; best = t; }
                });
                if (!best || bestN < 2) return results;

                var headers = [];
                var hrow = best.querySelector('tr');
                if (hrow) {
                    hrow.querySelectorAll('th,td').forEach(function(c) {
                        headers.push((c.innerText||'').trim());
                    });
                }

                var rows = best.querySelectorAll('tbody tr, tr:not(:first-child)');
                rows.forEach(function(row) {
                    var cells = row.querySelectorAll('td');
                    if (cells.length < 3) return;
                    var rec = {};
                    cells.forEach(function(c, i) {
                        rec[headers[i] || 'col' + i] = (c.innerText||c.textContent||'').trim();
                    });
                    if (Object.values(rec).some(function(v) { return v.length > 2; })) {
                        results.push(rec);
                    }
                });
                return results;
            """)

            if page == 1 and rows:
                print(f"    Headers: {list(rows[0].keys())[:6]}")
                print(f"    Sample: {dict(list(rows[0].items())[:4])}")

            results.extend(rows)
            print(f"    Page {page}: {len(rows)} records")

            # Next page
            try:
                nxt = driver.find_element(By.XPATH,
                    "//a[contains(text(),'Next') or contains(text(),'next') or contains(text(),'>')]")
                if nxt.is_displayed():
                    driver.execute_script("arguments[0].click();", nxt)
                    time.sleep(2)
                    page += 1
                else:
                    break
            except Exception:
                break

    except Exception as e:
        print(f"    Search error: {e}")

    return results


def parse_record(row: dict) -> Optional[dict]:
    """Parse a Sunbiz lien row into a normalized record.
    Confirmed columns: Name, City, State, FEI/EIN #, Status
    Status: T=terminated/released, L=active lien
    """
    # Confirmed field names from screenshot
    debtor  = str(row.get("Name") or row.get("Debtor Name") or
                  row.get("col0") or "").strip()
    city    = str(row.get("City") or row.get("col1") or "").strip()
    state   = str(row.get("State") or row.get("col2") or "FL").strip()
    fein    = str(row.get("FEI/EIN #") or row.get("FEI") or
                  row.get("col3") or "").strip()
    status  = str(row.get("Status") or row.get("col4") or "").strip()

    # Use FEIN as document number (unique identifier)
    doc_num = fein if fein and fein != "XXXXX" else ""
    if not doc_num:
        doc_num = f"{debtor[:20]}_{city}"  # fallback unique key

    address = ""
    filed   = ""
    amount  = ""

    # Skip terminated/released liens (Status T = terminated)
    # Status L = lien active — these are our targets
    # Keep both for now, filter later
    

    if not debtor or len(debtor) < 2:
        return None

    # Parse date
    filed_date = None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            filed_date = datetime.strptime(filed.split()[0], fmt).date()
            break
        except Exception:
            pass

    # Parse amount
    amount_val = None
    try:
        amount_val = float(re.sub(r"[^\d.]", "", amount)) if amount else None
    except Exception:
        pass

    # Guess county from city
    county = None
    city_lower = city.lower()
    for city_key, county_name in FL_COUNTY_CITIES.items():
        if city_key in city_lower:
            county = county_name
            break

    return {
        "document_number": doc_num,
        "debtor_name":     debtor.title(),
        "address":         address,
        "city":            city,
        "state":           state,
        "county_name":     county,
        "filed_date":      filed_date,
        "amount":          amount_val,
        "lien_type":       "federal_tax_lien",
        "source":          "sunbiz",
    }


def ensure_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sunbiz_liens (
            id              SERIAL PRIMARY KEY,
            document_number TEXT UNIQUE,
            debtor_name     TEXT,
            address         TEXT,
            city            TEXT,
            state           TEXT DEFAULT 'FL',
            county_name     TEXT,
            filed_date      DATE,
            amount          NUMERIC(12,2),
            lien_type       TEXT DEFAULT 'federal_tax_lien',
            imported_at     TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_sunbiz_debtor
        ON sunbiz_liens(debtor_name)
    """)


def import_records(cur, records: list[dict]) -> dict:
    inserted = skipped = 0
    for rec in records:
        if not rec or not rec.get("debtor_name"):
            skipped += 1
            continue
        try:
            cur.execute("""
                INSERT INTO sunbiz_liens
                    (document_number, debtor_name, address, city, state,
                     county_name, filed_date, amount, lien_type)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (document_number) DO NOTHING
            """, (
                rec["document_number"], rec["debtor_name"],
                rec["address"], rec["city"], rec["state"],
                rec["county_name"], rec["filed_date"],
                rec["amount"], rec["lien_type"],
            ))
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            skipped += 1
    return {"inserted": inserted, "skipped": skipped}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--search",  default=None)
    parser.add_argument("--bulk",    action="store_true")
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--no-db",   action="store_true")
    args = parser.parse_args()

    if args.bulk:
        prefixes = [chr(i) for i in range(ord('A'), ord('Z')+1)]
    elif args.search:
        prefixes = [args.search]
    else:
        print("Use --search 'Name' or --bulk")
        return

    driver = make_driver(visible=args.visible or True)  # always visible for Sunbiz
    conn   = None

    if not args.no_db and get_connection:
        conn = get_connection()
        conn.autocommit = False
        with conn.cursor() as cur:
            ensure_table(cur)
        conn.commit()

    total_inserted = total_records = 0

    try:
        for prefix in prefixes:
            print(f"\n  Searching: '{prefix}...' in FL")
            raw_rows = search_sunbiz(driver, prefix)
            print(f"    Found: {len(raw_rows)} raw rows")

            records = []
            seen = set()
            for row in raw_rows:
                rec = parse_record(row)
                if rec:
                    key = rec.get("document_number") or rec["debtor_name"]
                    if key not in seen:
                        seen.add(key)
                        records.append(rec)

            total_records += len(records)
            print(f"    Parsed: {len(records)} unique records")

            # Save JSON
            ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = RAW_DIR / f"sunbiz_{prefix}_{ts}.json"
            out.write_text(
                json.dumps(records, default=str, indent=2),
                encoding="utf-8")

            if conn and records:
                with conn.cursor() as cur:
                    stats = import_records(cur, records)
                conn.commit()
                total_inserted += stats["inserted"]
                print(f"    Inserted: {stats['inserted']}")

            time.sleep(3)  # polite rate limit

    finally:
        driver.quit()
        if conn:
            conn.close()

    print(f"\n{'='*60}")
    print(f"  Total records  : {total_records}")
    print(f"  Total inserted : {total_inserted}")


if __name__ == "__main__":
    main()