# arizona_roc_scraper.py (v4 - Production)
# Arizona ROC - City-based Salesforce Aura API scraper
# Proven approach: search by city, capture r=8 Aura response (115KB+ per city)
#
# Usage:
#   python scripts/scrapers/arizona_roc_scraper.py --scrape --county maricopa
#   python scripts/scrapers/arizona_roc_scraper.py --scrape --all --import
#   python scripts/scrapers/arizona_roc_scraper.py --file data/arizona/roc.csv --import
#   python scripts/scrapers/arizona_roc_scraper.py --stats
#   python scripts/scrapers/arizona_roc_scraper.py --match

from __future__ import annotations
import argparse
import json
import sys
import time
import csv
import re
from datetime import date, datetime
from pathlib import Path
from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

DATA_DIR = LEADFLOW_DIR / "data" / "arizona"
DATA_DIR.mkdir(parents=True, exist_ok=True)

try:
    from app.core.db import get_connection
    HAS_DB = True
except ImportError:
    HAS_DB = False

try:
    from pipeline_log import PipelineLogger
    HAS_LOGGER = True
except ImportError:
    HAS_LOGGER = False

ROC_PORTAL = "https://azroc.my.site.com/AZRoc/s/contractor-search"

# Cities grouped by county for targeted scraping
COUNTY_CITIES = {
    "Maricopa": [
        "Phoenix", "Scottsdale", "Mesa", "Tempe", "Chandler",
        "Gilbert", "Glendale", "Peoria", "Surprise", "Avondale",
        "Goodyear", "Buckeye", "Anthem", "Sun City West", "Tolleson",
        "Queen Creek", "Laveen", "Litchfield Park", "El Mirage", "Cave Creek",
    ],
    "Pima": ["Tucson", "Marana", "Sahuarita", "Oro Valley", "South Tucson"],
    "Pinal": ["Casa Grande", "Apache Junction", "Maricopa", "Coolidge", "Florence"],
    "Yavapai": ["Prescott", "Cottonwood", "Sedona", "Prescott Valley", "Chino Valley"],
    "Mohave": ["Kingman", "Bullhead City", "Lake Havasu City", "Fort Mohave"],
    "Yuma": ["Yuma", "San Luis", "Somerton", "Wellton"],
    "Cochise": ["Sierra Vista", "Douglas", "Bisbee", "Willcox"],
    "Navajo": ["Show Low", "Winslow", "Holbrook", "Pinetop"],
}

