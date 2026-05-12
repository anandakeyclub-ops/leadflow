"""
scrape_volusia_permits.py
===============================
Volusia County building permits — ported from Permit_Bot download_volusia_weekly.py.
Scraping logic unchanged. DB import uses LeadFlow normalized_permits format.

Usage:
  python -m app.workers.scrape_volusia_permits --days-back 30
  python -m app.workers.scrape_volusia_permits --days-back 180 --no-db
"""
from __future__ import annotations

import base64
import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from selenium import webdriver
from selenium.webdriver import ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait


STATE = "florida"
COUNTY = "volusia"

BASE = "https://connectlivepermits.org"
APP_URLS = [
    BASE + "/citizenportal/app/landing",
    BASE + "/citizenportal/app/search-advanced",  # Go directly here — token from this context works for API
]
PAGE_CONFIG_URL = BASE + "/citizenportal/rest/configurationservices/pageConfiguration/"
API_URL = BASE + "/citizenportal/rest/amandaservice/executeCustomTransaction/"

LOOKBACK_DAYS = 7
DISTRICT = "A"
FOLDER_TYPE = "P"

HEADLESS = False
TOKEN_CAPTURE_WAIT_SECONDS = 10

RAW_COLUMNS = [
    "PERMITNO",
    "RECORD_TYPE",
    "PERMIT_DESCRIPTION",
    "FULL_ADDRESS",
    "OWNER_NAME",
    "CONTRACTOR_NAME",
    "FINAL_VALUATION",
    "LAST_ISSUED_DATE",
    "STATUS",
    "TRADE",
    "SOURCE",
]


def find_project_root() -> Path:
    script_path = Path(__file__).resolve()
    if script_path.parent.name.lower() == "scripts":
        return script_path.parent.parent
    return Path.cwd().resolve()


def ensure_dirs(base_dir: Path) -> tuple[Path, Path]:
    raw_dir = base_dir / "data" / "raw" / STATE / COUNTY
    archive_dir = base_dir / "data" / "archive" / STATE / COUNTY
    raw_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir, archive_dir


def build_driver() -> webdriver.Chrome:
    options = ChromeOptions()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    if HEADLESS:
        options.add_argument("--headless=new")

    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")

    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd(
        "Network.enable",
        {
            "maxTotalBufferSize": 100000000,
            "maxResourceBufferSize": 50000000,
        },
    )
    return driver


def get_performance_messages(driver: webdriver.Chrome) -> list[dict]:
    messages = []
    for entry in driver.get_log("performance"):
        try:
            messages.append(json.loads(entry["message"])["message"])
        except Exception:
            continue
    return messages


def extract_token_from_logs(driver: webdriver.Chrome) -> str | None:
    token_candidates: list[str] = []

    for msg in get_performance_messages(driver):
        method = msg.get("method")
        params = msg.get("params", {})

        if method == "Network.requestWillBeSent":
            headers = params.get("request", {}).get("headers", {})
            auth = (
                headers.get("Authorization")
                or headers.get("authorization")
                or headers.get("AUTHORIZATION")
            )
            if auth and "bearer " in auth.lower():
                token_candidates.append(auth.strip())

        elif method == "Network.responseReceived":
            headers = params.get("response", {}).get("headers", {})
            xauth = (
                headers.get("x-auth-token")
                or headers.get("X-Auth-Token")
                or headers.get("X-AUTH-TOKEN")
            )
            if xauth:
                token_candidates.append("Bearer " + xauth.strip())

    return token_candidates[-1] if token_candidates else None


