"""
scrape_lee_liens.py
==========================
Scrapes IRS federal tax liens (LN TX, LN TX NC) from Palm Beach County
Official Records via Landmark Web.

Portal: https://or.leeclerk.org

Key findings:
  - SPA with reCAPTCHA on the Document Type search form
  - Script pauses for manual CAPTCHA solve, then auto-submits
  - Results load into #searchResults via AJAX
  - API: POST /Search/DocumentTypeSearch then /Search/GetSearchResults
  - Doc type checkboxes: dt-DocumentType-66 (LN TX), dt-DocumentType-67 (LN TX NC)
  - Date fields: beginDate-DocumentType, endDate-DocumentType
  - Submit: .submitButton with formname=documentTypeSearchForm

Usage:
  python -m app.workers.scrape_lee_liens --days-back 90
  python -m app.workers.scrape_lee_liens --days-back 90 --no-db
  python -m app.workers.scrape_lee_liens --days-back 90 --no-pdf
"""
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

try:
    import undetected_chromedriver as uc
    HAS_UC = True
except ImportError:
    HAS_UC = False
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
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
COUNTY_NAME = "Brevard"
SOURCE_NAME = "lee_irs_liens"
HOME_URL    = "https://www.brevardclerk.us/LandMarkWeb/Home/Index"

