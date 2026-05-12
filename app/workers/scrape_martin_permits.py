"""
scrape_martin_permits.py
==============================
Martin County building permits — ported from Permit_Bot download_martin_weekly.py.
Scraping logic unchanged. DB import uses LeadFlow normalized_permits format.

Usage:
  python -m app.workers.scrape_martin_permits --days-back 30
  python -m app.workers.scrape_martin_permits --days-back 180 --no-db
"""
from __future__ import annotations

"""
download_martin_weekly.py

Martin County building permit scraper.
Uses Selenium to submit the search, then requests+BeautifulSoup to parse.
Handles Accela's javascript:__doPostBack() pagination and detail links.

Output: data/raw/florida/martin/martin_permits_YYYY-MM-DD.csv
"""

import csv
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

print("STARTING download_martin_weekly.py")

BASE_URL = "https://aca-prod.accela.com"
HOME_URL = f"{BASE_URL}/MARTINCO/Cap/CapHome.aspx?TabName=Home&module=Building"

STATE          = "florida"
COUNTY         = "martin"
DAYS_BACK      = 7
SCRAPE_DETAILS = False  # Skip detail pages — they timeout

REQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
}

ID_START = "ctl00_PlaceHolderMain_generalSearchForm_txtGSStartDate"
ID_END   = "ctl00_PlaceHolderMain_generalSearchForm_txtGSEndDate"

# Extracts ('target', 'argument') from javascript:__doPostBack('target','arg')
POSTBACK_RE = re.compile(r"__doPostBack\('([^']+)','([^']*)'\)")


def ensure_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def build_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--no-sandbox")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


# ── Selenium: load, fill, submit ─────────────────────────────────────────────

def load_and_submit(start_str: str, end_str: str) -> tuple[str, dict, str]:
    """
    Open portal in Chrome, fill dates, submit search.
    Returns (results_html, cookies_dict, results_url).
    """
    from selenium.webdriver.common.action_chains import ActionChains

    driver = build_driver()
    wait   = WebDriverWait(driver, 20)

    try:
        driver.get(HOME_URL)
        time.sleep(4)
        print("Portal loaded.")

        el = wait.until(EC.presence_of_element_located((By.ID, ID_START)))
        el.click()
        el.send_keys(Keys.CONTROL + "a")
        el.send_keys(Keys.DELETE)
        el.send_keys(start_str)
        print(f"  Start date: {start_str}")

        el = driver.find_element(By.ID, ID_END)
        el.click()
        el.send_keys(Keys.CONTROL + "a")
        el.send_keys(Keys.DELETE)
        el.send_keys(end_str)
        print(f"  End date:   {end_str}")

        el.send_keys(Keys.TAB)
        time.sleep(0.5)

        search_btn = wait.until(EC.presence_of_element_located(
            (By.ID, "ctl00_PlaceHolderMain_btnNewSearch")
        ))
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", search_btn
        )
        time.sleep(0.3)
        ActionChains(driver).move_to_element(search_btn).click().perform()
        print("  Search clicked.")
        time.sleep(6)

        body = driver.find_element(By.TAG_NAME, "body").text.lower()
        if "--select--" in body and "record type:" in body:
            print("  Click did not submit — falling back to JS form submit...")
            driver.execute_script("""
                document.getElementById('__EVENTTARGET').value =
                    'ctl00$PlaceHolderMain$btnNewSearch';
                document.getElementById('__EVENTARGUMENT').value = '';
                document.getElementsByTagName('form')[0].submit();
            """)
            time.sleep(6)

        cookies      = {c["name"]: c["value"] for c in driver.get_cookies()}
        results_url  = driver.current_url
        results_html = driver.page_source
        print(f"  Results URL: {results_url}")
        print(f"  Cookies: {len(cookies)}")
        return results_html, cookies, results_url

    finally:
        driver.quit()
        print("Browser closed.")


# ── Hidden form state extraction ─────────────────────────────────────────────

def extract_form_state(html: str) -> dict:
    """Pull ASP.NET hidden fields from a page for subsequent POSTs."""
    soup = BeautifulSoup(html, "lxml")
    state = {}
    for field_id in [
        "__VIEWSTATE", "__VIEWSTATEGENERATOR",
        "__VIEWSTATEENCRYPTED", "__EVENTVALIDATION", "ACA_CS_FIELD",
    ]:
        el = soup.find("input", {"id": field_id})
        state[field_id] = el["value"] if el and el.get("value") else ""
    return state