def bootstrap_auth_token(raw_dir: Path) -> tuple[str, str | None]:
    """
    Uses CDP Fetch.enable to intercept pageConfiguration and executeCustomTransaction
    responses IN REAL TIME as they arrive — before Chrome evicts them.

    This is the only reliable way to read response bodies from a SPA.
    Returns (auth_header, transaction_code_or_None).
    """
    print("[BOOTSTRAP] Launching Chrome with CDP response interception...")
    driver = build_driver()

    captured: dict = {"token": None, "transaction_code": None}

    try:
        # Enable CDP Fetch interception — pause responses so we can read bodies
        driver.execute_cdp_cmd("Fetch.enable", {
            "patterns": [
                {"urlPattern": "*/pageConfiguration/*", "requestStage": "Response"},
                {"urlPattern": "*/executeCustomTransaction/*", "requestStage": "Response"},
                {"urlPattern": "*/configurationservices/*", "requestStage": "Response"},
            ]
        })

        # Store interception IDs for later fulfillment
        pending: dict[str, dict] = {}  # requestId → params

        def intercept_response(params: dict) -> None:
            request_id = params.get("requestId", "")
            url = params.get("request", {}).get("url", params.get("responseUrl", ""))
            status = params.get("responseStatusCode", 0)
            resp_headers = {h["name"].lower(): h["value"]
                            for h in params.get("responseHeaders", [])}

            # Capture rotating token from response headers
            xauth = resp_headers.get("x-auth-token", "")
            if xauth:
                captured["token"] = "Bearer " + xauth.strip()
                print(f"[BOOTSTRAP] Token from response header: {xauth[:40]}...")

            # Read response body
            if status == 200 and any(kw in url for kw in ["pageConfiguration", "configurationservices"]):
                try:
                    body_resp = driver.execute_cdp_cmd(
                        "Fetch.getResponseBody", {"requestId": request_id}
                    )
                    body_text = body_resp.get("body", "")
                    if body_resp.get("base64Encoded"):
                        import base64 as b64lib
                        body_text = b64lib.b64decode(body_text).decode("utf-8", errors="ignore")

                    if body_text and len(body_text) > 50:
                        try:
                            config_data = json.loads(body_text)
                            tc = find_transaction_code(config_data)
                            if tc:
                                captured["transaction_code"] = tc
                                print(f"[BOOTSTRAP] transactionCode captured: {tc}")
                            dbg = raw_dir / f"volusia_page_config_live_{date.today().isoformat()}.json"
                            dbg.write_text(json.dumps(config_data, indent=2), encoding="utf-8")
                        except json.JSONDecodeError:
                            pass
                except Exception as e:
                    print(f"[BOOTSTRAP] Could not read body for {url[:60]}: {e}")

            if "executeCustomTransaction" in url and status == 200:
                try:
                    body_resp = driver.execute_cdp_cmd(
                        "Fetch.getResponseBody", {"requestId": request_id}
                    )
                    body_text = body_resp.get("body", "")
                    if body_resp.get("base64Encoded"):
                        import base64 as b64lib
                        body_text = b64lib.b64decode(body_text).decode("utf-8", errors="ignore")
                    if body_text:
                        print(f"[BOOTSTRAP] executeCustomTransaction response: {len(body_text)} bytes")
                except Exception:
                    pass

            # MUST continue the request or page hangs
            try:
                driver.execute_cdp_cmd("Fetch.continueRequest", {"requestId": request_id})
            except Exception:
                pass

        # Attach CDP listener via execute_cdp_cmd polling workaround
        # (Selenium doesn't natively support CDP events — we poll logs)
        print("[BOOTSTRAP] Loading landing page...")
        driver.get(BASE + "/citizenportal/app/landing")
        time.sleep(5)
        print(f"[BOOTSTRAP] Landing: {driver.title!r}")

        # Also capture token from performance logs at landing
        for msg in get_performance_messages(driver):
            method = msg.get("method")
            params = msg.get("params", {})
            if method == "Network.responseReceived":
                resp_headers = params.get("response", {}).get("headers", {})
                xauth = resp_headers.get("x-auth-token") or resp_headers.get("X-Auth-Token", "")
                if xauth:
                    captured["token"] = "Bearer " + xauth.strip()
            elif method == "Network.requestWillBeSent":
                headers = params.get("request", {}).get("headers", {})
                auth = headers.get("Authorization") or headers.get("authorization", "")
                if auth and "bearer" in auth.lower():
                    captured["token"] = auth.strip()

        # Now navigate — the Fetch interceptor will catch pageConfiguration
        # The trick: use JavaScript to click the search nav link rather than direct URL navigation
        # so Angular's router fires, which triggers pageConfiguration
        print("[BOOTSTRAP] Triggering search-advanced via JS router...")
        driver.execute_script("""
            var links = document.querySelectorAll('a[href*="search"], a[href*="Search"]');
            for (var l of links) {
                if (l.offsetParent !== null) { l.click(); break; }
            }
        """)
        time.sleep(3)

        # Also try navigating via Angular router directly
        driver.execute_script("""
            try {
                var injector = document.querySelector('[ng-version]') ||
                               document.querySelector('app-root');
                if (window.ng && window.ng.getComponent) {
                    // Angular 9+ way
                }
            } catch(e) {}
        """)

        # Fallback: navigate directly and wait longer
        driver.get(BASE + "/citizenportal/app/public-search")
        time.sleep(8)
        print(f"[BOOTSTRAP] After public-search nav: {driver.current_url!r}")

        # Read Fetch interception from CDP logs (polling approach)
        # Selenium CDP events require polling the log
        perf_logs = driver.get_log("performance")
        for entry in perf_logs:
            try:
                msg = json.loads(entry["message"])["message"]
                method = msg.get("method", "")
                params = msg.get("params", {})

                if method == "Fetch.requestPaused":
                    intercept_response(params)

                elif method == "Network.responseReceived":
                    resp = params.get("response", {})
                    resp_headers = resp.get("headers", {})
                    xauth = resp_headers.get("x-auth-token") or resp_headers.get("X-Auth-Token", "")
                    if xauth and not captured["token"]:
                        captured["token"] = "Bearer " + xauth.strip()
                    url = resp.get("url", "")
                    if "pageConfiguration" in url and not captured["transaction_code"]:
                        rid = params.get("requestId")
                        try:
                            body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": rid})
                            bt = body.get("body", "")
                            if body.get("base64Encoded"):
                                import base64 as b64lib
                                bt = b64lib.b64decode(bt).decode("utf-8", errors="ignore")
                            if bt:
                                cd = json.loads(bt)
                                tc = find_transaction_code(cd)
                                if tc:
                                    captured["transaction_code"] = tc
                                    print(f"[BOOTSTRAP] transactionCode from perf log: {tc}")
                        except Exception:
                            pass

                elif method == "Network.requestWillBeSent":
                    headers = params.get("request", {}).get("headers", {})
                    auth = headers.get("Authorization") or headers.get("authorization", "")
                    if auth and "bearer" in auth.lower():
                        captured["token"] = auth.strip()

                    url = params.get("request", {}).get("url", "")
                    if "executeCustomTransaction" in url:
                        post_data = params.get("request", {}).get("postData", "")
                        if post_data:
                            try:
                                pl = json.loads(post_data)
                                tc = pl.get("transactionCode")
                                if tc:
                                    captured["transaction_code"] = tc
                                    print(f"[BOOTSTRAP] transactionCode from request: {tc}")
                            except Exception:
                                pass
            except Exception:
                continue

        token = captured["token"]
        transaction_code = captured["transaction_code"]

        if not token:
            debug_html = raw_dir / f"volusia_debug_{date.today().isoformat()}.html"
            debug_html.write_text(driver.page_source, encoding="utf-8", errors="ignore")
            raise RuntimeError(f"No auth token captured. Debug: {debug_html}")

        print(f"[BOOTSTRAP] Token: {token[:50]}...")
        print(f"[BOOTSTRAP] Transaction code: {transaction_code or 'NOT FOUND — will try pageConfig API'}")
        return token, transaction_code

    finally:
        try:
            driver.execute_cdp_cmd("Fetch.disable", {})
        except Exception:
            pass
        time.sleep(2)
        driver.quit()


