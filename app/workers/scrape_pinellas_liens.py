"""
scrape_pinellas_liens.py
=========================
Pinellas County IRS federal tax lien scraper.
Portal: https://officialrecords.mypinellasclerk.gov/search/SearchTypeDocType

Targets doc type "LIEN (IRS) (LN IRS)" (id 868) exclusively — no creditor
filtering needed since the doc type already identifies federal tax liens.

Navigation confirmed from manual session log:
  1. Load SearchTypeDocType directly (works without home-page nav)
  2. Set DocTypesDisplay-input = "IRS LIENS"
  3. Set DocTypesDisplay = LN IRS doc type value
  4. Set RecordDateFrom / RecordDateTo (text inputs, M/D/YYYY format)
  5. Set DateRangeList = "Specify Date Range..."
  6. Click btnSearch
  7. Click Export to CSV link → downloads to configured dir

Usage:
  python -m app.workers.scrape_pinellas_liens --days-back 30 --visible
  python -m app.workers.scrape_pinellas_liens --days-back 30
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import base64
import re
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

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
COUNTY_NAME  = "Pinellas"
SOURCE_NAME  = "pinellas_acclaim"
PORTAL_URL   = "https://officialrecords.mypinellasclerk.gov/search/SearchTypeDocType"
EXPORT_URL   = "https://officialrecords.mypinellasclerk.gov/Search/ExportCsv"

BASE_DIR     = Path(__file__).resolve().parents[2]
RAW_DIR      = BASE_DIR / "data" / "raw" / "pinellas" / "liens"
PDF_DIR      = RAW_DIR / "pdfs"
DOWNLOAD_DIR = RAW_DIR / "downloads"
DEBUG_DIR    = RAW_DIR / "debug"
for d in [RAW_DIR, PDF_DIR, DOWNLOAD_DIR, DEBUG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Pinellas has no dedicated IRS lien doc type — all liens are under "LIEN (LN)".
# We pull the full LIENS group and filter to IRS creditors in parse_acclaim_csv.
# 3-day chunks required — 30-day range hits the 5000-record portal limit.
DOC_GROUPS = [
    (
        "LIENS",
        "DocTypeGroup|Unknown (ASSMT),Unknown (CTF IND),Unknown (LIEN),Unknown (PT LN),Unknown (TAX LN),Unknown (TAX WAR),Unknown (TR LN),Unknown (TX),Unknown (TX LN),Unknown (TX WAR),CERTIFIED COPY OF A COURT JUDGMENT OR ORDER (CC JUD),CORPORATE LIEN (CORPORATE LIEN),JUDGEMENT LIEN (JUD LN),LIEN (LN),LIEN (IRS) (LN IRS)|186,291,407,587,794,795,807,813,818,823,852,855,867,868,897",
        "irs_liens",
        3,
    ),
]

# IRS creditor name patterns — belt-and-suspenders filter on top of doc type
IRS_PATTERNS = (
    "INTERNAL REVENUE",
    "INTERNAL REV",
    "IRS",
    "UNITED STATES",
    "US TREASURY",
    "U S TREASURY",
    "DEPT OF TREASURY",
    "DEPARTMENT OF TREASURY",
)

BUSINESS_MARKERS = {
    "LLC", "INC", "CORP", "LTD", "LP", "LLP", "ASSN", "ASSOCIATION",
    "BANK", "MORTGAGE", "FEDERAL", "STATE", "COUNTY", "CITY", "FLORIDA",
    "INTERNAL", "REVENUE", "SERVICE", "IRS", "ATTORNEY",
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
    legal:             Optional[str]  = None
    pdf_path:          Optional[str]  = None
    raw_payload:       Dict           = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()

def parse_date(v: Any) -> Optional[date]:
    s = clean(v)
    # Handle datetime strings like "2026-03-30T00:00:00" or "3/30/2026 12:00:00 AM"
    s = s.split("T")[0].strip()
    s = re.sub(r"\s+\d{1,2}:\d{2}:\d{2}.*$", "", s).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y%m%d"):
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
    # Realistic user agent — Cloudflare checks this
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    )
    opts.add_argument("--lang=en-US")
    opts.add_experimental_option("prefs", {
        "download.default_directory":   str(DOWNLOAD_DIR),
        "download.prompt_for_download": False,
        "download.directory_upgrade":   True,
        "intl.accept_languages":        "en-US,en;q=0.9",
    })
    if ChromeDriverManager:
        service = Service(ChromeDriverManager().install())
        drv = webdriver.Chrome(service=service, options=opts)
    else:
        drv = webdriver.Chrome(options=opts)
    # Spoof navigator.webdriver so Cloudflare Turnstile doesn't block us
    drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins',   {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            window.chrome = {runtime: {}};
        """
    })
    return drv


