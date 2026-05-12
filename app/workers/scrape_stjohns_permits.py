"""
scrape_stjohns_permits.py
=========================
St. Johns County permits — ported from Permit_Bot download_stjohns_weekly.py.
"""
from __future__ import annotations
import argparse, json
from datetime import datetime, timedelta
from pathlib import Path

COUNTY_NAME = "St. Johns"
SOURCE_NAME = "stjohns_wats"
HASH_PREFIX = "stjohns"
BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR  = BASE_DIR / "data" / "raw" / "stjohns" / "permits"
RAW_DIR.mkdir(parents=True, exist_ok=True)



import shutil
from datetime import datetime, timedelta
from pathlib import Path
from io import StringIO

import pandas as pd
import requests
from bs4 import BeautifulSoup

STATE     = "florida"
COUNTY    = "st_johns"
BASE_URL  = "https://webapp.sjcfl.us"
QUERY_URL  = f"{BASE_URL}/watswebx/WATSReport/MasterQuery.aspx"
SEARCH_URL = f"{BASE_URL}/watswebx/permit/SearchPermit.aspx"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; Trident/7.0; rv:11.0) like Gecko"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# MasterQuery checkbox field names → permit type codes
TARGET_CHECKBOXES = {
    "ctl00$cphBody$ckblPU$0":  "101",   # 1 SINGLE FAMILY(DETACHED)
    "ctl00$cphBody$ckblPU$1":  "101M",  # SINGLE FAMILY (MODULAR)
    "ctl00$cphBody$ckblPU$2":  "102",   # 1 SINGLE FAMILY (ATTACHED)
    "ctl00$cphBody$ckblPU$3":  "103",   # 2 FAMILIES
    "ctl00$cphBody$ckblPU$4":  "104",   # 3 & 4 FAMILIES
    "ctl00$cphBody$ckblPU$5":  "105",   # 5 OR MORE FAMILIES
    "ctl00$cphBody$ckblPU$21": "327",   # STORES/CUSTOMER SERVICES
    "ctl00$cphBody$ckblPU$22": "327S",  # STORES, RESTAURANTS, MALL, SHELL
    "ctl00$cphBody$ckblPU$23": "328",   # RESIDENTIAL ACCESSORY STRUCTURE
    "ctl00$cphBody$ckblPU$25": "328D",  # RESIDENTIAL ACCESSORY DWELLINGS
    "ctl00$cphBody$ckblPU$26": "329",   # RESIDENTIAL SWIMMING POOL
    "ctl00$cphBody$ckblPU$27": "329A",  # ABOVE GROUND POOL
    "ctl00$cphBody$ckblPU$28": "329C",  # COMMERCIAL PUBLIC POOL
    "ctl00$cphBody$ckblPU$29": "329E",  # POOL ENCLOSURE
    "ctl00$cphBody$ckblPU$31": "329R",  # SWIMMING POOL REPAIR
    "ctl00$cphBody$ckblPU$32": "329S",  # SPA
    "ctl00$cphBody$ckblPU$34": "335",   # RESIDENTIAL PORCH/SCREEN ROOM/LANAI
    "ctl00$cphBody$ckblPU$35": "434",   # RESIDENTIAL/ADDITION
    "ctl00$cphBody$ckblPU$36": "434E",  # RESIDENTIAL EXTERIOR REPAIRS
    "ctl00$cphBody$ckblPU$37": "434R",  # RESIDENTIAL RENOVATION/REPAIRS
    "ctl00$cphBody$ckblPU$38": "435",   # RESIDENTIAL ROOF
    "ctl00$cphBody$ckblPU$39": "435C",  # COMMERCIAL ROOF
    "ctl00$cphBody$ckblPU$40": "437",   # COMMERCIAL ADDITION
    "ctl00$cphBody$ckblPU$41": "437B",  # COMMERCIAL BUILD-OUT
    "ctl00$cphBody$ckblPU$42": "437R",  # COMMERCIAL RENOVATION
    "ctl00$cphBody$ckblPU$43": "438",   # RESIDENTIAL GARAGES/CARPORT
    "ctl00$cphBody$ckblPU$52": "601",   # SINGLE FAMILY W/ATTACHED COMMERCIAL
    "ctl00$cphBody$ckblPU$53": "602",   # TOWNHOUSE W/ ATTACHED COMMERCIAL
    "ctl00$cphBody$ckblPU$54": "603",   # DUPLEX W/ ATTACHED COMMERCIAL
    "ctl00$cphBody$ckblPU$55": "604",   # 3 & 4 FAMILIES W/ ATTACHED COMMERCIAL
    "ctl00$cphBody$ckblPU$56": "605",   # 5 OR MORE FAMILIES W/ATTACHED COMM
    "ctl00$cphBody$ckblPU$57": "645",   # DEMOLITION/RESIDENTIAL
    "ctl00$cphBody$ckblPU$58": "649",   # DEMOLITION/ALL OTHER BLDGS
    "ctl00$cphBody$ckblPU$62": "670",   # SOLAR
    "ctl00$cphBody$ckblPU$63": "670C",  # COMMERCIAL SOLAR
    "ctl00$cphBody$ckblPU$64": "700",   # MOBILE HOME
    "ctl00$cphBody$ckblPU$65": "800",   # Storm Damage (Property Owner)
    "ctl00$cphBody$ckblPU$66": "801",   # Storm Damage (Field Inspection)
}