BASE_DIR  = Path(__file__).resolve().parents[2]
RAW_DIR   = BASE_DIR / "data" / "raw" / "brevard" / "irs_liens"
PDF_DIR   = RAW_DIR / "pdfs"
DEBUG_DIR = RAW_DIR / "debug"
for d in [RAW_DIR, PDF_DIR, DEBUG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Lee County Landmark doc type IDs for tax liens
# LN TX / LN TX NC = Florida state tax liens (FL Dept of Revenue)
# LN = General Lien (includes IRS federal tax liens)
IRS_DOC_TYPES = [
    ("LN TX",    "dt-DocumentType-66"),   # FL State Tax Lien (FL Dept of Revenue)
    ("LN TX NC", "dt-DocumentType-67"),   # FL State Tax Lien No Charge
    ("LN",       "dt-DocumentType-23"),   # General Lien (IRS federal liens file here)
]

BUSINESS_MARKERS = {
    "LLC", "INC", "CORP", "LTD", "LP", "LLP", "PLLC",
    "TRUST", "ESTATE", "ASSOC", "COMPANY", "CO.", "BANK",
}
IRS_NAMES = {
    "UNITED STATES", "U.S.", "IRS",
    "INTERNAL REVENUE", "DEPT OF TREASURY",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class IRSLienRecord:
    instrument_number: str
    debtor_name:       Optional[str]   = None
    amount:            Optional[float] = None
    filed_date:        Optional[date]  = None
    book:              Optional[str]   = None
    page:              Optional[str]   = None
    doc_type:          Optional[str]   = None
    pdf_path:          Optional[str]   = None
    pdf_url:           Optional[str]   = None
    detail_url:        Optional[str]   = None
    raw_payload:       Dict            = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()

def parse_date(v: Any) -> Optional[date]:
    s = clean(v).split("T")[0].strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
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

def is_irs(name: str) -> bool:
    upper = name.upper()
    return any(m in upper for m in IRS_NAMES)

def parse_amount(v: str) -> Optional[float]:
    s = re.sub(r"[^\d.]", "", str(v or ""))
    try:
        return float(s) if s else None
    except ValueError:
        return None

def nowstamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def make_driver(visible: bool = True):
    """Simple visible Chrome with bot-detection spoofing."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

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

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        drv = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), options=opts)
    except Exception:
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


def save_debug(driver, label: str) -> None:
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        driver.save_screenshot(str(DEBUG_DIR / f"{ts}_{label}.png"))
        (DEBUG_DIR / f"{ts}_{label}.html").write_text(
            driver.page_source, encoding="utf-8", errors="ignore"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Session setup
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Account credentials — loaded from .env
# Add to .env:
#   LEE_USERNAME=your@email.com
#   LEE_PASSWORD=yourpassword
# ---------------------------------------------------------------------------
import os
from dotenv import load_dotenv
load_dotenv()
LEE_USERNAME = os.getenv("LEE_USERNAME", "")
LEE_PASSWORD = os.getenv("LEE_PASSWORD", "")


def login(driver) -> bool:
    """Log in to Lee County portal to get authenticated session (bypasses Akamai)."""
    if not LEE_USERNAME or not LEE_PASSWORD:
        return False

    print(f"  Logging in as {LEE_USERNAME} ...")
    driver.get("https://or.leeclerk.org/LandMarkWeb/Account/LogOn")
    time.sleep(4)

    try:
        driver.find_element(By.ID, "UserName").send_keys(LEE_USERNAME)
        driver.find_element(By.ID, "Password").send_keys(LEE_PASSWORD)
        driver.find_element(By.CSS_SELECTOR,
            "input[type='submit'], button[type='submit']").click()
        time.sleep(3)
        print(f"  Logged in — at: {driver.current_url}")
        return True
    except Exception as e:
        print(f"  Login failed: {e}")
        return False


def setup_session(driver) -> bool:
    """Load home page, log in if credentials set, accept disclaimer."""
    driver.get(HOME_URL)
    time.sleep(6)
    print(f"  Loaded: {driver.current_url}")

    # Log in if credentials provided
    if LEE_USERNAME and LEE_PASSWORD:
        login(driver)
        driver.get(HOME_URL)
        time.sleep(4)

    try:
        accept = driver.find_element(By.XPATH,
            "//a[normalize-space(text())='Accept']")
        driver.execute_script("arguments[0].click();", accept)
        time.sleep(1)
        if HOME_URL.rstrip('/') not in driver.current_url:
            driver.get(HOME_URL)
            time.sleep(3)
        print(f"  Disclaimer accepted, now at: {driver.current_url}")
    except Exception:
        print(f"  Disclaimer already accepted, at: {driver.current_url}")
    save_debug(driver, "01_home")
    return True


# ---------------------------------------------------------------------------
# Search one doc type
# ---------------------------------------------------------------------------

def search_doc_type(driver, doc_type: str, checkbox_id: str,
                    start: date, end: date,
                    download_pdfs: bool = True) -> List[IRSLienRecord]:
    """Search for one IRS doc type, pause for CAPTCHA, parse results."""

    start_str = f"{start.month:02d}/{start.day:02d}/{start.year}"
    end_str   = f"{end.month:02d}/{end.day:02d}/{end.year}"
    print(f"\n  [{doc_type}] {start_str} → {end_str}")

    # Confirmed from inspect: document icon uses LaunchDisclaimer('searchCriteriaDocuments')
    # NOT LaunchDisclaimerFromMenu — that's the Palm Beach version
    clicked_nav = False

    # Try clicking the anchor next to the document div directly
    for by, sel in [
        (By.XPATH, "//span[@class='searchName' and normalize-space(text())='document']/preceding-sibling::a"),
        (By.XPATH, "//div[@class='divInside' and .//span[normalize-space(text())='document']]//a"),
        (By.XPATH, "//a[contains(@onclick, \"searchCriteriaDocuments\")]"),
    ]:
        try:
            el = driver.find_element(by, sel)
            driver.execute_script("arguments[0].click();", el)
            clicked_nav = True
            time.sleep(4)
            print(f"  Clicked document icon via {sel[:50]}")
            break
        except Exception:
            continue

    if not clicked_nav:
        # Call JS function directly — confirmed safe, goes to /search not /search/index
        try:
            driver.execute_script("LaunchDisclaimer('searchCriteriaDocuments');")
            clicked_nav = True
            time.sleep(4)
            print("  Navigated via LaunchDisclaimer JS")
        except Exception as e:
            print(f"  LaunchDisclaimer failed: {e}")

    print(f"  Page after nav: {driver.current_url}")

    # Now on Document Type search form — fill it in
    # Step 1: Click "select" button to open doc type modal (Kendo UI button)
    for el in driver.find_elements(By.CSS_SELECTOR, "button[aria-label='select']"):
        try:
            if el.is_displayed():
                driver.execute_script("arguments[0].click();", el)
                time.sleep(2)
                print("  Opened doc type modal")
                break
        except Exception:
            continue

    # Step 2: Check the correct checkbox
    try:
        cb = driver.find_element(By.ID, checkbox_id)
        driver.execute_script("arguments[0].click();", cb)
        time.sleep(0.3)
        print(f"  Checked: {doc_type} ({checkbox_id})")
    except Exception as e:
        print(f"  Checkbox {checkbox_id} not found: {e}")

    # Step 3: Click Done to confirm selection
    try:
        done = driver.find_element(By.CSS_SELECTOR, "input[value='Done']")
        driver.execute_script("arguments[0].click();", done)
        time.sleep(1)
        print("  Clicked Done")
    except Exception as e:
        print(f"  Done button error: {e}")
        try:
            driver.execute_script("GetDocTypeString();")
            print("  Called GetDocTypeString()")
        except Exception:
            pass

    # Set date fields
    for field_id, value in [
        ("beginDate-DocumentType", start_str),
        ("endDate-DocumentType",   end_str),
    ]:
        try:
            el = driver.find_element(By.ID, field_id)
            driver.execute_script("""
                var el = arguments[0]; var val = arguments[1];
                el.value = val;
                el.dispatchEvent(new Event('input',  {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
            """, el, value)
            print(f"  Set {field_id}: {value}")
        except Exception as e:
            print(f"  Date field {field_id}: {e}")

    print(f"  Dates set: {start_str} → {end_str}")

    # -----------------------------------------------------------------------
    # CAPTCHA PAUSE — user must solve before we submit
    # -----------------------------------------------------------------------
    # Lee County has no reCAPTCHA — proceed directly
    print("  No reCAPTCHA on Lee — submitting directly")

    # Click Submit after CAPTCHA solved
    submitted = False
    for xpath in [
        "//a[contains(@class,'submitButton') and @formname='documentTypeSearchForm']",
        "//button[contains(text(),'Submit')]",
        "//input[@value='Submit']",
    ]:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                submitted = True
                print("  Clicked Submit")
                break
        except Exception:
            continue

    if not submitted:
        # Try CSS selectors for Lee's submit button
        for by, sel in [
            (By.CSS_SELECTOR, ".submitButton"),
            (By.CSS_SELECTOR, "input[type='submit']"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.CSS_SELECTOR, "input[value='Search']"),
            (By.CSS_SELECTOR, "input[value='Submit']"),
            (By.XPATH,        "//button[contains(text(),'Search')]"),
            (By.XPATH,        "//input[@value='Search' or @value='Submit']"),
        ]:
            try:
                el = driver.find_element(by, sel)
                if el.is_displayed():
                    driver.execute_script("arguments[0].click();", el)
                    submitted = True
                    print(f"  Clicked submit via {sel}")
                    break
            except Exception:
                continue

    if not submitted:
        # Print all visible buttons for debugging
        print("  No submit found — visible buttons on page:")
        for el in driver.find_elements(By.XPATH, "//input[@type='submit'] | //button"):
            try:
                if el.is_displayed():
                    print(f"    {el.tag_name} value={el.get_attribute('value')!r} "
                          f"text={el.text.strip()!r} class={el.get_attribute('class')!r}")
            except Exception:
                pass
        return []

    # Wait for results to load into #searchResults
    print("  Waiting for results...")
    for attempt in range(20):
        time.sleep(2)
        result = driver.execute_script("""
            var sr = document.getElementById('searchResults');
            var text = sr ? (sr.innerText || '') : '';
            var loading = text.indexOf('ajax-loader') >= 0;
            var rows = sr ? sr.querySelectorAll('tr').length : 0;
            var hasData = rows > 0 && !loading;
            return {rows: rows, hasData: hasData, loading: loading,
                    text: text.substring(0, 100)};
        """)
        print(f"  Attempt {attempt+1}: rows={result.get('rows')} hasData={result.get('hasData')}")
        if result.get('hasData'):
            print(f"  Results loaded!")
            break
    else:
        print("  Results never loaded — check browser")
        save_debug(driver, f"no_results_{doc_type.replace(' ','_')}")
        return []

    save_debug(driver, f"results_{doc_type.replace(' ','_')}")

    # Parse results
    return parse_results(driver, doc_type, download_pdfs)


# ---------------------------------------------------------------------------
# Parse results
# ---------------------------------------------------------------------------

def parse_results(driver, doc_type: str,
                  download_pdfs: bool) -> List[IRSLienRecord]:
    records = []
    seen = set()
    page_num = 0
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    )

    while True:
        page_num += 1
        # Confirmed column positions from manual_results.html:
        # col6=Direct Name (creditor), col7=Indirect Name (debtor)
        # col8=Date, col10=Doc Type, col12=Book, col13=Page
        # col14=Instrument#
        rows_data = driver.execute_script("""
            var results = [];
            var base = 'https://or.leeclerk.org';
            var sr = document.getElementById('searchResults');
            if (!sr) return results;

            // All records are in ONE giant <tr> with all tds concatenated
            // Each record is 31 cells wide (confirmed from HTML analysis)
            // Record structure within each 31-cell group:
            //   +0 = row# or "Returned X records" (skip first group)
            //   +6 = direct_name (creditor)
            //   +7 = indirect_name (debtor/taxpayer)
            //   +8 = date
            //   +10 = doc_type
            //   +12 = book
            //   +13 = page
            //   +14 = instrument#
            var RECORD_SIZE = 31;
            var allTds = sr.querySelectorAll('td');
            var cells = Array.from(allTds).map(function(td) {
                return td.innerText.trim();
            });

            var i = 0;
            // Skip first group if it's "Returned X records" info
            if (cells[0] && cells[0].indexOf('Returned') >= 0) {
                i = RECORD_SIZE;
            }

            while (i + RECORD_SIZE <= cells.length) {
                var c = cells.slice(i, i + RECORD_SIZE);
                var debtor = c[7] || '';
                // Skip if debtor looks like a number (row counter) or empty
                if (!debtor || debtor.length < 2 || /^[0-9]+$/.test(debtor)) {
                    i += RECORD_SIZE;
                    continue;
                }
                var rd = {
                    'direct_name':   c[6]  || '',
                    'indirect_name': c[7]  || '',
                    'date':          c[8]  || '',
                    'doc_type':      c[10] || '',
                    'book':          c[12] || '',
                    'page':          c[13] || '',
                    'instrument':    c[14] || '',
                };
                if (rd.indirect_name && rd.indirect_name.length > 1) {
                    results.push(rd);
                }
                i += RECORD_SIZE;
            }
            return results;
        """)

        print(f"    Page {page_num}: {len(rows_data or [])} rows")

        for row in (rows_data or []):
            rec = row_to_record(row, doc_type)
            if not rec or rec.instrument_number in seen:
                continue
            seen.add(rec.instrument_number)

            if download_pdfs:
                pdf_path = download_pdf(
                    driver, session,
                    instrument = rec.instrument_number,
                    book       = rec.book or "",
                    page       = rec.page or "",
                )
                if pdf_path:
                    rec.pdf_path = pdf_path
                time.sleep(1)

            records.append(rec)
            amt = f"${rec.amount:,.0f}" if rec.amount else "?"
            pdf = "✓" if rec.pdf_path else "✗"
            print(f"  [{pdf}] {rec.instrument_number} | {rec.debtor_name} | {amt}")

        # Next page
        try:
            nxt = driver.find_element(By.XPATH,
                "//a[normalize-space(text())='Next'][not(contains(@class,'disabled'))]")
            if nxt.is_displayed():
                driver.execute_script("arguments[0].click();", nxt)
                time.sleep(4)
                continue
        except Exception:
            pass
        break

    return records


# ---------------------------------------------------------------------------
# Row → record
# ---------------------------------------------------------------------------

def row_to_record(row: dict, doc_type: str) -> Optional[IRSLienRecord]:
    # Confirmed column mapping from manual navigation:
    # direct_name  = creditor (FL Dept of Revenue / IRS)
    # indirect_name = debtor (taxpayer — who we want to reach)
    # instrument   = instrument number (e.g. 20260035594)
    # book, page, date, doc_type all confirmed

    creditor = clean(row.get("direct_name", ""))
    debtor   = clean(row.get("indirect_name", ""))

    if not debtor:
        return None

    # Both FL state tax liens and IRS federal liens are valid leads
    # Skip if debtor IS a government entity
    if is_irs(debtor):
        return None

    filed_date = parse_date(row.get("date", ""))
    book       = clean(row.get("book", ""))
    page       = clean(row.get("page", ""))
    instrument = clean(row.get("instrument", "") or
                       f"PBC-{book}-{page}-{filed_date}")
    rec_type   = row.get("doc_type", "") or doc_type

    return IRSLienRecord(
        instrument_number = instrument,
        debtor_name       = title_name(debtor),
        filed_date        = filed_date,
        book              = book or None,
        page              = page or None,
        amount            = None,
        doc_type          = rec_type,
        detail_url        = row.get("_detail_url"),
        raw_payload       = row,
    )


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------

def download_pdf(driver, session: requests.Session,
                 instrument: str, book: str = "", page: str = "") -> Optional[str]:
    """
    Download PDF via Landmark direct URLs.
    Two methods:
    1. By instrument number: /Document/MultipleView?instrumentNumber=20260035594
    2. By book/page: /Search/DocumentAndInfoByBookPage?booktype=O&booknumber=36284&pagenumber=01295
    """
    safe = re.sub(r"[^\w\-]", "_", instrument)
    out  = PDF_DIR / f"{safe}.pdf"
    if out.exists() and out.stat().st_size > 500:
        return str(out)

    base = "https://or.leeclerk.org"

    # Sync browser cookies to requests session
    try:
        for c in driver.get_cookies():
            session.cookies.set(c["name"], c["value"])
    except Exception:
        pass

    # Try instrument number URL first
    urls_to_try = []
    if instrument and not instrument.startswith("PBC-"):
        urls_to_try.append(
            f"{base}/Document/MultipleView?instrumentNumber={instrument}"
        )
    # Try book/page URL
    if book and page:
        urls_to_try.append(
            f"{base}/Search/DocumentAndInfoByBookPage"
            f"?Key=Assessor&booktype=O&booknumber={book}&pagenumber={page}"
        )

    for url in urls_to_try:
        try:
            # Navigate to get the actual PDF link
            driver.get(url)
            time.sleep(3)

            # Find PDF/image/print/document links on the detail page.
            pdf_links = driver.execute_script("""
                var out = [];
                var base = 'https://or.leeclerk.org';
                function add(h) {
                    if (!h) return;
                    if (h.startsWith('/')) h = base + h;
                    if (h.startsWith('http') && out.indexOf(h) < 0) out.push(h);
                }
                document.querySelectorAll('a[href], iframe[src], embed[src], object[data], img[src]').forEach(function(el) {
                    add(el.getAttribute('href'));
                    add(el.getAttribute('src'));
                    add(el.getAttribute('data'));
                });
                document.querySelectorAll('[onclick]').forEach(function(el) {
                    var oc = el.getAttribute('onclick') || '';
                    var matches = oc.match(/['\"]([^'\"]*(?:Document|Image|PDF|Print|View|Get|Download)[^'\"]*)['\"]/gi) || [];
                    matches.forEach(function(m) { add(m.replace(/^['\"]|['\"]$/g, '')); });
                });
                return out;
            """) or []

            if instrument and not instrument.startswith("PBC-"):
                pdf_links.extend([
                    f"{base}/Document/GetDocument?instrumentNumber={instrument}",
                    f"{base}/Document/Download?instrumentNumber={instrument}",
                    f"{base}/Document/Print?instrumentNumber={instrument}",
                    f"{base}/Document/ViewImage?instrumentNumber={instrument}",
                ])

            for pdf_link in dict.fromkeys(pdf_links):
                low = (pdf_link or '').lower()
                if not any(x in low for x in ['document', 'image', 'pdf', 'print', 'view', 'download', 'get']):
                    continue
                for c in driver.get_cookies():
                    session.cookies.set(c["name"], c["value"])
                try:
                    r = session.get(pdf_link, timeout=30, stream=True, allow_redirects=True)
                    r.raise_for_status()
                    content_type = (r.headers.get('content-type') or '').lower()
                    content = r.content
                    if len(content) > 500 and (b'%PDF' in content[:50] or 'pdf' in content_type or 'octet-stream' in content_type):
                        out.write_bytes(content)
                        if out.stat().st_size > 500:
                            print(f"    PDF: {out.name} ({out.stat().st_size:,}b)")
                            return str(out)
                    if out.exists():
                        out.unlink()
                except Exception:
                    continue
        except Exception as e:
            print(f"    PDF attempt failed ({url[:50]}): {e}")
            continue

    return None


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


def import_records(records: List[IRSLienRecord]) -> Dict[str, int]:
    if not records or not get_connection:
        return {"inserted": 0, "skipped": 0}
    conn = get_connection()
    conn.autocommit = False
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    try:
        with conn.cursor() as cur:
            county_id = get_county_id(cur)
            for col in ["pdf_path TEXT", "pdf_url TEXT",
                        "amount NUMERIC", "lien_source TEXT"]:
                try:
                    cur.execute(f"ALTER TABLE normalized_liens "
                                f"ADD COLUMN IF NOT EXISTS {col}")
                except Exception:
                    conn.rollback()
            for rec in records:
                if not rec.instrument_number or not rec.debtor_name:
                    stats["skipped"] += 1
                    continue
                # Instrument number is the stable source key. Do not include debtor_name.
                n_hash = f"lee_irs::{rec.instrument_number}"
                # Truncate all string fields to fit VARCHAR(255)
                debtor_name  = (rec.debtor_name or "")[:250]
                filing_type  = (rec.doc_type or "LN TX")[:50]
                lien_source  = "IRS"[:50]
                pdf_path_val = (rec.pdf_path or "")[:250] or None
                cur.execute("""
                    INSERT INTO normalized_liens (
                        county_id, raw_lien_id, debtor_name, business_name,
                        address_1, filing_type, lien_type, filed_date,
                        normalized_hash, pdf_path, amount, lien_source
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (normalized_hash) DO UPDATE SET
                        debtor_name = EXCLUDED.debtor_name,
                        pdf_path    = COALESCE(EXCLUDED.pdf_path, normalized_liens.pdf_path),
                        amount      = COALESCE(EXCLUDED.amount,   normalized_liens.amount)
                    RETURNING id, (xmax = 0) AS is_insert
                """, (
                    county_id, None, debtor_name, None, None,
                    filing_type, "federal_tax_lien",
                    rec.filed_date, n_hash, pdf_path_val, rec.amount, lien_source,
                ))
                row = cur.fetchone()
                if row and row[1]:
                    stats["inserted"] += 1
                elif row:
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"  [DB] {e}")
        raise
    finally:
        conn.close()
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Lee County IRS federal tax lien scraper"
    )
    ap.add_argument("--days-back", type=int, default=90)
    ap.add_argument("--visible",   action="store_true")
    ap.add_argument("--no-db",     action="store_true")
    ap.add_argument("--no-pdf",    action="store_true")
    args = ap.parse_args()

    end   = date.today()
    start = end - timedelta(days=args.days_back)

    print(f"\n[Lee IRS Liens] {start} → {end}")
    print()

    driver = make_driver(visible=True)  # Always visible — needed for CAPTCHA
    all_records: List[IRSLienRecord] = []

    try:
        setup_session(driver)

        for doc_type, checkbox_id in IRS_DOC_TYPES:
            recs = search_doc_type(
                driver, doc_type, checkbox_id, start, end,
                download_pdfs=not args.no_pdf
            )
            all_records.extend(recs)
            # Return to home between searches — longer wait for Akamai
            driver.get(HOME_URL)
            time.sleep(6)

    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()
        save_debug(driver, "error")
    finally:
        driver.quit()

    print(f"\n  Total scraped: {len(all_records)}")
    print(f"  PDFs:          {sum(1 for r in all_records if r.pdf_path)}")

    if all_records:
        snap = RAW_DIR / f"lee_irs_{nowstamp()}.json"
        snap.write_text(
            json.dumps([{
                "instrument": r.instrument_number,
                "debtor":     r.debtor_name,
                "amount":     r.amount,
                "filed_date": str(r.filed_date),
                "pdf_path":   r.pdf_path,
            } for r in all_records], indent=2, default=str),
            encoding="utf-8"
        )
        print(f"  Saved: {snap}")
        print("\n  Sample:")
        for r in all_records[:5]:
            print(f"    {r.instrument_number} | {r.debtor_name} | "
                  f"{'$'+str(r.amount) if r.amount else '?'}")

    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    if not args.no_db and all_records:
        print("  Writing to database...")
        stats = import_records(all_records)

    print(f"\n--- Summary ---")
    print(f"  Scraped  : {len(all_records)}")
    print(f"  PDFs     : {sum(1 for r in all_records if r.pdf_path)}")
    if not args.no_db:
        print(f"  Inserted : {stats['inserted']}")
        print(f"  Updated  : {stats.get('updated', 0)}")
        print(f"  Skipped  : {stats['skipped']}")


if __name__ == "__main__":
    main()