def save_debug(driver, label: str) -> None:
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        driver.save_screenshot(str(DEBUG_DIR / f"{ts}_{label}.png"))
        (DEBUG_DIR / f"{ts}_{label}.html").write_text(
            driver.page_source, encoding="utf-8", errors="ignore"
        )
    except Exception:
        pass


def set_field(driver, field_id: str, value: str) -> bool:
    """Set a field value — tries By.ID then By.NAME, handles display:none."""
    el = None
    for by, selector in [(By.ID, field_id), (By.NAME, field_id)]:
        try:
            el = driver.find_element(by, selector)
            break
        except Exception:
            continue
    if el is None:
        try:
            el = driver.execute_script(
                "return document.querySelector(arguments[0]);",
                f"[name=\"{field_id}\"]"
            )
        except Exception:
            pass
    if el is None:
        print(f"  set_field({field_id}) failed: element not found")
        return False
    try:
        driver.execute_script("""
            var el  = arguments[0];
            var val = arguments[1];
            el.style.display = '';
            el.value = val;
            el.dispatchEvent(new Event('input',  {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            el.dispatchEvent(new Event('blur',   {bubbles: true}));
        """, el, value)
        return True
    except Exception as e:
        print(f"  set_field({field_id}) JS error: {e}")
        return False


def get_result_count(driver) -> int:
    try:
        m = re.search(r"Displaying items.*?of\s+([\d,]+)", driver.page_source)
        if m:
            return int(m.group(1).replace(",", ""))
        # Also check for the error dialog
        if "is not allowed to exceed more than 5000" in driver.page_source:
            return 9999
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------------------
# Search + Export
# ---------------------------------------------------------------------------