# High-value license classes for IRS lien targeting
TARGET_CLASSES = {
    "A", "B", "B-1", "B-2", "CR-35", "CR-67",
    "C-11", "C-37", "C-39", "L-11", "L-37", "L-39",
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS arizona_roc_contacts (
    id                  SERIAL PRIMARY KEY,
    license_number      VARCHAR(50)  UNIQUE NOT NULL,
    license_class       VARCHAR(100),
    status              VARCHAR(50),
    business_name       VARCHAR(200),
    owner_name          VARCHAR(200),
    business_city       VARCHAR(100),
    business_state      VARCHAR(10)  DEFAULT 'AZ',
    business_zip        VARCHAR(20),
    county              VARCHAR(100),
    phone               VARCHAR(30),
    email               VARCHAR(200),
    lien_match          BOOLEAN      DEFAULT FALSE,
    emailed             BOOLEAN      DEFAULT FALSE,
    source              VARCHAR(50)  DEFAULT 'az_roc_selenium',
    created_at          TIMESTAMP    DEFAULT NOW(),
    updated_at          TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_az_county ON arizona_roc_contacts(county);
CREATE INDEX IF NOT EXISTS idx_az_class  ON arizona_roc_contacts(license_class);
CREATE INDEX IF NOT EXISTS idx_az_email  ON arizona_roc_contacts(email)
    WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_az_lien   ON arizona_roc_contacts(lien_match)
    WHERE lien_match = TRUE;
"""


def get_driver(headless=True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    driver = webdriver.Chrome(options=opts)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def search_city(driver, city: str):
    """Enter city name and click Search using proven Shadow DOM approach."""
    # Click Advanced Search
    driver.execute_script("""
        document.querySelectorAll('button').forEach(b => {
            if(b.innerText.includes('Advanced')) b.click();
        });
    """)
    time.sleep(3)

    # Fill city field via Shadow DOM
    driver.execute_script("""
        function findAndFill(root, name, value) {
            for(var inp of root.querySelectorAll('input')) {
                if(inp.name === name) {
                    inp.focus(); inp.value = value;
                    inp.dispatchEvent(new Event('input', {bubbles:true}));
                    inp.dispatchEvent(new Event('change', {bubbles:true}));
                    return true;
                }
            }
            for(var el of root.querySelectorAll('*')) {
                if(el.shadowRoot && findAndFill(el.shadowRoot, name, value)) return true;
            }
            return false;
        }
        findAndFill(document, 'City', arguments[0]);
    """, city)
    time.sleep(2)

    # Click Search
    driver.execute_script("""
        document.querySelectorAll('button').forEach(b => {
            if(b.innerText.trim() === 'Search') b.click();
        });
    """)
    time.sleep(12)  # Wait for Aura API response


def capture_aura_records(driver) -> list[dict]:
    """Capture Aura r=8 response containing contractor records."""
    logs = driver.get_log("performance")
    best_records = []
    best_size = 0

    for entry in logs:
        try:
            msg = json.loads(entry["message"])["message"]
            if msg.get("method") != "Network.responseReceived":
                continue
            url = msg.get("params", {}).get("response", {}).get("url", "")
            if "aura?r=" not in url:
                continue
            req_id = msg["params"]["requestId"]
            try:
                body = driver.execute_cdp_cmd(
                    "Network.getResponseBody", {"requestId": req_id}
                )
                text = body.get("body", "")
                if len(text) < 1000:
                    continue
                data = json.loads(text)
                records = parse_aura_data(data)
                if len(records) > len(best_records):
                    best_records = records
                    best_size = len(text)
            except Exception:
                pass
        except Exception:
            pass

    return best_records


def parse_aura_data(data: dict) -> list[dict]:
    """Parse Aura API response into contractor records."""
    records = []
    for action in data.get("actions", []):
        rv = action.get("returnValue", [])
        if not isinstance(rv, list):
            continue
        for item in rv:
            if not isinstance(item, dict) or "accountName" not in item:
                continue
            # Parse address: "PHOENIX, AZ, 85027"
            addr = item.get("address", "")
            parts = [p.strip() for p in addr.split(",")]
            city    = parts[0] if parts else ""
            zipcode = parts[2] if len(parts) > 2 else ""

            # Get owner name from contacts
            owner = ""
            for contact in item.get("accountContactData", []):
                name = contact.get("contactName", "")
                if "Qualifying Party" in name or "Member" in name:
                    owner = name.split("(")[0].strip()
                    break

            # One record per license
            for lic in item.get("licenseData", []):
                records.append({
                    "license_number": lic.get("licenseNo", "").replace("ROC ", "").strip(),
                    "license_class":  lic.get("subType", ""),
                    "status":         lic.get("status", "Active"),
                    "business_name":  item.get("accountName", ""),
                    "owner_name":     owner,
                    "business_city":  city,
                    "business_state": "AZ",
                    "business_zip":   zipcode,
                    "phone":          item.get("phone", ""),
                    "county":         "",  # Set by caller
                    "email":          "",
                })
    return records


def scrape_city(driver, city: str, county: str) -> list[dict]:
    """Scrape one city and return records tagged with county."""
    print(f"    {city}...", end=" ", flush=True)
    try:
        search_city(driver, city)
        records = capture_aura_records(driver)
        for r in records:
            r["county"] = county
        print(f"{len(records)} records")
        return records
    except Exception as e:
        print(f"ERROR: {e}")
        return []


def scrape_county(county: str, dry_run=False, headless=True) -> list[dict]:
    """Scrape all cities in a county."""
    cities = COUNTY_CITIES.get(county, [county])
    print(f"\n  {county} County ({len(cities)} cities):")

    if dry_run:
        print(f"  [DRY RUN] Would scrape: {cities}")
        return []

    driver = get_driver(headless=headless)
    all_records = []
    seen_licenses = set()

    try:
        driver.get(ROC_PORTAL)
        time.sleep(8)

        for city in cities:
            records = scrape_city(driver, city, county)
            # Deduplicate by license number
            for r in records:
                ln = r.get("license_number", "")
                if ln and ln not in seen_licenses:
                    seen_licenses.add(ln)
                    all_records.append(r)
            time.sleep(3)

    except Exception as e:
        print(f"  County error: {e}")
    finally:
        driver.quit()

    print(f"  {county} total: {len(all_records)} unique records")
    return all_records


def save_to_csv(records: list[dict], label: str = "all") -> Path:
    if not records:
        return None
    out = DATA_DIR / f"az_roc_{label.lower().replace(' ','-')}_{date.today().isoformat()}.csv"
    fields = [
        "license_number", "license_class", "status", "business_name",
        "owner_name", "business_city", "business_state", "business_zip",
        "county", "phone", "email",
    ]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(records)
    print(f"  CSV: {out} ({len(records)} records)")
    return out


def import_to_db(records: list[dict], dry_run=False) -> dict:
    if not HAS_DB or not records:
        return {"inserted": 0, "updated": 0}
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    ins = upd = 0
    for rec in records:
        if not rec.get("license_number"):
            continue
        if dry_run:
            ins += 1
            continue
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO arizona_roc_contacts (
                        license_number, license_class, status, business_name,
                        owner_name, business_city, business_state, business_zip,
                        county, phone, email, source
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (license_number) DO UPDATE SET
                        status=EXCLUDED.status,
                        business_name=EXCLUDED.business_name,
                        owner_name=EXCLUDED.owner_name,
                        business_city=EXCLUDED.business_city,
                        county=EXCLUDED.county,
                        phone=EXCLUDED.phone,
                        updated_at=NOW()
                    RETURNING (xmax=0) AS is_new
                """, (
                    rec["license_number"], rec.get("license_class"),
                    rec.get("status", "Active"), rec.get("business_name"),
                    rec.get("owner_name"), rec.get("business_city"),
                    rec.get("business_state", "AZ"), rec.get("business_zip"),
                    rec.get("county"), rec.get("phone"), rec.get("email"),
                    "az_roc_selenium",
                ))
                row = cur.fetchone()
                if row and row[0]:
                    ins += 1
                else:
                    upd += 1
            conn.commit()
        except Exception as e:
            conn.rollback()
    conn.close()
    return {"inserted": ins, "updated": upd}


def import_from_csv_file(path: str, dry_run=False) -> dict:
    records = []
    with open(path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rec = {
                "license_number": row.get("License Number", row.get("license_number", "")),
                "license_class":  row.get("License Class", row.get("license_class", "")),
                "status":         row.get("Status", "Active"),
                "business_name":  row.get("Business Name", row.get("business_name", "")),
                "owner_name":     row.get("Qualifier Name", row.get("owner_name", "")),
                "business_city":  row.get("City", row.get("business_city", "")),
                "business_state": row.get("State", "AZ"),
                "business_zip":   row.get("Zip", row.get("business_zip", "")),
                "county":         row.get("County", row.get("county", "")),
                "phone":          row.get("Phone", row.get("phone", "")),
                "email":          row.get("Email", row.get("email", "")),
            }
            if rec["license_number"]:
                records.append(rec)
    print(f"  Parsed {len(records):,} records from CSV")
    return import_to_db(records, dry_run=dry_run)


def show_stats():
    if not HAS_DB:
        print("  No DB connection")
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT county, COUNT(*) total, COUNT(email) with_email,
               COUNT(CASE WHEN lien_match THEN 1 END) matched
        FROM arizona_roc_contacts
        GROUP BY county ORDER BY total DESC
    """)
    rows = cur.fetchall()
    if not rows:
        print("  No AZ ROC records in DB yet")
        conn.close()
        return
    print(f"\n  {'County':20} {'Total':>8} {'Email':>8} {'Matched':>8}")
    print("  " + "-"*46)
    total = 0
    for row in rows:
        print(f"  {(row[0] or 'Unknown'):20} {row[1]:>8} {row[2]:>8} {row[3]:>8}")
        total += row[1]
    print(f"  {'TOTAL':20} {total:>8}")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Arizona ROC Scraper v4")
    parser.add_argument("--scrape",  action="store_true")
    parser.add_argument("--all",     action="store_true", help="All counties")
    parser.add_argument("--county",  default="maricopa")
    parser.add_argument("--import",  action="store_true", dest="do_import")
    parser.add_argument("--file",    default=None, help="CSV from PRR")
    parser.add_argument("--stats",   action="store_true")
    parser.add_argument("--match",   action="store_true")
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"\n{'='*55}")
    print(f"  Arizona ROC Scraper v4")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    if args.dry_run:
        print("  DRY RUN")
    print(f"{'='*55}\n")

    logger = None
    if HAS_LOGGER:
        try:
            logger = PipelineLogger("az_roc_scraper")
            logger.start()
        except Exception:
            pass

    if args.stats:
        show_stats()
        return

    if args.file:
        print(f"Importing from: {args.file}")
        result = import_from_csv_file(args.file, dry_run=args.dry_run)
        print(f"  Inserted: {result['inserted']:,} | Updated: {result['updated']:,}")
        if logger:
            logger.finish(result)
        return

    if args.scrape:
        if args.all:
            counties = list(COUNTY_CITIES.keys())
        else:
            counties = [args.county.title()]

        all_records = []
        for county in counties:
            records = scrape_county(county, dry_run=args.dry_run,
                                    headless=not args.visible)
            all_records.extend(records)
            if records:
                save_to_csv(records, county)
                if args.do_import and not args.dry_run:
                    result = import_to_db(records)
                    print(f"  DB: +{result['inserted']:,} new, ~{result['updated']:,} updated")
            time.sleep(5)

        print(f"\n{'='*55}")
        print(f"  Total records: {len(all_records):,}")
        if logger:
            logger.finish({"records": len(all_records), "counties": counties})
        return

    if args.match:
        if HAS_DB:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE arizona_roc_contacts arc
                    SET lien_match = TRUE
                    FROM texas_liens tl
                    WHERE arc.lien_match IS NOT TRUE
                    AND arc.county = 'Maricopa'
                    AND (
                        similarity(UPPER(arc.owner_name), UPPER(tl.debtor_name)) > 0.5
                        OR similarity(UPPER(arc.business_name), UPPER(tl.debtor_name)) > 0.5
                    )
                """)
                print(f"  Matched: {cur.rowcount}")
            conn.commit()
            conn.close()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
