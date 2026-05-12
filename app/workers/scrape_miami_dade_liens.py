"""
scrape_miami_dade_liens.py
==========================
Miami-Dade IRS federal tax lien scraper.

Confirmed from network trace:
  API: POST /api/home/standardsearch (with x-recaptcha-token header)
  Results: GET /api/SearchResults/getStandardRecords?qs={token}
  CSV: GET /api/SearchResults/DownloadResults?qs={token}

Strategy:
  1. Login with registered account
  2. Navigate to Name/Document search (triggers reCAPTCHA token generation)
  3. Extract reCAPTCHA token from browser JS (using execute_async_script)
  4. POST to standardsearch API with token
  5. Download CSV using qs token
  6. Chunk by 90 days (portal uses date range, not page limit)

Usage:
  python scrape_miami_dade_liens.py --days-back 90 --no-db
  python scrape_miami_dade_liens.py --days-back 90
  python scrape_miami_dade_liens.py --start 01/01/2024 --end 12/31/2024
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_WDM = True
except ImportError:
    HAS_WDM = False

try:
    from app.core.db import get_connection
except ImportError:
    get_connection = None

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
COUNTY_NAME = "Miami-Dade"
BASE_URL    = "https://onlineservices.miamidadeclerk.gov/officialrecords"

# FIX 1: Single space before dash — double space caused 0 results from API
DOC_TYPES = [
    ("FEDERAL TAX LIEN  - FTL", "federal_tax_lien"),
    ("STATE TAX LIEN  - STL",   "state_tax_lien"),
]

CHUNK_DAYS  = 30  # API caps at 500 records — 30-day chunks stay well under that

# reCAPTCHA v3 site key (confirmed from page source)
RECAPTCHA_SITE_KEY = "6LfI8ikaAAAAAH0qlQMApskMGd1U6EqDyniH5t0x"

# Walk up from this file to find the project root (dir containing .env).
# Anchors on .env only 2014 avoids false-positives from app/ subdirs.
# Works correctly whether run as `python script.py` or `python -m app.workers.script`
def _find_project_root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in [here, here.parent, here.parent.parent, here.parent.parent.parent]:
        if (candidate / ".env").exists():
            return candidate
    return here

BASE_DIR = _find_project_root()
load_dotenv(BASE_DIR / ".env")

MDC_EMAIL    = os.getenv("MDC_EMAIL", "")
MDC_PASSWORD = os.getenv("MDC_PASSWORD", "")

RAW_DIR = BASE_DIR / "data" / "raw" / "miami_dade" / "irs_liens"
PDF_DIR = RAW_DIR / "pdfs"
DBG_DIR = RAW_DIR / "debug"
for _d in [RAW_DIR, PDF_DIR, DBG_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

IRS_NAMES = {"INTERNAL REV", "INTERNAL REVENUE", "IRS", "UNITED STATES"}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class IRSLienRecord:
    instrument_number: str
    debtor_name:       Optional[str]  = None
    address:           Optional[str]  = None
    filed_date:        Optional[date] = None
    book:              Optional[str]  = None
    page:              Optional[str]  = None
    pdf_path:          Optional[str]  = None
    pdf_url:           Optional[str]  = None
    raw_payload:       Dict           = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def parse_dt(v: Any) -> Optional[date]:
    # Split on T (ISO format) then space (MDC returns "1/15/2026 12:00:00 AM")
    s = clean(v).split("T")[0].split(" ")[0]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def is_irs(name: str) -> bool:
    u = name.upper()
    return any(m in u for m in IRS_NAMES)


def split_party(party: str):
    """Split 'INTERNAL REVENUE / JOHN DOE' → (irs_name, debtor_name)."""
    if " / " in party:
        a, b = party.split(" / ", 1)
        if is_irs(a.strip()):
            return a.strip(), b.strip()
        return b.strip(), a.strip()
    return "", party.strip()


def nowstamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_debug(driver, label: str):
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        driver.save_screenshot(str(DBG_DIR / f"{ts}_{label}.png"))
        (DBG_DIR / f"{ts}_{label}.html").write_text(
            driver.page_source, encoding="utf-8", errors="ignore")
        print(f"  [debug] saved {ts}_{label}.png/.html")
    except Exception:
        pass


def wait_for(driver, by, selector, timeout=15):
    try:
        return WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, selector)))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def make_driver(headless: bool = True) -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--window-size=1440,900")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    if headless:
        opts.add_argument("--headless=new")
    if HAS_WDM:
        return webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), options=opts)
    return webdriver.Chrome(options=opts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_visible(el) -> bool:
    """Return True if a WebElement is displayed, enabled, and has non-zero size."""
    try:
        return (
            el.is_displayed() and
            el.is_enabled() and
            el.size.get("width", 0) > 0 and
            el.size.get("height", 0) > 0
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

# The Official Records search portal (onlineservices.miamidadeclerk.gov)
# does NOT have its own login page. Authentication is handled by the
# separate User Management Services (UMS) portal on www2.miamidadeclerk.gov.
# After login there, session cookies are shared back to the search portal.
UMS_LOGIN_URL = "https://www2.miamidadeclerk.gov/usermanagementservices"

def login(driver) -> bool:
    """
    Log in via the UMS portal (www2.miamidadeclerk.gov/usermanagementservices).

    From the debug screenshot: the form has two bare <input> elements with NO
    type attribute — just plain <input> tags. Neither input[type='text'] nor
    input[type='email'] will match them. We select by position instead:
      - All inputs on the page → inputs[0] = User ID/Email, inputs[1] = Password
    The LOGIN button is a plain <button> with text "LOGIN".
    """
    print(f"  Logging in via UMS as {MDC_EMAIL} …")
    driver.get(UMS_LOGIN_URL)
    time.sleep(4)
    save_debug(driver, "01_ums_login_page")

    # Wait for inputs to render (JS page)
    wait_for(driver, By.TAG_NAME, "input", timeout=15)
    time.sleep(1)

    # Dump ALL inputs so we can see which are visible/hidden
    all_inputs = driver.find_elements(By.TAG_NAME, "input")
    print(f"  Found {len(all_inputs)} total input(s) on UMS page:")
    for i, el in enumerate(all_inputs):
        try:
            d = el.is_displayed()
            e = el.is_enabled()
        except Exception:
            d = e = False
        print(f"    [{i}] type={str(el.get_attribute('type') or 'none'):8} "
              f"name={str(el.get_attribute('name') or ''):25} "
              f"displayed={d} enabled={e}")

    # Filter to visible + enabled only (skips hidden framework fields)
    visible = [el for el in all_inputs if _is_visible(el)]
    print(f"  Visible/interactable inputs: {len(visible)}")

    if len(visible) < 2:
        print("  Not enough visible inputs — check debug screenshot")
        save_debug(driver, "01_ums_missing_fields")
        return False

    # First visible = User ID/Email, second visible = Password
    email_el = visible[0]
    pwd_el   = visible[1]
    print(f"  Using: email name={email_el.get_attribute('name')} | "
          f"pwd name={pwd_el.get_attribute('name')}")

    # Use JS to set values to bypass ElementNotInteractableException
    # on inputs with unusual styling/positioning
    driver.execute_script("arguments[0].value = arguments[1];", email_el, MDC_EMAIL)
    driver.execute_script("arguments[0].value = arguments[1];", pwd_el, MDC_PASSWORD)
    # Fire input+change events so React/Angular picks up the new values
    for field in [email_el, pwd_el]:
        for ev in ["input", "change"]:
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event(arguments[1], {bubbles:true}));",
                field, ev
            )

    # LOGIN button — plain <button> with text "LOGIN" (confirmed from screenshot)
    submit = None
    for btn in driver.find_elements(By.TAG_NAME, "button"):
        if "login" in btn.text.strip().lower():
            submit = btn
            break
    if not submit:
        submit = (
            wait_for(driver, By.CSS_SELECTOR, "button[type='submit']", timeout=5) or
            wait_for(driver, By.CSS_SELECTOR, "input[type='submit']",  timeout=5)
        )

    if submit:
        driver.execute_script("arguments[0].click();", submit)
        print("  Clicked LOGIN button")
    else:
        pwd_el.submit()
        print("  Submitted via form.submit()")

    time.sleep(5)
    save_debug(driver, "02_post_ums_login")

    page        = driver.page_source.lower()
    current_url = driver.current_url.lower()

    if (
        "logout"    in page or
        "sign out"  in page or
        "my account" in page or
        MDC_EMAIL.lower() in page or
        "usermanagementservices/home" in current_url
    ):
        print(f"  ✓ UMS login confirmed (URL: {driver.current_url})")
        # Visit the search portal to transfer auth cookies across subdomains
        driver.get(f"{BASE_URL}/")
        time.sleep(3)
        save_debug(driver, "03_search_portal_after_login")
        return True

    if "invalid" in page or "incorrect" in page or "failed" in page:
        print("  ✗ Login failed — check MDC_EMAIL / MDC_PASSWORD in .env")
        save_debug(driver, "02_ums_login_error")
        return False

    print(f"  Login status unclear (URL: {driver.current_url}) — continuing")
    driver.get(f"{BASE_URL}/")
    time.sleep(3)
    return True


# ---------------------------------------------------------------------------
# reCAPTCHA token
# ---------------------------------------------------------------------------

def search_with_recaptcha(driver, start: date, end: date, doc_type="FEDERAL TAX LIEN  - FTL") -> Optional[str]:
    """
    Navigate to the search page, get a fresh reCAPTCHA token, and immediately
    fire the search POST — all in ONE execute_async_script call so the token
    is always fresh and the page context never changes between steps.

    Confirmed from network trace:
      - POST with all params in query string, content-length: 0
      - Dates: YYYY-MM-DD
      - DOC_TYPE: "FEDERAL TAX LIEN  - FTL" (double space)
      - x-recaptcha-token header required
    """
    start_str = start.strftime("%Y-%m-%d")
    end_str   = end.strftime("%Y-%m-%d")
    # doc_type passed as parameter

    params = (
        f"partyName="
        f"&dateRangeFrom={start_str}"
        f"&dateRangeTo={end_str}"
        f"&documentType={urllib.parse.quote(doc_type)}"
        f"&searchT={urllib.parse.quote(doc_type)}"
        f"&firstQuery=y"
        f"&searchtype=Name/Document"
    )
    search_url = f"{BASE_URL}/api/home/standardsearch?{params}"

    print(f"  Getting reCAPTCHA token …")

    # Step 1: navigate to search page (loads grecaptcha)
    driver.get(f"{BASE_URL}/")
    time.sleep(3)
    try:
        nav = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//*[contains(text(),'Name') and contains(text(),'Document')]")
            )
        )
        driver.execute_script("arguments[0].click();", nav)
        time.sleep(3)
    except Exception:
        driver.get(f"{BASE_URL}/StandardSearch")
        time.sleep(3)

    save_debug(driver, "03_search_page")

    print(f"    POST {start_str} -> {end_str}  (token+fetch in one script)")

    # Step 2: get token AND fire search in a single async script
    # The token is generated and immediately used before any navigation occurs
    try:
        result = driver.execute_async_script("""
            var done       = arguments[arguments.length - 1];
            var searchUrl  = arguments[0];
            var siteKey    = arguments[1];

            function doSearch(token) {
                var hdrs = {
                    "Accept":       "application/json",
                    "Content-Type": "application/json; charset=utf-8",
                    "Origin":       "https://onlineservices.miamidadeclerk.gov",
                    "Referer":      "https://onlineservices.miamidadeclerk.gov/officialrecords/"
                };
                if (token) { hdrs["x-recaptcha-token"] = token; }

                fetch(searchUrl, {
                    method:      "POST",
                    credentials: "include",
                    headers:     hdrs,
                    body:        null
                })
                .then(function(r) { return r.text(); })
                .then(function(t) { done({ok: true, body: t, token_len: token ? token.length : 0}); })
                .catch(function(e) { done({ok: false, error: e.toString()}); });
            }

            try {
                if (typeof grecaptcha === 'undefined') {
                    console.log('grecaptcha not loaded — searching without token');
                    doSearch(null);
                    return;
                }
                grecaptcha.ready(function() {
                    grecaptcha.execute(siteKey, {action: 'search'})
                        .then(function(token) { doSearch(token); })
                        .catch(function(e)    { doSearch(null); });
                });
            } catch(e) {
                doSearch(null);
            }
        """, search_url, RECAPTCHA_SITE_KEY)

        if not result:
            print("  execute_async_script returned None")
            return None

        if not result.get("ok"):
            print(f"  Fetch error: {result.get('error')}")
            return None

        print(f"  ✓ reCAPTCHA token used ({result.get('token_len', 0)} chars)")

        data = json.loads(result["body"])
        print(f"  Response: {data}")

        qs = (data.get("qs") or data.get("queryString") or
              data.get("token") or data.get("QueryString"))
        if qs:
            print(f"  ✓ qs token ({len(str(qs))} chars)")
            return qs

        print(f"  isValidSearch={data.get('isValidSearch')} — no qs returned")
        return None

    except Exception as e:
        print(f"  search_with_recaptcha error: {e}")
        import traceback; traceback.print_exc()
        return None



# ---------------------------------------------------------------------------
# Download results
# ---------------------------------------------------------------------------

def fetch_results_via_browser(driver, qs: str) -> List[dict]:
    """
    Download results using the browser's own fetch() — this carries the correct
    server-side session that the qs token was issued against.

    Tries JSON records endpoint first (returns structured data), then CSV.
    The qs token is tied to the browser session and expires quickly, so we
    must use the browser rather than a separate requests.Session.
    """
    encoded_qs = urllib.parse.quote(qs, safe="")

    # Attempt 1: JSON records endpoint
    json_url = f"{BASE_URL}/api/SearchResults/getStandardRecords?qs={encoded_qs}"
    print(f"    Fetching JSON via browser: ...getStandardRecords?qs=<token>")
    try:
        result = driver.execute_async_script(f"""
            var done = arguments[arguments.length - 1];
            fetch({json.dumps(json_url)}, {{
                method: "GET",
                credentials: "include",
                headers: {{"Accept": "application/json, text/plain, */*"}}
            }})
            .then(function(r) {{ return r.text(); }})
            .then(function(t) {{ done(t); }})
            .catch(function(e) {{ done("ERROR:" + e.toString()); }});
        """)
        if result and not result.startswith("ERROR:"):
            try:
                data = json.loads(result)
                if isinstance(data, list) and len(data) > 0:
                    print(f"    JSON records: {len(data)} rows")
                    if not getattr(fetch_results_via_browser, "_cols_printed", False):
                        fetch_results_via_browser._cols_printed = True
                        print(f"    Columns: {list(data[0].keys())}")
                    return data
                elif isinstance(data, dict):
                    # Print all keys so we can see the actual structure
                    print(f"    JSON dict keys: {list(data.keys())}")
                    # Try every value that is a non-empty list
                    for key, val in data.items():
                        if isinstance(val, list) and len(val) > 0:
                            print(f"    Found records under key '{key}': {len(val)} rows")
                            if not getattr(fetch_results_via_browser, "_cols_printed", False):
                                fetch_results_via_browser._cols_printed = True
                                print(f"    Columns: {list(val[0].keys()) if isinstance(val[0], dict) else type(val[0])}")
                                print(f"    Sample: {val[0]}")
                            return val
                    print(f"    JSON dict had no list values. Full response: {str(data)[:500]}")
            except json.JSONDecodeError:
                print(f"    JSON parse failed, raw: {result[:200]}")
        elif result and result.startswith("ERROR:"):
            print(f"    Browser fetch error: {result}")
    except Exception as e:
        print(f"    execute_async_script error: {e}")

    # Attempt 2: CSV download endpoint
    csv_url = f"{BASE_URL}/api/SearchResults/DownloadResults?qs={encoded_qs}"
    print(f"    Fetching CSV via browser: ...DownloadResults?qs=<token>")
    try:
        result = driver.execute_async_script(f"""
            var done = arguments[arguments.length - 1];
            fetch({json.dumps(csv_url)}, {{
                method: "GET",
                credentials: "include",
                headers: {{"Accept": "text/csv, text/plain, */*"}}
            }})
            .then(function(r) {{
                if (!r.ok) {{ done("HTTP_ERROR:" + r.status); return; }}
                return r.text();
            }})
            .then(function(t) {{ done(t || ""); }})
            .catch(function(e) {{ done("ERROR:" + e.toString()); }});
        """)
        if result and not result.startswith("ERROR:") and not result.startswith("HTTP_ERROR:") and len(result) > 50:
            rows = list(csv.DictReader(io.StringIO(result)))
            print(f"    CSV: {len(rows)} rows")
            if rows and not getattr(fetch_results_via_browser, "_cols_printed", False):
                fetch_results_via_browser._cols_printed = True
                print(f"    Columns: {list(rows[0].keys())}")
            return rows
        else:
            print(f"    CSV result: {str(result)[:200] if result else 'empty'}")
    except Exception as e:
        print(f"    CSV browser fetch error: {e}")

    return []


# Keep requests-based fallbacks for reference but use browser fetch as primary
def download_csv(session: requests.Session, qs: str) -> List[dict]:
    """Requests-based CSV download (fallback only — use fetch_results_via_browser)."""
    try:
        r = session.get(f"{BASE_URL}/api/SearchResults/DownloadResults",
                        params={"qs": qs}, timeout=60)
        r.raise_for_status()
        if r.text and len(r.text) > 50:
            rows = list(csv.DictReader(io.StringIO(r.text)))
            print(f"    CSV (requests): {len(rows)} rows")
            return rows
    except Exception as e:
        print(f"    CSV (requests) error: {e}")
    return []


def fetch_json(session: requests.Session, qs: str) -> List[dict]:
    """Requests-based JSON fetch (fallback only — use fetch_results_via_browser)."""
    try:
        r = session.get(f"{BASE_URL}/api/SearchResults/getStandardRecords",
                        params={"qs": qs}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("records") or data.get("results") or data.get("data") or []
    except Exception as e:
        print(f"    JSON (requests) error: {e}")
    return []



# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------

# MDC document URL patterns confirmed from portal inspection.
# The per-record `qs` in each row is a viewer token; `key` is the document id.
DOC_VIEWER_PATTERNS = [
    # Pattern 1: document detail page using per-record qs token
    "{base}/DocumentDetail.aspx?qs={row_qs}",
    # Pattern 2: direct image via key
    "{base}/api/DocumentImages/{key}",
    # Pattern 3: book/page image path
    "{base}/DocumentImages/{book}/{page}",
    # Pattern 4: CFN-based viewer
    "{base}/DocumentViewer.aspx?QS={row_qs}",
]


def download_pdf(driver, rec: "IRSLienRecord") -> Optional[str]:
    """
    Attempt to download the lien document PDF for a record.
    Uses the browser (credentials: include) to follow auth-gated URLs.
    Saves to PDF_DIR / {cfn_safe}.pdf and returns the path string, or None.
    """
    import base64

    raw      = rec.raw_payload
    row_qs   = clean(raw.get("qs") or "")
    key      = str(raw.get("key") or "")
    book     = str(raw.get("reC_BOOK") or rec.book or "")
    page     = str(raw.get("reC_PAGE") or rec.page or "")
    cfn_safe = re.sub(r"[^\w\-]", "_", rec.instrument_number)[:80]
    pdf_path = PDF_DIR / f"mdc_{cfn_safe}.pdf"

    if pdf_path.exists() and pdf_path.stat().st_size > 500:
        return str(pdf_path)

    urls_to_try = []
    if row_qs:
        urls_to_try.append(f"{BASE_URL}/DocumentDetail.aspx?qs={urllib.parse.quote(row_qs, safe='')}")
        urls_to_try.append(f"{BASE_URL}/DocumentViewer.aspx?QS={urllib.parse.quote(row_qs, safe='')}")
    if key:
        urls_to_try.append(f"{BASE_URL}/api/DocumentImages/{key}")
    if book and page:
        urls_to_try.append(f"{BASE_URL}/DocumentImages/{book}/{page}")

    for url in urls_to_try:
        try:
            # Try fetching as binary via browser fetch
            result = driver.execute_async_script("""
                var done = arguments[arguments.length - 1];
                var url  = arguments[0];
                fetch(url, {method: "GET", credentials: "include"})
                .then(function(r) {
                    if (!r.ok) { done({status: r.status, data: null}); return; }
                    var ct = r.headers.get("content-type") || "";
                    return r.arrayBuffer().then(function(buf) {
                        // Convert to base64 for transfer
                        var bytes = new Uint8Array(buf);
                        var binary = "";
                        for (var i = 0; i < bytes.byteLength; i++) {
                            binary += String.fromCharCode(bytes[i]);
                        }
                        done({status: r.status, data: btoa(binary), ct: ct});
                    });
                })
                .catch(function(e) { done({status: 0, data: null, error: e.toString()}); });
            """, url)

            if not result or not result.get("data"):
                continue

            ct = result.get("ct", "")
            raw_bytes = base64.b64decode(result["data"])

            # Check if it's a PDF
            if raw_bytes[:4] == b"%PDF" or "pdf" in ct.lower():
                pdf_path.write_bytes(raw_bytes)
                return str(pdf_path)

            # If it's HTML (viewer page), try printing to PDF via Chrome DevTools
            if "html" in ct.lower() and len(raw_bytes) > 200:
                driver.get(url)
                time.sleep(3)
                pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                    "printBackground": True,
                    "paperWidth": 8.5,
                    "paperHeight": 11,
                })
                if pdf_data and pdf_data.get("data"):
                    pdf_path.write_bytes(base64.b64decode(pdf_data["data"]))
                    return str(pdf_path)

        except Exception as e:
            continue

    return None


def download_pdfs(
    driver,
    records: List["IRSLienRecord"],
    limit: Optional[int] = None,
) -> dict:
    """Download PDFs for a list of records. Returns stats dict."""
    stats = {"attempted": 0, "saved": 0, "failed": 0}
    targets = records[:limit] if limit else records

    for i, rec in enumerate(targets, 1):
        print(f"  [pdf {i}/{len(targets)}] {rec.instrument_number} | {rec.debtor_name}")
        stats["attempted"] += 1
        path = download_pdf(driver, rec)
        if path:
            rec.pdf_path = path
            rec.pdf_url  = rec.raw_payload.get("qs") and (
                f"{BASE_URL}/DocumentDetail.aspx?qs="
                + urllib.parse.quote(rec.raw_payload.get("qs",""), safe="")
            )
            stats["saved"] += 1
            print(f"    saved: {Path(path).name}")
        else:
            stats["failed"] += 1
            print(f"    failed")
        time.sleep(1)

    return stats

# ---------------------------------------------------------------------------
# Parse rows
# ---------------------------------------------------------------------------

def parse_row(row: dict) -> Optional[IRSLienRecord]:
    """
    Parse a row from the Miami-Dade recordingModels JSON response.

    Confirmed field names from live API response:
      seconD_PARTY  → debtor (person/entity with lien against them)
      firsT_PARTY   → creditor (IRS)
      clerk_File    → CFN e.g. "2026 R 31534"
      reC_DATE      → "1/15/2026 12:00:00 AM"
      reC_BOOKPAGE  → "35119/4192"
      reC_BOOK      → 35119
      reC_PAGE      → 4192
      address       → property address (often None)
      doC_TYPE      → "FEDERAL TAX LIEN - FTL"
    """
    # Debtor = second party (the taxpayer)
    debtor_raw = clean(row.get("seconD_PARTY") or row.get("parties") or "")

    # If parties field used, extract debtor from "IRS / DEBTOR NAME" format
    if " / " in debtor_raw and is_irs(debtor_raw.split(" / ")[0]):
        debtor_raw = debtor_raw.split(" / ", 1)[1].strip()

    if not debtor_raw or is_irs(debtor_raw):
        return None

    # CFN — clerk_File e.g. "2026 R 31534"
    cfn = clean(row.get("clerk_File") or "")
    if not cfn or cfn in ("0", ""):
        # fallback: build from year + seq
        yr  = str(row.get("cfN_YEAR") or "")
        seq = str(row.get("cfN_SEQ")  or "")
        cfn = f"MDC-{yr}-{seq}" if yr and seq else ""

    # Date
    filed_date = parse_dt(row.get("reC_DATE") or row.get("doC_DATE") or "")

    # Book / page
    book_page = clean(row.get("reC_BOOKPAGE") or "")
    book = page_val = ""
    if "/" in book_page:
        book, page_val = book_page.split("/", 1)
        book, page_val = book.strip(), page_val.strip()
    if not book:
        book     = str(row.get("reC_BOOK") or "")
        page_val = str(row.get("reC_PAGE") or "")

    if not cfn:
        cfn = f"MDC-IRS-{book}-{page_val}-{filed_date}"

    address = clean(row.get("address") or "")

    return IRSLienRecord(
        instrument_number = cfn[:200],
        debtor_name       = debtor_raw.title()[:250],
        address           = address[:250] if address else None,
        filed_date        = filed_date,
        book              = book or None,
        page              = page_val or None,
        raw_payload       = row,
    )


# ---------------------------------------------------------------------------
# Main scrape loop
# ---------------------------------------------------------------------------

def scrape(start: date, end: date, headless: bool = True) -> List[IRSLienRecord]:
    print(f"\n[Miami-Dade IRS Liens] {start} → {end}")

    driver = make_driver(headless=headless)
    all_records: List[IRSLienRecord] = []
    seen: set = set()

    try:
        logged_in = login(driver)
        if not logged_in:
            print("  WARNING: Proceeding without confirmed login — may hit reCAPTCHA blocks")

        current = start
        while current <= end:
            chunk_end = min(current + timedelta(days=CHUNK_DAYS - 1), end)

            for doc_type_str, lien_type_tag in DOC_TYPES:
                qs = search_with_recaptcha(driver, current, chunk_end, doc_type=doc_type_str)

                if qs:
                    time.sleep(1)
                    rows = fetch_results_via_browser(driver, qs)
                    if not rows:
                        print(f"    No rows for {doc_type_str}")

                    added = 0
                    for row in rows:
                        rec = parse_row(row)
                        if rec and rec.instrument_number not in seen:
                            rec.raw_payload["_lien_type"] = lien_type_tag
                            seen.add(rec.instrument_number)
                            all_records.append(rec)
                            added += 1

                    print(f"    +{added} new {lien_type_tag} records (total: {len(all_records)})")
                else:
                    print(f"    No qs for {doc_type_str} {current} → {chunk_end}")

                time.sleep(2)

            current = chunk_end + timedelta(days=1)

    except KeyboardInterrupt:
        print("\n  Interrupted — saving what we have …")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()
        save_debug(driver, "error")
    finally:
        driver.quit()

    print(f"\n  Total unique records: {len(all_records)}")
    return all_records


# ---------------------------------------------------------------------------
# DB import
# ---------------------------------------------------------------------------

def ensure_cols():
    if not get_connection:
        return
    conn = get_connection()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for col in ["pdf_path TEXT", "amount NUMERIC", "lien_source TEXT"]:
                try:
                    cur.execute(
                        f"ALTER TABLE normalized_liens ADD COLUMN IF NOT EXISTS {col}")
                except Exception:
                    pass
    finally:
        conn.close()


def get_county_id(cur) -> int:
    cur.execute("SELECT id FROM counties WHERE county_name = %s", (COUNTY_NAME,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO counties (county_name, state, active, created_at) "
        "VALUES (%s,'FL',true,NOW()) RETURNING id", (COUNTY_NAME,))
    return cur.fetchone()[0]


def import_records(records: List[IRSLienRecord]) -> Dict[str, int]:
    if not records or not get_connection:
        return {"inserted": 0, "skipped": 0}
    ensure_cols()
    conn = get_connection()
    conn.autocommit = False
    stats = {"inserted": 0, "skipped": 0}
    try:
        with conn.cursor() as cur:
            cid = get_county_id(cur)
            for rec in records:
                if not rec.instrument_number or not rec.debtor_name:
                    stats["skipped"] += 1
                    continue
                n_hash = (f"mdc_irs::{rec.instrument_number[:80]}::"
                          f"{rec.debtor_name[:40]}")
                try:
                    cur.execute("""
                        INSERT INTO normalized_liens (
                            county_id, raw_lien_id, debtor_name, business_name,
                            address_1, filing_type, lien_type, filed_date,
                            normalized_hash, lien_source
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (normalized_hash) DO UPDATE SET
                            debtor_name = EXCLUDED.debtor_name,
                            address_1   = COALESCE(EXCLUDED.address_1, normalized_liens.address_1),
                            filed_date  = COALESCE(EXCLUDED.filed_date, normalized_liens.filed_date)
                        RETURNING id, (xmax = 0) AS is_insert
                    """, (
                        cid, None,
                        rec.debtor_name[:250], None,
                        rec.address[:250] if rec.address else None,
                        "FEDERAL TAX LIEN - FTL", "federal_tax_lien",
                        rec.filed_date, n_hash, "IRS",
                    ))
                    result = cur.fetchone()
                    if result and result[1]:
                        stats["inserted"] += 1
                    else:
                        stats["skipped"] += 1
                except Exception as e:
                    conn.rollback()
                    print(f"  Insert error for {rec.instrument_number}: {e}")
                    stats["skipped"] += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"  [DB] Fatal: {e}")
        raise
    finally:
        conn.close()
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Miami-Dade IRS Federal Tax Lien scraper")
    ap.add_argument("--days-back", type=int, default=90,
                    help="Pull records N days back from today (default: 90)")
    ap.add_argument("--start", type=str, default=None,
                    help="Start date MM/DD/YYYY (overrides --days-back)")
    ap.add_argument("--end",   type=str, default=None,
                    help="End date MM/DD/YYYY (default: today)")
    ap.add_argument("--no-db",      action="store_true", help="Skip DB import, save JSON only")
    ap.add_argument("--no-headless", action="store_true", help="Show browser window (for debugging)")
    ap.add_argument("--no-pdf",     action="store_true", help="Skip PDF download")
    ap.add_argument("--pdf-limit",  type=int, default=None, help="Max PDFs to download (for testing)")
    args = ap.parse_args()

    end   = date.today()
    start = end - timedelta(days=args.days_back)

    if args.end:
        end = datetime.strptime(args.end, "%m/%d/%Y").date()
    if args.start:
        start = datetime.strptime(args.start, "%m/%d/%Y").date()

    records = scrape(start, end, headless=not args.no_headless)

    if records:
        snap = RAW_DIR / f"mdc_irs_{nowstamp()}.json"
        snap.write_text(
            json.dumps([{
                "instrument": r.instrument_number,
                "debtor":     r.debtor_name,
                "address":    r.address,
                "filed_date": str(r.filed_date),
                "book":       r.book,
                "page":       r.page,
                "pdf_path":   r.pdf_path,
            } for r in records], indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n  Saved JSON snapshot: {snap}")
        print("\nSample records:")
        for r in records[:5]:
            print(f"  {r.instrument_number} | {r.debtor_name} | {r.filed_date}")

    # Download PDFs (reuse the browser from scrape — open a new one here)
    pdf_stats = {"attempted": 0, "saved": 0, "failed": 0}
    if not args.no_pdf and records:
        print(f"\nDownloading PDFs to {PDF_DIR} …")
        pdf_driver = make_driver(headless=not args.no_headless)
        try:
            login(pdf_driver)
            pdf_stats = download_pdfs(pdf_driver, records, limit=args.pdf_limit)
        finally:
            pdf_driver.quit()
        print(f"  PDFs: {pdf_stats['saved']}/{pdf_stats['attempted']} saved, {pdf_stats['failed']} failed")

    stats = {"inserted": 0, "skipped": 0}
    if not args.no_db and records:
        print("\nWriting to DB …")
        stats = import_records(records)

    print(f"\n{'─'*40}")
    print(f"Miami-Dade IRS Lien Summary")
    print(f"  Date range : {start} → {end}")
    print(f"  Scraped    : {len(records)}")
    if not args.no_db:
        print(f"  Inserted   : {stats['inserted']}")
        print(f"  Skipped    : {stats['skipped']}")
    print(f"  Debug files: {DBG_DIR}")


if __name__ == "__main__":
    main()