def build_requests_session(auth_header: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "authorization": auth_header,
            "content-type": "application/json",
            "origin": BASE,
            "referer": BASE + "/citizenportal/app/search-advanced",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/147.0.0.0 Safari/537.36"
            ),
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
    )
    return session


def rotate_token_from_response(session: requests.Session, response: requests.Response) -> None:
    token = response.headers.get("x-auth-token") or response.headers.get("X-Auth-Token")
    if token:
        session.headers.update({"authorization": "Bearer " + token})


def get_page_config(session: requests.Session, page: str, raw_dir: Path) -> dict:
    print(f"[PAGE CONFIG] {page}")
    r = session.post(PAGE_CONFIG_URL, json={"currentPage": page}, timeout=60)
    print(f"              status={r.status_code} bytes={len(r.text)}")
    rotate_token_from_response(session, r)

    if not r.ok:
        print(f"              {r.text[:200]}")
        if r.status_code == 401:
            print(f"[PAGE CONFIG] 401 on {page} — token may need page context refresh")
            return {}
        r.raise_for_status()

    data = r.json()

    debug_path = raw_dir / f"volusia_page_config_{page}_{date.today().isoformat()}.json"
    debug_path.write_text(json.dumps(data, indent=2), encoding="utf-8", errors="ignore")

    return data


