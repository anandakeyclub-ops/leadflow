"""
scrape_stjohns_liens.py
======================
Martin County IRS federal + state tax lien scraper.
Built from live inspector session 2026-05-09.

Portal: https://apps.stjohnsclerk.com/Landmark/Home/Index/LandMarkWeb/Home/Index
- No login required
- Accept disclaimer -> Document Type Search form
- documentType-DocumentType textarea = 'LN TX' (federal lien codes)
- beginDate-DocumentType / endDate-DocumentType
- Submit via id='submit-DocumentType'
- Results in #resultsTable (DataTables), 4 pages confirmed
- Columns: Grantor(1), Grantee(2), Date(3), DocType(4), ClerkFileNum(8)
- Pagination: id='resultsTable_next'

Usage:
  python -m app.workers.scrape_stjohns_liens --days-back 180 --no-pdf
  python -m app.workers.scrape_stjohns_liens --days-back 14
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

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
# Config — confirmed from inspector 2026-05-09
# ---------------------------------------------------------------------------
COUNTY_NAME = "St. Johns"
SOURCE_NAME = "stjohns_liens"

HOME_URL = "https://apps.stjohnsclerk.com/Landmark/Home/Index"

# Document type codes confirmed from inspector
# documentType-DocumentType textarea showed 'CCJ,LN' after modal selection
# We want FTL (Federal Tax Lien) and STL (State Tax Lien)
# Try both the short codes and full names
LIEN_DOC_TYPES = "LN TX"   # Will be set via modal checkboxes

# Confirmed field IDs from inspector
FIELD_BEGIN     = "beginDate-DocumentType"
FIELD_END       = "endDate-DocumentType"
FIELD_SUBMIT    = "submit-DocumentType"
FIELD_DOC_TYPE  = "documentType-DocumentType"  # textarea that shows selected types
SELECT_MODAL    = "documentTypeSelection-DocumentType"  # opens modal

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR  = BASE_DIR / "data" / "raw" / "stjohns" / "liens"
DBG_DIR  = RAW_DIR / "debug"
PDF_DIR  = RAW_DIR / "pdfs"
for d in [RAW_DIR, DBG_DIR, PDF_DIR]:
    d.mkdir(parents=True, exist_ok=True)

PORTAL_BASE = HOME_URL.split("/LandMarkWeb")[0]


def download_pdf(driver, rec: dict) -> Optional[str]:
    """Download lien PDF via LandMarkWeb Document/Index/{doc_id}."""
    import base64
    doc_id = str(rec.get("doc_id", "") or "").strip()
    instr  = str(rec.get("instrument_number", "") or "").strip()
    if not doc_id and not instr:
        return None

    safe     = re.sub(r"[^\w\-]", "_", instr or doc_id)[:60]
    pdf_path = PDF_DIR / f"stjohns_{safe}.pdf"
    if pdf_path.exists() and pdf_path.stat().st_size > 500:
        return str(pdf_path)

    urls = []
    if doc_id:
        urls.append(f"{PORTAL_BASE}/LandMarkWeb/Document/Index/{doc_id}")
    if instr:
        urls.append(f"{PORTAL_BASE}/LandMarkWeb/Document/MultipleView?instrumentNumber={instr}")

    for url in urls:
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

IRS_KEYWORDS = {
    "INTERNAL REVENUE", "IRS", "UNITED STATES", "US TREASURY",
    "FLORIDA DEPT", "FL DEPT", "DEPARTMENT OF REVENUE", "STATE OF FLORIDA",
}

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
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    )
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    if HAS_WDM:
        drv = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    else:
        drv = webdriver.Chrome(options=opts)
    drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    })
    return drv

def save_debug(driver, label: str):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        driver.save_screenshot(str(DBG_DIR / f"{ts}_{label}.png"))
    except Exception:
        pass

def nowstamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

# ---------------------------------------------------------------------------
# Step 1: Load portal and accept disclaimer
# ---------------------------------------------------------------------------
def load_and_accept(driver) -> bool:
    print(f"  Loading: {HOME_URL}")
    driver.get(HOME_URL)
    time.sleep(4)

    # Accept disclaimer
    for xpath in [
        "//a[normalize-space(text())='Accept']",
        "//button[normalize-space(text())='Accept']",
        "//input[@value='Accept']",
    ]:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(3)
            print(f"  Disclaimer accepted — {driver.current_url}")
            break
        except Exception:
            continue

    # Login to bypass reCAPTCHA — St. Johns has reCAPTCHA on search
    # Logged-in users skip reCAPTCHA (confirmed from inspector)
    user = os.getenv("STJOHNS_USERNAME", "")
    pwd  = os.getenv("STJOHNS_PASSWORD", "")
    if user and pwd:
        try:
            login_link = driver.find_element(By.XPATH,
                "//a[contains(text(),'Log On') or contains(text(),'Login')]")
            driver.execute_script("arguments[0].click();", login_link)
            time.sleep(3)
            driver.find_element(By.ID, "UserName").send_keys(user)
            driver.find_element(By.ID, "Password").send_keys(pwd)
            driver.find_element(By.CSS_SELECTOR,
                "input[type=submit], button[type=submit]").click()
            time.sleep(3)
            print(f"  Logged in as {user}")
            driver.get(HOME_URL)
            time.sleep(3)
        except Exception as e:
            print(f"  Login attempt failed: {e}")
    else:
        print("  NOTE: Set STJOHNS_USERNAME/PASSWORD in .env to bypass reCAPTCHA")

    # Click Document Type Search tab
    time.sleep(1)
    try:
        tab = driver.find_element(By.ID, "searchCriteriaDocuments-tab")
        driver.execute_script("arguments[0].click();", tab)
        time.sleep(2)
        print(f"  Clicked Document Type Search tab")
    except Exception:
        try:
            tab = driver.find_element(By.XPATH,
                "//a[contains(text(),'Document Type')]")
            driver.execute_script("arguments[0].click();", tab)
            time.sleep(2)
        except Exception as e:
            print(f"  Could not click Document Type tab: {e}")

    return True

# ---------------------------------------------------------------------------
# Step 2: Select lien document types via modal
# ---------------------------------------------------------------------------
def select_lien_types(driver) -> bool:
    """
    Confirmed from inspector 2026-05-09:
    - Category 'LIEN' sets documentCategory to '29,63,70,71,131,136,137,141'
    - Modal checkboxes include: LIEN(71), LIS PENDENS(72) — only want LIEN
    - Federal Tax Lien = val 71 (LIEN category)
    - Modal closes via GetDocTypeString() — confirmed working
    - textarea gets 'CCJ,LN' after correct selection
    """
    # Step 1: Select LIEN category to filter checkboxes
    try:
        driver.execute_script("""
            var sel = document.getElementById('documentCategory-DocumentType');
            if (!sel) return;
            for (var i=0; i<sel.options.length; i++) {
                if (sel.options[i].text.toUpperCase() === 'LIEN') {
                    sel.value = sel.options[i].value;
                    sel.dispatchEvent(new Event('change', {bubbles:true}));
                    break;
                }
            }
        """)
        time.sleep(1)
        cat = driver.execute_script(
            "var s=document.getElementById('documentCategory-DocumentType'); "
            "return s ? s.options[s.selectedIndex].text : '';")
        print(f"  Category: {cat!r}")
    except Exception as e:
        print(f"  Category error: {e}")

    # Step 2: Open modal
    try:
        btn = driver.find_element(By.ID, SELECT_MODAL)
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(3)
        print(f"  Modal opened")
    except Exception as e:
        print(f"  Modal open error: {e}")
        return False

    # Step 3: Uncheck everything first, then check ONLY tax lien types
    # Federal Tax Lien = 'LIEN' val=71, State Tax Lien may be separate
    # Explicitly exclude LIS PENDENS (val=72) and others
    # Check ALL checkboxes that contain 'LIEN' in their label
    # Don't hardcode vals — they differ per county (Martin=71, StJohns=90, etc.)
    result = driver.execute_script("""
        var checked = [], unchecked = [];
        document.querySelectorAll('input[type=checkbox]').forEach(function(cb) {
            var label = document.querySelector('label[for="' + cb.id + '"]');
            var text = (label ? label.innerText : (cb.parentElement ? cb.parentElement.innerText : '')).trim().toUpperCase();

            // Only want plain LIEN — exclude NOTICE OF CONTEST, LIS PENDENS, etc.
            var wantLien = (text === 'LIEN' || text === 'FEDERAL TAX LIEN' || text === 'STATE TAX LIEN' || text === 'TAX LIEN')
                        || (text.indexOf('LIEN') >= 0
                            && text.indexOf('NOTICE') < 0
                            && text.indexOf('CONTEST') < 0
                            && text.indexOf('LIS PENDENS') < 0
                            && text.indexOf('SUBORDINAT') < 0
                            && text.indexOf('RELEASE') < 0
                            && text.indexOf('SATISFAC') < 0
                            && text.indexOf('PARTIAL') < 0
                            && text.indexOf('MECHANIC') < 0);

            if (wantLien && !cb.checked) {
                cb.checked = true;
                cb.dispatchEvent(new Event('change', {bubbles:true}));
                checked.push(cb.value + ':' + text.substring(0,30));
            } else if (!wantLien && cb.checked) {
                cb.checked = false;
                cb.dispatchEvent(new Event('change', {bubbles:true}));
                unchecked.push(cb.value + ':' + text.substring(0,30));
            } else if (wantLien && cb.checked) {
                checked.push(cb.value + ':' + text.substring(0,30) + '(already)');
            }
        });
        return {checked: checked, unchecked: unchecked};
    """)

    print(f"  Checked: {result['checked']}")
    print(f"  Unchecked: {result['unchecked']}")

    # Step 4: Click the "Select" button to confirm and close modal
    # Confirmed from screenshot: modal has "Select" (blue) and "Close" (white) buttons
    # "Select" confirms the checked items and closes modal
    closed = False
    for by, sel in [
        (By.XPATH, "//div[contains(@class,'modal')]//button[normalize-space(text())='Select']"),
        (By.XPATH, "//button[normalize-space(text())='Select' and not(contains(@class,'selectAll'))]"),
        (By.XPATH, "//div[@class='modal-footer']//button[contains(text(),'Select')]"),
    ]:
        try:
            btn = driver.find_element(by, sel)
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(1)
                print(f"  Clicked Select button")
                closed = True
                break
        except Exception:
            continue

    if not closed:
        # Try the × close button top right of modal
        try:
            x_btn = driver.find_element(By.XPATH,
                "//div[contains(@class,'modal')]//button[contains(@class,'close') or text()='×']")
            driver.execute_script("arguments[0].click();", x_btn)
            time.sleep(1)
            print(f"  Clicked × close button")
            closed = True
        except Exception:
            pass

    if not closed:
        print(f"  WARNING: Could not close modal")

    time.sleep(1)
    textarea_val = driver.execute_script(
        "var el=document.getElementById('documentType-DocumentType');"
        "return el ? el.value : '';")
    print(f"  After close, textarea={textarea_val!r}")

    if not textarea_val:
        # Inject directly as confirmed fallback
        textarea_val = driver.execute_script("""
            var ta = document.getElementById('documentType-DocumentType');
            if (ta) { ta.value = 'CCJ,LN'; return ta.value; }
            return '';
        """)
        print(f"  Fallback injection: textarea={textarea_val!r}")

    return bool(textarea_val)
    # Step 1: Select LIEN category from the dropdown BEFORE opening modal
    # This populates the checkbox list with lien doc types
    try:
        driver.execute_script("""
            var sel = document.getElementById('documentCategory-DocumentType');
            if (!sel) return;
            // Find the LIEN option
            for (var i=0; i<sel.options.length; i++) {
                if (sel.options[i].text.toUpperCase().indexOf('LIEN') >= 0) {
                    sel.value = sel.options[i].value;
                    sel.dispatchEvent(new Event('change', {bubbles:true}));
                    break;
                }
            }
        """)
        time.sleep(1)
        cat_val = driver.execute_script(
            "var s=document.getElementById('documentCategory-DocumentType'); return s?s.value:''")
        print(f"  Category set to: {cat_val!r}")
    except Exception as e:
        print(f"  Category select: {e}")

    # Step 2: Open the modal
    try:
        btn = driver.find_element(By.ID, SELECT_MODAL)
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(3)
        print(f"  Opened document type modal")
    except Exception as e:
        print(f"  Could not open modal: {e}")

    # Step 3: Check all visible lien checkboxes
    checked = driver.execute_script("""
        var checked = [];
        var cbs = document.querySelectorAll('input[type=checkbox]');
        cbs.forEach(function(cb) {
            var label = document.querySelector('label[for="' + cb.id + '"]');
            var text = label ? label.innerText.trim().toUpperCase() : '';
            if (!text && cb.parentElement) {
                text = cb.parentElement.innerText.trim().toUpperCase();
            }
            // Check if this is a lien type
            var isLien = text.indexOf('LIEN') >= 0 ||
                         text.indexOf('LN ') >= 0 ||
                         ['71','72','73','74','75','CCJ','LN','STL','FTL','TL','TLI'].indexOf(cb.value) >= 0;
            if (isLien && !cb.checked) {
                cb.checked = true;
                cb.dispatchEvent(new Event('change', {bubbles:true}));
                checked.push({val: cb.value, text: text.substring(0,40)});
            } else if (isLien && cb.checked) {
                checked.push({val: cb.value, text: text.substring(0,40), already: true});
            }
        });
        return checked;
    """)
    print(f"  Checked lien types: {checked}")

    # Step 4: Close modal — try GetDocTypeString multiple times (confirmed working on 3rd try)
    textarea_val = ''
    for attempt in range(5):
        try:
            driver.execute_script("GetDocTypeString();")
            time.sleep(0.5)
        except Exception:
            pass
        # Also try clicking Done input
        try:
            done = driver.find_element(By.CSS_SELECTOR, "input[value='Done']")
            if done.is_displayed():
                driver.execute_script("arguments[0].click();", done)
                time.sleep(0.5)
        except Exception:
            pass
        textarea_val = driver.execute_script(
            "var el=document.getElementById('documentType-DocumentType'); return el?el.value:'';")
        if textarea_val:
            print(f"  Modal closed — textarea={textarea_val!r} (attempt {attempt+1})")
            break
    else:
        # Final fallback — inject directly since we know the codes
        textarea_val = driver.execute_script("""
            var ta = document.getElementById('documentType-DocumentType');
            if (ta) {
                ta.value = 'CCJ,LN';
                ta.dispatchEvent(new Event('change',{bubbles:true}));
                return ta.value;
            }
            return '';
        """)
        print(f"  Fallback injection: textarea={textarea_val!r}")

    return bool(textarea_val)
    # Strategy 1: Set textarea directly with confirmed codes from inspector
    # Inspector showed val='CCJ,LN' after manual selection
    lien_codes_to_try = [
        "LN TX",        # Federal Tax Lien codes
        "LN,CCJ",       # Confirmed from inspector session
        "CCJ,LN",       # Same, different order
        "LN",           # Just federal
    ]

    # First open the modal to see what's available
    try:
        btn = driver.find_element(By.ID, SELECT_MODAL)
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(3)  # Wait for AJAX labels to load
        print(f"  Opened document type modal — waiting for labels...")
        time.sleep(2)  # Extra wait for label AJAX

        # Dump checkboxes WITH their labels now
        checkboxes = driver.execute_script("""
            var cbs = [];
            document.querySelectorAll('input[type=checkbox]').forEach(function(cb) {
                // Try multiple label strategies
                var label = document.querySelector('label[for="' + cb.id + '"]');
                var text = label ? label.innerText.trim() : '';
                // Also check parent/sibling text
                if (!text && cb.parentElement) {
                    text = cb.parentElement.innerText.trim().replace(/^\\s+/, '');
                }
                cbs.push({
                    id: cb.id,
                    value: cb.value,
                    text: text.substring(0, 50),
                    checked: cb.checked
                });
            });
            return cbs;
        """)

        print(f"  Checkboxes with labels ({len(checkboxes)}):")
        lien_keywords = ['TAX LIEN', 'FTL', 'STL', 'FEDERAL', 'STATE TAX',
                         'IRS', 'LIEN', 'LN ', 'LN,', ',LN']
        checked_ids = []

        for cb in checkboxes:
            text_upper = (cb['text'] or '').upper()
            val_upper  = (cb['value'] or '').upper()
            if any(k in text_upper for k in lien_keywords) or \
               cb['value'] in ['LN', 'TX', 'STL', 'FTL', 'CCJ']:
                print(f"    CHECKING: {cb['text']!r} val={cb['value']!r}")
                try:
                    el = driver.find_element(By.ID, cb['id'])
                    driver.execute_script(
                        "arguments[0].checked=true; "
                        "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", el)
                    checked_ids.append(cb['id'])
                except Exception:
                    pass

        if not checked_ids:
            print(f"  No lien checkboxes found by label — first 10 options:")
            for cb in checkboxes[:10]:
                print(f"    id={cb['id']!r} val={cb['value']!r} text={cb['text']!r}")

        # Close modal
        for xpath in ["//input[@value='Done']", "//button[text()='Done']",
                      "//a[text()='Done']"]:
            try:
                done = driver.find_element(By.XPATH, xpath)
                if done.is_displayed():
                    driver.execute_script("arguments[0].click();", done)
                    time.sleep(1)
                    break
            except Exception:
                continue
        else:
            try:
                driver.execute_script("GetDocTypeString();")
                time.sleep(1)
            except Exception:
                pass

    except Exception as e:
        print(f"  Modal error: {e}")

    # Check what got into the textarea
    selected = driver.execute_script(
        "var el=document.getElementById('documentType-DocumentType'); return el?el.value:'';")
    print(f"  After modal, textarea = {selected!r}")

    # Strategy 2: If modal didn't work, inject codes directly into textarea
    if not selected:
        print(f"  Injecting lien codes directly into textarea...")
        # Try each set of codes until one produces results
        injected = driver.execute_script("""
            var ta = document.getElementById('documentType-DocumentType');
            if (!ta) return false;
            // Use the confirmed value from inspector: CCJ,LN
            ta.value = 'LN TX';
            ta.dispatchEvent(new Event('input', {bubbles:true}));
            ta.dispatchEvent(new Event('change', {bubbles:true}));
            return ta.value;
        """)
        print(f"  Injected: {injected!r}")
        selected = injected

    return bool(selected)

# ---------------------------------------------------------------------------
# Step 3: Set dates and submit
# ---------------------------------------------------------------------------
def set_dates_and_submit(driver, start: date, end: date) -> bool:
    start_str = start.strftime("%m/%d/%Y")
    end_str   = end.strftime("%m/%d/%Y")

    # Confirmed field IDs from inspector — Document Type Search tab
    # beginDate-DocumentType and endDate-DocumentType
    for fid, val in [(FIELD_BEGIN, start_str), (FIELD_END, end_str)]:
        try:
            el = driver.find_element(By.ID, fid)
            # Clear and set
            driver.execute_script("""
                arguments[0].value = '';
                arguments[0].value = arguments[1];
                arguments[0].dispatchEvent(new Event('input',  {bubbles:true}));
                arguments[0].dispatchEvent(new Event('change', {bubbles:true}));
                arguments[0].dispatchEvent(new Event('blur',   {bubbles:true}));
            """, el, val)
            actual = driver.execute_script("return arguments[0].value;", el)
            print(f"  {fid}: {actual}")
        except Exception as e:
            print(f"  Date field {fid} error: {e}")

    # Set to show max records (2000)
    try:
        driver.execute_script("""
            var sel = document.getElementById('numberOfRecords-DocumentType');
            if (sel) {
                sel.value = '2000';
                sel.dispatchEvent(new Event('change', {bubbles:true}));
            }
        """)
        print(f"  Set max records to 2000")
    except Exception:
        pass

    # Verify dates before submitting
    verify = driver.execute_script("""
        return {
            begin: (document.getElementById('beginDate-DocumentType')||{}).value || '',
            end:   (document.getElementById('endDate-DocumentType')||{}).value || '',
            docType: (document.getElementById('documentType-DocumentType')||{}).value || ''
        };
    """)
    print(f"  Pre-submit: begin={verify['begin']!r} end={verify['end']!r} docType={verify['docType']!r}")

    # Submit via id='submit-DocumentType' (confirmed from inspector)
    try:
        btn = driver.find_element(By.ID, FIELD_SUBMIT)
        driver.execute_script("arguments[0].click();", btn)
        print(f"  Submitted")
        # St. Johns has reCAPTCHA — pause for manual solve if not logged in
        user = os.getenv("STJOHNS_USERNAME", "")
        if not user:
            print(f"\n  *** reCAPTCHA required ***")
            print(f"  Solve the CAPTCHA in the browser window, then press Enter here...")
            try:
                input("  [Press Enter after solving CAPTCHA] ")
            except EOFError:
                time.sleep(30)  # headless fallback
        return True
    except Exception as e:
        print(f"  Submit error: {e}")
        return False

# ---------------------------------------------------------------------------
# Step 4: Wait for results and parse all pages
# ---------------------------------------------------------------------------
def wait_and_parse(driver) -> List[dict]:
    print(f"  Waiting for results...")
    rows_found = []

    # Wait for resultsTable to have rows — confirmed from inspector
    for attempt in range(25):
        time.sleep(2)
        check = driver.execute_script("""
            var rt = document.getElementById('resultsTable');
            var addLinks = document.querySelectorAll('[onclick*="AddDocumentToList"]').length;
            var rows = rt ? rt.querySelectorAll('tbody tr:not(.dataTables_empty)').length : 0;
            return {rows: rows, addLinks: addLinks};
        """)
        print(f"  [{attempt+1}] rows={check['rows']} addLinks={check['addLinks']}")
        if check['rows'] > 0 or check['addLinks'] > 0:
            print(f"  Results loaded!")
            break
    else:
        save_debug(driver, "no_results")
        print(f"  Results never loaded")
        return []

    # Parse all pages
    page = 1
    while True:
        print(f"  Parsing page {page}...")

        page_rows = driver.execute_script("""
            var results = [];
            var tbl = document.getElementById('tableWrapper') ||
                      document.getElementById('resultsTable');
            if (!tbl) return results;

            // The table renders ALL records in ONE giant row of sequential TDs
            // Each record = 28 cells. Confirmed offsets from column dump:
            // +0  = row counter (1,2,3...)      skip first row (header info)
            // +2  = empty
            // +4  = I/O indicator
            // +6  = Grantor (creditor/IRS agency)
            // +7  = Grantee (debtor/taxpayer)
            // +8  = Record Date (MM/DD/YYYY)
            // +9  = Doc Type (LIEN, CERTIFIED COPY...)
            // +11 = Book
            // +12 = Page
            // +13 = Instrument Number (ClerkFileNum)
            // +27 = Doc ID (AddDocumentToList)
            var RECORD_SIZE = 28;

            var rows = tbl.querySelectorAll('tbody tr');
            for (var r=0; r<rows.length; r++) {
                var allCells = rows[r].querySelectorAll('td');
                if (allCells.length < RECORD_SIZE) continue;

                function ct(c) { return c ? (c.innerText||c.textContent||'').trim() : ''; }

                // Skip the first cell group — it's "Returned X records of X"
                var startIdx = 0;
                if (ct(allCells[0]).indexOf('Returned') >= 0) startIdx = 1;

                // Parse each 28-cell record group
                var i = startIdx;
                while (i * RECORD_SIZE + RECORD_SIZE <= allCells.length || i === startIdx) {
                    var base = i * RECORD_SIZE;
                    if (base + 13 >= allCells.length) break;

                    var counter  = ct(allCells[base + 1]);
                    var grantor  = ct(allCells[base + 6]);
                    var grantee  = ct(allCells[base + 7]);
                    var recDate  = ct(allCells[base + 8]);
                    var docType  = ct(allCells[base + 9]);
                    var instrNum = ct(allCells[base + 13]);
                    var docId    = ct(allCells[base + 27]);

                    // Skip if no meaningful data
                    if (!grantor && !grantee) { i++; continue; }
                    // Skip pure numbers (row counters leaked as data)
                    if (!grantor && /^\\d+$/.test(grantee)) { i++; continue; }

                    results.push({
                        grantor: grantor,
                        grantee: grantee,
                        record_date: recDate,
                        doc_type: docType,
                        instrument: instrNum || docId,
                        doc_id: docId,
                        counter: counter
                    });
                    i++;
                }
            }
            return results;
        """)
        print(f"    {len(page_rows)} rows on page {page}")
        rows_found.extend(page_rows)

        # Next page — confirmed id='resultsTable_next' from inspector
        try:
            next_btn = driver.find_element(By.ID, "resultsTable_next")
            cls = next_btn.get_attribute("class") or ""
            if "disabled" in cls:
                print(f"  No more pages")
                break
            driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(2)
            page += 1
        except Exception:
            break

    return rows_found

# ---------------------------------------------------------------------------
# Parse into IRSLienRecord format
# ---------------------------------------------------------------------------
def parse_record(row: dict, lien_type: str) -> Optional[dict]:
    grantor = row.get('grantor', '')
    grantee = row.get('grantee', '')

    # Determine debtor vs creditor
    # IRS/state agency is creditor, taxpayer is debtor
    g_upper = grantee.upper()
    gr_upper = grantor.upper()

    if any(k in g_upper for k in IRS_KEYWORDS):
        debtor, creditor = grantor, grantee
    elif any(k in gr_upper for k in IRS_KEYWORDS):
        debtor, creditor = grantee, grantor
    else:
        debtor, creditor = grantor, grantee

    if not debtor or len(debtor.strip()) < 2:
        return None

    rec_date = row.get('record_date', '')
    filed = None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            filed = datetime.strptime(rec_date.split()[0], fmt).date()
            break
        except Exception:
            pass

    return {
        "instrument_number": row.get('instrument', '') or row.get('doc_id', ''),
        "debtor_name":       debtor.title(),
        "creditor_name":     creditor.title() if creditor else None,
        "lien_type":         lien_type,
        "filed_date":        filed,
        "raw_payload":       row,
    }

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

def import_records(records: list) -> dict:
    if not records or not get_connection:
        return {"inserted": 0, "skipped": 0}
    conn  = get_connection()
    conn.autocommit = False
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    try:
        with conn.cursor() as cur:
            county_id = get_county_id(cur)
            for rec in records:
                if not rec.get("instrument_number") or not rec.get("debtor_name"):
                    stats["skipped"] += 1
                    continue
                source_id = f"{SOURCE_NAME}::{rec['instrument_number']}"
                payload   = json.dumps(rec["raw_payload"], default=str)
                try:
                    cur.execute("""
                        INSERT INTO raw_liens
                            (county_id, source_file, source_record_id, raw_payload, filed_date)
                        VALUES (%s,%s,%s,%s::jsonb,%s)
                        ON CONFLICT (county_id, source_record_id) DO UPDATE SET
                            raw_payload = EXCLUDED.raw_payload
                        RETURNING id, (xmax = 0) AS is_insert
                    """, (county_id, SOURCE_NAME, source_id, payload, rec["filed_date"]))
                    rl = cur.fetchone()
                    if not rl or not rl[1]:
                        stats["skipped"] += 1
                        continue
                    raw_id = rl[0]
                except Exception as e:
                    conn.rollback()
                    stats["skipped"] += 1
                    continue

                n_hash = f"stjohns::{rec['instrument_number']}::{(rec['debtor_name'] or '')[:40]}"
                pdf_path_val = (rec.get("pdf_path") or "")[:250] or None
                try:
                    cur.execute("""
                        INSERT INTO normalized_liens (
                            county_id, raw_lien_id, debtor_name,
                            lien_type, filed_date, normalized_hash, pdf_path
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (normalized_hash) DO UPDATE SET
                            debtor_name = EXCLUDED.debtor_name,
                            filed_date  = COALESCE(EXCLUDED.filed_date, normalized_liens.filed_date),
                            pdf_path    = COALESCE(EXCLUDED.pdf_path, normalized_liens.pdf_path)
                    """, (county_id, raw_id, rec["debtor_name"],
                          rec["lien_type"], rec["filed_date"], n_hash, pdf_path_val))
                    stats["inserted"] += 1
                except Exception as e:
                    conn.rollback()
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
    parser = argparse.ArgumentParser(description="Martin County lien scraper")
    parser.add_argument("--days-back", type=int, default=180)
    parser.add_argument("--no-db",     action="store_true")
    parser.add_argument("--visible",   action="store_true")
    parser.add_argument("--no-pdf",    action="store_true")
    args = parser.parse_args()

    end   = date.today()
    start = end - timedelta(days=args.days_back)
    print(f"\n[St. Johns IRS Liens] {start} → {end}")

    driver = make_driver(visible=args.visible)
    raw_rows = []

    try:
        # Step 1: load and accept disclaimer
        load_and_accept(driver)
        save_debug(driver, "01_after_accept")

        # Step 2: select lien document types
        select_lien_types(driver)
        save_debug(driver, "02_after_modal")

        # Step 3: set dates and submit
        set_dates_and_submit(driver, start, end)
        save_debug(driver, "03_after_submit")

        # Step 4: wait for results and parse
        raw_rows = wait_and_parse(driver)

    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()
        save_debug(driver, "error")
    finally:
        driver.quit()

    # Convert to records
    records = []
    seen = set()
    for row in raw_rows:
        rec = parse_record(row, "federal_tax_lien")
        if rec and rec["instrument_number"] not in seen:
            seen.add(rec["instrument_number"])
            # Carry doc_id for PDF download
            rec["doc_id"] = row.get("doc_id", "")
            records.append(rec)

    print(f"\n  Total scraped: {len(records)}")

    # Download PDFs unless --no-pdf
    if not args.no_pdf and records:
        print(f"  Downloading PDFs...")
        driver2 = make_driver(visible=args.visible)
        try:
            load_and_accept(driver2)
            pdf_count = 0
            for i, rec in enumerate(records):
                pdf_path = download_pdf(driver2, rec)
                if pdf_path:
                    rec["pdf_path"] = pdf_path
                    pdf_count += 1
                if (i + 1) % 10 == 0:
                    print(f"    PDFs: {pdf_count}/{i+1}")
            print(f"  PDFs downloaded: {pdf_count}/{len(records)}")
        except Exception as e:
            print(f"  PDF download error: {e}")
        finally:
            driver2.quit()

    # Save snapshot
    if records:
        snap = RAW_DIR / f"stjohns_liens_{nowstamp()}.json"
        snap.write_text(json.dumps([{
            "instrument": r["instrument_number"],
            "debtor":     r["debtor_name"],
            "filed_date": str(r["filed_date"]),
        } for r in records], indent=2, default=str), encoding="utf-8")
        print(f"  Saved: {snap.name}")
        print("\n  Sample:")
        for r in records[:5]:
            print(f"    {r['instrument_number']} | {r['debtor_name']} | {r['filed_date']}")

    stats = {"inserted": 0, "skipped": 0}
    if not args.no_db and records:
        stats = import_records(records)

    print(f"\n--- St. Johns Summary ---")
    print(f"  Scraped  : {len(records)}")
    print(f"  Inserted : {stats.get('inserted', 0)}")
    print(f"  Skipped  : {stats.get('skipped', 0)}")


if __name__ == "__main__":
    main()