def make_postback_payload(form_state: dict, event_target: str,
                          event_arg: str = "") -> dict:
    """Build a minimal ASP.NET postback payload."""
    return {
        "ACA_CS_FIELD":         form_state.get("ACA_CS_FIELD", ""),
        "__EVENTTARGET":        event_target,
        "__EVENTARGUMENT":      event_arg,
        "__LASTFOCUS":          "",
        "__VIEWSTATE":          form_state.get("__VIEWSTATE", ""),
        "__VIEWSTATEGENERATOR": form_state.get("__VIEWSTATEGENERATOR", ""),
        "__VIEWSTATEENCRYPTED": form_state.get("__VIEWSTATEENCRYPTED", ""),
        "__EVENTVALIDATION":    form_state.get("__EVENTVALIDATION", ""),
    }


# ── BeautifulSoup parsing ────────────────────────────────────────────────────

def find_results_table(soup: BeautifulSoup):
    """Find the permit results grid table."""
    # 1. ACA_GridView class (Accela standard)
    t = soup.find("table", class_=lambda c: c and "ACA_GridView" in c)
    if t:
        return t
    # 2. Table with permit-related headers
    for table in soup.find_all("table"):
        ths = " ".join(th.get_text(strip=True).lower()
                       for th in table.find_all("th"))
        if any(w in ths for w in [
            "record number", "permit", "address", "status", "record type"
        ]):
            return table
    return None


def parse_results(html: str) -> tuple[list[str], list[tuple[list[str], str, str]]]:
    """
    Parse a results page.
    Returns (headers, [(row_values, detail_postback_target, detail_postback_arg)])
    detail_postback_target/arg are empty strings if no postback found.
    """
    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True).lower()

    if "no record" in page_text or "0 record" in page_text:
        print("  No records on this page.")
        return [], []

    table = find_results_table(soup)
    if table is None:
        all_tables = soup.find_all("table")
        print(f"  No results table found. ({len(all_tables)} total tables)")
        for i, t in enumerate(all_tables[:6]):
            ths = [th.get_text(strip=True) for th in t.find_all("th")]
            print(f"    Table {i}: {len(t.find_all('tr'))} rows, "
                  f"headers={ths[:4]}")
        return [], []

    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    print(f"  Headers: {headers}")

    results = []
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        values = [td.get_text(" ", strip=True) for td in cells]
        if not any(v.strip() for v in values):
            continue

        # Extract detail postback target from any <a> in the row
        pb_target = ""
        pb_arg    = ""
        for a in tr.find_all("a"):
            text = (a.get("href", "") + " " + a.get("onclick", ""))
            m = POSTBACK_RE.search(text)
            if m:
                pb_target = m.group(1)
                pb_arg    = m.group(2)
                break

        results.append((values, pb_target, pb_arg))

    print(f"  Parsed {len(results)} rows "
          f"({sum(1 for _, t, _ in results if t)} with detail links).")
    return headers, results


def get_next_postback(soup: BeautifulSoup) -> tuple[str, str] | None:
    """
    Find the Next page postback target/arg.
    Returns (event_target, event_arg) or None.
    """
    for a in soup.find_all("a"):
        label = a.get_text(strip=True).lower()
        if label in ("next", ">", "next >", "next page"):
            text = (a.get("href", "") + " " + a.get("onclick", ""))
            m = POSTBACK_RE.search(text)
            if m:
                return m.group(1), m.group(2)
    return None


# ── Detail page fetching ─────────────────────────────────────────────────────