def find_transaction_code(config: Any) -> str | None:
    """
    Find dataLoad.parameters.transactionCode recursively.
    """
    if isinstance(config, dict):
        data_load = config.get("dataLoad")
        if isinstance(data_load, dict):
            params = data_load.get("parameters")
            if isinstance(params, dict):
                code = params.get("transactionCode")
                if code:
                    return str(code)

        for v in config.values():
            found = find_transaction_code(v)
            if found:
                return found

    elif isinstance(config, list):
        for item in config:
            found = find_transaction_code(item)
            if found:
                return found

    return None


def extract_rows_from_columnar_response(data: Any) -> list[dict]:
    if not isinstance(data, list):
        return []

    columns = []
    max_len = 0

    for item in data:
        if not isinstance(item, dict):
            continue

        name = item.get("columnName") or item.get("name") or item.get("label")
        values = item.get("columnValues") or item.get("values")

        if not name or not isinstance(values, list):
            continue

        columns.append((str(name), values))
        max_len = max(max_len, len(values))

    if not columns or max_len == 0:
        return []

    rows = []
    for i in range(max_len):
        row = {}
        for name, values in columns:
            row[name] = values[i] if i < len(values) else ""
        rows.append(row)

    return rows


def norm_value(row: dict, keys: list[str]) -> str:
    lower_map = {str(k).lower().strip(): k for k in row.keys()}

    for key in keys:
        lk = key.lower().strip()
        if lk in lower_map:
            val = row.get(lower_map[lk], "")
            return "" if val is None else str(val).strip()

    for key in keys:
        lk = key.lower().strip()
        for actual in row.keys():
            ak = str(actual).lower().strip()
            if lk in ak or ak in lk:
                val = row.get(actual, "")
                return "" if val is None else str(val).strip()

    return ""


def classify_trade(text: str) -> str:
    t = text.lower()

    if any(x in t for x in ["roof", "reroof", "re-roof", "shingle", "tile roof", "metal roof"]):
        return "roofing"
    if any(x in t for x in ["pool", "spa", "swimming"]):
        return "pool"
    if any(x in t for x in ["solar", "photovoltaic", "pv system"]):
        return "solar"
    if any(x in t for x in ["electrical", "electric", "meter", "panel", "service change"]):
        return "electrical"
    if any(x in t for x in ["plumbing", "sewer", "water heater", "gas line"]):
        return "plumbing"
    if any(x in t for x in ["mechanical", "hvac", "air conditioning", "a/c", "ac change", "duct"]):
        return "hvac"

    return "general_contractor"


def normalize_rows(rows: list[dict]) -> pd.DataFrame:
    out = []

    for row in rows:
        permitno = norm_value(row, ["File Number", "Permit Number", "Permit No", "Folder Number", "Folder"])
        folder_rsn = norm_value(row, ["FolderRSN", "Folder RSN"])
        record_type = norm_value(row, ["Folder Type", "Type", "Permit Type", "Sub Type"])
        desc = norm_value(row, ["Description", "Work Description", "Folder Description", "Sub Type", "Work Type"])
        address = norm_value(row, ["Address", "Property Address", "Site Address", "Location"])
        owner = norm_value(row, ["Owner", "Owner Name", "Property Owner"])
        contractor = norm_value(row, ["Contractor", "Contractor Name", "Applicant", "Licensee"])
        value = norm_value(row, ["Valuation", "Value", "Construction Value", "Project Value"])
        issued = norm_value(row, ["Date", "Issue Date", "Issued Date", "In Date", "Application Date"])
        status = norm_value(row, ["Status", "Folder Status", "Permit Status"])

        combined = " ".join([permitno, record_type, desc, status])

        out.append(
            {
                "PERMITNO": permitno or folder_rsn,
                "RECORD_TYPE": record_type,
                "PERMIT_DESCRIPTION": desc,
                "FULL_ADDRESS": address,
                "OWNER_NAME": owner,
                "CONTRACTOR_NAME": contractor,
                "FINAL_VALUATION": value,
                "LAST_ISSUED_DATE": issued,
                "STATUS": status,
                "TRADE": classify_trade(combined),
                "SOURCE": "volusia_connectlive_hybrid_api",
            }
        )

    df = pd.DataFrame(out)

    for col in RAW_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df = df[RAW_COLUMNS].fillna("").astype(str)

    df = df[
        df["PERMITNO"].str.strip().ne("")
        | df["FULL_ADDRESS"].str.strip().ne("")
        | df["PERMIT_DESCRIPTION"].str.strip().ne("")
    ].copy()

    df = df.drop_duplicates(
        subset=["PERMITNO", "FULL_ADDRESS", "LAST_ISSUED_DATE", "PERMIT_DESCRIPTION"]
    )

    return df


