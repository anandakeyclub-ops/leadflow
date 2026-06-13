# check_releases.py
# Scrapes Release of Federal Tax Lien documents from TX PublicSearch
# Cross-references with active liens in DB and marks released ones

import psycopg2
import time
import re
from urllib.parse import quote_plus
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

COUNTIES = {
    "dallas":  {"subdomain": "dallas",  "name": "Dallas"},
    "tarrant": {"subdomain": "tarrant", "name": "Tarrant"},
    "collin":  {"subdomain": "collin",  "name": "Collin"},
}

DB_PARAMS = {
    "host": "localhost",
    "port": 5434,
    "dbname": "leadflow",
    "user": "postgres",
    "password": "postgres",
}


def get_driver(headless=False):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    return webdriver.Chrome(options=opts)


def wait_for_results(driver, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
            if "Loading Results" not in body and "results" in body.lower():
                return True
            if "No Results" in body or "0 results" in body.lower():
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def extract_names_from_page(driver):
    names = set()
    # Try table rows first
    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
    if rows:
        for row in rows:
            cells = [c.text.strip() for c in row.find_elements(By.TAG_NAME, "td")]
            for cell in cells[1:5]:
                if cell and len(cell) > 3 and "INTERNAL REVENUE" not in cell.upper():
                    names.add(cell.upper().strip())
    else:
        # Try card/list layout
        for selector in ["[class*='grantor']", "[class*='name']", "[class*='party']"]:
            els = driver.find_elements(By.CSS_SELECTOR, selector)
            for el in els:
                text = el.text.strip().upper()
                if text and len(text) > 3 and "INTERNAL REVENUE" not in text:
                    names.add(text)
    return names


def scrape_releases(county_key, headless=False):
    cfg = COUNTIES[county_key]
    print(f"\n  Scraping {cfg['name']} County releases...")
    released_names = set()
    driver = get_driver(headless=headless)

    try:
        offset = 0
        limit = 250
        page = 1

        while True:
            url = (
                f"https://{cfg['subdomain']}.tx.publicsearch.us/results"
                f"?department=RP"
                f"&searchType=quickSearch"
                f"&searchValue={quote_plus('Release of Federal Tax Lien')}"
                f"&limit={limit}&offset={offset}"
            )
            print(f"  Page {page} (offset={offset})...", end=" ", flush=True)
            driver.get(url)
            time.sleep(4)
            loaded = wait_for_results(driver, timeout=30)

            if not loaded:
                print("timeout")
                break

            body = driver.find_element(By.TAG_NAME, "body").text

            # Check for no results
            if "No Results" in body or "0 results" in body.lower():
                print("no results")
                break

            # Extract names
            names = extract_names_from_page(driver)
            released_names.update(names)
            print(f"{len(names)} releases (total: {len(released_names)})")

            # Check pagination
            m = re.search(r"([\d,]+)-([\d,]+)\s+of\s+([\d,]+)", body)
            if m:
                end = int(m.group(2).replace(",", ""))
                total = int(m.group(3).replace(",", ""))
                if end >= total:
                    print(f"  All {total} releases collected")
                    break
                offset = end
                page += 1
            else:
                break

    except Exception as e:
        print(f"  Error: {e}")
    finally:
        driver.quit()

    return released_names


def mark_released_in_db(county_name, released_names):
    if not released_names:
        print(f"  No releases to mark for {county_name}")
        return 0

    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    marked = 0

    for name in released_names:
        # Exact match
        cur.execute("""
            UPDATE texas_liens
            SET status='released', updated_at=NOW()
            WHERE county=%s
            AND UPPER(debtor_name) = %s
            AND status='active'
        """, (county_name, name))
        marked += cur.rowcount

        # Partial match for names with extra info
        if cur.rowcount == 0:
            cur.execute("""
                UPDATE texas_liens
                SET status='released', updated_at=NOW()
                WHERE county=%s
                AND UPPER(debtor_name) LIKE %s
                AND status='active'
            """, (county_name, f"%{name[:20]}%"))
            marked += cur.rowcount

    conn.commit()

    # Report final counts
    cur.execute("SELECT status, COUNT(*) FROM texas_liens WHERE county=%s GROUP BY status", (county_name,))
    print(f"\n  {county_name} DB status after update:")
    for row in cur.fetchall():
        print(f"    {row[0]}: {row[1]}")

    conn.close()
    return marked


def main():
    print("=" * 60)
    print("  TX PublicSearch - Release of Federal Tax Lien Checker")
    print("=" * 60)

    # Show current DB state
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    cur.execute("""
        SELECT county, status, COUNT(*)
        FROM texas_liens
        GROUP BY county, status
        ORDER BY county, status
    """)
    print("\nCurrent DB state:")
    for row in cur.fetchall():
        print(f"  {row[0]:12} {row[1]:10} {row[2]}")
    conn.close()

    # Scrape and mark releases for each county
    for county_key, cfg in COUNTIES.items():
        releases = scrape_releases(county_key, headless=False)
        print(f"  Found {len(releases)} unique released taxpayer names")
        marked = mark_released_in_db(cfg["name"], releases)
        print(f"  Marked {marked} liens as released in DB")

    # Final summary
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    cur.execute("""
        SELECT county, status, COUNT(*)
        FROM texas_liens
        GROUP BY county, status
        ORDER BY county, status
    """)
    print("\nFinal DB state:")
    for row in cur.fetchall():
        print(f"  {row[0]:12} {row[1]:10} {row[2]}")

    cur.execute("SELECT COUNT(*) FROM texas_liens WHERE status='active'")
    active = cur.fetchone()[0]
    print(f"\nTotal active liens ready for outreach: {active}")
    conn.close()


if __name__ == "__main__":
    main()
