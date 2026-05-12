"""
scrape_broward_permits.py  (full Broward edition)
=================================================
Scrapes building permits from ALL major Broward County municipalities.

Sources:
  Accela portal cities (confirmed working pattern):
    - weston_accela         ✅ confirmed working
    - hollywood_accela      (same pattern, likely works)
    - cooper_city_accela    (same pattern, likely works)
    - fort_lauderdale_accela ⚠ search returns 0 — use scrape_fort_lauderdale_reports.py

  Broward County open data (non-Accela cities):
    - broward_county_open_data  Covers: unincorporated Broward + many cities
      Source: https://gis.broward.org/GISData/PermitsData/

Usage:
  python -m app.workers.scrape_broward_permits --all-broward --days-back 90 --visible
  python -m app.workers.scrape_broward_permits --source weston_accela --days-back 30 --visible
  python -m app.workers.scrape_broward_permits --source broward_county_open_data --days-back 90
  python -m app.workers.scrape_broward_permits --source hollywood_accela --days-back 30 --visible
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    from webdriver_manager.chrome import ChromeDriverManager
except Exception:
    ChromeDriverManager = None

from app.core.db import get_connection

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR       = Path(__file__).resolve().parents[2]
DEBUG_DIR      = BASE_DIR / "data" / "debug" / "broward_permits"
RAW_EXPORT_DIR = BASE_DIR / "data" / "raw" / "broward" / "permits"
for d in [DEBUG_DIR, RAW_EXPORT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

COUNTY_NAME = "Broward"

# ---------------------------------------------------------------------------
# Source definitions
# ---------------------------------------------------------------------------
DEFAULT_SOURCES: List[Dict[str, Any]] = [
    {
        "name":                   "weston_accela",
        "jurisdiction":           "Weston",
        "type":                   "accela",
        "base_url":               "https://aca-prod.accela.com/WESTON/Cap/CapHome.aspx?module=Building&TabName=Building",
        "record_type_contains":   "Building",
    },
    {
        "name":                   "hollywood_accela",
        "jurisdiction":           "Hollywood",
        "type":                   "accela",
        "base_url":               "https://aca-prod.accela.com/HOLLYWOOD/Cap/CapHome.aspx?module=Building&TabName=Building",
        "record_type_contains":   None,
        # Hollywood uses a specific permit type dropdown — iterate key types
        "permit_type_dropdown_id": "ctl00_PlaceHolderMain_generalSearchForm_ddlGSPermitType",
        "permit_type_filters": [
            "Residential Electrical Permit",
            "Commercial Electrical Permit",
            "Commercial Mechanical Permit",
            "Commercial Plumbing Permit",
            "Residential Demolition Permit",
            "Commercial Demolition Permit",
            "Fence Permit",
        ],
    },
    # Cooper City uses BS&A Online, not Accela:
    # https://bsaonline.com/?uid=2339
    # Use scrape_cooper_city_bsa.py (separate scraper) for Cooper City permits.
    # Deerfield Beach: uses Broward ePermits OneStop
    # Pompano Beach: check pompanobeachfl.gov for permit portal
    # These are not on aca-prod.accela.com — add when portals confirmed
    {
        "name":                   "broward_county_open_data",
        "jurisdiction":           "Broward County",
        "type":                   "open_data",
        # Broward County GIS open data permit feed
        # Covers unincorporated areas + feeds from many municipalities
        "api_url":                "https://gis.broward.org/arcgis/rest/services/PermitsAndCertificates/MapServer/0/query",
    },
]

PERMIT_NUMBER_RE = re.compile(r"\b[A-Z]{1,4}[-\s]?\d{2,6}[-\s]\d{3,6}(?:\.\d{3})?\b", re.I)
DATE_RE          = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class PermitRecord:
    source_name:         str
    jurisdiction:        str
    permit_number:       str
    permit_type:         Optional[str]    = None
    project_description: Optional[str]   = None
    issued_date:         Optional[date]  = None
    owner_name:          Optional[str]   = None
    business_name:       Optional[str]   = None
    address_1:           Optional[str]   = None
    city:                Optional[str]   = None
    state:               str             = "FL"
    zip:                 Optional[str]   = None
    project_value:       Optional[float] = None
    status:              Optional[str]   = None
    raw_payload:         Dict[str, Any]  = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def clean_text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()

def parse_date(v: Any) -> Optional[date]:
    s = clean_text(v)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

def parse_money(v: Any) -> Optional[float]:
    s = re.sub(r"[^\d.]", "", str(v or ""))
    try:
        return float(s) if s else None
    except ValueError:
        return None

def today_local() -> date:
    return date.today()

def extract_permit_number(text: str) -> Optional[str]:
    m = PERMIT_NUMBER_RE.search(str(text or ""))
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Selenium / Accela helpers (copied and refined from original scraper)
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
    if ChromeDriverManager:
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=opts)
    return webdriver.Chrome(options=opts)


def save_debug(driver: webdriver.Chrome, source: str, label: str) -> None:
    d = DEBUG_DIR / source
    d.mkdir(parents=True, exist_ok=True)
    try:
        (d / f"{label}.html").write_text(driver.page_source, encoding="utf-8", errors="ignore")
        driver.save_screenshot(str(d / f"{label}.png"))
    except Exception:
        pass


def set_input_value(driver: webdriver.Chrome, el, value: str) -> None:
    """Reliably set an input value using JS + keyboard simulation."""
    from selenium.webdriver.common.keys import Keys
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.2)
    driver.execute_script("arguments[0].value = '';", el)
    el.click()
    el.send_keys(Keys.CONTROL + "a")
    el.send_keys(Keys.DELETE)
    el.send_keys(value)
    el.send_keys(Keys.TAB)
    time.sleep(0.3)
    # Verify
    actual = clean_text(driver.execute_script("return arguments[0].value;", el))
    if actual != value:
        # JS injection fallback
        driver.execute_script("arguments[0].value = arguments[1];", el, value)
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));",
            el
        )


def smart_find_date_input(driver: webdriver.Chrome, kind: str):
    """Find start or end date input by scanning common Accela patterns."""
    kind_l = kind.lower()
    is_start = "start" in kind_l or "begin" in kind_l or "from" in kind_l
    candidates = []

    for el in driver.find_elements(By.XPATH, "//input[@type='text' or not(@type)]"):
        try:
            attrs = " ".join([
                clean_text(el.get_attribute(a)).lower()
                for a in ("id", "name", "placeholder", "title", "aria-label")
            ])
            val = clean_text(el.get_attribute("value")).lower()
            # Score by relevance
            score = 0
            if "date" in attrs: score += 3
            if ("start" in attrs or "begin" in attrs or "from" in attrs) and is_start: score += 4
            if ("end" in attrs or "to" in attrs) and not is_start: score += 4
            if re.search(r"\d{1,2}/\d{1,2}/\d{4}", val): score += 2
            if score > 0:
                candidates.append((score, el))
        except Exception:
            continue

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    # Fallback: return first/second text input with date-like value
    inputs = driver.find_elements(By.XPATH, "//input[@type='text' or not(@type)]")
    date_inputs = [el for el in inputs if re.search(r"\d{1,2}/\d{1,2}/\d{4}", clean_text(el.get_attribute("value") or ""))]
    if date_inputs:
        return date_inputs[0] if is_start else date_inputs[-1]
    # Final fallback — first/second visible text input
    visible = [el for el in inputs if el.is_displayed()]
    if not visible:
        return None  # Cannot find date input — caller should handle gracefully
    return visible[0] if is_start else (visible[1] if len(visible) > 1 else visible[0])


def set_search_dates(driver: webdriver.Chrome, start: date, end: date, source_name: str = "") -> None:
    start_text = start.strftime("%m/%d/%Y")
    end_text   = end.strftime("%m/%d/%Y")
    try:
        start_el = smart_find_date_input(driver, "start")
        end_el   = smart_find_date_input(driver, "end")

        if start_el is None:
            save_debug(driver, source_name, "date_fields_not_found")
            raise RuntimeError(f"Start date input not found on {source_name}")
        if end_el is None:
            save_debug(driver, source_name, "date_fields_not_found")
            raise RuntimeError(f"End date input not found on {source_name}")

        set_input_value(driver, start_el, start_text)
        set_input_value(driver, end_el,   end_text)
        actual_start = clean_text(driver.execute_script("return arguments[0].value;", start_el))
        actual_end   = clean_text(driver.execute_script("return arguments[0].value;", end_el))
        print(f"  [dates] requested {start_text}→{end_text} | actual {actual_start}→{actual_end}")
        if actual_start != start_text or actual_end != end_text:
            save_debug(driver, source_name, "date_fields_wrong")
            raise RuntimeError(f"Dates did not stick: got {actual_start}→{actual_end}")
    except Exception as e:
        save_debug(driver, source_name, "date_fields_error")
        raise


def try_accept_disclaimer(driver: webdriver.Chrome) -> None:
    for text in ["I Agree", "Accept", "Continue", "OK"]:
        try:
            btn = driver.find_element(By.XPATH, f"//button[contains(text(),'{text}')] | //input[@value='{text}']")
            btn.click()
            time.sleep(1)
            return
        except Exception:
            continue


def maybe_select_record_type(driver: webdriver.Chrome, contains_text: Optional[str]) -> None:
    if not contains_text:
        return
    target = contains_text.lower()
    for sel in driver.find_elements(By.TAG_NAME, "select"):
        try:
            # Skip License Type dropdowns — only target Record Type selects
            sel_id   = (sel.get_attribute("id")   or "").lower()
            sel_name = (sel.get_attribute("name")  or "").lower()
            if "license" in sel_id or "license" in sel_name:
                continue
            for opt in sel.find_elements(By.TAG_NAME, "option"):
                if target in clean_text(opt.text).lower():
                    opt.click()
                    time.sleep(0.5)
                    print(f"  [filter] record type: {clean_text(opt.text)}")
                    return
        except Exception:
            continue


def click_search(driver: webdriver.Chrome) -> None:
    for xpath in [
        "//input[@value='Search']",
        "//button[contains(text(),'Search')]",
        "//input[@id='ctl00_PlaceHolderMain_btnNewSearch']",
        "//input[contains(@id,'Search') and @type='submit']",
        "//a[contains(text(),'Search')]",
    ]:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(4)
            print(f"  [search] clicked via: {xpath}")
            return
        except Exception:
            continue
    raise RuntimeError("Could not find Search button")


def extract_permit_number(text: Any) -> Optional[str]:
    m = PERMIT_NUMBER_RE.search(str(text or ""))
    return m.group(0) if m else None


def normalize_detail_ref(driver: webdriver.Chrome, href: str, onclick: str, eid: str = "") -> Optional[str]:
    if href and "javascript" not in href.lower() and href.startswith("http"):
        return href
    if "capdetail" in href.lower() or "capid" in href.lower():
        if href.startswith("/"):
            base = driver.current_url.split("/Cap/")[0]
            return base + href
        return href
    if "dopostback" in onclick.lower():
        return f"__postback__{onclick}"
    return None


def extract_result_links(driver: webdriver.Chrome) -> List[Tuple[str, str, Dict]]:
    found = []
    rows = driver.find_elements(By.XPATH, "//tr[.//a or .//td]")
    for row in rows:
        try:
            row_text = clean_text(row.text)
            permit_number = extract_permit_number(row_text)
            if not permit_number:
                continue
            anchors = row.find_elements(By.XPATH, ".//a")
            detail_ref = None
            best_score = -1
            best_anchor_data = {}
            for a in anchors:
                href    = clean_text(a.get_attribute("href") or "")
                onclick = clean_text(a.get_attribute("onclick") or "")
                text    = clean_text(a.text)
                eid     = clean_text(a.get_attribute("id") or "")
                blob    = " ".join([href, onclick, text, eid]).lower()
                score   = 0
                if "capdetail" in blob or "capid" in blob: score += 5
                if extract_permit_number(text): score += 4
                if "dopostback" in blob: score += 3
                if "view" in blob or "detail" in blob: score += 1
                if href or onclick: score += 1
                if score > best_score:
                    best_score = score
                    detail_ref = normalize_detail_ref(driver, href, onclick, eid)
                    best_anchor_data = {"href": href, "onclick": onclick, "text": text}
            if not detail_ref:
                continue
            cells = [clean_text(td.text) for td in row.find_elements(By.XPATH, ".//td")]
            payload = {f"cell_{i}": v for i, v in enumerate(cells, 1) if v}
            payload["row_text"] = row_text
            payload.update(best_anchor_data)
            found.append((permit_number, detail_ref, payload))
        except Exception:
            continue

    # Dedup
    out, seen = [], set()
    for pn, dr, pl in found:
        k = (pn, dr)
        if k not in seen:
            seen.add(k)
            out.append((pn, dr, pl))
    return out


def next_results_page(driver: webdriver.Chrome) -> bool:
    for xpath in [
        "//a[contains(text(),'Next') and not(contains(@class,'disabled'))]",
        "//a[@title='Next page']",
        "//input[@value='Next']",
    ]:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(3)
            return True
        except Exception:
            continue
    return False


def extract_text_map(html: str) -> Dict[str, str]:
    """Extract label:value pairs from Accela detail page HTML."""
    result = {}
    label_re = re.compile(r"<(?:td|span|div|label)[^>]*>\s*([^<]{2,80}?)\s*</(?:td|span|div|label)>", re.I)
    labels = label_re.findall(html)
    for i, label in enumerate(labels):
        label = clean_text(label).rstrip(":")
        if label and i + 1 < len(labels):
            val = clean_text(labels[i + 1])
            if val and label.lower() not in {"", "search", "home", "help"}:
                result[label] = val
    return result


def guess_field(payload: Dict, *keys: str) -> str:
    pl = {k.lower(): v for k, v in payload.items()}
    for key in keys:
        if key.lower() in pl and pl[key.lower()]:
            return clean_text(pl[key.lower()])
    # Partial match
    for k, v in pl.items():
        for key in keys:
            if key.lower() in k and v:
                return clean_text(v)
    return ""


# ---------------------------------------------------------------------------
# Accela scraper
# ---------------------------------------------------------------------------

def scrape_accela_source(source: Dict, start: date, end: date,
                          visible: bool, limit: int, pages: int,
                          debug: bool = False) -> List[PermitRecord]:
    source_name  = source["name"]
    jurisdiction = source.get("jurisdiction", COUNTY_NAME)
    records: List[PermitRecord] = []
    seen_urls: set = set()
    seen_pages: set = set()

    driver = make_driver(visible=visible)
    try:
        print(f"\n[{source_name}] Opening {source['base_url']}")
        driver.get(source["base_url"])
        time.sleep(4)
        try_accept_disclaimer(driver)
        time.sleep(2)

        if debug:
            save_debug(driver, source_name, "01_loaded")

        set_search_dates(driver, start, end, source_name)
        maybe_select_record_type(driver, source.get("record_type_contains"))

        # Handle permit_type_filters (e.g. Hollywood) — run one search per type
        permit_type_filters = source.get("permit_type_filters")
        dropdown_id = source.get("permit_type_dropdown_id")
        if permit_type_filters and dropdown_id:
            for ptype in permit_type_filters:
                print(f"  [{source_name}] selecting permit type: {ptype}")
                try:
                    from selenium.webdriver.support.ui import Select as _Select
                    sel_el = driver.find_element(By.ID, dropdown_id)
                    _sel = _Select(sel_el)
                    _sel.select_by_visible_text(ptype)
                    time.sleep(0.5)
                except Exception as e:
                    print(f"  [{source_name}] could not select {ptype}: {e}")
                    continue

                if debug:
                    save_debug(driver, source_name, f"02_form_filled_{ptype.replace(' ','_')}")

                click_search(driver)

                for page_num in range(1, pages + 1):
                    links = extract_result_links(driver)
                    sig = tuple(sorted(u for _, u, _ in links))
                    if sig and sig in seen_pages:
                        print(f"  [{source_name}] page {page_num}: repeated — stopping")
                        break
                    if sig:
                        seen_pages.add(sig)
                    new_links = [(pn, url, pl) for pn, url, pl in links if url not in seen_urls]
                    print(f"  [{source_name}] {ptype} page {page_num}: {len(links)} links, {len(new_links)} new")
                    for permit_number, detail_ref, row_data in new_links:
                        seen_urls.add(detail_ref)
                        if limit and len(records) >= limit:
                            break
                        rec = PermitRecord(
                            source_name=source_name,
                            jurisdiction=jurisdiction,
                            permit_number=permit_number,
                            permit_type=ptype,
                            address_1=clean_text(row_data.get("cell_2") or row_data.get("row_text","")[:80]),
                            raw_payload=row_data,
                        )
                        records.append(rec)
                    if limit and len(records) >= limit:
                        break
                    if not next_results_page(driver):
                        break

                # Return to search form for next permit type
                driver.get(source["base_url"])
                time.sleep(3)
                set_search_dates(driver, start, end, source_name)

            # Already handled all permit types — skip normal search flow
            if debug:
                save_debug(driver, source_name, "02_form_filled")
            print(f"  [{source_name}] permit type iteration complete")
            return records

        if debug:
            save_debug(driver, source_name, "02_form_filled")

        click_search(driver)

        if debug:
            save_debug(driver, source_name, "03_results")

        for page_num in range(1, pages + 1):
            links = extract_result_links(driver)
            sig = tuple(sorted(u for _, u, _ in links))
            if sig and sig in seen_pages:
                print(f"  [{source_name}] page {page_num}: repeated page detected — stopping")
                break
            if sig:
                seen_pages.add(sig)

            new_links = [(pn, url, pl) for pn, url, pl in links if url not in seen_urls]
            print(f"  [{source_name}] page {page_num}: {len(links)} links, {len(new_links)} new")

            for permit_number, detail_ref, row_data in new_links:
                seen_urls.add(detail_ref)
                if limit and len(records) >= limit:
                    break

                # For postback links, parse from row data directly (no detail page needed)
                if detail_ref.startswith("__postback__"):
                    rec = PermitRecord(
                        source_name=source_name,
                        jurisdiction=jurisdiction,
                        permit_number=permit_number,
                        address_1=guess_field(row_data, "address", "site address", "location"),
                        owner_name=guess_field(row_data, "owner", "applicant", "name"),
                        permit_type=guess_field(row_data, "type", "permit type", "description"),
                        issued_date=parse_date(guess_field(row_data, "issued", "date", "issue date")),
                        raw_payload=row_data,
                    )
                    records.append(rec)
                    continue

                # Navigate to detail page
                try:
                    driver.get(detail_ref)
                    time.sleep(2)
                    html = driver.page_source
                    detail = extract_text_map(html)
                    detail.update(row_data)

                    rec = PermitRecord(
                        source_name=source_name,
                        jurisdiction=jurisdiction,
                        permit_number=permit_number,
                        address_1=guess_field(detail,
                            "Site Address", "Address", "Property Address", "Location"),
                        owner_name=guess_field(detail,
                            "Owner", "Owner Name", "Applicant", "Name"),
                        business_name=guess_field(detail,
                            "Business Name", "Contractor", "Company"),
                        permit_type=guess_field(detail,
                            "Permit Type", "Type", "Description", "Record Type"),
                        project_description=guess_field(detail,
                            "Description", "Work Description", "Project Description"),
                        issued_date=parse_date(guess_field(detail,
                            "Issued Date", "Issue Date", "Date Issued", "Approval Date")),
                        project_value=parse_money(guess_field(detail,
                            "Valuation", "Value", "Job Value", "Estimated Value")),
                        status=guess_field(detail, "Status", "Record Status"),
                        city=jurisdiction,
                        state="FL",
                        raw_payload=detail,
                    )
                    records.append(rec)

                    # Navigate back
                    driver.back()
                    time.sleep(2)

                except Exception as e:
                    print(f"  [{source_name}] detail error for {permit_number}: {e}")
                    try:
                        driver.get(source["base_url"])
                        time.sleep(3)
                    except Exception:
                        pass

            if limit and len(records) >= limit:
                print(f"  [{source_name}] reached limit {limit}")
                break

            if not next_results_page(driver):
                print(f"  [{source_name}] no more pages after page {page_num}")
                break

    except Exception as e:
        print(f"  [{source_name}] ERROR: {e}")
        save_debug(driver, source_name, "error")
    finally:
        driver.quit()

    print(f"  [{source_name}] collected {len(records)} records")
    return records


# ---------------------------------------------------------------------------
# Broward County open data scraper (non-Accela cities)
# ---------------------------------------------------------------------------

def scrape_broward_open_data(source: Dict, start: date, end: date) -> List[PermitRecord]:
    """
    Query the Broward County ArcGIS REST API for permits.
    Covers unincorporated Broward + many municipalities that feed into the county system.
    """
    api_url = source.get("api_url", "https://gis.broward.org/arcgis/rest/services/PermitsAndCertificates/MapServer/0/query")
    records = []

    start_ts = int(datetime.combine(start, datetime.min.time()).timestamp() * 1000)
    end_ts   = int(datetime.combine(end,   datetime.max.time()).timestamp() * 1000)

    params = {
        "where":         f"IssueDate >= DATE '{start.isoformat()}' AND IssueDate <= DATE '{end.isoformat()}'",
        "outFields":     "*",
        "f":             "json",
        "resultOffset":  0,
        "resultRecordCount": 1000,
    }

    print(f"\n[broward_open_data] Querying ArcGIS: {start} → {end}")

    offset = 0
    while True:
        params["resultOffset"] = offset
        try:
            resp = requests.get(api_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [broward_open_data] API error at offset {offset}: {e}")
            break

        features = data.get("features", [])
        print(f"  [broward_open_data] offset={offset} features={len(features)}")

        if not features:
            break

        for feat in features:
            attrs = feat.get("attributes", {})
            permit_number = clean_text(
                attrs.get("PermitNumber") or attrs.get("PERMIT_NUMBER") or
                attrs.get("PermitNum") or attrs.get("PERMITNO") or ""
            )
            if not permit_number:
                continue

            # Date handling — ArcGIS returns epoch ms
            issued_ts = attrs.get("IssueDate") or attrs.get("ISSUED_DATE") or attrs.get("DateIssued")
            issued_date = None
            if issued_ts:
                try:
                    issued_date = datetime.fromtimestamp(int(issued_ts) / 1000).date()
                except Exception:
                    issued_date = parse_date(str(issued_ts))

            address_parts = [
                clean_text(attrs.get("SiteAddress") or attrs.get("SITE_ADDRESS") or attrs.get("Address") or ""),
                clean_text(attrs.get("SiteCity") or attrs.get("CITY") or ""),
            ]
            address_1 = " ".join(p for p in address_parts if p).strip()

            owner = clean_text(
                attrs.get("OwnerName") or attrs.get("OWNER_NAME") or
                attrs.get("Owner") or attrs.get("ApplicantName") or ""
            )
            permit_type = clean_text(
                attrs.get("PermitType") or attrs.get("PERMIT_TYPE") or
                attrs.get("WorkType") or attrs.get("Description") or ""
            )
            value = parse_money(attrs.get("EstimatedValue") or attrs.get("ESTIMATED_VALUE") or attrs.get("JobValue") or "")
            city = clean_text(attrs.get("SiteCity") or attrs.get("CITY") or attrs.get("Municipality") or "Broward County")

            rec = PermitRecord(
                source_name="broward_county_open_data",
                jurisdiction=city or "Broward County",
                permit_number=permit_number,
                permit_type=permit_type,
                project_description=clean_text(attrs.get("WorkDescription") or attrs.get("Scope") or ""),
                issued_date=issued_date,
                owner_name=owner,
                address_1=address_1,
                city=city,
                state="FL",
                zip=clean_text(attrs.get("SiteZip") or attrs.get("ZIP") or ""),
                project_value=value,
                status=clean_text(attrs.get("Status") or attrs.get("PermitStatus") or ""),
                raw_payload=attrs,
            )
            records.append(rec)

        if len(features) < 1000:
            break  # No more pages
        offset += 1000
        time.sleep(0.5)

    print(f"  [broward_open_data] total records: {len(records)}")
    return records


# ---------------------------------------------------------------------------
# Database import
# ---------------------------------------------------------------------------

def get_county_id(cur) -> int:
    cur.execute("SELECT id FROM counties WHERE county_name = %s", (COUNTY_NAME,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO counties (county_name, state_code, created_at, updated_at) "
        "VALUES (%s, 'FL', NOW(), NOW()) RETURNING id",
        (COUNTY_NAME,)
    )
    return cur.fetchone()[0]


def import_records(records: List[PermitRecord]) -> Dict[str, int]:
    if not records:
        return {"raw": 0, "normalized": 0, "skipped": 0}

    conn = get_connection()
    stats = {"raw": 0, "normalized": 0, "skipped": 0}
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            county_id = get_county_id(cur)

            for rec in records:
                if not rec.permit_number:
                    stats["skipped"] += 1
                    continue

                source_record_id = f"{rec.source_name}::{rec.permit_number}"
                payload = json.dumps(rec.raw_payload, default=str)

                # raw_permits
                cur.execute("""
                    INSERT INTO raw_permits (county_id, source_file, source_record_id, raw_payload, issued_date)
                    VALUES (%s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (county_id, source_record_id) DO UPDATE SET
                        raw_payload = EXCLUDED.raw_payload,
                        issued_date = EXCLUDED.issued_date
                    RETURNING id, (xmax = 0) AS is_insert
                """, (county_id, rec.source_name, source_record_id, payload, rec.issued_date))
                rp_result = cur.fetchone()
                raw_permit_id = rp_result[0]
                if rp_result[1]:
                    stats["raw"] += 1

                # normalized_permits
                cur.execute("""
                    INSERT INTO normalized_permits (
                        county_id, raw_permit_id, owner_name, business_name,
                        address_1, city, state, zip,
                        permit_number, permit_type, project_description,
                        issued_date, trade, normalized_hash
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (county_id, permit_number) DO UPDATE SET
                        owner_name          = EXCLUDED.owner_name,
                        business_name       = EXCLUDED.business_name,
                        address_1           = EXCLUDED.address_1,
                        city                = EXCLUDED.city,
                        permit_type         = EXCLUDED.permit_type,
                        project_description = EXCLUDED.project_description,
                        issued_date         = EXCLUDED.issued_date,
                        trade               = EXCLUDED.trade
                    RETURNING id, (xmax = 0) AS is_insert
                """, (
                    county_id, raw_permit_id,
                    rec.owner_name, rec.business_name,
                    rec.address_1, rec.city or rec.jurisdiction, "FL", rec.zip,
                    rec.permit_number, rec.permit_type, rec.project_description,
                    rec.issued_date,
                    (rec.permit_type or "").lower()[:100] if rec.permit_type else None,
                    f"{rec.source_name}::{rec.permit_number}",
                ))
                np_result = cur.fetchone()
                if np_result and np_result[1]:
                    stats["normalized"] += 1

        conn.commit()

    except Exception as e:
        conn.rollback()
        print(f"  [import] ERROR: {e}")
        raise
    finally:
        conn.close()

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Broward County permits from all sources")
    parser.add_argument("--days-back",   type=int, default=30)
    parser.add_argument("--start",       type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--end",         type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--source",      type=str, default=None,
                        help="Run one source by name. Options: " +
                             ", ".join(s["name"] for s in DEFAULT_SOURCES))
    parser.add_argument("--all-broward", action="store_true", help="Run all sources")
    parser.add_argument("--visible",     action="store_true", help="Show Chrome window")
    parser.add_argument("--limit",       type=int, default=0, help="Max records per source (0=unlimited)")
    parser.add_argument("--pages",       type=int, default=25, help="Max result pages per source")
    parser.add_argument("--debug-pages", action="store_true", help="Save debug screenshots")
    return parser.parse_args()


def resolve_dates(args: argparse.Namespace) -> Tuple[date, date]:
    start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else today_local() - timedelta(days=args.days_back)
    end   = datetime.strptime(args.end,   "%Y-%m-%d").date() if args.end   else today_local()
    return start, end


def main() -> None:
    args  = parse_args()
    start, end = resolve_dates(args)
    all_sources = DEFAULT_SOURCES

    if args.source:
        sources = [s for s in all_sources if s["name"] == args.source]
        if not sources:
            available = ", ".join(s["name"] for s in all_sources)
            raise SystemExit(f"Unknown source: {args.source}. Available: {available}")
    elif args.all_broward:
        sources = all_sources
    else:
        # Default: Weston only (confirmed working)
        sources = [s for s in all_sources if s["name"] == "weston_accela"]

    print(f"Broward permit scraper | {start} → {end} | sources: {', '.join(s['name'] for s in sources)}")

    all_records: List[PermitRecord] = []

    for source in sources:
        source_type = source.get("type", "accela")
        try:
            if source_type == "open_data":
                recs = scrape_broward_open_data(source, start, end)
            else:
                recs = scrape_accela_source(
                    source, start, end,
                    visible=args.visible,
                    limit=args.limit,
                    pages=args.pages,
                    debug=args.debug_pages,
                )
            all_records.extend(recs)
        except Exception as e:
            print(f"  [{source['name']}] FAILED: {e}")
            continue

    # Dedup across sources
    unique: List[PermitRecord] = []
    seen: set = set()
    for rec in all_records:
        key = (rec.source_name, rec.permit_number)
        if key not in seen:
            seen.add(key)
            unique.append(rec)

    print(f"\nTotal unique records collected: {len(unique)}")

    if unique:
        # Save raw snapshot
        snap = RAW_EXPORT_DIR / f"broward_permits_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        snap.write_text(json.dumps([asdict(r) for r in unique], default=str, indent=2), encoding="utf-8")
        print(f"Raw snapshot: {snap}")

        stats = import_records(unique)
        print(f"\n--- Broward import summary ---")
        print(f"  raw_permits inserted      : {stats['raw']}")
        print(f"  normalized_permits inserted: {stats['normalized']}")
        print(f"  skipped (no permit#)       : {stats['skipped']}")
        print(f"\nNext: python -m app.workers.match_and_score")
    else:
        print("No records collected.")


if __name__ == "__main__":
    main()