# scrape_maricopa_results.py
# Scrapes Federal Tax Liens from Maricopa County Recorder
# Direct URL approach - no CAPTCHA needed
# Matches to arizona_roc_contacts in DB

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import time
import re
import csv
import json
import psycopg2
from pathlib import Path
from datetime import date

DATA_DIR = Path("data/arizona")
DATA_DIR.mkdir(exist_ok=True)

DB = dict(host="localhost", port=5434, dbname="leadflow",
          user="postgres", password="postgres")

BASE_URL = "https://recorder.maricopa.gov/recording/document-search-results.html"
SEARCH_PARAMS = (
    "?lastNames=&firstNames=&middleNameIs="
    "&documentTypeSelector=code"
    "&documentCode=FL"
    "&beginDate=2025-01-01"
    "&endDate=2026-06-01"
)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS arizona_liens (
    id               SERIAL PRIMARY KEY,
    recording_number VARCHAR(50) UNIQUE,
    debtor_name      VARCHAR(200),
    filing_date      VARCHAR(20),
    document_type    VARCHAR(100) DEFAULT 'FEDERAL TAX LIEN',
    county           VARCHAR(50)  DEFAULT 'Maricopa',
    status           VARCHAR(20)  DEFAULT 'active',
    source           VARCHAR(50)  DEFAULT 'maricopa_recorder',
    created_at       TIMESTAMP    DEFAULT NOW(),
    updated_at       TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_az_lien_debtor
    ON arizona_liens(debtor_name);
CREATE INDEX IF NOT EXISTS idx_az_lien_status
    ON arizona_liens(status);
"""


def get_driver(headless=False):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return webdriver.Chrome(options=opts)


def parse_table_rows(driver) -> list[dict]:
    liens = []
    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
    for row in rows:
        cells = [c.text.strip() for c in row.find_elements(By.TAG_NAME, "td")]
        if not cells:
            continue
        rec = cells[0].replace(" ", "")
        if re.match(r"2\d{9}", rec):
            liens.append({
                "recording_number": rec,
                "filing_date":      cells[1] if len(cells) > 1 else "",
                "document_type":    "FEDERAL TAX LIEN",
                "county":           "Maricopa",
                "debtor_name":      "",
            })
    return liens


def get_debtor_name(driver, recording_number: str) -> str:
    try:
        # Find and click the recording number link/button
        el = driver.find_element(By.XPATH,
            f"//td[normalize-space()='{recording_number}'] | "
            f"//button[contains(.,'{recording_number}')] | "
            f"//a[contains(.,'{recording_number}')]"
        )
        driver.execute_script("arguments[0].scrollIntoView(true)", el)
        driver.execute_script("arguments[0].click()", el)
        time.sleep(2)

        # Read popup content
        body = driver.find_element(By.TAG_NAME, "body").text

        # Extract name - pattern: NAME(S) section, debtor is before INTERNAL REVENUE SERVICE
        m = re.search(
            r"NAME\(S\)\s+(.*?)\s+INTERNAL REVENUE SERVICE",
            body, re.DOTALL
        )
        if m:
            name = m.group(1).strip().replace("\n", " ").strip()
            # Clean up extra whitespace
            name = re.sub(r"\s+", " ", name)
        else:
            # Try alternative pattern
            lines = body.split("\n")
            for i, line in enumerate(lines):
                if "NAME(S)" in line and i + 1 < len(lines):
                    name = lines[i + 1].strip()
                    break
            else:
                name = ""

        # Close popup
        for selector in [
            "button.close", "[aria-label='Close']",
            "button[class*='close']", ".modal-close", "[data-dismiss]"
        ]:
            try:
                driver.find_element(By.CSS_SELECTOR, selector).click()
                break
            except Exception:
                pass
        else:
            driver.execute_script("""
                var closes = document.querySelectorAll('.close, [aria-label="Close"], button[data-dismiss]');
                if(closes.length) closes[0].click();
            """)

        time.sleep(1)
        return name

    except Exception as e:
        return ""


def find_next_page(driver) -> bool:
    # Try clicking Next button
    for selector in [
        "//button[contains(text(),'Next')]",
        "//a[contains(text(),'Next')]",
        "//button[contains(@class,'next')]",
        "//li[contains(@class,'next')]/a",
        "//*[@aria-label='Next page']",
    ]:
        try:
            el = driver.find_element(By.XPATH, selector)
            if el.is_displayed() and el.is_enabled():
                driver.execute_script("arguments[0].click()", el)
                return True
        except Exception:
            pass

    # Try URL offset approach
    return False


def try_url_pagination(driver, base_url: str, current_count: int) -> bool:
    # Try common pagination parameters
    for param in [f"&start={current_count}", f"&page={current_count//20 + 1}",
                  f"&offset={current_count}", f"&from={current_count}"]:
        try:
            next_url = base_url + param
            driver.get(next_url)
            time.sleep(5)
            body = driver.find_element(By.TAG_NAME, "body").text
            if "Search Results" in body and "No results" not in body:
                rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
                if rows:
                    return True
        except Exception:
            pass
    return False


def save_to_db(liens: list[dict]) -> dict:
    if not liens:
        return {"inserted": 0, "updated": 0, "matched": 0}

    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute(CREATE_SQL)
    conn.commit()

    ins = upd = 0
    for lien in liens:
        if not lien.get("recording_number"):
            continue
        try:
            cur.execute("""
                INSERT INTO arizona_liens
                    (recording_number, debtor_name, filing_date, document_type, county)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (recording_number) DO UPDATE SET
                    debtor_name = CASE
                        WHEN EXCLUDED.debtor_name != '' THEN EXCLUDED.debtor_name
                        ELSE arizona_liens.debtor_name
                    END,
                    updated_at = NOW()
                RETURNING (xmax = 0) AS is_new
            """, (
                lien["recording_number"],
                lien.get("debtor_name", ""),
                lien.get("filing_date", ""),
                lien.get("document_type", "FEDERAL TAX LIEN"),
                lien.get("county", "Maricopa"),
            ))
            row = cur.fetchone()
            if row and row[0]:
                ins += 1
            else:
                upd += 1
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"  DB error: {e}")

    # Match to ROC contacts
    cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    conn.commit()
    cur.execute("""
        UPDATE arizona_roc_contacts arc
        SET lien_match = TRUE
        FROM arizona_liens al
        WHERE arc.lien_match IS NOT TRUE
        AND al.debtor_name != ''
        AND (
            similarity(UPPER(arc.owner_name),    UPPER(al.debtor_name)) > 0.55
            OR similarity(UPPER(arc.business_name), UPPER(al.debtor_name)) > 0.55
        )
    """)
    matched = cur.rowcount
    conn.commit()
    conn.close()

    return {"inserted": ins, "updated": upd, "matched": matched}


def save_csv(liens: list[dict]):
    out = DATA_DIR / f"maricopa_liens_{date.today().isoformat()}.csv"
    fields = ["recording_number", "filing_date", "document_type", "county", "debtor_name"]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(liens)
    print(f"  CSV: {out} ({len(liens)} records)")
    return out


def main():
    print("\n" + "="*55)
    print("  Maricopa County Federal Tax Lien Scraper")
    print(f"  {date.today().strftime('%A %B %d, %Y')}")
    print("="*55 + "\n")

    driver = get_driver(headless=False)
    all_liens = []
    page = 1
    base_with_params = BASE_URL + SEARCH_PARAMS

    try:
        print(f"  Loading: {base_with_params[:80]}")
        driver.get(base_with_params)
        time.sleep(8)

        while True:
            body = driver.find_element(By.TAG_NAME, "body").text

            # Check for CAPTCHA
            if "captcha" in body.lower() or "are you a robot" in body.lower():
                print("  CAPTCHA detected - solve it in the browser then press Enter")
                input("  Press Enter when done: ")
                time.sleep(3)
                body = driver.find_element(By.TAG_NAME, "body").text

            # Check we're on results page
            if "Search Results" not in body:
                print(f"  Not on results page. Body: {body[:200]}")
                driver.save_screenshot(str(DATA_DIR / f"debug_page{page}.png"))
                break

            # Parse showing count
            m = re.search(r"Showing (\d+) - (\d+) of ([\d+,]+)", body)
            if m:
                print(f"  Page {page}: {m.group(1)}-{m.group(2)} of {m.group(3)}")

            # Parse table rows
            page_liens = parse_table_rows(driver)
            print(f"  Rows parsed: {len(page_liens)}")
            all_liens.extend(page_liens)

            # Debug: show pagination elements
            pag_els = driver.find_elements(By.XPATH,
                "//*[contains(@class,'page') or contains(@class,'next') or contains(@class,'pag') or contains(text(),'Next')]"
            )
            if page == 1:
                print(f"  Pagination elements found: {len(pag_els)}")
                for el in pag_els[:5]:
                    print(f"    {el.tag_name} class={el.get_attribute('class') or ''} text={el.text[:20]}")

            driver.save_screenshot(str(DATA_DIR / f"maricopa_page{page}.png"))

            # Try to go to next page
            clicked = find_next_page(driver)
            if clicked:
                time.sleep(6)
                page += 1
            else:
                # Try URL-based pagination
                if try_url_pagination(driver, base_with_params, len(all_liens)):
                    page += 1
                else:
                    print(f"  No more pages. Total: {len(all_liens)} recording numbers")
                    break

            # Safety limit
            if page > 50:
                print("  Safety limit reached (50 pages)")
                break

        print(f"\n  Collected {len(all_liens)} recording numbers")
        print("  Now fetching debtor names...")

        # Get debtor names - go back to page 1
        driver.get(base_with_params)
        time.sleep(6)

        for i, lien in enumerate(all_liens):
            name = get_debtor_name(driver, lien["recording_number"])
            if name:
                lien["debtor_name"] = name
            if (i + 1) % 10 == 0:
                print(f"  Names fetched: {i+1}/{len(all_liens)} "
                      f"({sum(1 for l in all_liens if l['debtor_name'])} with names)")
            # Go back to results if needed
            if "Search Results" not in driver.find_element(By.TAG_NAME, "body").text:
                driver.get(base_with_params)
                time.sleep(5)

    except Exception as e:
        print(f"  Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.quit()

    # Save and match
    print(f"\n  Liens with names: {sum(1 for l in all_liens if l['debtor_name'])}/{len(all_liens)}")
    save_csv(all_liens)

    result = save_to_db(all_liens)
    print(f"  DB: +{result['inserted']} new | ~{result['updated']} updated | {result['matched']} ROC matched")

    print("\n" + "="*55)
    print("  Done")
    print("="*55)


if __name__ == "__main__":
    main()