def search_and_export(
    driver, group_name: str, doc_types_value: str,
    start: date, end: date
) -> Optional[str]:
    """
    Load portal, set doc type group + date range, click Search, Export to CSV.
    Returns CSV text or None.
    """
    # Windows-safe date formatting (no leading zeros)
    start_str = f"{start.month}/{start.day}/{start.year}"
    end_str   = f"{end.month}/{end.day}/{end.year}"

    print(f"    {group_name}: {start_str} → {end_str}")

    driver.get(PORTAL_URL)
    time.sleep(5)

    # Check for and wait out Cloudflare challenge ("Just a moment...")
    for _ in range(6):  # wait up to 30 seconds
        if "just a moment" in driver.title.lower() or "cf-turnstile" in driver.page_source:
            print(f"    Waiting for Cloudflare challenge to resolve...")
            time.sleep(5)
        else:
            break
    else:
        print(f"    WARNING: Cloudflare challenge did not resolve — try running with --visible")
        save_debug(driver, "cloudflare_blocked")
        return None

    # Dismiss disclaimer — try each selector separately (| doesn't work in find_element)
    disclaimer_dismissed = False
    for xpath in [
        "//input[@value='I accept the conditions above.']",
        "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'accept')]",
        "//input[contains(@value,'accept') or contains(@value,'Accept')]",
        "//button[contains(text(),'Accept')]",
        "//a[contains(text(),'Accept')]",
    ]:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                disclaimer_dismissed = True
                print(f"    Accepted disclaimer")
                time.sleep(3)
                break
        except Exception:
            continue

    if not disclaimer_dismissed:
        # Check if disclaimer is even present
        if "accept" in driver.page_source.lower():
            print(f"    WARNING: Disclaimer may not have been dismissed")
            save_debug(driver, "disclaimer_not_dismissed")

    # Verify page loaded and form is present
    if "SearchTypeDocType" not in driver.current_url and "search" not in driver.current_url.lower():
        print(f"    Unexpected URL: {driver.current_url}")
        save_debug(driver, "unexpected_url")
        return None

    # Wait for the doc type input to actually appear in DOM (JS-rendered form)
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "DocTypesDisplay_input"))
        )
    except Exception:
        # Try waiting for any input
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.TAG_NAME, "input"))
            )
        except Exception:
            print(f"    Form did not render — saving debug")
            save_debug(driver, "form_not_rendered")
            return None

    # Set doc type group display input (visible text field)
    set_field(driver, "DocTypesDisplay_input", group_name)
    time.sleep(0.5)

    # Set hidden encoded doc type value
    set_field(driver, "DocTypesDisplay", doc_types_value)
    time.sleep(0.5)

    # Set date range mode to custom
    set_field(driver, "DateRangeList", "Specify Date Range...")
    time.sleep(0.3)

    # Set date fields
    set_field(driver, "RecordDateFrom", start_str)
    set_field(driver, "RecordDateTo",   end_str)
    time.sleep(0.5)

    # Click Search — try multiple selectors in case portal changed
    search_btn = None
    for selector in [
        (By.ID,   "btnSearch"),
        (By.ID,   "searchButton"),
        (By.CSS_SELECTOR, "input[value='Search']"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.XPATH, "//input[contains(@value,'Search')]"),
        (By.XPATH, "//button[contains(text(),'Search')]"),
        (By.XPATH, "//a[contains(text(),'Search')]"),
    ]:
        try:
            search_btn = driver.find_element(*selector)
            break
        except Exception:
            continue
    if search_btn:
        driver.execute_script("arguments[0].click();", search_btn)
        print(f"    Clicked Search")
    else:
        print(f"    Search button not found — saving debug")
        save_debug(driver, f"no_search_btn_{group_name}")
        return None

    time.sleep(7)

    # Check result count
    count = get_result_count(driver)
    print(f"    Results: {count}")

    if count == 0:
        save_debug(driver, f"zero_results_{group_name}_{start_str.replace('/','-')}")
        return None

    if count >= 9999:
        print(f"    ⚠ 5000 limit hit — need smaller date range")
        # Try to dismiss dialog
        for xpath in ["//button[contains(text(),'OK')]", "//button[contains(text(),'Close')]", "//span[@class='t-icon t-close']"]:
            try:
                driver.find_element(By.XPATH, xpath).click()
                time.sleep(1)
                break
            except Exception:
                continue
        return None

    # Clear old downloads
    for f in DOWNLOAD_DIR.glob("*.csv"):
        try:
            f.unlink()
        except Exception:
            pass

    # Click Export to CSV
    try:
        export_link = driver.find_element(By.XPATH, "//a[contains(text(),'Export to CSV')]")
        driver.execute_script("arguments[0].click();", export_link)
        print(f"    Clicked Export to CSV")
        time.sleep(8)
    except Exception as e:
        print(f"    Export to CSV link not found: {e}")
        # Try direct navigation
        driver.get(EXPORT_URL)
        time.sleep(8)

    # Find downloaded CSV
    csv_files = sorted(DOWNLOAD_DIR.glob("*.csv"), key=lambda f: f.stat().st_mtime, reverse=True)
    if csv_files and (datetime.now().timestamp() - csv_files[0].stat().st_mtime) < 60:
        size = csv_files[0].stat().st_size
        print(f"    Downloaded: {csv_files[0].name} ({size} bytes)")
        if size > 100:
            return csv_files[0].read_text(encoding="utf-8-sig", errors="ignore")
        else:
            print(f"    File too small, likely empty")
            return None
    else:
        print(f"    No CSV downloaded")
        save_debug(driver, f"no_download_{group_name}_{start_str.replace('/','-')}")
        return None


