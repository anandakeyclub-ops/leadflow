r"""
georgia_scraper.py
==================
Georgia data sources for the TaxCase Review data engine.

LIENS  — GSCCCA statewide lien index (Georgia Superior Court Clerks'
         Cooperative Authority), federal tax lien filings.
         https://www.gsccca.org/search  /  https://search.gsccca.org
LICENSES — Georgia Secretary of State business search.
         https://ecorp.sos.ga.gov/BusinessSearch

HTTP-first (hard rule #6): both functions attempt a plain `requests` flow.
The GSCCCA lien index requires a (free) registered login and has anti-bot
protection, so an unauthenticated GET returns the login page rather than
results — when that wall is detected the scraper logs it and returns 0 instead
of crashing the daily runner. The GA SOS business search is an ASP.NET app
guarded by an antiforgery token; we fetch the token + cookies, POST the search,
and parse the results table. If the live response can't be parsed (markup
change / block) we return 0.

DB writes:
  liens    -> normalized_liens     (state='GA', lien_source='gsccca')
  licenses -> normalized_contacts  (state='GA', license_source='GA_SOS')

Credentials (optional, for a future authenticated/Selenium path) come from .env:
  GSCCCA_USERNAME / GSCCCA_PASSWORD
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from app.core.db import get_connection, release_connection  # noqa: E402
from scripts.data_engine.data_collector import (  # noqa: E402
    http_get, get_or_create_county, MAX_PER_COUNTY, is_business,
)

GSCCCA_BASE        = "https://search.gsccca.org"
GSCCCA_LIEN        = "https://search.gsccca.org/Lien/namesearch.asp"
GSCCCA_LOGIN_URL   = "https://apps.gsccca.org/login.asp"
GSCCCA_SEARCH_URL  = "https://search.gsccca.org/Lien/namesearch.asp"
GA_SOS_SEARCH      = "https://ecorp.sos.ga.gov/BusinessSearch"

# Verified GSCCCA lien-search form values (see namesearch.asp form):
#   txtInstrCode '3' = Federal Tax Lien
#   txtPartyType '1' = Direct Party (Debtor)  -> the taxpayer, not the IRS
GSCCCA_INSTR_FTL   = "3"
GSCCCA_PARTY_DEBTOR = "1"
GA_LIEN_COUNTIES   = ["Fulton", "Gwinnett", "DeKalb", "Cobb"]
GA_DEBUG_DIR       = LEADFLOW_DIR / "data" / "data_engine" / "ga_debug"
# Saved authenticated cookies (so a manual login can be reused headlessly).
# Lives under data/ which is gitignored — session cookies are never committed.
GA_SESSION_FILE    = LEADFLOW_DIR / "data" / "data_engine" / "ga_session.json"
# A-Z + 0-9 name-prefix sweep: GSCCCA's lien index requires a name in
# txtSearchName, so sweeping prefixes captures every debtor without knowing
# names in advance.
GA_PREFIXES        = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + [str(d) for d in range(10)]
GA_MAX_TOTAL       = 5000      # cap per full run, across all counties
GA_PREFIX_DELAY    = 1.5       # seconds between prefix searches
GA_CHECKPOINT_FILE = LEADFLOW_DIR / "data" / "data_engine" / "ga_checkpoint.json"

# Contractor entity types we care about (search keywords).
GA_CONTRACTOR_KEYWORDS = [
    "roofing", "hvac", "heating and air", "general contractor",
    "electrical", "plumbing",
]


# ── LIENS — GSCCCA (authenticated Selenium) ────────────────────────────────────
def collect_ga_liens(limit: int = GA_MAX_TOTAL, counties: list | None = None,
                     days_back: int = 3650, dry_run: bool = False,
                     headless: bool = True, manual: bool = False,
                     use_session: bool = False) -> int:
    """
    Federal Tax Lien (instrument code 3) search on GSCCCA per county
    (default Fulton, Gwinnett, DeKalb, Cobb), parsed into normalized_liens
    (state='GA'). Authentication mode:
      - use_session=True : reuse cookies saved by save_ga_session()/--manual
                           (skips login; works headless).
      - manual=True      : visible browser, creds pre-filled, pause for Dana to
                           solve the CAPTCHA, then continue (and cache cookies).
      - otherwise        : fully automated login (blocked by GSCCCA's CAPTCHA).
    dry_run parses + reports without writing. Returns count stored (or parsed).
    """
    user = os.getenv("GA_GSCCCA_USERNAME")
    pw   = os.getenv("GA_GSCCCA_PASSWORD")
    if not use_session and (not user or not pw):
        print("    GA/GSCCCA: GA_GSCCCA_USERNAME/PASSWORD not set — HTTP fallback.")
        return _collect_ga_liens_http(limit)

    counties = counties or GA_LIEN_COUNTIES
    driver = None
    all_rows: list[dict] = []
    total_stored = 0
    collected = 0
    try:
        # Manual mode needs a visible window so Dana can solve the CAPTCHA.
        driver = _ga_get_driver(headless=(headless and not manual))

        if use_session:
            if not (_ga_load_cookies(driver) and _ga_session_valid(driver)):
                print("    GA/GSCCCA: no valid saved session — run "
                      "`--save-session` (or `--manual`) first. (0 liens)")
                return 0
            print("    GA/GSCCCA: authenticated via saved session.")
        elif manual:
            if not _gsccca_manual_login(driver, user, pw):
                print("    GA/GSCCCA: manual login not confirmed. (0 liens)")
                return 0
            _ga_save_cookies(driver)  # cache so future runs can --use-session
            print("    GA/GSCCCA: authenticated (manual).")
        else:
            if not _gsccca_login(driver, user, pw):
                print("    GA/GSCCCA: login failed — see data/data_engine/ga_debug/."
                      " (GSCCCA gates automated login behind a CAPTCHA; use "
                      "--manual or --save-session/--use-session.)")
                return 0
            print("    GA/GSCCCA: logged in.")

        # A-Z + 0-9 name-prefix sweep with resume + dedup by file_number.
        cp = _ga_load_checkpoint()
        seen = set(cp.get("seen_file_numbers", []))
        progress = cp.get("progress", {})
        run_limit = min(limit, GA_MAX_TOTAL)
        stop = False
        for county in counties:
            if stop:
                break
            done = progress.get(county)
            start = (GA_PREFIXES.index(done) + 1) if done in GA_PREFIXES else 0
            if start:
                print(f"    GA/GSCCCA: resuming {county} after prefix '{done}'.")
            for prefix in GA_PREFIXES[start:]:
                if collected >= run_limit:
                    print(f"    GA/GSCCCA: run cap {run_limit} reached — stopping.")
                    stop = True
                    break
                try:
                    rows = _gsccca_search_prefix(driver, county, prefix, days_back)
                except Exception as e:
                    print(f"      [{county}/{prefix}] error: "
                          f"{type(e).__name__}: {str(e)[:140]}")
                    rows = []
                new_rows = []
                for r in rows:
                    key = (r.get("file_number")
                           or f"{r.get('name','')}|{r.get('date','')}|{county}")
                    if key in seen:
                        continue
                    seen.add(key)
                    r["county"] = county
                    new_rows.append(r)
                if new_rows:
                    all_rows.extend(new_rows)
                    collected += len(new_rows)
                    if not dry_run:
                        total_stored += _store_ga_liens(new_rows, quiet=True)
                print(f"    {county} / {prefix} -> {len(rows)} results "
                      f"({collected} new this run, {len(seen)} total)")
                # Checkpoint AFTER this prefix's rows are stored, so a resume
                # never skips a prefix whose data wasn't saved.
                progress[county] = prefix
                _ga_save_checkpoint({
                    "seen_file_numbers": sorted(seen)[-20000:],
                    "progress": progress,
                    "updated": datetime.now().isoformat(timespec="seconds"),
                })
                time.sleep(GA_PREFIX_DELAY)
    except Exception as e:
        print(f"    GA/GSCCCA selenium error: {type(e).__name__}: {str(e)[:200]}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    if dry_run:
        print(f"    [DRY RUN] {len(all_rows)} GA FTL liens parsed (not stored):")
        for r in all_rows[:15]:
            print(f"       {(r.get('name') or '')[:42]:<42} | "
                  f"{r.get('county',''):<10} | {r.get('date','')} | "
                  f"{r.get('file_number','')}")
        return len(all_rows)
    print(f"    GA/GSCCCA: stored {total_stored} new liens this run "
          f"({collected} unique parsed).")
    return total_stored


# ── Option A: manual CAPTCHA login ─────────────────────────────────────────────
def _gsccca_manual_login(driver, user: str, pw: str) -> bool:
    """Open the login page in a visible browser, pre-fill credentials, then pause
    so a human can solve the CAPTCHA and click Login. Returns True once the
    resulting page shows an authenticated state."""
    from selenium.webdriver.common.by import By
    driver.get(GSCCCA_LOGIN_URL)
    time.sleep(2)
    try:
        u = driver.find_elements(By.NAME, "txtUserID")
        p = driver.find_elements(By.NAME, "txtPassword")
        if u and user:
            u[0].clear(); u[0].send_keys(user)
        if p and pw:
            p[0].clear(); p[0].send_keys(pw)
    except Exception:
        pass
    print("\n" + "=" * 64)
    print("  GSCCCA MANUAL LOGIN")
    print("  A Chrome window is open on the GSCCCA login page with your")
    print("  username/password pre-filled. Solve the CAPTCHA and click Login.")
    print("=" * 64)
    try:
        input("  Solve the CAPTCHA in the browser, then press Enter here... ")
    except EOFError:
        print("  (no interactive stdin — cannot do manual login here)")
        return False
    time.sleep(1)
    _ga_save_debug(driver, "02_after_manual_login")
    low = driver.page_source.lower()
    return ("log out" in low or "logout" in low or "my account" in low
            or not driver.find_elements(By.NAME, "txtPassword"))


def save_ga_session(headless: bool = False) -> bool:
    """Open a visible browser, let Dana log in manually (solving the CAPTCHA),
    and save the authenticated cookies to GA_SESSION_FILE for later reuse."""
    user = os.getenv("GA_GSCCCA_USERNAME")
    pw   = os.getenv("GA_GSCCCA_PASSWORD")
    driver = _ga_get_driver(headless=False)  # must be visible for the CAPTCHA
    try:
        if _gsccca_manual_login(driver, user, pw):
            _ga_save_cookies(driver)
            return True
        print("    GA/GSCCCA: manual login not confirmed — session not saved.")
        return False
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ── Option B: cookie session reuse ─────────────────────────────────────────────
def _ga_save_cookies(driver):
    try:
        GA_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        cookies = driver.get_cookies()
        GA_SESSION_FILE.write_text(json.dumps(cookies, indent=2))
        print(f"    GA/GSCCCA: saved {len(cookies)} cookies -> "
              f"data/data_engine/{GA_SESSION_FILE.name}")
    except Exception as e:
        print(f"    GA/GSCCCA: could not save session: {e}")


def _ga_load_cookies(driver) -> bool:
    if not GA_SESSION_FILE.exists():
        print("    GA/GSCCCA: no saved session file (run --save-session first).")
        return False
    try:
        cookies = json.loads(GA_SESSION_FILE.read_text())
    except Exception:
        print("    GA/GSCCCA: session file unreadable.")
        return False
    # Must be on a gsccca.org page before add_cookie; .gsccca.org cookies then
    # apply across the apps/search subdomains.
    driver.get("https://www.gsccca.org/")
    time.sleep(2)
    n = 0
    for c in cookies:
        c.pop("sameSite", None)
        try:
            driver.add_cookie(c)
            n += 1
        except Exception:
            continue
    print(f"    GA/GSCCCA: loaded {n}/{len(cookies)} saved cookies.")
    return n > 0


def _ga_session_valid(driver) -> bool:
    """Confirm the loaded cookies still authenticate the lien search."""
    from selenium.webdriver.common.by import By
    driver.get(GSCCCA_SEARCH_URL)
    time.sleep(2)
    # The search form (txtInstrCode) should be present and we should not be
    # bounced to a login form as the page's primary content.
    has_form = bool(driver.find_elements(By.NAME, "txtInstrCode"))
    title = (driver.title or "").lower()
    return has_form and "login" not in title


def _ga_get_driver(headless: bool = True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    for a in ("--no-sandbox", "--disable-dev-shm-usage", "--window-size=1920,1080",
              "--disable-blink-features=AutomationControlled"):
        opts.add_argument(a)
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(60)
    driver.execute_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    return driver


def _ga_save_debug(driver, label: str):
    """Save page HTML + screenshot so the live DOM can be inspected/refined."""
    try:
        GA_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        (GA_DEBUG_DIR / f"{label}.html").write_text(
            driver.page_source, encoding="utf-8", errors="replace")
        try:
            driver.save_screenshot(str(GA_DEBUG_DIR / f"{label}.png"))
        except Exception:
            pass
    except Exception:
        pass


def _gsccca_login(driver, user: str, pw: str) -> bool:
    from selenium.webdriver.common.by import By
    driver.get(GSCCCA_LOGIN_URL)
    time.sleep(2)
    u = driver.find_elements(By.NAME, "txtUserID")
    p = driver.find_elements(By.NAME, "txtPassword")
    if not u or not p:
        _ga_save_debug(driver, "01_login_page_no_fields")
        # If there's no password field at all we may already be authenticated.
        return not p
    try:
        u[0].clear(); u[0].send_keys(user)
        p[0].clear(); p[0].send_keys(pw)
    except Exception as e:
        print(f"      login fill error: {e}")
        return False
    # Submit the form that actually contains the password field, flipping the
    # hidden FormSubmission flag the server checks (the page has two login forms;
    # we must post the credential one, not the header mini-login).
    try:
        driver.execute_script(
            "var p=document.getElementsByName('txtPassword')[0];"
            "var f=(p&&p.form)?p.form:(document.frmLogin||document.getElementById('loginform'));"
            "if(f){var fs=f.querySelector(\"input[name='FormSubmission']\");"
            "if(fs)fs.value='True';f.submit();}")
    except Exception:
        try:
            p[0].submit()
        except Exception:
            pass
    time.sleep(4)
    _ga_save_debug(driver, "02_after_login")

    low = driver.page_source.lower()
    if "log out" in low or "logout" in low or "my account" in low:
        return True
    # Diagnose why login did not take.
    reasons = []
    if any(k in low for k in ("incorrect", "invalid login", "username or password",
                              "login failed")):
        reasons.append("credentials rejected")
    if re.search(r"captcha", low) and re.search(
            r"recaptcha|g-recaptcha|captchaimage|<img[^>]*captcha|captcha\.(jpg|png|gif|aspx)",
            low):
        reasons.append("CAPTCHA challenge (anti-bot)")
    print(f"    GA/GSCCCA: login did not succeed "
          f"({', '.join(reasons) or 'still on login page'}).")
    return False


def _gsccca_search_county(driver, county: str, days_back: int,
                          max_rows: int) -> list:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import Select

    driver.get(GSCCCA_SEARCH_URL)
    time.sleep(2)
    if driver.find_elements(By.NAME, "txtPassword"):
        _ga_save_debug(driver, f"03_{county}_needs_login")
        print(f"      [{county}] redirected to login — session not authenticated")
        return []

    def setsel(name, value=None, text=None) -> bool:
        els = driver.find_elements(By.NAME, name)
        if not els:
            return False
        try:
            s = Select(els[0])
            if value is not None:
                s.select_by_value(value)
            elif text is not None:
                s.select_by_visible_text(text)
            return True
        except Exception:
            return False

    setsel("txtInstrCode", value=GSCCCA_INSTR_FTL)     # Federal Tax Lien
    setsel("txtPartyType", value=GSCCCA_PARTY_DEBTOR)  # Direct Party (Debtor)
    setsel("MaxRows", value="100")
    if not setsel("intCountyID", text=county.upper()):
        setsel("intCountyID", text=county)

    today = date.today()
    frm = today - timedelta(days=days_back)
    for nm, val in (("txtFromDate", frm.strftime("%m/%d/%Y")),
                    ("txtToDate", today.strftime("%m/%d/%Y"))):
        els = driver.find_elements(By.NAME, nm)
        if els:
            try:
                els[0].clear(); els[0].send_keys(val)
            except Exception:
                pass

    # Click the Search button (input type=button value=Search).
    btns = driver.find_elements(
        By.CSS_SELECTOR,
        "input[value='Search'], input[type='submit'][value='Search']")
    if btns:
        try:
            btns[0].click()
        except Exception:
            pass
    else:
        try:
            driver.find_element(By.NAME, "txtInstrCode").submit()
        except Exception:
            pass

    time.sleep(5)
    # A blank-name search may trigger a JS alert demanding a name — capture it.
    try:
        alert = driver.switch_to.alert
        print(f"      [{county}] alert: {alert.text[:100]}")
        alert.accept()
        time.sleep(2)
    except Exception:
        pass

    _ga_save_debug(driver, f"03_results_{county}")
    return _parse_gsccca_results(driver.page_source)[:max_rows]


def _ga_load_checkpoint() -> dict:
    try:
        if GA_CHECKPOINT_FILE.exists():
            return json.loads(GA_CHECKPOINT_FILE.read_text())
    except Exception:
        pass
    return {}


def _ga_save_checkpoint(cp: dict):
    try:
        GA_CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        GA_CHECKPOINT_FILE.write_text(json.dumps(cp, indent=2, default=str))
    except Exception:
        pass


def _gsccca_search_prefix(driver, county: str, prefix: str,
                          days_back: int) -> list:
    """One GSCCCA Federal Tax Lien search for txtSearchName=<prefix> in <county>.
    Returns parsed rows (each {name, date, file_number})."""
    from selenium.webdriver.common.by import By
    driver.get(GSCCCA_SEARCH_URL)
    time.sleep(1.5)
    if driver.find_elements(By.NAME, "txtPassword"):
        _ga_save_debug(driver, f"prefix_{county}_{prefix}_needs_login")
        return []
    _ga_apply_search_params(driver, instr_value=GSCCCA_INSTR_FTL,
                            party=GSCCCA_PARTY_DEBTOR, county=county,
                            set_dates=True, days_back=days_back, name=prefix)
    _ga_click_search(driver)
    time.sleep(3)
    # GSCCCA may pop an alert (e.g. "too many results, narrow your search").
    try:
        alert = driver.switch_to.alert
        print(f"      [{county}/{prefix}] alert: {alert.text[:100]}")
        alert.accept()
        time.sleep(1)
    except Exception:
        pass
    return _parse_gsccca_results(driver.page_source)


# ── Search-form debugger ───────────────────────────────────────────────────────
def _ga_apply_search_params(driver, instr_value=None, instr_text=None,
                            party=None, county=None, set_dates=True,
                            days_back=3650, name=""):
    """Set the GSCCCA lien-search form fields. county=None/'-1' => all counties."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import Select

    def setsel(nm, value=None, text=None) -> bool:
        els = driver.find_elements(By.NAME, nm)
        if not els:
            return False
        try:
            s = Select(els[0])
            if value is not None:
                s.select_by_value(value)
            elif text is not None:
                s.select_by_visible_text(text)
            return True
        except Exception:
            return False

    ok = False
    if instr_value is not None:
        ok = setsel("txtInstrCode", value=instr_value)
    if not ok and instr_text is not None:
        ok = setsel("txtInstrCode", text=instr_text)
    if party is not None:
        setsel("txtPartyType", value=party)
    setsel("MaxRows", value="100")
    if county in (None, "", "ALL", "-1"):
        setsel("intCountyID", value="-1")
    elif not setsel("intCountyID", text=county.upper()):
        setsel("intCountyID", text=county)

    for el in driver.find_elements(By.NAME, "txtSearchName"):
        el.clear()
        if name:
            el.send_keys(name)
    for nm in ("txtFromDate", "txtToDate"):
        for el in driver.find_elements(By.NAME, nm):
            el.clear()
    if set_dates:
        today = date.today()
        frm = today - timedelta(days=days_back)
        for nm, val in (("txtFromDate", frm.strftime("%m/%d/%Y")),
                        ("txtToDate", today.strftime("%m/%d/%Y"))):
            for el in driver.find_elements(By.NAME, nm):
                el.send_keys(val)


def _ga_form_state(driver):
    """Read the lien-search form's action + the exact field values that will be
    POSTed (selected option values, checked radios, text inputs)."""
    return driver.execute_script("""
        var anchor = document.getElementsByName('txtInstrCode')[0];
        var f = anchor ? anchor.form : (document.forms.length ? document.forms[0] : null);
        if (!f) return null;
        var out = {};
        for (var i = 0; i < f.elements.length; i++) {
            var el = f.elements[i];
            if (!el.name) continue;
            if ((el.type === 'radio' || el.type === 'checkbox') && !el.checked) continue;
            out[el.name] = el.value;
        }
        return {action: f.action, method: (f.method || 'get').toUpperCase(), params: out};
    """)


def _ga_click_search(driver):
    from selenium.webdriver.common.by import By
    btns = driver.find_elements(
        By.CSS_SELECTOR, "input[value='Search'], input[type='submit'][value='Search']")
    if btns:
        try:
            btns[0].click()
            return
        except Exception:
            pass
    try:
        driver.find_element(By.NAME, "txtInstrCode").submit()
    except Exception:
        pass


def debug_ga_search(county: str = "Fulton"):
    """Visible-browser GSCCCA search debugger (--debug). Loads the saved session,
    pauses on the first attempt so the form state can be inspected, then runs a
    set of parameter variations — printing the exact POST URL+params and saving
    each raw response to data/data_engine/ga_debug/."""
    from selenium.webdriver.common.by import By
    GA_DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    # Confirm the prefix-sweep approach: search txtSearchName="A" for the county.
    variations = [
        ("prefixA_instr3_party1_dates", dict(instr_value="3", party="1",
                                             county=county, name="A", set_dates=True)),
        ("prefixA_no_dates",            dict(instr_value="3", party="1",
                                             county=county, name="A", set_dates=False)),
        ("prefixA_party2_all",          dict(instr_value="3", party="2",
                                             county=county, name="A", set_dates=False)),
    ]

    driver = _ga_get_driver(headless=False)
    try:
        if not (_ga_load_cookies(driver) and _ga_session_valid(driver)):
            print("  No valid saved session — run `--save-session` first.")
            return
        print("  Authenticated via saved session.\n")

        for i, (label, kw) in enumerate(variations):
            driver.get(GSCCCA_SEARCH_URL)
            time.sleep(2)
            if driver.find_elements(By.NAME, "txtPassword"):
                print(f"  [{label}] redirected to login — session expired.")
                break
            _ga_apply_search_params(driver, **kw)
            fs = _ga_form_state(driver) or {}
            print("=" * 70)
            print(f"  ATTEMPT {i + 1}/{len(variations)}: {label}")
            print(f"  POST {fs.get('method','POST')} -> {fs.get('action','?')}")
            print("  PARAMS:")
            for k, v in (fs.get("params") or {}).items():
                print(f"     {k} = {v!r}")

            if i == 0:
                print("\n  Form loaded. Check the browser. Press Enter to submit...")
                try:
                    input()
                except EOFError:
                    print("  (no interactive stdin — submitting anyway)")

            _ga_click_search(driver)
            time.sleep(5)
            try:
                alert = driver.switch_to.alert
                print(f"  ALERT: {alert.text[:140]}")
                alert.accept()
                time.sleep(1)
            except Exception:
                pass

            html = driver.page_source
            fn = (GA_DEBUG_DIR / "search_response.html") if i == 0 \
                else (GA_DEBUG_DIR / f"search_response_{label}.html")
            fn.write_text(html, encoding="utf-8", errors="replace")
            try:
                driver.save_screenshot(str(fn.with_suffix(".png")))
            except Exception:
                pass
            rows = _parse_gsccca_results(html)
            print(f"  RESULT url: {driver.current_url}")
            print(f"  saved -> {fn.relative_to(LEADFLOW_DIR)}  "
                  f"({len(html):,} bytes) | parser found {len(rows)} rows")
            low = html.lower()
            for hint in ("no records", "no results", "please enter", "must enter",
                         "required", "exceeded", "too many", "no matches",
                         "invalid", "not authorized", "session"):
                if hint in low:
                    print(f"     response mentions: {hint!r}")
            if i == 0:
                print("\n  ----- RAW RESPONSE (first 4000 chars) -----")
                print(html[:4000])
                print("  ----- END RAW RESPONSE -----\n")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")


def _parse_gsccca_results(html: str) -> list[dict]:
    """Parse a GSCCCA lien results page -> [{name, date}]. A row counts only if
    it carries a filing date (MM/DD/YYYY), which filters out headers/nav/chrome."""
    out: list[dict] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        for tr in soup.find_all("tr"):
            cells = [td.get_text(" ", strip=True)
                     for td in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            joined = " ".join(cells)
            m = _DATE_RE.search(joined)
            if not m:
                continue  # real lien rows always have a filing date
            # Name = first cell with >=2 alphabetic words that isn't the date.
            name = ""
            for c in cells:
                if _DATE_RE.fullmatch(c.strip()):
                    continue
                letters = re.sub(r"[^A-Za-z ]", "", c).strip()
                if len(letters.split()) >= 2 and len(letters) >= 5:
                    name = c
                    break
            if not name:
                continue
            low = name.lower()
            if low.startswith(("name", "party", "grantor", "grantee", "instrument")):
                continue
            # file/instrument number: a numeric (book/page/instrument) token.
            fn = ""
            for c in cells:
                t = c.strip()
                if re.fullmatch(r"\d{3,}([-/]\d+)*", t):
                    fn = t
                    break
            out.append({"name": name.strip(), "date": m.group(0),
                        "file_number": fn})
    except Exception:
        pass
    return out


def _to_iso_date(s):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _store_ga_liens(rows: list[dict], quiet: bool = False) -> int:
    conn = get_connection()
    conn.autocommit = False
    added = 0
    try:
        for rec in rows:
            name = (rec.get("name") or "").strip()
            if len(name) < 3:
                continue
            county_name = (rec.get("county") or "Fulton").strip() or "Fulton"
            filed = _to_iso_date(rec.get("date"))
            # Dedup key: prefer the file/instrument number when present so the
            # same lien collapses regardless of which prefix surfaced it.
            dedup = rec.get("file_number") or f"{name.upper()}|{rec.get('date')}"
            h = hashlib.md5(
                f"gsccca|{county_name}|{dedup}".encode()).hexdigest()
            with conn.cursor() as cur:
                county_id = get_or_create_county(cur, county_name, "GA")
                cur.execute("""
                    INSERT INTO normalized_liens
                        (county_id, debtor_name, business_name, filing_type,
                         lien_type, lien_source, normalized_hash, state,
                         filed_date, created_at)
                    VALUES (%s,%s,%s,'FEDERAL TAX LIEN','FEDERAL TAX LIEN',
                            'gsccca',%s,'GA',%s,NOW())
                    ON CONFLICT (normalized_hash) DO NOTHING
                    RETURNING id
                """, (county_id, name[:250],
                      name[:250] if is_business(name) else None,
                      h, filed))
                if cur.fetchone():
                    added += 1
            conn.commit()
        if not quiet:
            print(f"    GA/GSCCCA: +{added} new GA liens")
    except Exception as e:
        conn.rollback()
        print(f"    GA/GSCCCA store error: {e}")
    finally:
        release_connection(conn)
    return added


def _collect_ga_liens_http(limit: int = MAX_PER_COUNTY) -> int:
    """HTTP-only fallback used when no GSCCCA credentials are configured."""
    r = http_get(GSCCCA_LIEN, params={"bsearch": "Federal Tax Lien"})
    if r is None:
        print("    GA/GSCCCA: unreachable — 0 liens.")
        return 0
    body = r.text or ""
    if (r.status_code in (301, 302, 401, 403)
            or "txtpassword" in body.lower() or "sign in" in body.lower()):
        print("    GA/GSCCCA: login required — set GA_GSCCCA_USERNAME/PASSWORD "
              "for the Selenium path. (0 liens)")
        return 0
    rows = _parse_gsccca_results(body)
    if not rows:
        print("    GA/GSCCCA: no parseable rows over HTTP — 0 liens.")
        return 0
    return _store_ga_liens(rows[:limit])


# ── LICENSES — GA Secretary of State business search ───────────────────────────
def collect_ga_licenses(limit: int = MAX_PER_COUNTY) -> int:
    """Search GA SOS business records for contractor entity types and store them
    in normalized_contacts (state='GA', license_source='GA_SOS'). Paginates by
    keyword. Returns count written."""
    session = requests.Session()
    session.headers.update({"User-Agent":
                            "Mozilla/5.0 (compatible; LeadFlowDataEngine/1.0)"})
    token = _ga_sos_token(session)
    if token is None:
        print("    GA/SOS: could not obtain search page/token — pending. (0 licenses)")
        return 0

    all_rows: list[dict] = []
    for kw in GA_CONTRACTOR_KEYWORDS:
        if len(all_rows) >= limit:
            break
        rows = _ga_sos_search(session, token, kw)
        all_rows.extend(rows)
    # de-dupe by control number / name
    seen, deduped = set(), []
    for r in all_rows:
        key = (r.get("control_number") or r.get("name", "")).lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(r)

    if not deduped:
        print("    GA/SOS: no parseable business rows returned (ASP.NET markup "
              "change or block) — pending. (0 licenses)")
        return 0
    return _store_ga_licenses(deduped[:limit])


def _ga_sos_token(session: requests.Session):
    """Fetch the search page and extract the ASP.NET antiforgery token."""
    try:
        r = session.get(GA_SOS_SEARCH, timeout=30)
        if r.status_code != 200:
            return None
        m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"',
                      r.text)
        return m.group(1) if m else ""
    except requests.RequestException:
        return None


def _ga_sos_search(session: requests.Session, token: str,
                   keyword: str) -> list[dict]:
    """POST one keyword search and parse the results grid. Best-effort."""
    out: list[dict] = []
    try:
        data = {
            "BusinessName":  keyword,
            "searchType":    "Contains",
        }
        if token:
            data["__RequestVerificationToken"] = token
        r = session.post(GA_SOS_SEARCH, data=data, timeout=30)
        if r.status_code != 200:
            return out
        soup = BeautifulSoup(r.text, "lxml")
        for tr in soup.select("table tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) < 2:
                continue
            name = cells[0]
            if not name or name.lower() in ("business name", "name"):
                continue
            out.append({
                "name":           name,
                "control_number": cells[1] if len(cells) > 1 else "",
                "status":         cells[2] if len(cells) > 2 else "",
                "keyword":        keyword,
            })
    except Exception:
        pass
    return out


def _looks_like_control(v: str) -> bool:
    """GA SOS control numbers are 6-12 digits. Anything else is page chrome
    (labels, nav) the naive HTML parse picked up — reject it."""
    return bool(re.fullmatch(r"\d{6,12}", (v or "").strip()))


def _store_ga_licenses(rows: list[dict]) -> int:
    conn = get_connection()
    conn.autocommit = False
    added = 0
    try:
        for rec in rows:
            name = (rec.get("name") or "").strip()
            lic = (rec.get("control_number") or "").strip()
            # Only real business rows: a valid control number + a plausible name.
            if not name or len(name) < 3 or not _looks_like_control(lic):
                continue
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO normalized_contacts
                        (state, state_name, license_number, license_type,
                         license_status, license_source, business_name,
                         owner_name, data_source)
                    VALUES ('GA','Georgia',%s,%s,%s,'GA_SOS',%s,%s,'georgia_scraper')
                    ON CONFLICT (state, license_number) DO NOTHING
                """, (lic[:100], (rec.get("keyword", "") or "")[:100],
                      (rec.get("status", "") or "")[:50],
                      name[:200], name[:200]))
                if cur.rowcount:
                    added += 1
            conn.commit()
        print(f"    GA/SOS: +{added} contractor businesses")
    except Exception as e:
        conn.rollback()
        print(f"    GA/SOS store error: {e}")
    finally:
        release_connection(conn)
    return added


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Georgia GSCCCA lien scraper")
    ap.add_argument("--dry-run", action="store_true",
                    help="log in + search + parse, but do not write to the DB")
    ap.add_argument("--counties", default=None,
                    help="comma list (default: Fulton,Gwinnett,DeKalb,Cobb)")
    ap.add_argument("--days", type=int, default=3650,
                    help="lookback window in days (default 3650 = ~10 yrs)")
    ap.add_argument("--limit", type=int, default=MAX_PER_COUNTY)
    ap.add_argument("--no-headless", action="store_true",
                    help="show the browser window")
    ap.add_argument("--manual", action="store_true",
                    help="visible browser; pause for Dana to solve the CAPTCHA, "
                         "then automate the search (Option A)")
    ap.add_argument("--save-session", action="store_true",
                    help="log in manually once and save cookies for reuse (Option B)")
    ap.add_argument("--use-session", action="store_true",
                    help="reuse saved cookies and skip login (Option B)")
    ap.add_argument("--debug", action="store_true",
                    help="visible-browser search debugger: load saved session, "
                         "pause before submit, try parameter variations, dump "
                         "the POST params + raw response HTML")
    ap.add_argument("--licenses", action="store_true",
                    help="run the GA SOS license scrape instead of liens")
    args = ap.parse_args()

    if args.debug:
        first_county = (args.counties.split(",")[0].strip()
                        if args.counties else "Fulton")
        debug_ga_search(county=first_county)
        return
    if args.save_session:
        ok = save_ga_session()
        print(f"\nGA session {'saved' if ok else 'NOT saved'}.")
        return
    if args.licenses:
        print(f"GA licenses: {collect_ga_licenses(args.limit)}")
        return
    counties = ([c.strip() for c in args.counties.split(",") if c.strip()]
                if args.counties else None)
    n = collect_ga_liens(limit=args.limit, counties=counties,
                         days_back=args.days, dry_run=args.dry_run,
                         headless=not args.no_headless, manual=args.manual,
                         use_session=args.use_session)
    tag = "(dry-run) " if args.dry_run else ""
    print(f"\nGA liens {tag}result: {n}")


if __name__ == "__main__":
    main()
