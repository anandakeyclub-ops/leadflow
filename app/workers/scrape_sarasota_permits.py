"""
scrape_sarasota_permits.py
==========================
Sarasota County building permits via Accela Citizen Access + Legacy portal.
Ported directly from Permit_Bot download_sarasota_weekly.py (confirmed working).

Portal 1: https://aca-prod.accela.com/SARASOTACO (login required)
Portal 2: https://building.scgov.net (legacy, no login)

Field IDs confirmed from session recording 2026-04-27.

Usage:
  python -m app.workers.scrape_sarasota_permits --days-back 14 --no-db
  python -m app.workers.scrape_sarasota_permits --days-back 180
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
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

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
COUNTY_NAME = "Sarasota"
SOURCE_NAME = "sarasota_accela"
DAYS_BACK   = 14

ACCELA_BASE         = "https://aca-prod.accela.com/SARASOTACO"
ACCELA_LOGIN_URL    = f"{ACCELA_BASE}/Account/Login.aspx"
ACCELA_SEARCH_URL   = (
    f"{ACCELA_BASE}/Cap/CapHome.aspx?module=Building&TabName=Building"
    "&TabList=Home%7C0%7CBuilding%7C1%7CPlanning%7C2%7CLicenses%7C3"
    "%7CPublicWorks%7C4%7CEnforcement%7C5%7CFire%7C6%7CAdministrative"
    "%7C7%7CCurrentTabIndex%7C1"
)
ACCELA_DATE_START   = "ctl00_PlaceHolderMain_generalSearchForm_txtGSStartDate"
ACCELA_DATE_END     = "ctl00_PlaceHolderMain_generalSearchForm_txtGSEndDate"
ACCELA_SEARCH_BTN   = "ctl00_PlaceHolderMain_btnNewSearch"
ACCELA_DOWNLOAD_ID  = "ctl00_PlaceHolderMain_PermitList_gdvPermitList_gdvPermitListtop4btnExport"

LEGACY_URL = "https://building.scgov.net/"

SARASOTA_USER = os.getenv("SARASOTA_ACCELA_USER", "")
SARASOTA_PASS = os.getenv("SARASOTA_ACCELA_PASS", "")

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR  = BASE_DIR / "data" / "raw" / "sarasota" / "permits"
DBG_DIR  = RAW_DIR / "debug"
for _d in [RAW_DIR, DBG_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

OUTPUT_COLUMNS = [
    "PERMITNO", "RECORD_TYPE", "PERMIT_DESCRIPTION", "FULL_ADDRESS",
    "OWNER_NAME", "CONTRACTOR_NAME", "FINAL_VALUATION",
    "LAST_ISSUED_DATE", "STATUS", "TRADE",
]

COLUMN_MAP = {
    "Record #": "PERMITNO", "Record Number": "PERMITNO", "Permit #": "PERMITNO",
    "Record Type": "RECORD_TYPE", "Permit Type": "RECORD_TYPE", "Type": "RECORD_TYPE",
    "Description": "PERMIT_DESCRIPTION", "Project Name": "PROJECT_NAME",
    "Address": "FULL_ADDRESS", "Site Address": "FULL_ADDRESS", "Location": "FULL_ADDRESS",
    "Issued Date": "LAST_ISSUED_DATE", "Issue Date": "LAST_ISSUED_DATE",
    "Applied Date": "LAST_ISSUED_DATE", "Date": "LAST_ISSUED_DATE",
    "Status": "STATUS", "Permit Status": "STATUS",
    "Valuation": "FINAL_VALUATION", "Job Value": "FINAL_VALUATION", "Value": "FINAL_VALUATION",
    "Contractor": "CONTRACTOR_NAME", "Contractor Name": "CONTRACTOR_NAME",
    "Owner": "OWNER_NAME", "Owner Name": "OWNER_NAME",
}

def nowstamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def safe_find(driver, by, value):
    try:
        return driver.find_element(by, value)
    except Exception:
        return None

def classify_trade(text: str) -> str:
    low = text.lower()
    if any(k in low for k in ["roof", "reroof", "re-roof"]): return "roofing"
    if any(k in low for k in ["mechanical", "hvac", "air condition", "a/c", " ac ", "heat pump"]): return "hvac"
    if any(k in low for k in ["electrical", "electric", "generator", "panel"]): return "electrical"
    if any(k in low for k in ["plumbing", "water heater", "repipe", "gas", "sewer"]): return "plumbing"
    if any(k in low for k in ["pool", "spa", "swimming"]): return "pool"
    if any(k in low for k in ["solar", "photovoltaic", "pv system"]): return "solar"
    return "general_contractor"

def normalize_rows(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    df = pd.DataFrame(rows)
    df = df.rename(columns=COLUMN_MAP)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    for col in df.columns:
        df[col] = df[col].astype(str).str.strip()
    if "PERMIT_DESCRIPTION" not in df.columns:
        df["PERMIT_DESCRIPTION"] = df.get("RECORD_TYPE", "")
    for col in OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df["TRADE"] = df.apply(
        lambda r: classify_trade(
            f"{r.get('RECORD_TYPE','')} {r.get('PERMIT_DESCRIPTION','')}"
        ), axis=1
    )
    return df[OUTPUT_COLUMNS]



# ── Strategy 1: Legacy portal (building.scgov.net) ────────────────────────────

def build_fresh_driver(download_dir: Path) -> webdriver.Chrome:
    """Fresh Chrome with temp profile — confirmed working from Permit_Bot."""
    import tempfile as _tempfile
    temp_profile = Path(_tempfile.mkdtemp(prefix="sarasota_chrome_"))
    opts = Options()
    opts.add_argument(f"--user-data-dir={temp_profile}")
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_experimental_option("prefs", {
        "download.default_directory":   str(download_dir),
        "download.prompt_for_download": False,
        "download.directory_upgrade":   True,
        "safebrowsing.enabled":         True,
    })
    if HAS_WDM:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), options=opts)
    else:
        driver = webdriver.Chrome(options=opts)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver



def scrape_legacy_portal(raw_dir: Path) -> list[dict]:
    """
    MudBlazor React SPA. Find inputs by label[for] attribute.
    Labels confirmed: 'Start Date', 'End Date'
    Button: class=mud-button-filled-primary, text=SEARCH PERMITS
    """
    today      = datetime.today()
    start_date = (today - timedelta(days=DAYS_BACK)).strftime("%m/%d/%Y")
    end_date   = today.strftime("%m/%d/%Y")
    all_rows: list[dict] = []

    print(f"  [Legacy] Date range: {start_date} → {end_date}")
    driver = build_fresh_driver(raw_dir)

    try:
        driver.get(LEGACY_URL)
        time.sleep(5)
        print(f"  [Legacy] Title: {driver.title!r}")

        def fill_by_label(label_text: str, value: str) -> bool:
            try:
                label = driver.find_element(
                    By.XPATH, f"//label[normalize-space(text())='{label_text}']"
                )
                for_id = label.get_attribute("for")
                el = driver.find_element(By.ID, for_id)
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.3)
                driver.execute_script("arguments[0].click();", el)
                time.sleep(0.2)
                el.send_keys(Keys.CONTROL + "a")
                el.send_keys(value)
                el.send_keys(Keys.TAB)
                print(f"  [Legacy] '{label_text}' filled → #{for_id}")
                return True
            except Exception as e:
                print(f"  [Legacy] Could not fill '{label_text}': {type(e).__name__}")
                return False

        fill_by_label("Start Date", start_date)
        time.sleep(0.5)
        fill_by_label("End Date", end_date)
        time.sleep(0.5)

        # Click Search Permits button (mud-button-filled-primary)
        submitted = False
        for xpath in [
            "//button[contains(@class,'mud-button-filled-primary')]"
            "[.//span[contains(translate(normalize-space(.),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'SEARCH PERMITS')]]",
            "//button[contains(@class,'mud-button-filled-primary')]",
        ]:
            el = safe_find(driver, By.XPATH, xpath)
            if el and el.is_displayed() and el.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.3)
                driver.execute_script("arguments[0].click();", el)
                print(f"  [Legacy] Clicked: {el.text.strip()!r}")
                submitted = True
                break

        if not submitted:
            print("  [Legacy] Search button not found")

        time.sleep(8)
        print(f"  [Legacy] After search: {driver.title!r}")

        # Extract results — MudBlazor table or ARIA grid
        page = 1
        while page <= 30:
            soup = BeautifulSoup(driver.page_source, "lxml")
            page_rows = []

            for tbl in soup.find_all("table"):
                ths = [th.get_text(" ", strip=True) for th in tbl.find_all("th")]
                if not ths or not any(kw in " ".join(ths).lower()
                                      for kw in ["permit", "address", "date", "type", "status"]):
                    continue
                for tr in tbl.find_all("tr")[1:]:
                    cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                    if len(cells) >= 3 and any(c.strip() for c in cells):
                        page_rows.append(dict(zip(ths, cells)))
                if page_rows:
                    break

            if not page_rows:
                grid_rows = soup.find_all(attrs={"role": "row"})
                if grid_rows:
                    headers = [c.get_text(" ", strip=True)
                               for c in grid_rows[0].find_all(
                                   attrs={"role": ["columnheader", "cell"]})]
                    for row_el in grid_rows[1:]:
                        cells = [c.get_text(" ", strip=True)
                                 for c in row_el.find_all(attrs={"role": "cell"})]
                        if len(cells) >= 3 and any(c.strip() for c in cells):
                            page_rows.append(dict(zip(headers, cells)))

            print(f"  [Legacy] Page {page}: {len(page_rows)} rows")
            all_rows.extend(page_rows)

            if not page_rows:
                if page == 1:
                    src = raw_dir / "legacy_results_source.html"
                    src.write_text(driver.page_source, encoding="utf-8", errors="ignore")
                    snippet = soup.get_text(" ", strip=True)[200:600]
                    print(f"  [Legacy] Page text sample: {snippet!r}")
                break

            next_el = safe_find(driver, By.XPATH,
                "//button[@aria-label='Next page'] | "
                "//button[contains(@class,'mud-pagination-item-next')]"
            )
            if not next_el or not next_el.is_displayed() or not next_el.is_enabled():
                break
            driver.execute_script("arguments[0].click();", next_el)
            time.sleep(3)
            page += 1

    except Exception as e:
        print(f"  [Legacy] Error: {e}")
        import traceback; traceback.print_exc()
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return all_rows


# ── Strategy 2: Accela — temp profile login + confirmed download button ────────

def scrape_accela(raw_dir: Path, username: str, password: str) -> list[dict]:
    """
    Logs into Sarasota Accela using a fresh temp Chrome profile.
    Uses confirmed field IDs from session recording 2026-04-27.
    Downloads CSV via the confirmed Download Results __doPostBack target.
    """
    today      = datetime.today()
    start_date = (today - timedelta(days=DAYS_BACK)).strftime("%m/%d/%Y")
    end_date   = today.strftime("%m/%d/%Y")
    all_rows: list[dict] = []

    print(f"  [Accela] Date range: {start_date} → {end_date}")
    driver = build_fresh_driver(raw_dir)

    try:
        # ── Login ──────────────────────────────────────────────────────────
        # IMPORTANT: Sarasota Accela throws Error.aspx on direct login URL
        # Must warm the session by loading home first, then clicking Login
        print(f"  [Accela] Loading portal home to warm session...")
        driver.get(ACCELA_BASE)
        time.sleep(5)
        print(f"  [Accela] Home: {driver.title!r} — {driver.current_url}")

        # Click the Login link from the home page nav
        login_clicked = False
        for xpath in [
            "//a[@id='ctl00_HeaderNavigation_btnLogin']",
            "//a[normalize-space(text())='Login']",
            "//a[contains(@href,'Login')]",
        ]:
            el = safe_find(driver, By.XPATH, xpath)
            if el and el.is_displayed():
                driver.execute_script("arguments[0].click();", el)
                print(f"  [Accela] Clicked Login link")
                login_clicked = True
                break

        time.sleep(4)
        print(f"  [Accela] Login page: {driver.title!r}")
        print(f"  [Accela] Login URL:  {driver.current_url}")

        # Login form is inside an iframe: /SARASOTACO/AngularUI/CommunityView/login-panel
        # Must switch into it before filling credentials
        print(f"  [Accela] Switching into login iframe...")
        try:
            iframe = driver.find_element(By.ID, "LoginFrame")
            driver.switch_to.frame(iframe)
            print(f"  [Accela] Inside LoginFrame")
        except Exception as e:
            print(f"  [Accela] Could not find LoginFrame by ID: {e}")
            # Try by src
            try:
                iframe = driver.find_element(
                    By.XPATH,
                    "//iframe[contains(@src,'login-panel')]"
                )
                driver.switch_to.frame(iframe)
                print(f"  [Accela] Inside login-panel iframe")
            except Exception as e2:
                print(f"  [Accela] No login iframe found: {e2}")

        time.sleep(3)

        # Now find inputs inside the iframe
        inputs = driver.find_elements(By.TAG_NAME, "input")
        print(f"  [Accela] Inputs inside iframe ({len(inputs)}):")
        for i in inputs:
            print(f"    id={i.get_attribute('id')!r} "
                  f"type={i.get_attribute('type')!r} "
                  f"placeholder={i.get_attribute('placeholder')!r} "
                  f"visible={i.is_displayed()}")

        # Fill by type
        text_inputs = [i for i in inputs
                       if i.get_attribute("type") in ("text", "email", "")
                       and i.is_displayed()]
        pass_inputs = [i for i in inputs
                       if i.get_attribute("type") == "password"
                       and i.is_displayed()]

        if text_inputs:
            text_inputs[0].clear()
            text_inputs[0].send_keys(username)
            print(f"  [Accela] Username filled in iframe")
        if pass_inputs:
            pass_inputs[0].clear()
            pass_inputs[0].send_keys(password)
            print(f"  [Accela] Password filled in iframe")

        time.sleep(0.5)

        # Submit inside iframe
        submitted = False
        for xpath in [
            "//button[@type='submit']",
            "//button[contains(translate(.,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'LOGIN')]",
            "//button[contains(translate(.,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'SIGN IN')]",
            "//input[@type='submit']",
        ]:
            el = safe_find(driver, By.XPATH, xpath)
            if el and el.is_displayed():
                driver.execute_script("arguments[0].click();", el)
                print(f"  [Accela] Submit clicked: {el.get_attribute('outerHTML')[:80]!r}")
                submitted = True
                break

        if not submitted and pass_inputs:
            pass_inputs[0].send_keys(Keys.RETURN)
            print(f"  [Accela] Submitted via Enter")

        # Switch back to main page
        driver.switch_to.default_content()
        time.sleep(1)

        time.sleep(8)
        print(f"  [Accela] After login: {driver.title!r}")
        print(f"  [Accela] After login URL: {driver.current_url}")

        # Verify login
        src = driver.page_source
        logged_in = (
            "logout" in src.lower() or
            "account management" in src.lower() or
            "logged in as" in src.lower() or
            "dana richard" in src.lower()
        )
        print(f"  [Accela] Authenticated: {'YES ✓' if logged_in else 'NO ✗'}")
        if not logged_in:
            print("  [Accela] Login failed — check SARASOTA_ACCELA_PASSWORD in .env")
            return []

        # ── Navigate to Building search ────────────────────────────────────
        print(f"  [Accela] Loading search page...")
        driver.get(ACCELA_SEARCH_URL)
        time.sleep(6)
        print(f"  [Accela] Search: {driver.current_url}")

        if "login" in driver.current_url.lower():
            print("  [Accela] Redirected to login — session lost")
            return []

        # Navigate directly to the general search form
        # The Building tab search form URL confirmed from session recording
        GENERAL_SEARCH_URL = (
            f"{ACCELA_BASE}/Cap/CapHome.aspx?module=Building&TabName=Building"
            "&TabList=Home%7C0%7CBuilding%7C1%7CPlanning%7C2%7CLicenses%7C3"
            "%7CPublicWorks%7C4%7CEnforcement%7C5%7CFire%7C6%7CAdministrative"
            "%7C7%7CCurrentTabIndex%7C1&sp=Search"
        )
        print(f"  [Accela] Navigating to general search form...")
        driver.get(GENERAL_SEARCH_URL)
        time.sleep(4)
        print(f"  [Accela] Search form URL: {driver.current_url}")

        # If Search Applications link is visible, click it to activate the form
        try:
            els = driver.find_elements(
                By.XPATH, "//a[normalize-space(text())='Search Applications']"
            )
            for el in els:
                if el.is_displayed():
                    driver.execute_script("arguments[0].click();", el)
                    print(f"  [Accela] Clicked Search Applications")
                    time.sleep(4)
                    break
        except Exception:
            pass

        # Confirm date fields exist before filling
        start_el = safe_find(driver, By.ID, ACCELA_DATE_START)
        end_el   = safe_find(driver, By.ID, ACCELA_DATE_END)
        print(f"  [Accela] Date fields: start={'FOUND' if start_el and start_el.is_displayed() else 'NOT FOUND'} end={'FOUND' if end_el and end_el.is_displayed() else 'NOT FOUND'}")

        if not start_el or not start_el.is_displayed():
            # Try clicking Search Applications again from current page
            try:
                els = driver.find_elements(
                    By.XPATH, "//a[normalize-space(text())='Search Applications']"
                )
                for el in els:
                    if el.is_displayed():
                        driver.execute_script("arguments[0].click();", el)
                        print(f"  [Accela] Clicked Search Applications (retry)")
                        time.sleep(5)
                        break
            except Exception:
                pass
            start_el = safe_find(driver, By.ID, ACCELA_DATE_START)

        # ── Fill confirmed date fields ─────────────────────────────────────
        # Use JavaScript to set values AND trigger ASP.NET change events
        # This ensures UpdatePanel captures the values before postback
        for fid, val, label in [
            (ACCELA_DATE_START, start_date, "Start date"),
            (ACCELA_DATE_END,   end_date,   "End date"),
        ]:
            driver.execute_script(f"""
                var el = document.getElementById('{fid}');
                if (el) {{
                    el.value = '{val}';
                    el.dispatchEvent(new Event('input', {{bubbles: true}}));
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    el.dispatchEvent(new Event('blur', {{bubbles: true}}));
                }}
            """)
            time.sleep(0.3)
            # Verify value was set
            el = safe_find(driver, By.ID, fid)
            actual = el.get_attribute("value") if el else "NOT FOUND"
            print(f"  [Accela] {label}: set={val!r} actual={actual!r}")

        # ── Submit search ──────────────────────────────────────────────────
        # Verify dates are still set before submitting
        start_check = driver.execute_script(
            f"var el=document.getElementById('{ACCELA_DATE_START}'); return el ? el.value : 'NOT FOUND';"
        )
        end_check = driver.execute_script(
            f"var el=document.getElementById('{ACCELA_DATE_END}'); return el ? el.value : 'NOT FOUND';"
        )
        print(f"  [Accela] Pre-submit check: start={start_check!r} end={end_check!r}")

        # If values cleared, re-set them
        if start_check != start_date:
            driver.execute_script(f"""
                var el = document.getElementById('{ACCELA_DATE_START}');
                if (el) {{ el.value = '{start_date}'; }}
                var el2 = document.getElementById('{ACCELA_DATE_END}');
                if (el2) {{ el2.value = '{end_date}'; }}
            """)
            print(f"  [Accela] Re-set dates via JS")

        # Click Search button — confirmed ID: ctl00_PlaceHolderMain_btnNewSearch
        el = safe_find(driver, By.ID, ACCELA_SEARCH_BTN)
        if el:
            driver.execute_script("arguments[0].click();", el)
            print(f"  [Accela] Search submitted via #{ACCELA_SEARCH_BTN}")
        else:
            driver.execute_script(
                "WebForm_DoPostBackWithOptions(new WebForm_PostBackOptions("
                f"'ctl00$PlaceHolderMain$btnNewSearch','',true,'','',false,true));"
            )
            print(f"  [Accela] Search submitted via WebForm_DoPostBackWithOptions")

        time.sleep(15)  # wait longer for results grid to fully render
        print(f"  [Accela] Results: {driver.title!r}")
        print(f"  [Accela] Results URL: {driver.current_url}")

        # Dump all visible links to find download button
        all_links = driver.find_elements(By.TAG_NAME, "a")
        print(f"  [Accela] Links on page ({len(all_links)}):")
        for a in all_links:
            txt = a.text.strip()
            aid = a.get_attribute("id") or ""
            if txt or "export" in aid.lower() or "download" in aid.lower():
                print(f"    id={aid!r} text={txt!r}")

        # ── Find and click Download Results ───────────────────────────────
        dl_el = None

        # 1. Try confirmed ID
        el = safe_find(driver, By.ID, ACCELA_DOWNLOAD_ID)
        if el and el.is_displayed():
            dl_el = el
            print(f"  [Accela] Found by confirmed ID")

        # 2. Search by text/id patterns
        if not dl_el:
            for xpath in [
                "//a[contains(translate(.,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'DOWNLOAD')]",
                "//a[contains(@id,'btnExport') or contains(@id,'Export') or contains(@id,'Download')]",
                "//input[contains(translate(@value,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'DOWNLOAD')]",
            ]:
                for el in driver.find_elements(By.XPATH, xpath):
                    if el.is_displayed():
                        dl_el = el
                        print(f"  [Accela] Found via XPath: id={el.get_attribute('id')!r} text={el.text.strip()!r}")
                        break
                if dl_el:
                    break

        if dl_el:
            print(f"  [Accela] Clicking download button...")
            driver.execute_script("arguments[0].click();", dl_el)
        else:
            print(f"  [Accela] Download button not found — saving debug HTML")
            dbg = raw_dir / "accela_results_debug.html"
            dbg.write_text(driver.page_source, encoding="utf-8", errors="ignore")
            print(f"  [Accela] Saved → {dbg.name}")

        # Wait for download
        # Record time BEFORE the wait so age check is accurate
        download_start = datetime.now().timestamp()
        time.sleep(30)  # Accela CSV can take up to 30s to generate

        # Check both the raw_dir AND the system Downloads folder
        import os
        search_dirs = [
            raw_dir,
            Path.home() / "Downloads",
            Path(os.environ.get("USERPROFILE", "")) / "Downloads",
        ]

        # Find the downloaded file — search broadly, any CSV from last 5 minutes
        csv_files = []
        check_time = datetime.now().timestamp()
        for search_dir in search_dirs:
            try:
                for pattern in ["*.csv", "RecordList*.csv", "sarasota*.csv"]:
                    for f in search_dir.glob(pattern):
                        age = check_time - f.stat().st_mtime
                        if age < 300 and f.stat().st_size > 1000:  # within 5 min, >1KB
                            csv_files.append(f)
                            print(f"  [Accela] Found CSV: {f} ({f.stat().st_size:,}b, age={age:.0f}s)")
            except Exception:
                continue
        # Also check raw_dir for any recent sarasota CSV (from today or yesterday)
        for existing in sorted(raw_dir.glob("sarasota_permits_*.csv"), reverse=True):
            if existing.stat().st_size > 10000:
                csv_files.append(existing)
                print(f"  [Accela] Found existing CSV: {existing.name} ({existing.stat().st_size:,}b)")
                break  # just the most recent one
        if not csv_files:
            import glob as _glob
            for pattern in [
                str(Path.home() / "Downloads" / "*.csv"),
                str(Path.home() / "Desktop" / "*.csv"),
                "C:/Users/*/Downloads/*.csv",
                "C:/Users/*/Desktop/*.csv",
            ]:
                for fp in _glob.glob(pattern):
                    f = Path(fp)
                    age = check_time - f.stat().st_mtime
                    if age < 300 and f.stat().st_size > 1000:
                        csv_files.append(f)
                        print(f"  [Accela] Found CSV (wide search): {f} ({f.stat().st_size:,}b)")

        if csv_files:
            f = max(csv_files, key=lambda x: x.stat().st_mtime)
            std_name = raw_dir / f"sarasota_permits_{today.strftime('%Y-%m-%d')}.csv"
            print(f"  [Accela] CSV found: {f} → renaming to {std_name.name}")
            try:
                if f != std_name:
                    f.replace(std_name)
                    f = std_name
                print(f"  [Accela] Downloaded: {f.name} ({f.stat().st_size:,} bytes)")
                df = pd.read_csv(f, dtype=str)
                print(f"  [Accela] Rows: {len(df)}, Columns: {list(df.columns)[:6]}")
                return df.to_dict("records")
            except Exception as e:
                print(f"  [Accela] CSV error: {e}")
                import traceback; traceback.print_exc()
                return []
        else:
            print(f"  [Accela] No CSV downloaded — falling back to HTML parse")
            soup = BeautifulSoup(driver.page_source, "lxml")
            for tbl in soup.find_all("table"):
                ths = [th.get_text(" ", strip=True) for th in tbl.find_all("th")]
                if not ths or not any(kw in " ".join(ths).lower()
                                      for kw in ["record", "permit", "date", "type"]):
                    continue
                for tr in tbl.find_all("tr")[1:]:
                    cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                    if len(cells) >= 3 and any(c.strip() for c in cells):
                        all_rows.append(dict(zip(ths, cells)))
                if all_rows:
                    print(f"  [Accela] Parsed {len(all_rows)} rows from HTML")
                    break
            if not all_rows:
                dbg = raw_dir / "accela_results_debug.html"
                dbg.write_text(driver.page_source, encoding="utf-8", errors="ignore")
                print(f"  [Accela] Saved debug HTML → {dbg.name}")

    except Exception as e:
        print(f"  [Accela] Error: {e}")
        import traceback; traceback.print_exc()
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return all_rows


# ---------------------------------------------------------------------------
# DB import (LeadFlow format)
# ---------------------------------------------------------------------------

@dataclass
class PermitRecord:
    permit_number:       str
    permit_type:         Optional[str]  = None
    owner_name:          Optional[str]  = None
    contractor_name:     Optional[str]  = None
    address:             Optional[str]  = None
    project_description: Optional[str]  = None
    issued_date:         Optional[date] = None
    raw_payload:         Dict           = field(default_factory=dict)




def rows_to_records(rows: list) -> List[PermitRecord]:
    df = normalize_rows(rows)
    records = []
    for _, r in df.iterrows():
        num = str(r.get("PERMITNO","")).strip()
        if not num or num == "nan":
            continue
        issued = None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                issued = datetime.strptime(str(r.get("LAST_ISSUED_DATE","")).split()[0], fmt).date()
                break
            except Exception:
                pass
        records.append(PermitRecord(
            permit_number       = num,
            permit_type         = str(r.get("RECORD_TYPE","")) or None,
            owner_name          = str(r.get("OWNER_NAME","")) or None,
            contractor_name     = str(r.get("CONTRACTOR_NAME","")) or None,
            address             = str(r.get("FULL_ADDRESS","")) or None,
            project_description = str(r.get("PERMIT_DESCRIPTION","")) or None,
            issued_date         = issued,
            raw_payload         = r.to_dict(),
        ))
    return records


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


def import_records(records: List[PermitRecord]) -> Dict[str, int]:
    if not records or not get_connection:
        return {"inserted": 0, "updated": 0, "skipped": 0}
    conn  = get_connection()
    conn.autocommit = False
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    try:
        with conn.cursor() as cur:
            county_id = get_county_id(cur)
            for rec in records:
                if not rec.permit_number:
                    stats["skipped"] += 1
                    continue
                source_id = f"{SOURCE_NAME}::{rec.permit_number}"
                payload   = json.dumps({
                    "permit_number": rec.permit_number,
                    "permit_type":   rec.permit_type,
                    "address":       rec.address,
                    "owner":         rec.owner_name,
                    "contractor":    rec.contractor_name,
                    "description":   rec.project_description,
                    "issued_date":   str(rec.issued_date) if rec.issued_date else None,
                }, default=str)
                raw_id = None
                try:
                    cur.execute("""
                        INSERT INTO raw_permits
                            (county_id, source_file, source_record_id, raw_payload, issued_date)
                        VALUES (%s,%s,%s,%s::jsonb,%s)
                        ON CONFLICT (county_id, source_record_id) DO UPDATE SET
                            raw_payload = EXCLUDED.raw_payload
                        RETURNING id, (xmax = 0) AS is_insert
                    """, (county_id, SOURCE_NAME, source_id, payload, rec.issued_date))
                    rl = cur.fetchone()
                    if rl:
                        raw_id = rl[0]
                except Exception as e:
                    conn.rollback()
                    stats["skipped"] += 1
                    continue
                n_hash = f"sar_permit::{rec.permit_number}"
                try:
                    cur.execute("""
                        INSERT INTO normalized_permits (
                            county_id, raw_permit_id, permit_number,
                            permit_type, owner_name, business_name,
                            address_1, project_description, issued_date,
                            normalized_hash
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (normalized_hash) DO UPDATE SET
                            owner_name          = COALESCE(EXCLUDED.owner_name, normalized_permits.owner_name),
                            business_name       = COALESCE(EXCLUDED.business_name, normalized_permits.business_name),
                            project_description = COALESCE(EXCLUDED.project_description, normalized_permits.project_description),
                            issued_date         = COALESCE(EXCLUDED.issued_date, normalized_permits.issued_date),
                            updated_at          = NOW()
                        RETURNING id, (xmax = 0) AS is_insert
                    """, (
                        county_id, raw_id,
                        rec.permit_number, rec.permit_type,
                        rec.owner_name      or None,
                        rec.contractor_name or None,
                        rec.address         or None,
                        rec.project_description or None,
                        rec.issued_date, n_hash,
                    ))
                    result = cur.fetchone()
                    if result:
                        if result[1]:
                            stats["inserted"] += 1
                        else:
                            stats["updated"] += 1
                except Exception as e:
                    conn.rollback()
                    print(f"  Insert error {rec.permit_number}: {e}")
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
    parser = argparse.ArgumentParser(description="Sarasota County permit scraper")
    global DAYS_BACK
    parser.add_argument("--days-back", type=int, default=DAYS_BACK)
    parser.add_argument("--no-db",     action="store_true")
    parser.add_argument("--visible",   action="store_true")
    args = parser.parse_args()
    DAYS_BACK = args.days_back

    print("=" * 60)
    print(f"[Sarasota] Scraping last {args.days_back} days")
    print("=" * 60)

    all_rows: list = []

    # Strategy 1: Legacy portal (no login needed)
    print("\n[1/2] Legacy portal (building.scgov.net)...")
    try:
        legacy_rows = scrape_legacy_portal(RAW_DIR)
        if legacy_rows:
            print(f"  Got {len(legacy_rows)} rows")
            all_rows.extend(legacy_rows)
    except Exception as e:
        print(f"  Failed: {e}")

    # Strategy 2: Accela (login required)
    if not all_rows:
        print("\n[2/2] Accela portal...")
        if SARASOTA_USER and SARASOTA_PASS:
            try:
                accela_rows = scrape_accela(RAW_DIR, SARASOTA_USER, SARASOTA_PASS)
                if accela_rows:
                    print(f"  Got {len(accela_rows)} rows")
                    all_rows.extend(accela_rows)
            except Exception as e:
                print(f"  Failed: {e}")
        else:
            print("  Set SARASOTA_ACCELA_USER and SARASOTA_ACCELA_PASS in .env")

    if not all_rows:
        print("\nNo data scraped.")
        return

    records = rows_to_records(all_rows)
    seen = set()
    records = [r for r in records if r.permit_number not in seen and not seen.add(r.permit_number)]

    snap = RAW_DIR / f"sarasota_permits_{nowstamp()}.json"
    snap.write_text(json.dumps([{
        "permit_number": r.permit_number,
        "type":          r.permit_type,
        "address":       r.address,
        "contractor":    r.contractor_name,
        "issued_date":   str(r.issued_date),
    } for r in records], indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {snap.name}")
    print("\nSample:")
    for r in records[:5]:
        print(f"  {r.permit_number} | {r.permit_type} | {r.address} | {r.contractor_name}")

    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    if not args.no_db:
        stats = import_records(records)

    print(f"\n--- Sarasota summary ---")
    print(f"  Records scraped    : {len(records)}")
    print(f"  raw inserted       : {stats['inserted']}")
    print(f"  normalized inserted: {stats['inserted']}")
    print(f"  skipped            : {stats['skipped']}")


if __name__ == "__main__":
    main()