# Trade permit types from SearchPermit.aspx Permit Type dropdown
# These cover electrical, plumbing, mechanical — not in MasterQuery
TRADE_PERMIT_TYPES = [
    "Electrical Permit",
    "Plumbing Permit",
    "Mechanical Permit",
]


def ensure_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def archive_existing(raw_folder: Path, archive_folder: Path) -> None:
    ensure_folder(archive_folder)
    for f in raw_folder.glob("stjohns_permits_*"):
        target = archive_folder / f.name
        if not target.exists():
            shutil.copy2(f, target)
            print(f"Archived: {f.name}")


def update_counties_csv(base_folder: Path, new_filename: str) -> None:
    counties_path = base_folder / "config" / "counties.csv"
    df = pd.read_csv(counties_path, dtype=str, keep_default_na=False, comment="#")
    df.columns = [c.strip() for c in df.columns]
    mask = (
        (df["state"].str.lower()  == STATE) &
        (df["county"].str.lower() == COUNTY)
    )
    if mask.any():
        df.loc[mask, "input_filename"] = f"data/raw/{STATE}/{COUNTY}/{new_filename}"
        df.loc[mask, "is_active"]      = "true"
        df.to_csv(counties_path, index=False)
        print(f"Updated counties.csv → {new_filename}")
    else:
        print(f"Warning: no {STATE}/{COUNTY} row in counties.csv.")


def parse_html_excel(content: bytes) -> pd.DataFrame:
    """
    Parse HTML content (either an HTML-disguised-Excel or a search results page)
    into a DataFrame. Finds the largest meaningful table in the page.
    """
    try:
        text = content.decode("utf-8", errors="replace")
        tables = pd.read_html(StringIO(text), header=0)
        if not tables:
            return pd.DataFrame()

        # Pick the largest table (most rows) that looks like permit data
        # Permit data tables have many columns and many rows
        best = None
        best_score = 0
        for t in tables:
            # Score = rows × columns, but penalize tiny tables
            if len(t) < 2 or len(t.columns) < 3:
                continue
            score = len(t) * len(t.columns)
            if score > best_score:
                best_score = score
                best = t

        if best is None:
            return pd.DataFrame()

        df = best.fillna("").astype(str)
        # If columns are all numeric, promote first row to header
        if all(str(c).replace(".", "").isdigit() for c in df.columns):
            df.columns = df.iloc[0]
            df = df.iloc[1:].reset_index(drop=True)

        # Drop obvious navigation/footer rows (very short text in all columns)
        if len(df) > 0:
            df = df[df.apply(lambda r: any(len(str(v)) > 3 for v in r), axis=1)]

        return df.reset_index(drop=True)
    except Exception as e:
        print(f"  HTML parse error: {e}")
        return pd.DataFrame()


def get_base_post_data(session: requests.Session, url: str) -> dict:
    """GET a page and extract all default ASP.NET form fields."""
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    post_data: dict = {}
    for hidden_id in ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
                      "__EVENTTARGET", "__EVENTARGUMENT", "__LASTFOCUS"]:
        el = soup.find("input", {"id": hidden_id})
        if el:
            post_data[el.get("name", hidden_id)] = el.get("value", "")
        elif hidden_id in ("__EVENTTARGET", "__EVENTARGUMENT", "__LASTFOCUS"):
            post_data[hidden_id] = ""

    for inp in soup.find_all("input"):
        name = inp.get("name")
        typ  = inp.get("type", "text").lower()
        val  = inp.get("value", "")
        if not name or name in post_data:
            continue
        if typ == "hidden":
            post_data[name] = val
        elif typ == "text":
            post_data[name] = val
        elif typ == "radio" and inp.get("checked"):
            post_data[name] = val

    return post_data