def fetch_detail(session: requests.Session, form_state: dict,
                 pb_target: str, pb_arg: str) -> dict:
    """POST to the detail page via postback and extract contractor info."""
    info = {"contractor": "", "license_number": "", "phone": "", "email": ""}
    if not pb_target:
        return info

    payload = make_postback_payload(form_state, pb_target, pb_arg)
    try:
        r = session.post(
            HOME_URL, data=payload,
            headers={**REQ_HEADERS, "Referer": HOME_URL},
            timeout=15,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        # Email
        m = soup.find("a", href=lambda h: h and "mailto:" in h)
        if m:
            info["email"] = m["href"].replace("mailto:", "").strip()

        # Label/value pairs
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            label = tds[0].get_text(" ", strip=True).lower()
            value = tds[-1].get_text(" ", strip=True)
            if not value:
                continue
            if not info["contractor"] and any(
                k in label for k in ["contractor", "licensed professional",
                                     "applicant", "business name"]
            ):
                info["contractor"] = value
            if not info["phone"] and "phone" in label:
                info["phone"] = value
            if not info["license_number"] and "license" in label:
                info["license_number"] = value

    except Exception as e:
        print(f"    Detail warning: {e}")

    return info


# ── Output ───────────────────────────────────────────────────────────────────

def save_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        if headers:
            writer.writerow(headers)
        writer.writerows(rows)
    print(f"Saved {len(rows)} records → {path}")


def update_counties_csv(base: Path, output_file: Path) -> None:
    counties_path = base / "config" / "counties.csv"
    if not counties_path.exists():
        return
    import pandas as pd
    df = pd.read_csv(counties_path, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    mask = (
        (df["state"].str.lower() == STATE) &
        (df["county"].str.lower() == COUNTY)
    )
    try:
        rel = str(output_file.relative_to(base)).replace("\\", "/")
    except ValueError:
        rel = output_file.name
    df.loc[mask, "input_filename"] = rel
    df.to_csv(counties_path, index=False)
    print(f"Updated counties.csv → {rel}")


# ── Main ─────────────────────────────────────────────────────────────────────


# LeadFlow constants
COUNTY_NAME = "Martin"
SOURCE_NAME = "martin_accela"
HASH_PREFIX = "martin"


# ---------------------------------------------------------------------------
# DB import (LeadFlow format — same as all other county scrapers)
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


def import_records(records: list) -> dict:
    if not records:
        return {"inserted": 0, "updated": 0, "skipped": 0}
    try:
        from app.core.db import get_connection
    except ImportError:
        print("  DB not available")
        return {"inserted": 0, "updated": 0, "skipped": 0}

    conn  = get_connection()
    conn.autocommit = False
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    import json
    from datetime import datetime

    try:
        with conn.cursor() as cur:
            county_id = get_county_id(cur)
            for rec in records:
                permit_num = str(rec.get("permit_number") or rec.get("PERMITNO") or "").strip()
                # Skip pagination rows and garbage
                if not permit_num or "Showing" in permit_num or "Next" in permit_num or len(permit_num) < 4:
                    stats["skipped"] += 1
                    continue
                # Martin permit numbers contain digits
                import re as _re
                if not _re.search(r"\d", permit_num):
                    stats["skipped"] += 1
                    continue

                issued_raw = rec.get("issued_date") or rec.get("LAST_ISSUED_DATE") or ""
                issued = None
                for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
                    try:
                        issued = datetime.strptime(str(issued_raw).split()[0], fmt).date()
                        break
                    except Exception:
                        pass

                source_id = f"{SOURCE_NAME}::{permit_num}"
                payload   = json.dumps(rec, default=str)

                raw_id = None
                try:
                    cur.execute("""
                        INSERT INTO raw_permits
                            (county_id, source_file, source_record_id, raw_payload, issued_date)
                        VALUES (%s,%s,%s,%s::jsonb,%s)
                        ON CONFLICT (county_id, source_record_id) DO UPDATE SET
                            raw_payload = EXCLUDED.raw_payload
                        RETURNING id
                    """, (county_id, SOURCE_NAME, source_id, payload, issued))
                    row = cur.fetchone()
                    if row:
                        raw_id = row[0]
                except Exception as e:
                    conn.rollback()
                    stats["skipped"] += 1
                    continue

                n_hash = f"{HASH_PREFIX}::{permit_num}"
                try:
                    cur.execute("""
                        INSERT INTO normalized_permits (
                            county_id, raw_permit_id, permit_number,
                            permit_type, owner_name, business_name,
                            address_1, project_description, issued_date,
                            normalized_hash
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (normalized_hash) DO UPDATE SET
                            owner_name    = COALESCE(EXCLUDED.owner_name,    normalized_permits.owner_name),
                            business_name = COALESCE(EXCLUDED.business_name, normalized_permits.business_name),
                            updated_at    = NOW()
                        RETURNING id, (xmax = 0) AS is_insert
                    """, (
                        county_id, raw_id, permit_num,
                        rec.get("permit_type") or rec.get("RECORD_TYPE") or None,
                        rec.get("owner_name")  or rec.get("OWNER_NAME")  or None,
                        rec.get("contractor")  or rec.get("CONTRACTOR_NAME") or rec.get("Contractor") or None,
                        rec.get("address")     or rec.get("FULL_ADDRESS") or rec.get("Address") or None,
                        rec.get("description") or rec.get("PERMIT_DESCRIPTION") or None,
                        issued, n_hash,
                    ))
                    result = cur.fetchone()
                    if result:
                        stats["inserted" if result[1] else "updated"] += 1
                except Exception as e:
                    conn.rollback()
                    print(f"  Insert error {permit_num}: {e}")
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
    import argparse, json
    from pathlib import Path
    from datetime import datetime, timedelta

    parser = argparse.ArgumentParser(description="Martin County permit scraper")
    parser.add_argument("--days-back", type=int, default=7)
    parser.add_argument("--no-db",     action="store_true")
    parser.add_argument("--visible",   action="store_true")
    args = parser.parse_args()

    print(f"\n[Martin] Scraping last {args.days_back} days")

    # Run the Permit_Bot scraping logic
    # Import the original main logic inline
    today      = datetime.today()
    start_date = today - timedelta(days=args.days_back)
    start_str  = start_date.strftime("%m/%d/%Y")
    end_str    = today.strftime("%m/%d/%Y")

    BASE_DIR = Path(__file__).resolve().parents[2]
    RAW_DIR  = BASE_DIR / "data" / "raw" / "martin" / "permits"
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # Call the scraping function from Permit_Bot
    # Each county has a different entry point — detected below
    all_rows = []
    try:
        results_html, cookies, results_url = load_and_submit(start_str, end_str)
        import requests
        session = requests.Session()
        for name_c, value in cookies.items():
            session.cookies.set(name_c, value, domain="aca-prod.accela.com")
        from bs4 import BeautifulSoup
        headers_out = []
        page = 1
        current_html = results_html
        form_state = extract_form_state(current_html)
        while True:
            headers_row, page_results = parse_results(current_html)
            if not page_results:
                break
            if not headers_out and headers_row:
                headers_out = headers_row + ["contractor", "license_number", "phone", "email"]
            for row_vals, pb_target, pb_arg in page_results:
                # Skip detail page fetch (times out) — use empty contractor
                info = {"contractor": "", "license_number": "", "phone": "", "email": ""}
                try:
                    info = fetch_detail(session, form_state, pb_target, pb_arg)
                except Exception:
                    pass  # Timeout or error — continue with empty contractor
                row_dict = dict(zip(headers_out, row_vals + [
                    info.get("contractor",""), info.get("license_number",""),
                    info.get("phone",""), info.get("email","")
                ]))
                row_dict["permit_number"] = row_vals[0] if row_vals else ""
                row_dict["address"] = row_dict.get("address", "")
                row_dict["contractor"] = info["contractor"]
                row_dict["issued_date"] = row_dict.get("issued_date", "")
                all_rows.append(row_dict)
            soup = BeautifulSoup(current_html, "lxml")
            next_pb = get_next_postback(soup)
            if not next_pb:
                break
            import requests as req
            payload = make_postback_payload(form_state, next_pb[0], next_pb[1])
            r = session.post(HOME_URL, data=payload,
                headers={**REQ_HEADERS, "Referer": HOME_URL}, timeout=45)
            current_html = r.text
            form_state = extract_form_state(current_html)
            page += 1
        print(f"  Scraped {len(all_rows)} permits")
    except Exception as e:
        import traceback
        print(f"  Scraping error: {e}")
        traceback.print_exc()

    if not all_rows:
        print("  No data scraped.")
        return

    # Save snapshot
    snap = RAW_DIR / f"martin_permits_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    snap.write_text(json.dumps(all_rows[:5], indent=2, default=str), encoding="utf-8")
    print(f"  Sample saved: {snap.name}")
    print("  Sample:")
    for r in all_rows[:3]:
        pnum = r.get("permit_number") or r.get("PERMITNO","")
        addr = r.get("address") or r.get("Address") or r.get("FULL_ADDRESS","")
        cont = r.get("contractor") or r.get("Contractor") or r.get("CONTRACTOR_NAME","")
        print(f"    {pnum} | {addr[:50]} | {cont}")

    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    if not args.no_db:
        stats = import_records(all_rows)

    print(f"\n--- Martin summary ---")
    print(f"  Records scraped    : {len(all_rows)}")
    print(f"  raw inserted       : {stats['inserted']}")
    print(f"  normalized inserted: {stats['inserted']}")
    print(f"  skipped            : {stats['skipped']}")


if __name__ == "__main__":
    main()