# ---------------------------------------------------------------------------
# CSV Parsing
# ---------------------------------------------------------------------------

def parse_acclaim_csv(csv_text: str, doc_group: str) -> List[LienRecord]:
    """
    Parse Acclaim Export CSV.
    Columns: FIRST DIRECT NAME (creditor), FIRST INDIRECT NAME (debtor),
             RECORD DATE, DOC TYPE, BOOK TYPE, BOOK/PAGE, LEGAL, INSTRUMENT#
    """
    records = []
    if not csv_text or len(csv_text) < 50:
        return records

    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        headers = [h.strip().upper() for h in (reader.fieldnames or [])]
        if not headers:
            print(f"    No headers in CSV")
            return records
        print(f"    CSV columns: {headers[:8]}")

        for row in reader:
            nrow = {k.strip().upper(): (v or "").strip() for k, v in row.items()}

            instrument = (
                nrow.get("INSTRUMENTNUMBER") or nrow.get("INSTRUMENT#") or
                nrow.get("INSTRUMENT NUMBER") or nrow.get("INST#") or ""
            ).strip()
            if not instrument:
                continue

            # INDIRECTNAME = debtor (person with lien against them)
            debtor_raw = (
                nrow.get("INDIRECTNAME") or nrow.get("FIRST INDIRECT NAME") or
                nrow.get("INDIRECT NAME") or ""
            ).strip()
            # DIRECTNAME = creditor (Florida, IRS, bank, etc.)
            creditor_raw = (
                nrow.get("DIRECTNAME") or nrow.get("FIRST DIRECT NAME") or
                nrow.get("DIRECT NAME") or ""
            ).strip()

            # Skip if debtor is missing
            if not debtor_raw:
                continue

            # Filter by doc type — must contain LIEN (skips SATISFACTION, RELEASE, etc.)
            doc_type_raw = (nrow.get("DOCTYPEDESCRIPTION") or nrow.get("DOC TYPE") or "").upper()
            if "LIEN" not in doc_type_raw:
                continue

            # Require creditor to match IRS patterns — no creditor = not an IRS lien
            if not creditor_raw or not any(p in creditor_raw.upper() for p in IRS_PATTERNS):
                continue

            # NOTE: do NOT skip business debtors — IRS liens businesses too


            debtor = title_name(debtor_raw)
            creditor = title_name(creditor_raw) if creditor_raw else None

            # Parse book/page from "23503/505"
            book_page = nrow.get("BOOKPAGE") or nrow.get("BOOK/PAGE") or nrow.get("BOOK PAGE") or ""
            book, page = "", ""
            if "/" in book_page:
                parts = book_page.split("/", 1)
                book, page = parts[0].strip(), parts[1].strip()

            rec_date = parse_date(nrow.get("RECORDDATE") or nrow.get("RECORD DATE") or nrow.get("DATE") or "")
            doc_type = nrow.get("DOCTYPEDESCRIPTION") or nrow.get("DOC TYPE") or doc_group

            records.append(LienRecord(
                instrument_number = instrument,
                debtor_name       = debtor,
                creditor_name     = creditor,
                doc_type          = doc_type,
                filed_date        = rec_date,
                book              = book or None,
                page              = page or None,
                legal             = nrow.get("LEGAL") or None,
                raw_payload       = dict(row),
            ))
    except Exception as e:
        print(f"    CSV parse error: {e}")

    return records


# ---------------------------------------------------------------------------
# Main scrape
# ---------------------------------------------------------------------------