def download_master_query(session: requests.Session, from_str: str, to_str: str) -> pd.DataFrame:
    """Download building permits from MasterQuery — GC, roofing, pool, solar."""
    print("  Downloading MasterQuery (building permits)...")
    post_data = get_base_post_data(session, QUERY_URL)
    print(f"    Base fields: {len(post_data)}")

    post_data["ctl00$cphBody$Type"]              = "rdoWeek"
    post_data["ctl00$cphBody$RadioButtonListType"] = "D"
    for date_field in ["ctl00$cphBody$RadioButtonListDateType",
                       "ctl00$cphBody$rdoIssueDate"]:
        post_data[date_field] = "I"
    for field, code in TARGET_CHECKBOXES.items():
        post_data[field] = code
    post_data["ctl00$cphBody$btnXLS"] = "MS Excel"
    for btn in ["ctl00$cphBody$btnTXT", "ctl00$cphBody$btnXML",
                "ctl00$cphBody$btnSubdiv"]:
        post_data.pop(btn, None)

    resp = session.post(QUERY_URL, data=post_data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        timeout=60)
    ct = resp.headers.get("Content-Type", "").lower()
    print(f"    Response: {resp.status_code} | {len(resp.content):,} bytes | {ct[:40]}")

    if "text/html" in ct and "attachment" not in resp.headers.get("Content-Disposition","").lower():
        return pd.DataFrame()

    df = parse_html_excel(resp.content)
    if not df.empty:
        df["PERMIT_TYPE_SOURCE"] = "Building Permit"
    print(f"    Parsed {len(df)} building permit rows")
    return df


# Confirmed SearchPermit field names from live form dump 2026-04-09:
#   Permit type dropdown: ctl00$cphBody$ddPermitType
#     values: 1=Building, 2=Electrical, 3=Plumbing, 4=Mechanical
#   Issue Date After:  ctl00$cphBody$TextBoxFromDt
#   Issue Date Before: ctl00$cphBody$TextBoxToDt
#   Search button:     ctl00$cphBody$btnSearch = "Search"
PERMIT_TYPE_VALUES = {
    "Electrical Permit": "2",
    "Plumbing Permit":   "3",
    "Mechanical Permit": "4",
}