def fetch_permits(session: requests.Session, transaction_code: str, start: date, end: date) -> tuple[pd.DataFrame, Any]:
    payload = {
        "transactionCode": transaction_code,
        "transactionParameters": [
            {"fieldName": "folderType", "fieldValue": FOLDER_TYPE},
            {"fieldName": "district", "fieldValue": DISTRICT},
            {"fieldName": "inDateFrom", "fieldValue": start.strftime("%Y-%m-%d")},
            {"fieldName": "inDateTo", "fieldValue": end.strftime("%Y-%m-%d")},
        ],
    }

    print(f"[POST RESULTS] {API_URL}")
    print(f"               Date range: {start} to {end}")
    print(f"               transactionCode: {transaction_code}")

    r = session.post(API_URL, json=payload, timeout=90)
    print(f"               status={r.status_code} bytes={len(r.text)}")

    rotate_token_from_response(session, r)

    if not r.ok:
        print("[ERROR BODY]")
        print(r.text[:3000])
        r.raise_for_status()

    data = r.json()
    rows = extract_rows_from_columnar_response(data)
    print(f"[PARSE] rows from columnar response: {len(rows)}")

    df = normalize_rows(rows)
    return df, data



# LeadFlow constants
COUNTY_NAME = "Volusia"
SOURCE_NAME = "volusia_connectlive"
HASH_PREFIX = "volusia"


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
                if not permit_num:
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

    parser = argparse.ArgumentParser(description="Volusia County permit scraper")
    parser.add_argument("--days-back", type=int, default=7)
    parser.add_argument("--no-db",     action="store_true")
    parser.add_argument("--visible",   action="store_true")
    args = parser.parse_args()

    print(f"\n[Volusia] Scraping last {args.days_back} days")

    # Run the Permit_Bot scraping logic
    # Import the original main logic inline
    today      = datetime.today()
    start_date = today - timedelta(days=args.days_back)
    start_str  = start_date.strftime("%m/%d/%Y")
    end_str    = today.strftime("%m/%d/%Y")

    BASE_DIR = Path(__file__).resolve().parents[2]
    RAW_DIR  = BASE_DIR / "data" / "raw" / "volusia" / "permits"
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # Call the scraping function from Permit_Bot
    # Each county has a different entry point — detected below
    all_rows = []
    try:
        # Volusia ConnectLive: capture Bearer token via Selenium, then use API
        from datetime import date as date_cls, timedelta as td
        start_d = date_cls.today() - td(days=args.days_back)
        end_d   = date_cls.today()

        auth_header, captured_code = bootstrap_auth_token(RAW_DIR)
        session_v = build_requests_session(auth_header)

        transaction_code = captured_code
        if not transaction_code:
            search_config = get_page_config(session_v, "search-advanced", RAW_DIR)
            transaction_code = find_transaction_code(search_config)

        if not transaction_code:
            KNOWN_CODES = ["cOQ4Se3do2KFYz65w2kF3hfEc8Tneqc="]
            for code in KNOWN_CODES:
                try:
                    df_test, _ = fetch_permits(session_v, code, start_d, end_d)
                    if not df_test.empty:
                        transaction_code = code
                        break
                except Exception:
                    continue

        if not transaction_code:
            raise RuntimeError("Could not find Volusia transactionCode")

        df, _ = fetch_permits(session_v, transaction_code, start_d, end_d)
        all_rows = df.to_dict("records") if not df.empty else []
        print(f"  Scraped {len(all_rows)} permits")
    except Exception as e:
        import traceback
        print(f"  Scraping error: {e}")
        traceback.print_exc()

    if not all_rows:
        print("  No data scraped.")
        return

    # Save snapshot
    snap = RAW_DIR / f"volusia_permits_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
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

    print(f"\n--- Volusia summary ---")
    print(f"  Records scraped    : {len(all_rows)}")
    print(f"  raw inserted       : {stats['inserted']}")
    print(f"  normalized inserted: {stats['inserted']}")
    print(f"  skipped            : {stats['skipped']}")


if __name__ == "__main__":
    main()