def scrape_pinellas_liens(start: date, end: date, visible: bool = False) -> List[LienRecord]:
    print(f"\n[Pinellas Liens] Scraping {start} → {end}")

    driver = make_driver(visible=visible)
    all_records: List[LienRecord] = []
    seen: set = set()

    try:
        for group_name, doc_types_value, label, max_chunk_days in DOC_GROUPS:
            print(f"\n  [{label.upper()}] {group_name}")

            # Chunk to stay under 5000-record limit
            chunk_start = start
            while chunk_start < end:
                chunk_end = min(chunk_start + timedelta(days=max_chunk_days - 1), end)

                csv_text = search_and_export(
                    driver, group_name, doc_types_value, chunk_start, chunk_end
                )

                if csv_text:
                    recs = parse_acclaim_csv(csv_text, group_name)
                    new_recs = [r for r in recs if r.instrument_number not in seen]
                    print(f"    Parsed: {len(recs)} records, {len(new_recs)} new")
                    for r in new_recs:
                        seen.add(r.instrument_number)
                        all_records.append(r)

                chunk_start = chunk_end + timedelta(days=1)
                time.sleep(1)

    except Exception as e:
        print(f"  [Pinellas] Error: {e}")
        import traceback
        traceback.print_exc()
        save_debug(driver, "fatal_error")
    finally:
        driver.quit()

    print(f"\n  Total unique liens: {len(all_records)}")
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

                source_record_id = f"{SOURCE_NAME}::{rec.instrument_number}::{rec.debtor_name[:30]}"
                payload = json.dumps({
                    "instrument_number": rec.instrument_number,
                    "debtor_name":       rec.debtor_name,
                    "creditor_name":     rec.creditor_name,
                    "doc_type":          rec.doc_type,
                    "filed_date":        str(rec.filed_date) if rec.filed_date else None,
                    "book":              rec.book,
                    "page":              rec.page,
                }, default=str)

                # raw_liens
                try:
                    cur.execute("""
                        INSERT INTO raw_liens
                            (county_id, source_file, source_record_id, raw_payload, filed_date)
                        VALUES (%s, %s, %s, %s::jsonb, %s)
                        ON CONFLICT (county_id, source_record_id) DO UPDATE SET
                            raw_payload = EXCLUDED.raw_payload
                        RETURNING id, (xmax = 0) AS is_insert
                    """, (county_id, SOURCE_NAME, source_record_id, payload, rec.filed_date))
                    rl = cur.fetchone()
                    raw_id = rl[0]
                    if rl[1]:
                        stats["raw"] += 1
                except Exception:
                    # raw_liens table may not exist — use normalized directly
                    raw_id = None

                n_hash = f"pinellas::{rec.instrument_number}::{(rec.debtor_name or '')[:40]}"
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
                    county_id,
                    raw_id,
                    rec.debtor_name,
                    rec.creditor_name if rec.creditor_name and is_business(rec.creditor_name) else None,
                    None,
                    rec.doc_type,
                    "federal_tax_lien",
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
# Pinellas public document viewer — no auth required
DOC_VIEWER_BASE = "https://officialrecords.mypinellasclerk.gov"