def download_trade_permits(session: requests.Session,
                           permit_type: str,
                           from_str: str, to_str: str) -> pd.DataFrame:
    """
    Scrape electrical / plumbing / mechanical permits from SearchPermit.aspx.
    Field names confirmed from live form dump. Filters by Issue Date range.
    """
    print(f"  Downloading {permit_type}...")

    post_data = get_base_post_data(session, SEARCH_URL)

    # Set permit type, date range, submit button (confirmed field names)
    post_data["ctl00$cphBody$ddPermitType"]  = PERMIT_TYPE_VALUES[permit_type]
    post_data["ctl00$cphBody$TextBoxFromDt"] = from_str
    post_data["ctl00$cphBody$TextBoxToDt"]   = to_str
    post_data["ctl00$cphBody$btnSearch"]     = "Search"
    # Remove other submit buttons that might conflict
    for btn in ["ctl00$cphBody$btnClear", "ctl00$cphBody$btnLogin",
                "ctl00$ContentPlaceHolder1$Button1"]:
        post_data.pop(btn, None)

    # POST and scrape paginated results
    all_rows = []
    page = 1
    current_url = SEARCH_URL

    while True:
        resp = session.post(
            current_url, data=post_data,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Referer": SEARCH_URL},
            timeout=60,
        )
        print(f"    Page {page}: {resp.status_code} | {len(resp.content):,} bytes")

        soup = BeautifulSoup(resp.text, "lxml")

        # Find results table — look for table with permit data columns
        results_table = None
        for tbl in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
            tds = [td.get_text(strip=True).lower() for td in tbl.find_all("td")[:5]]
            all_text = headers + tds
            if any(kw in " ".join(all_text) for kw in
                   ["permit", "address", "status", "issued", "contractor"]):
                results_table = tbl
                break

        if results_table is None:
            print(f"    No results table found on page {page}")
            break

        col_names = [th.get_text(strip=True) for th in results_table.find_all("th")]
        tbody = results_table.find("tbody") or results_table
        page_rows = []
        for tr in tbody.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) < 2:
                continue
            values = [td.get_text(" ", strip=True) for td in cells]
            if any(v.strip() for v in values):
                page_rows.append(dict(zip(col_names, values)))

        print(f"      Found {len(page_rows)} rows")
        all_rows.extend(page_rows)

        if not page_rows or page >= 20:
            break

        # Look for Next page link
        next_link = None
        for a in soup.find_all("a"):
            txt = a.get_text(strip=True).lower()
            href = a.get("href","")
            if txt in ("next", ">", "next page") or "next" in txt:
                next_link = a
                break
            # ASP.NET postback next page
            if "__dopostback" in href.lower() and "next" in href.lower():
                next_link = a
                break

        if not next_link:
            break

        # Handle ASP.NET postback pagination
        onclick = next_link.get("href","")
        if "__doPostBack" in onclick:
            import re
            m = re.search(r"__doPostBack\('([^']+)','([^']*)'\)", onclick)
            if m:
                post_data["__EVENTTARGET"]   = m.group(1)
                post_data["__EVENTARGUMENT"] = m.group(2)
                # Refresh ViewState from current page
                vs = soup.find("input", {"id":"__VIEWSTATE"})
                ev = soup.find("input", {"id":"__EVENTVALIDATION"})
                if vs: post_data[vs["name"]] = vs["value"]
                if ev: post_data[ev["name"]] = ev["value"]
                page += 1
                continue
        break

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["PERMIT_TYPE_SOURCE"] = permit_type
    print(f"    Total: {len(df)} {permit_type} rows")
    return df



def get_county_id(cur):
    cur.execute("SELECT id FROM counties WHERE county_name = %s", (COUNTY_NAME,))
    r = cur.fetchone()
    if r: return r[0]
    cur.execute("INSERT INTO counties (county_name,state,active,created_at) VALUES(%s,'FL',true,NOW()) RETURNING id",(COUNTY_NAME,))
    return cur.fetchone()[0]

