"""
selenium_tx_scraper.py
======================
Working Selenium scraper for Texas county PublicSearch / Neumo portals.

Covers Dallas, Tarrant, Collin.

Key fix:
- Default search is "Federal Tax Lien" because Dallas PublicSearch indexes lien rows by document type better than IRS grantor text.
- Keeps ONLY active Federal Tax Lien / Notice of Federal Tax Lien rows.
- Skips partial releases, releases, withdrawals, certificates, abstracts, judgments, and other noise.
- Restores show_stats() as a real function.

Usage:
  python scripts/scrapers/selenium_tx_scraper.py --county dallas --days 365 --visible --debug --test
  python scripts/scrapers/selenium_tx_scraper.py --county dallas --days 365 --match
  python scripts/scrapers/selenium_tx_scraper.py --all --days 365 --match
  python scripts/scrapers/selenium_tx_scraper.py --stats
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

DATA_DIR = LEADFLOW_DIR / "data" / "texas"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEBUG_DIR = DATA_DIR / "debug" / "publicsearch"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

try:
    from app.core.db import get_connection
    HAS_DB = True
except Exception:
    HAS_DB = False

COUNTIES = {
    "dallas": {"name": "Dallas", "subdomain": "dallas", "est_liens": 48000},
    "tarrant": {"name": "Tarrant", "subdomain": "tarrant", "est_liens": 46000},
    "collin": {"name": "Collin", "subdomain": "collin", "est_liens": 8000},
}

DEFAULT_SEARCH_VALUE = "Federal Tax Lien"
SEARCH_VARIANTS = ["Federal Tax Lien", "Notice of Federal Tax Lien", "Federal Tax Liens"]
SKIP_DOC_TYPES = [
    "RELEASE", "PARTIAL RELEASE", "CERTIFICATE", "WITHDRAWAL", "WITHDRAW",
    "ERROR", "ABSTRACT", "JUDGMENT", "ABST", "SATISFACTION", "TERMINATION",
]


def get_driver(headless: bool = True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--log-level=3")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if Path(chrome_path).exists():
        options.binary_location = chrome_path
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    try:
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    except Exception:
        pass
    return driver


def build_url(subdomain: str, date_from: str, date_to: str, page: int = 1, search_value: str = DEFAULT_SEARCH_VALUE) -> str:
    url = (
        f"https://{subdomain}.tx.publicsearch.us/results"
        f"?department=RP"
        f"&keywordSearch=false"
        f"&recordedDateRange={date_from},{date_to}"
        f"&searchOcrText=false"
        f"&searchType=quickSearch"
        f"&perPage=250"
        f"&searchValue={quote_plus(search_value)}"
    )
    limit = 250
    offset = (page - 1) * limit
    url += f"&limit={limit}&offset={offset}"
    return url


def safe_name(text: str, max_len: int = 48) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", text or "")
    return text[:max_len].strip("_") or "debug"


def save_debug(driver, county_key: str, page: int, label: str) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"{county_key}_p{page}_{safe_name(label)}_{ts}"
    try:
        (DEBUG_DIR / f"{stem}.html").write_text(driver.page_source, encoding="utf-8", errors="ignore")
    except Exception:
        pass
    try:
        driver.save_screenshot(str(DEBUG_DIR / f"{stem}.png"))
    except Exception:
        pass
    print(f"    🧪 Debug saved: {DEBUG_DIR / (stem + '.*')}")


def wait_for_results(driver, timeout: int = 25) -> None:
    from selenium.webdriver.common.by import By
    start = time.time()
    while time.time() - start < timeout:
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
            rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
            if rows or "No Results" in body or "0 results" in body or "results" in body.lower():
                return
        except Exception:
            pass
        time.sleep(1)


def click_federal_tax_lien_filter(driver, debug: bool = False) -> bool:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.action_chains import ActionChains
    candidates = ["FEDERAL TAX LIENS", "FEDERAL TAX LIEN", "Federal Tax Liens", "Federal Tax Lien"]
    for text in candidates:
        for xp in [f"//*[normalize-space(text())='{text}']", f"//*[contains(normalize-space(text()), '{text}')]"]:
            try:
                for el in driver.find_elements(By.XPATH, xp):
                    if not el.is_displayed():
                        continue
                    try:
                        ActionChains(driver).move_to_element(el).pause(0.25).click(el).perform()
                    except Exception:
                        driver.execute_script("arguments[0].click();", el)
                    time.sleep(4)
                    if debug:
                        print(f"    Applied filter candidate: {text}")
                    return True
            except Exception:
                continue
    return False


def is_active_federal_tax_lien_doc_type(doc_type: str) -> bool:
    dt = (doc_type or "").upper().strip()
    if not dt:
        return False
    if any(skip in dt for skip in SKIP_DOC_TYPES):
        return False
    return any(p in dt for p in ["FEDERAL TAX LIEN", "NOTICE OF FEDERAL TAX LIEN", "FED TAX LIEN", "FTL", "FLTX"])


def normalize_cell_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def parse_page(driver, county_name: str) -> tuple[list[dict], bool]:
    from selenium.webdriver.common.by import By
    records, has_more = [], False
    wait_for_results(driver, timeout=25)
    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
    if not rows:
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            body_text = ""
        return parse_body_text(body_text, county_name)
    for row in rows:
        try:
            cells = [normalize_cell_text(c.text) for c in row.find_elements(By.TAG_NAME, "td")]
            if len(cells) < 8:
                continue
            nonempty = [c for c in cells if c and c not in {"...", "•"}]
            rec = parse_table_cells(cells, nonempty, county_name)
            if rec:
                records.append(rec)
        except Exception:
            continue
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        m = re.search(r"(\d+)\s*-\s*(\d+)\s+of\s+(\d+)\s+results", body_text, re.I)
        if m:
            end_row, total = int(m.group(2)), int(m.group(3))
            has_more = end_row < total
            if has_more:
                print(f"    ({end_row}/{total})", end=" ")
    except Exception:
        pass
    return records, has_more


def parse_table_cells(cells: list[str], nonempty: list[str], county_name: str) -> dict | None:
    grantor = grantee = doc_type = rec_date = doc_num = ""
    for gi, gei, dti, rdi, dni in [(3,4,5,6,7), (2,3,4,5,6), (1,2,3,4,5), (0,1,2,3,4)]:
        if max(gi, gei, dti, rdi, dni) >= len(cells):
            continue
        g, ge, dt, rd, dn = cells[gi], cells[gei], cells[dti], cells[rdi], cells[dni]
        if is_active_federal_tax_lien_doc_type(dt):
            grantor, grantee, doc_type, rec_date, doc_num = g, ge, dt, rd, dn
            break
    if not doc_type and nonempty:
        for i, val in enumerate(nonempty):
            if is_active_federal_tax_lien_doc_type(val):
                doc_type = val
                grantor = nonempty[i - 2] if i >= 2 else ""
                grantee = nonempty[i - 1] if i >= 1 else ""
                for item in nonempty[i + 1:]:
                    if not rec_date and re.search(r"\d{1,2}/\d{1,2}/\d{4}", item):
                        rec_date = item
                    if not doc_num and re.fullmatch(r"\d{8,14}", item):
                        doc_num = item
                break
    if not is_active_federal_tax_lien_doc_type(doc_type):
        return None
    return build_record(grantor, grantee, doc_type, rec_date, doc_num, county_name)


def parse_body_text(text: str, county_name: str) -> tuple[list[dict], bool]:
    records, has_more = [], False
    lines = [normalize_cell_text(l) for l in (text or "").splitlines() if normalize_cell_text(l)]
    for i, line in enumerate(lines):
        if not is_active_federal_tax_lien_doc_type(line):
            continue
        window = lines[max(0, i-4): min(len(lines), i+8)]
        rec = parse_row_text(" | ".join(window), county_name)
        if rec:
            records.append(rec)
    m = re.search(r"(\d+)\s*-\s*(\d+)\s+of\s+(\d+)", text or "", re.I)
    if m:
        has_more = int(m.group(2)) < int(m.group(3))
    return records, has_more


def parse_row_text(text: str, county_name: str) -> dict | None:
    if not is_active_federal_tax_lien_doc_type(text):
        return None
    doc_match = re.search(r"\b(\d{8,14})\b", text or "")
    date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", text or "")
    doc_num = doc_match.group(1) if doc_match else ""
    rec_date = date_match.group(1) if date_match else ""
    parts = [p.strip() for p in re.split(r"\s+\|\s+", text or "") if p.strip()]
    grantor = grantee = ""
    for i, p in enumerate(parts):
        if is_active_federal_tax_lien_doc_type(p):
            grantor = parts[i-2] if i >= 2 else ""
            grantee = parts[i-1] if i >= 1 else ""
            break
    return build_record(grantor, grantee, "FEDERAL TAX LIEN", rec_date, doc_num, county_name)


def parse_date_value(value: str):
    for fmt in ["%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"]:
        try:
            return datetime.strptime((value or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def normalize_taxpayer_name(name: str) -> str:
    name = normalize_cell_text(name).upper()
    name = re.sub(r"\bN/?A\b", "", name)
    return re.sub(r"\s+", " ", name).strip(" -|")


def build_record(grantor: str, grantee: str, doc_type: str, rec_date: str, doc_num: str, county_name: str) -> dict | None:
    grantor = normalize_cell_text(grantor).upper()
    grantee = normalize_cell_text(grantee).upper()
    doc_type = normalize_cell_text(doc_type).upper()
    doc_num = normalize_cell_text(doc_num)
    if not is_active_federal_tax_lien_doc_type(doc_type):
        return None
    irs_indicators = ["INTERNAL REVENUE", "UNITED STATES", "U S A", "USA", "I R S"]
    grantor_is_irs = any(ind in grantor for ind in irs_indicators)
    grantee_is_irs = any(ind in grantee for ind in irs_indicators)
    if grantor_is_irs and not grantee_is_irs:
        taxpayer = grantee
    elif grantee_is_irs and not grantor_is_irs:
        taxpayer = grantor
    else:
        taxpayer = grantee or grantor
    taxpayer = normalize_taxpayer_name(taxpayer)
    if not taxpayer or len(taxpayer) < 3 or "INTERNAL REVENUE" in taxpayer:
        return None
    filing_date = parse_date_value(rec_date)
    file_number = doc_num or f"TX-{county_name}-{abs(hash((taxpayer, filing_date, doc_type))) % 10_000_000_000}"
    return {
        "file_number": file_number,
        "taxpayer_name": taxpayer[:300],
        "grantor_name": grantor[:300],
        "grantee_name": grantee[:300],
        "instrument_type": "FEDERAL TAX LIEN",
        "raw_instrument_type": doc_type[:150],
        "filing_date": filing_date,
        "county": county_name,
    }


def scrape_county(county_key: str, days_back: int = 180, headless: bool = True, dry_run: bool = False, debug: bool = False, search_value: str | None = None, use_variants: bool = False) -> list[dict]:
    cfg = COUNTIES[county_key]
    end_date = date.today()
    start = end_date - timedelta(days=days_back)
    date_from, date_to = start.strftime("%Y%m%d"), end_date.strftime("%Y%m%d")
    print(f"  {cfg['name']} County: {start.strftime('%m/%d/%Y')} → {end_date.strftime('%m/%d/%Y')}")
    print(f"  Starting Chrome {'(headless)' if headless else '(visible)'}...")
    search_values = [search_value] if search_value else (SEARCH_VARIANTS if use_variants else [DEFAULT_SEARCH_VALUE])
    driver = None
    all_records, seen = [], set()
    try:
        driver = get_driver(headless=headless)
        for sv in search_values:
            print(f"  Search: {sv!r}")
            page, max_pages = 1, 100
            while page <= max_pages:
                url = build_url(cfg["subdomain"], date_from, date_to, page, sv)
                print(f"  Page {page}...", end=" ", flush=True)
                driver.get(url)
                wait_for_results(driver, timeout=25)
                # Click filter on every page - portal resets filter on pagination
                filter_clicked = click_federal_tax_lien_filter(driver, debug=debug)
                if filter_clicked:
                    time.sleep(6)  # Wait for filtered results to fully load
                    wait_for_results(driver, timeout=20)
                records, has_more = parse_page(driver, cfg["name"])
                new = 0
                for rec in records:
                    key = rec.get("file_number") or f"{rec.get('taxpayer_name')}|{rec.get('filing_date')}"
                    if key and key not in seen:
                        seen.add(key)
                        all_records.append(rec)
                        new += 1
                if debug and new == 0:
                    save_debug(driver, county_key, page, f"{sv}_zero_records")
                print(f"{new} liens (total: {len(all_records)}){' [last page]' if not has_more else ''}")
                if not has_more or new == 0:
                    break
                page += 1
                time.sleep(2)
            if all_records and not use_variants:
                break
        print(f"  ✅ {cfg['name']}: {len(all_records)} liens found")
        return all_records
    except Exception as e:
        print(f"  ❌ Error: {e}")
        if debug and driver:
            save_debug(driver, county_key, 0, "fatal_error")
        return []
    finally:
        if driver:
            driver.quit()


def ensure_table():
    if not HAS_DB:
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            statements = [
                """
                CREATE TABLE IF NOT EXISTS texas_liens (
                    id SERIAL PRIMARY KEY,
                    filing_number VARCHAR(100) UNIQUE,
                    debtor_name VARCHAR(300),
                    grantor_name VARCHAR(300),
                    grantee_name VARCHAR(300),
                    filing_type VARCHAR(150),
                    filing_date DATE,
                    county VARCHAR(100),
                    source VARCHAR(100),
                    tdlr_match_id INTEGER,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
                """,
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS grantor_name VARCHAR(300)",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS grantee_name VARCHAR(300)",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS county VARCHAR(100)",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS town VARCHAR(100)",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS filing_type VARCHAR(150)",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS filing_date DATE",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS source VARCHAR(100)",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS tdlr_match_id INTEGER",
                "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_texas_liens_filing_number ON texas_liens(filing_number)",
                "CREATE INDEX IF NOT EXISTS idx_tx_liens_grantor ON texas_liens(grantor_name)",
                "CREATE INDEX IF NOT EXISTS idx_tx_liens_county ON texas_liens(county)",
                "CREATE INDEX IF NOT EXISTS idx_tx_liens_source ON texas_liens(source)",
            ]
            for sql in statements:
                cur.execute(sql)
        conn.commit()
        print("  ✅ Table ready: texas_liens")
    finally:
        conn.close()


def save_liens(liens: list[dict], dry_run: bool = False) -> dict:
    if not liens:
        return {"inserted": 0, "updated": 0, "skipped": 0}
    out = DATA_DIR / f"texas_liens_selenium_{date.today().isoformat()}.json"
    out.write_text(json.dumps(liens, indent=2, default=str), encoding="utf-8")
    if not HAS_DB:
        print(f"  💾 Saved JSON only: {out}")
        return {"inserted": 0, "updated": 0, "skipped": 0}
    inserted = updated = skipped = 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for lien in liens:
                try:
                    cur.execute("""
                        INSERT INTO texas_liens (
                            filing_number, debtor_name, grantor_name, grantee_name,
                            filing_type, filing_date, county, source
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (filing_number) DO UPDATE SET
                            debtor_name = EXCLUDED.debtor_name,
                            grantor_name = EXCLUDED.grantor_name,
                            grantee_name = EXCLUDED.grantee_name,
                            filing_type = EXCLUDED.filing_type,
                            filing_date = EXCLUDED.filing_date,
                            county = EXCLUDED.county,
                            source = EXCLUDED.source,
                            updated_at = NOW()
                        RETURNING (xmax = 0) AS was_inserted
                    """, (
                        lien["file_number"], lien["taxpayer_name"], lien["grantor_name"],
                        lien["grantee_name"], lien["instrument_type"], lien["filing_date"],
                        lien["county"], "selenium_publicsearch",
                    ))
                    row = cur.fetchone()
                    inserted += 1 if row and row[0] else 0
                    updated += 0 if row and row[0] else 1
                except Exception as e:
                    skipped += 1
                    if skipped <= 5:
                        print(f"  ⚠ DB row skipped: {e}")
        if dry_run:
            conn.rollback()
            print(f"  [DRY RUN] Would save {inserted + updated:,} rows")
        else:
            conn.commit()
            print(f"  ✅ {inserted:,} new, {updated:,} updated, {skipped:,} errors")
    finally:
        conn.close()
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def match_to_tdlr(dry_run: bool = False) -> dict:
    if not HAS_DB:
        return {"matched": 0}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE texas_liens tl
                SET tdlr_match_id = t.id
                FROM texas_tdlr_contacts t
                WHERE tl.tdlr_match_id IS NULL
                  AND tl.source = 'selenium_publicsearch'
                  AND (
                    similarity(
                        regexp_replace(UPPER(COALESCE(tl.grantor_name, tl.debtor_name, '')), '(LLC|INC|CORP|LTD|CO|LP|COMPANY)', '', 'g'),
                        regexp_replace(UPPER(COALESCE(t.business_name, '')), '(LLC|INC|CORP|LTD|CO|LP|COMPANY)', '', 'g')
                    ) > 0.5
                    OR similarity(UPPER(COALESCE(tl.grantor_name, tl.debtor_name, '')), UPPER(COALESCE(t.owner_name, ''))) > 0.5
                  )
                RETURNING tl.id, COALESCE(tl.grantor_name, tl.debtor_name), t.business_name, t.license_type, t.business_county
            """)
            rows = cur.fetchall()
        if rows and not dry_run:
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE texas_tdlr_contacts
                    SET lien_match = TRUE, confidence = 'high', updated_at = NOW()
                    WHERE id IN (SELECT tdlr_match_id FROM texas_liens WHERE tdlr_match_id IS NOT NULL)
                """)
            conn.commit()
        else:
            conn.rollback()
        print(f"  ✅ Matched {len(rows):,} liens to TDLR contacts")
        for row in rows[:15]:
            print(f"    {(row[1] or '')[:40]:<40} → {(row[2] or '')[:30]} ({row[3]}, {row[4]})")
        if len(rows) > 15:
            print(f"    ... and {len(rows) - 15} more")
        return {"matched": len(rows)}
    finally:
        conn.close()


def download_lien_pdfs(county_key: str | None = None, headless: bool = True) -> dict:
    print("  PDF download stub preserved. Use existing PDF workflow after lien extraction is stable.")
    return {"downloaded": 0, "failed": 0}


def show_stats():
    if not HAS_DB:
        print("  DB not available — no stats")
        return
    conn = get_connection()
    try:
        print(f"\n{'=' * 60}")
        print("  Texas PublicSearch Liens Stats")
        print(f"{'=' * 60}")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT county, COUNT(*), COUNT(*) FILTER (WHERE tdlr_match_id IS NOT NULL)
                FROM texas_liens
                WHERE source = 'selenium_publicsearch'
                GROUP BY county
                ORDER BY COUNT(*) DESC
            """)
            rows = cur.fetchall()
            if rows:
                print(f"  {'County':<15} {'Liens':>8}  {'Matched':>8}")
                print(f"  {'─' * 15} {'─' * 8}  {'─' * 8}")
                total = matched = 0
                for county, cnt, mat in rows:
                    total += cnt or 0
                    matched += mat or 0
                    print(f"  {county:<15} {cnt:>8,}  {mat:>8,}")
                print(f"  {'─' * 15} {'─' * 8}  {'─' * 8}")
                print(f"  {'TOTAL':<15} {total:>8,}  {matched:>8,}")
            else:
                print("  No data yet")
            try:
                cur.execute("SELECT COUNT(*) FROM texas_tdlr_contacts WHERE lien_match = TRUE")
                print(f"\n  TDLR contacts with lien match : {cur.fetchone()[0]:,}")
                cur.execute("SELECT COUNT(*) FROM texas_tdlr_contacts WHERE lien_match = TRUE AND email IS NOT NULL AND email != ''")
                print(f"  Matched + email ready      : {cur.fetchone()[0]:,}")
            except Exception as e:
                print(f"\n  TDLR stats unavailable: {e}")
        print(f"{'=' * 60}\n")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Selenium TX PublicSearch Scraper — Dallas/Tarrant/Collin")
    parser.add_argument("--county", default=None, choices=list(COUNTIES.keys()))
    parser.add_argument("--all", action="store_true", help="Scrape all configured counties")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--year", action="store_true", help="Pull full year / 365 days")
    parser.add_argument("--match", action="store_true", help="Match to TDLR after scraping")
    parser.add_argument("--pdfs", action="store_true", help="Download PDF lien documents for matched contacts")
    parser.add_argument("--visible", action="store_true", help="Show Chrome window")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test", action="store_true", help="Stop after first county")
    parser.add_argument("--debug", action="store_true", help="Save debug screenshots/html when 0 rows")
    parser.add_argument("--search-value", default=None, help="Override searchValue query")
    parser.add_argument("--variants", action="store_true", help="Try multiple search variants")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return
    if not args.county and not args.all:
        parser.print_help()
        return

    counties = list(COUNTIES.keys()) if args.all else [args.county]
    headless = not args.visible
    days_back = 365 if args.year else args.days
    print(f"\n{'=' * 60}")
    print("  Selenium TX PublicSearch Scraper")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    print(f"  Counties : {', '.join(c.title() for c in counties)}")
    print(f"  Days back: {days_back}{'  (full year)' if args.year else ''}")
    print(f"  Chrome   : {'headless' if headless else 'visible'}")
    print(f"  Debug    : {'ON' if args.debug else 'OFF'}")
    print(f"  {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"{'=' * 60}\n")

    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("selenium_tx_scraper")
        logger.start()
    except Exception:
        logger = None

    ensure_table()
    all_liens, results = [], {}
    for county_key in counties:
        print(f"\n── {COUNTIES[county_key]['name']} County ──")
        if logger:
            logger.step_start(f"scrape_{county_key}")
        liens = scrape_county(county_key, days_back, headless, args.dry_run, args.debug, args.search_value, args.variants)
        all_liens.extend(liens)
        if liens:
            backup = DATA_DIR / f"{county_key}_selenium_{date.today().isoformat()}.json"
            backup.write_text(json.dumps(liens, indent=2, default=str), encoding="utf-8")
            print(f"  💾 Backup: {backup}")
            results[county_key] = save_liens(liens, dry_run=args.dry_run)
        else:
            results[county_key] = {"inserted": 0, "updated": 0, "skipped": 0}
        if logger:
            logger.step_done(f"scrape_{county_key}", ok=True, detail=f"{len(liens)} liens")
        if args.test:
            print("  Test mode — stopping after first county")
            break
        time.sleep(3)

    if args.match and not args.dry_run and all_liens:
        print("\n── Matching to TDLR Contacts ──")
        if logger:
            logger.step_start("match_tdlr")
        match_result = match_to_tdlr(dry_run=args.dry_run)
        if logger:
            logger.step_done("match_tdlr", ok=True, detail=str(match_result))

    if args.pdfs and not args.dry_run:
        print("\n── Downloading Lien PDFs ──")
        if logger:
            logger.step_start("download_pdfs")
        pdf_result = download_lien_pdfs(headless=headless)
        if logger:
            logger.step_done("download_pdfs", ok=True, detail=str(pdf_result))

    print(f"\n{'=' * 60}")
    print("  Selenium Scraper Complete")
    for county, result in results.items():
        name = COUNTIES[county]["name"]
        print(f"  {name:<10} {result.get('inserted', 0):>5,} new  {result.get('updated', 0):>5,} updated  {result.get('skipped', 0):>5,} skipped")
    print(f"{'=' * 60}\n")
    show_stats()
    if logger:
        logger.finish({"counties": counties, "total": len(all_liens), "days_back": days_back, "dry_run": args.dry_run})


if __name__ == "__main__":
    main()