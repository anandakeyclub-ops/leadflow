r"""
illinois_scraper.py
===================
Illinois data sources for the TaxCase Review data engine.

LIENS  — Illinois Secretary of State UCC / Federal Tax Lien index (Selenium).
         https://apps.ilsos.gov/uccsearch/
         Filters document type = Federal Tax Lien for debtor types
         Organization (businesses/contractors) and Individual (sole props),
         paginates (2s/page, max 1000/run), and extracts debtor name, city/zip,
         filing date, lien amount, and file number. Checkpoints to
         illinois_scraper_checkpoint.json.
LICENSES — Illinois Dept. of Financial & Professional Regulation (IDFPR).
         https://www.idfpr.com/LicenseLookUp/licenselookup.asp
         Target license types: roofing (058), HVAC (004),
         general contractor (016), electrician (017).

Both are best-effort against live state portals. apps.ilsos.gov sits behind a
WAF/anti-bot layer and IDFPR is a classic ASP.NET form, so each function detects
a block / unparseable response and returns 0 with clear logging (+ debug
artifacts under data/data_engine/il_debug/) rather than crash the daily runner.

DB writes:
  liens    -> normalized_liens     (state='IL', lien_source='IL_SOS_UCC',
                                     county derived from city)
  licenses -> normalized_contacts  (state='IL', license_source='IL_DPR')
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from app.core.db import get_connection, release_connection  # noqa: E402
from scripts.data_engine.data_collector import (  # noqa: E402
    http_get, get_or_create_county, MAX_PER_COUNTY, is_business,
)

IDFPR_LOOKUP = "https://www.idfpr.com/LicenseLookUp/licenselookup.asp"

# IDFPR license type codes from the task.
IL_LICENSE_TYPES = {
    "058": "roofing",
    "004": "hvac",
    "016": "general contractor",
    "017": "electrician",
}

# ── IL Secretary of State UCC / Federal Tax Lien search (Selenium) ─────────────
IL_SOS_UCC_URL  = os.getenv("IL_SOS_UCC_URL", "https://apps.ilsos.gov/uccsearch/")
IL_DEBTOR_TYPES = ("Organization", "Individual")
IL_PAGE_DELAY   = 2.0          # seconds between result pages (rate limit)
IL_MAX_RECORDS  = 1000         # hard cap per run
IL_CHECKPOINT   = LEADFLOW_DIR / "data" / "data_engine" / "illinois_scraper_checkpoint.json"
IL_DEBUG_DIR    = LEADFLOW_DIR / "data" / "data_engine" / "il_debug"

# Major IL cities -> county (for deriving county from a debtor's city).
IL_CITY_COUNTY = {
    "chicago": "Cook", "cicero": "Cook", "evanston": "Cook", "schaumburg": "Cook",
    "skokie": "Cook", "oak park": "Cook", "berwyn": "Cook", "aurora": "Kane",
    "elgin": "Kane", "naperville": "DuPage", "wheaton": "DuPage", "joliet": "Will",
    "rockford": "Winnebago", "peoria": "Peoria", "springfield": "Sangamon",
    "champaign": "Champaign", "urbana": "Champaign", "bloomington": "McLean",
    "decatur": "Macon", "waukegan": "Lake", "elmhurst": "DuPage",
    "des plaines": "Cook", "arlington heights": "Cook", "palatine": "Cook",
}

# ── CourtListener (federal docket API) — primary IL lien source, no WAF ────────
COURTLISTENER_TOKEN = os.getenv("COURTLISTENER_TOKEN", "")
CL_DOCKETS_URL = "https://www.courtlistener.com/api/rest/v4/dockets/"
# IL federal district court id -> representative county (court seat).
IL_FED_DISTRICTS = {"ilnd": "Cook", "ilcd": "Sangamon", "ilsd": "St. Clair"}


# ── LIENS — CourtListener federal dockets (primary; no WAF) ────────────────────
def _clean_cl_name(name: str) -> str:
    """Strip the 'United States of America v.' prefix so the taxpayer/debtor
    remains as the stored name."""
    n = (name or "").strip()
    n = re.sub(r"^(the\s+)?united states( of america)?\s+v\.?\s+", "", n, flags=re.I)
    n = re.sub(r"^usa?\s+v\.?\s+", "", n, flags=re.I)
    return n.strip() or (name or "").strip()


def collect_il_liens_courtlistener(limit: int = IL_MAX_RECORDS,
                                   dry_run: bool = False) -> int:
    """
    Query the CourtListener v4 dockets API for federal-tax-lien cases in the IL
    federal districts (ilnd=Cook/Chicago, ilcd, ilsd), paginating via `next`,
    and store them in normalized_liens (state='IL', lien_source='COURTLISTENER').
    Needs COURTLISTENER_TOKEN (free registration at courtlistener.com); returns 0
    if absent/unauthorized so the caller can fall back to the SOS UCC scraper.
    """
    token = COURTLISTENER_TOKEN or os.getenv("COURTLISTENER_TOKEN", "")
    if not token:
        print("    IL/CourtListener: COURTLISTENER_TOKEN not set. (0 liens)")
        return 0
    headers = {"Authorization": f"Token {token}",
               "User-Agent": "TaxCaseReview/1.0 (research@taxcasereview.org)"}
    limit = min(limit, IL_MAX_RECORDS)
    all_rows: list[dict] = []
    for court, county in IL_FED_DISTRICTS.items():
        if len(all_rows) >= limit:
            break
        url = CL_DOCKETS_URL
        params = {"description": "federal tax lien", "court": court,
                  "page_size": 100}
        pages = 0
        while url and len(all_rows) < limit and pages < 20:
            pages += 1
            r = http_get(url, params=params, headers=headers, timeout=40)
            params = None  # subsequent `next` URLs already carry the query
            if r is None:
                print(f"    IL/CourtListener: {court} unreachable.")
                break
            if r.status_code in (401, 403):
                print(f"    IL/CourtListener: auth failed ({r.status_code}) — "
                      "check COURTLISTENER_TOKEN. (0 liens)")
                return 0
            if r.status_code != 200:
                print(f"    IL/CourtListener: {court} HTTP {r.status_code}.")
                break
            try:
                j = r.json()
            except ValueError:
                break
            for d in j.get("results", []):
                name = _clean_cl_name(d.get("case_name") or "")
                if len(name) < 3:
                    continue
                all_rows.append({
                    "debtor_name": name,
                    "filing_date": d.get("date_filed"),
                    "file_number": (d.get("docket_number") or "").strip(),
                    "county": county,
                })
            url = j.get("next")
            time.sleep(IL_PAGE_DELAY)
        print(f"    IL/CourtListener: {court} ({county}) -> "
              f"{sum(1 for x in all_rows if x['county'] == county)} rows so far")

    all_rows = all_rows[:limit]
    if dry_run:
        print(f"    [DRY RUN] {len(all_rows)} IL CourtListener liens parsed:")
        for r in all_rows[:15]:
            print(f"       {(r.get('debtor_name') or '')[:40]:<40} | "
                  f"{r.get('county',''):<10} | {r.get('filing_date','')} | "
                  f"{r.get('file_number','')}")
        return len(all_rows)
    return _store_il_liens(all_rows, lien_source="COURTLISTENER")


# ── LIENS — IL Secretary of State UCC / Federal Tax Lien index (Selenium) ──────
def collect_il_liens(limit: int = IL_MAX_RECORDS,
                     debtor_types=IL_DEBTOR_TYPES,
                     dry_run: bool = False, headless: bool = True) -> int:
    """
    Selenium scrape of the IL SOS UCC/Federal-Tax-Lien index
    (apps.ilsos.gov/uccsearch). For each debtor type (Organization, then
    Individual) it filters document type = Federal Tax Lien, paginates the
    results (2s/page), extracts debtor name + address + filing date + amount +
    file number, and stores them in normalized_liens (state='IL',
    lien_source='IL_SOS_UCC', county derived from city). Checkpoints to
    illinois_scraper_checkpoint.json; max 1000 records/run. dry_run parses +
    reports without writing. Returns count stored (or parsed, in dry_run).
    """
    limit = min(limit, IL_MAX_RECORDS)
    cp = _il_load_checkpoint()
    seen = set(cp.get("seen_file_numbers", []))
    driver = None
    all_rows: list[dict] = []
    try:
        driver = _il_get_driver(headless=headless)
        if not _il_open_ucc_search(driver):
            print("    IL/SOS UCC: search form unreachable — the apps.ilsos.gov "
                  "portal blocked the automated client (WAF/anti-bot) or the page "
                  "moved. See data/data_engine/il_debug/. (0 liens)")
            return 0
        for dtype in debtor_types:
            if len(all_rows) >= limit:
                break
            try:
                rows = _il_ucc_search(driver, dtype, limit - len(all_rows), seen)
            except Exception as e:
                print(f"      [{dtype}] search error: {type(e).__name__}: {str(e)[:160]}")
                rows = []
            for r in rows:
                r["debtor_type"] = dtype
                if r.get("file_number"):
                    seen.add(r["file_number"])
            print(f"    IL/SOS UCC: {dtype} -> {len(rows)} FTL rows")
            all_rows.extend(rows)
    except Exception as e:
        print(f"    IL/SOS UCC selenium error: {type(e).__name__}: {str(e)[:200]}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    all_rows = all_rows[:limit]
    cp["seen_file_numbers"] = list(seen)[-5000:]
    cp["last_run"] = datetime.now().isoformat(timespec="seconds")
    _il_save_checkpoint(cp)

    if dry_run:
        print(f"    [DRY RUN] {len(all_rows)} IL FTL liens parsed (not stored):")
        for r in all_rows[:15]:
            print(f"       {(r.get('debtor_name') or '')[:38]:<38} | "
                  f"{(r.get('city') or ''):<14} | {r.get('filing_date','')} | "
                  f"{r.get('file_number','')}")
        return len(all_rows)
    return _store_il_liens(all_rows)


def _il_get_driver(headless: bool = True):
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


def _il_save_debug(driver, label: str):
    try:
        IL_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        (IL_DEBUG_DIR / f"{label}.html").write_text(
            driver.page_source, encoding="utf-8", errors="replace")
        try:
            driver.save_screenshot(str(IL_DEBUG_DIR / f"{label}.png"))
        except Exception:
            pass
    except Exception:
        pass


def _il_load_checkpoint() -> dict:
    try:
        if IL_CHECKPOINT.exists():
            return json.loads(IL_CHECKPOINT.read_text())
    except Exception:
        pass
    return {}


def _il_save_checkpoint(cp: dict):
    try:
        IL_CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
        IL_CHECKPOINT.write_text(json.dumps(cp, indent=2, default=str))
    except Exception:
        pass


_IL_WAF_MARKERS = ("page you are looking for is not available", "reference id",
                   "access denied", "request blocked", "forbidden")


def _il_open_ucc_search(driver) -> bool:
    """Open the UCC search page; click through any disclaimer/Accept gate; return
    True when a usable search form (with a Federal Tax Lien option) is present."""
    from selenium.webdriver.common.by import By
    driver.get(IL_SOS_UCC_URL)
    time.sleep(3)
    low = driver.page_source.lower()
    if any(m in low for m in _IL_WAF_MARKERS):
        _il_save_debug(driver, "00_blocked")
        return False
    # Disclaimer / "I Accept" / "Continue" gate, if any.
    for kw in ("accept", "agree", "continue", "search"):
        for e in driver.find_elements(
                By.CSS_SELECTOR, "button, input[type='submit'], input[type='button'], a"):
            lbl = (e.get_attribute("value") or e.text or "").strip().lower()
            if lbl in (kw, "i " + kw, kw + " >"):
                try:
                    e.click(); time.sleep(2)
                except Exception:
                    pass
                break
    _il_save_debug(driver, "01_search_form")
    # Usable if a control exposes a "Federal Tax Lien" choice, or any select exists.
    if "federal tax lien" in driver.page_source.lower():
        return True
    return len(driver.find_elements(By.TAG_NAME, "select")) > 0


def _il_select_by_text(driver, want_substrings) -> bool:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import Select
    wants = [w.lower() for w in want_substrings]
    for sel_el in driver.find_elements(By.TAG_NAME, "select"):
        try:
            sel = Select(sel_el)
            for opt in sel.options:
                if opt.text and any(w in opt.text.lower() for w in wants):
                    sel.select_by_visible_text(opt.text)
                    return True
        except Exception:
            continue
    return False


def _il_ucc_search(driver, debtor_type: str, max_rows: int, seen: set) -> list:
    """Filter to Federal Tax Lien + debtor type, submit, and paginate results
    (2s/page) until exhausted or max_rows. Returns parsed rows (new file numbers
    only)."""
    from selenium.webdriver.common.by import By

    if not _il_open_ucc_search(driver):
        return []
    _il_select_by_text(driver, ["federal tax lien"])          # document/lien type
    _il_select_by_text(driver, [debtor_type.lower()])         # Organization / Individual
    _il_select_by_text(driver, ["illinois"])                  # state, if a dropdown
    # Submit the search.
    btn = None
    for sel in ("input[type='submit'][value*='Search' i]",
                "button", "input[type='submit']", "input[type='button']"):
        for e in driver.find_elements(By.CSS_SELECTOR, sel):
            lbl = (e.get_attribute("value") or e.text or "").lower()
            if "search" in lbl:
                btn = e; break
        if btn:
            break
    try:
        if btn:
            btn.click()
        else:
            driver.find_elements(By.TAG_NAME, "form")[0].submit()
    except Exception:
        pass
    time.sleep(IL_PAGE_DELAY)

    rows: list[dict] = []
    page = 0
    while len(rows) < max_rows and page < 60:
        page += 1
        _il_save_debug(driver, f"02_{debtor_type}_p{page}")
        page_rows = _il_parse_results(driver.page_source)
        fresh = [r for r in page_rows
                 if r.get("file_number") and r["file_number"] not in seen]
        rows.extend(fresh)
        # advance to next page
        nxt = None
        for e in driver.find_elements(By.CSS_SELECTOR, "a, button, input[type='submit']"):
            lbl = (e.get_attribute("value") or e.text or "").strip().lower()
            if lbl in ("next", "next >", ">", "next page"):
                nxt = e; break
        if not nxt or not page_rows:
            break
        try:
            nxt.click()
        except Exception:
            break
        time.sleep(IL_PAGE_DELAY)
    return rows[:max_rows]


_IL_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_IL_AMT_RE  = re.compile(r"\$\s?[\d,]+(?:\.\d{2})?")
_IL_CSZ_RE  = re.compile(r"([A-Za-z .'-]+),?\s+(IL|ILLINOIS)\s+(\d{5})", re.I)


def _il_parse_results(html: str) -> list[dict]:
    """Heuristic parse of a UCC results table into structured lien rows. A row
    needs at least a name and a filing date to count."""
    out: list[dict] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        for tr in soup.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            joined = " ".join(cells)
            dm = _IL_DATE_RE.search(joined)
            if not dm:
                continue
            # debtor name: first cell with >=2 alpha words, not the date
            name = ""
            for c in cells:
                if _IL_DATE_RE.fullmatch(c.strip()):
                    continue
                letters = re.sub(r"[^A-Za-z ]", "", c).strip()
                if len(letters.split()) >= 2 and len(letters) >= 5:
                    name = c
                    break
            if not name or name.lower().startswith(("debtor", "name", "secured", "file")):
                continue
            city = zipc = ""
            cm = _IL_CSZ_RE.search(joined)
            if cm:
                city, _, zipc = cm.group(1).strip(), cm.group(2), cm.group(3)
            amt = None
            am = _IL_AMT_RE.search(joined)
            if am:
                try:
                    amt = float(am.group(0).replace("$", "").replace(",", "").strip())
                except ValueError:
                    amt = None
            # file number: a long digit/dash token
            fn = ""
            for c in cells:
                t = c.strip()
                if re.fullmatch(r"[0-9][0-9\-]{5,}", t):
                    fn = t; break
            out.append({"debtor_name": name.strip()[:250], "city": city[:100],
                        "zip": zipc[:20], "filing_date": dm.group(0),
                        "lien_amount": amt, "file_number": fn})
    except Exception:
        pass
    return out


def _il_city_to_county(city: str) -> str:
    return IL_CITY_COUNTY.get((city or "").strip().lower(), "Cook")


def _il_to_iso(s):
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _store_il_liens(rows: list[dict], lien_source: str = "IL_SOS_UCC") -> int:
    conn = get_connection()
    conn.autocommit = False
    added = 0
    try:
        for rec in rows:
            name = (rec.get("debtor_name") or "").strip()
            if len(name) < 3:
                continue
            county_name = (rec.get("county") or "").strip() \
                or _il_city_to_county(rec.get("city"))
            filed = _il_to_iso(rec.get("filing_date"))
            key = rec.get("file_number") or f"{name}|{rec.get('filing_date')}"
            h = hashlib.md5(f"{lien_source.lower()}|{key}".encode()).hexdigest()
            with conn.cursor() as cur:
                county_id = get_or_create_county(cur, county_name, "IL")
                cur.execute("""
                    INSERT INTO normalized_liens
                        (county_id, debtor_name, business_name, filing_type,
                         lien_type, lien_source, normalized_hash, state,
                         amount, filed_date, city, zip, created_at)
                    VALUES (%s,%s,%s,'FEDERAL TAX LIEN','FEDERAL TAX LIEN',
                            %s,%s,'IL',%s,%s,%s,%s,NOW())
                    ON CONFLICT (normalized_hash) DO NOTHING
                    RETURNING id
                """, (county_id, name[:250],
                      name[:250] if is_business(name) else None,
                      lien_source, h, rec.get("lien_amount"), filed,
                      (rec.get("city") or "")[:100] or None,
                      (rec.get("zip") or "")[:20] or None))
                if cur.fetchone():
                    added += 1
            conn.commit()
        print(f"    IL/{lien_source}: +{added} new IL liens")
    except Exception as e:
        conn.rollback()
        print(f"    IL/{lien_source} store error: {e}")
    finally:
        release_connection(conn)
    return added


# ── LICENSES — IDFPR ───────────────────────────────────────────────────────────
def collect_il_licenses(limit: int = MAX_PER_COUNTY) -> int:
    """Look up IL contractor licenses (roofing/HVAC/GC/electrician) via IDFPR and
    store them in normalized_contacts (state='IL', license_source='IL_DPR').
    Returns count written."""
    session = requests.Session()
    session.headers.update({"User-Agent":
                            "Mozilla/5.0 (compatible; LeadFlowDataEngine/1.0)"})
    form = _idfpr_form_state(session)
    if form is None:
        print("    IL/IDFPR: could not load lookup page — pending. (0 licenses)")
        return 0

    all_rows: list[dict] = []
    for code, label in IL_LICENSE_TYPES.items():
        if len(all_rows) >= limit:
            break
        all_rows.extend(_idfpr_search(session, form, code, label))

    if not all_rows:
        print("    IL/IDFPR: no parseable license rows (ASP.NET viewstate/markup "
              "change or block) — pending. (0 licenses)")
        return 0
    return _store_il_licenses(all_rows[:limit])


def _idfpr_form_state(session: requests.Session):
    """Fetch the IDFPR lookup page and capture ASP.NET viewstate fields."""
    try:
        r = session.get(IDFPR_LOOKUP, timeout=30)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "lxml")
        state = {}
        for fid in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
            el = soup.find("input", {"name": fid})
            if el and el.get("value"):
                state[fid] = el["value"]
        return state
    except requests.RequestException:
        return None


def _idfpr_search(session: requests.Session, form: dict,
                  type_code: str, label: str) -> list[dict]:
    """POST one license-type search and parse the results table. Best-effort —
    IDFPR field names vary by page version, so we post viewstate + a license
    type and parse whatever tabular rows come back."""
    out: list[dict] = []
    try:
        data = dict(form)
        data.update({"LicenseType": type_code, "txtLicenseType": type_code})
        r = session.post(IDFPR_LOOKUP, data=data, timeout=30)
        if r.status_code != 200:
            return out
        soup = BeautifulSoup(r.text, "lxml")
        for tr in soup.select("table tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) < 2:
                continue
            name = cells[0]
            if not name or name.lower() in ("name", "licensee name"):
                continue
            out.append({
                "name":     name,
                "license":  cells[1] if len(cells) > 1 else "",
                "status":   cells[2] if len(cells) > 2 else "",
                "city":     cells[3] if len(cells) > 3 else "",
                "type":     label,
            })
    except Exception:
        pass
    return out


def _looks_like_license(v: str) -> bool:
    """IDFPR license numbers contain digits and are reasonably long. Reject UI
    labels / page chrome the naive HTML parse may have picked up."""
    v = (v or "").strip()
    return len(v) >= 6 and any(ch.isdigit() for ch in v)


def _store_il_licenses(rows: list[dict]) -> int:
    conn = get_connection()
    conn.autocommit = False
    added = 0
    try:
        for rec in rows:
            name = (rec.get("name") or "").strip()
            lic = (rec.get("license") or "").strip()
            if not name or len(name) < 3 or not _looks_like_license(lic):
                continue
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO normalized_contacts
                        (state, state_name, license_number, license_type,
                         license_status, license_source, business_name,
                         owner_name, business_city, data_source)
                    VALUES ('IL','Illinois',%s,%s,%s,'IL_DPR',%s,%s,%s,'illinois_scraper')
                    ON CONFLICT (state, license_number) DO NOTHING
                """, (lic[:100], (rec.get("type", "") or "")[:100],
                      (rec.get("status", "") or "")[:50],
                      name[:200], name[:200], (rec.get("city") or "")[:100]))
                if cur.rowcount:
                    added += 1
            conn.commit()
        print(f"    IL/IDFPR: +{added} contractor licenses")
    except Exception as e:
        conn.rollback()
        print(f"    IL/IDFPR store error: {e}")
    finally:
        release_connection(conn)
    return added


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Illinois SOS UCC lien + IDFPR license scraper")
    ap.add_argument("--dry-run", action="store_true",
                    help="search + parse, but do not write to the DB")
    ap.add_argument("--debtor-types", default=None,
                    help="comma list (default: Organization,Individual)")
    ap.add_argument("--limit", type=int, default=IL_MAX_RECORDS)
    ap.add_argument("--no-headless", action="store_true")
    ap.add_argument("--courtlistener", action="store_true",
                    help="use the CourtListener federal docket API instead of SOS UCC")
    ap.add_argument("--licenses", action="store_true",
                    help="run the IDFPR license scrape instead of SOS UCC liens")
    args = ap.parse_args()

    if args.licenses:
        print(f"IL licenses: {collect_il_licenses(args.limit)}")
        return
    if args.courtlistener:
        n = collect_il_liens_courtlistener(limit=args.limit, dry_run=args.dry_run)
    else:
        dtypes = (tuple(t.strip() for t in args.debtor_types.split(",") if t.strip())
                  if args.debtor_types else IL_DEBTOR_TYPES)
        n = collect_il_liens(limit=args.limit, debtor_types=dtypes,
                             dry_run=args.dry_run, headless=not args.no_headless)
    tag = "(dry-run) " if args.dry_run else ""
    print(f"\nIL liens {tag}result: {n}")


if __name__ == "__main__":
    main()