def import_df(df) -> dict:
    """Import a pandas DataFrame directly into normalized_permits."""
    if df is None or df.empty:
        return {"inserted":0,"updated":0,"skipped":0}
    try:
        from app.core.db import get_connection
    except ImportError:
        return {"inserted":0,"updated":0,"skipped":0}

    # Print actual columns for debugging
    print(f"  DB import: {len(df)} rows, columns: {list(df.columns)[:8]}")

    # Find the permit number column
    perm_col = next((c for c in df.columns if "permit" in c.lower() and "no" in c.lower()), None)
    if not perm_col:
        perm_col = next((c for c in df.columns if "permit" in c.lower()), None)
    date_col = next((c for c in df.columns if "issue" in c.lower()), None)
    addr_col = next((c for c in df.columns if "address" in c.lower() or "site" in c.lower()), None)
    cont_col = next((c for c in df.columns if any(w in c.lower() for w in ["contractor","applicant","company"])), None)
    type_col = next((c for c in df.columns if "type" in c.lower()), None)

    print(f"  Columns mapped: permit={perm_col} date={date_col} addr={addr_col} contractor={cont_col}")

    if not perm_col:
        print("  ERROR: no permit number column found")
        return {"inserted":0,"updated":0,"skipped":len(df)}

    conn  = get_connection()
    conn.autocommit = False
    stats = {"inserted":0,"updated":0,"skipped":0}
    try:
        with conn.cursor() as cur:
            county_id = get_county_id(cur)
            for _, row in df.iterrows():
                permit_num = str(row.get(perm_col,"") or "").strip()
                if not permit_num or permit_num in ("nan","None","") or len(permit_num) < 3:
                    stats["skipped"] += 1
                    continue
                # Skip navigation/menu text captured as rows
                if len(permit_num) > 30 or " " in permit_num:
                    stats["skipped"] += 1
                    continue
                issued_raw = str(row.get(date_col,"") or "").split(" ")[0] if date_col else ""
                issued = None
                for fmt in ("%m/%d/%Y","%Y-%m-%d"):
                    try: issued = datetime.strptime(issued_raw, fmt).date(); break
                    except Exception: pass
                source_id = f"{SOURCE_NAME}::{permit_num}"
                payload   = json.dumps(row.to_dict(), default=str)
                raw_id = None
                try:
                    cur.execute("""
                        INSERT INTO raw_permits(county_id,source_file,source_record_id,raw_payload,issued_date)
                        VALUES(%s,%s,%s,%s::jsonb,%s)
                        ON CONFLICT(county_id,source_record_id) DO UPDATE SET raw_payload=EXCLUDED.raw_payload
                        RETURNING id
                    """,(county_id,SOURCE_NAME,source_id,payload,issued))
                    r = cur.fetchone()
                    if r: raw_id = r[0]
                except Exception: conn.rollback(); stats["skipped"]+=1; continue
                n_hash = f"{HASH_PREFIX}::{permit_num}"
                try:
                    cur.execute("""
                        INSERT INTO normalized_permits(
                            county_id,raw_permit_id,permit_number,permit_type,
                            owner_name,business_name,address_1,project_description,
                            issued_date,normalized_hash)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(normalized_hash) DO UPDATE SET
                            owner_name=COALESCE(EXCLUDED.owner_name,normalized_permits.owner_name),
                            business_name=COALESCE(EXCLUDED.business_name,normalized_permits.business_name),
                            updated_at=NOW()
                        RETURNING id,(xmax=0) AS is_insert
                    """,(county_id,raw_id,permit_num,
                        str(row.get(type_col,"") or "") or None,
                        None, # owner
                        str(row.get(cont_col,"") or "") or None if cont_col else None,
                        str(row.get(addr_col,"") or "") or None if addr_col else None,
                        None, issued, n_hash))
                    res = cur.fetchone()
                    if res: stats["inserted" if res[1] else "updated"] += 1
                except Exception as e:
                    conn.rollback(); print(f"  Insert error {permit_num}: {e}"); stats["skipped"]+=1; continue
        conn.commit()
    except Exception: conn.rollback(); raise
    finally: conn.close()
    return stats

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-back", type=int, default=7)
    parser.add_argument("--no-db", action="store_true")
    args = parser.parse_args()

    today      = datetime.today()
    start_date = today - timedelta(days=args.days_back)
    from_str   = start_date.strftime("%m/%d/%Y")
    to_str     = today.strftime("%m/%d/%Y")
    print(f"\n[St. Johns] Scraping last {args.days_back} days")

    import requests as rq
    session = rq.Session()
    session.headers.update(HEADERS)

    df1 = download_master_query(session, from_str, to_str)
    trade_dfs = []
    for permit_type in PERMIT_TYPE_VALUES:
        try:
            df_t = download_trade_permits(session, permit_type, from_str, to_str)
            if not df_t.empty:
                trade_dfs.append(df_t)
                print(f"    {permit_type}: {len(df_t)} permits")
        except Exception as e:
            print(f"    {permit_type} error: {e}")

    import pandas as pd
    df2 = pd.concat(trade_dfs, ignore_index=True) if trade_dfs else pd.DataFrame()
    print(f"  Scraped {len(df1)+len(df2)} permits ({len(df1)} building + {len(df2)} trade)")

    # Save snapshot
    combined = pd.concat([df1, df2], ignore_index=True) if not df2.empty else df1
    snap = RAW_DIR / f"stjohns_permits_{today.strftime('%Y%m%d_%H%M%S')}.json"
    snap.write_text(combined.head(5).to_json(orient="records"), encoding="utf-8")
    print(f"  Sample saved: {snap.name}")

    stats = {"inserted":0,"updated":0,"skipped":0}
    if not args.no_db:
        # Import df1 and df2 separately — they have different column structures
        s1 = import_df(df1)
        s2 = import_df(df2) if not df2.empty else {"inserted":0,"updated":0,"skipped":0}
        stats = {k: s1[k]+s2[k] for k in stats}

    print(f"\n--- St. Johns summary ---")
    print(f"  Records scraped    : {len(df1)+len(df2)}")
    print(f"  raw inserted       : {stats['inserted']}")
    print(f"  normalized inserted: {stats['inserted']}")
    print(f"  skipped            : {stats['skipped']}")

if __name__ == "__main__":
    main()