def download_pdf_pinellas(driver, rec: LienRecord) -> Optional[str]:
    """
    Download the lien document PDF from the Pinellas public portal.
    Instrument number maps directly to the document viewer URL.
    Saves to PDF_DIR / pinellas_{instrument}.pdf
    """
    instrument = re.sub(r"[^\w\-]", "_", rec.instrument_number)[:40]
    pdf_path   = PDF_DIR / f"pinellas_{instrument}.pdf"

    if pdf_path.exists() and pdf_path.stat().st_size > 500:
        return str(pdf_path)

    # Pinellas document URLs confirmed from portal
    urls = [
        f"{DOC_VIEWER_BASE}/search/DetailSearch/{rec.instrument_number}",
        f"{DOC_VIEWER_BASE}/oripublicaccess/?instrument={rec.instrument_number}",
        f"{DOC_VIEWER_BASE}/Document/GetDocument/{rec.instrument_number}",
    ]

    # Status 0 = CORS block on fetch — navigate directly instead
    for url in urls:
        try:
            print(f"    Trying: {url[:80]}")
            driver.get(url)
            time.sleep(4)

            page_text = driver.find_element(By.TAG_NAME, "body").text
            page_src  = driver.page_source
            print(f"    Page: {len(page_text)} chars, title={driver.title[:50]!r}")

            # Skip if it's just an error or redirect page
            if len(page_text) < 100:
                print(f"    Page too short — skipping")
                continue

            pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                "printBackground": True,
                "paperWidth":  8.5,
                "paperHeight": 11,
                "marginTop":   0.4,
                "marginBottom":0.4,
            })
            if pdf_data and pdf_data.get("data"):
                pdf_path.write_bytes(base64.b64decode(pdf_data["data"]))
                size = pdf_path.stat().st_size
                print(f"    PDF size: {size:,} bytes")
                if size > 5000:
                    return str(pdf_path)
                else:
                    print(f"    PDF too small — likely error/redirect page")
                    pdf_path.unlink(missing_ok=True)

        except Exception as e:
            print(f"    Error on {url[:70]}: {e}")
            continue

    return None


def download_pdfs_pinellas(
    driver, records: List[LienRecord], limit: Optional[int] = None
) -> dict:
    stats = {"attempted": 0, "saved": 0, "failed": 0}
    targets = records[:limit] if limit else records
    for i, rec in enumerate(targets, 1):
        print(f"  [pdf {i}/{len(targets)}] {rec.instrument_number} | {rec.debtor_name}")
        stats["attempted"] += 1
        path = download_pdf_pinellas(driver, rec)
        if path:
            rec.pdf_path = path
            stats["saved"] += 1
            print(f"    saved: {Path(path).name}")
        else:
            stats["failed"] += 1
            print(f"    failed")
        time.sleep(1)
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Pinellas County IRS federal tax lien scraper")
    parser.add_argument("--days-back",  type=int, default=30)
    parser.add_argument("--visible",    action="store_true", default=True,
                        help="Show browser (default True — Cloudflare blocks headless)")
    parser.add_argument("--headless",   action="store_true",
                        help="Force headless mode (may trigger Cloudflare)")
    parser.add_argument("--no-db",      action="store_true")
    parser.add_argument("--no-pdf",     action="store_true", help="Skip PDF download")
    parser.add_argument("--pdf-limit",  type=int, default=None, help="Max PDFs to download")
    args = parser.parse_args()

    end   = date.today()
    start = end - timedelta(days=args.days_back)

    records = scrape_pinellas_liens(start, end, visible=args.visible)

    if records:
        snap = RAW_DIR / f"pinellas_ftl_{nowstamp()}.json"
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

    # Download PDFs
    pdf_stats = {"attempted": 0, "saved": 0, "failed": 0}
    if not args.no_pdf and records:
        print(f"\nDownloading PDFs to {PDF_DIR} …")
        from selenium.webdriver.support.ui import WebDriverWait
        pdf_driver = make_driver(visible=args.visible)
        try:
            pdf_stats = download_pdfs_pinellas(pdf_driver, records, limit=args.pdf_limit)
        finally:
            pdf_driver.quit()
        print(f"  PDFs: {pdf_stats['saved']}/{pdf_stats['attempted']} saved")

    stats = {"raw": 0, "normalized": 0, "skipped": 0}
    if not args.no_db and records:
        stats = import_records(records)

    print(f"\n--- Pinellas IRS federal tax lien summary ---")
    print(f"  Records scraped    : {len(records)}")
    print(f"  raw inserted       : {stats['raw']}")
    print(f"  normalized inserted: {stats['normalized']}")
    print(f"  skipped            : {stats['skipped']}")
    if not args.no_pdf:
        print(f"  pdf saved          : {pdf_stats['saved']}/{pdf_stats['attempted']}")


if __name__ == "__main__":
    main()