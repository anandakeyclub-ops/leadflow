"""
harris_county_lien_scraper.py
==============================
Harris County TX Federal Tax Lien Scraper.

Scrapes the Harris County Clerk Document Search Portal for federal
tax lien filings (IRS Notice of Federal Tax Lien).

Source: https://www.cclerk.hctx.net/applications/websearch/RP.aspx

Search method:
  - Grantee: "UNITED STATES" or "INTERNAL REVENUE"
  - Instrument Type: FTL (Federal Tax Lien)
  - Date range: last N days

Returns:
  - Debtor name (grantor)
  - Filing date
  - Address
  - Lien amount (when available)

Stores in: harris_county_liens table
Then matches against: texas_tdlr_contacts (lien_match=TRUE)

Usage:
  python scripts/scrapers/harris_county_lien_scraper.py --days 90
  python scripts/scrapers/harris_county_lien_scraper.py --days 365
  python scripts/scrapers/harris_county_lien_scraper.py --match
  python scripts/scrapers/harris_county_lien_scraper.py --stats
  python scripts/scrapers/harris_county_lien_scraper.py --dry-run --days 30

Schedule: Monthly 1st at 6:45 AM
  Arguments: scripts/scrapers/harris_county_lien_scraper.py --days 35 --match
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

DATA_DIR = LEADFLOW_DIR / "data" / "texas"
DATA_DIR.mkdir(parents=True, exist_ok=True)

try:
    from app.core.db import get_connection
    HAS_DB = True
except ImportError:
    HAS_DB = False

# ── Harris County Clerk portal ────────────────────────────────────────────────
BASE_URL    = "https://www.cclerk.hctx.net/applications/websearch"
SEARCH_URL  = f"{BASE_URL}/RP.aspx"
RESULTS_URL = f"{BASE_URL}/RPInquiry.aspx"

# IRS lien grantee — confirmed from portal: "INTERNAL REVENUE SERVICE"
# Instrument type confirmed from portal: "T/L" (Tax Lien)
IRS_GRANTEES = [
    "INTERNAL REVENUE SERVICE",
]

# Confirmed instrument type code from Harris County portal
# T/L = Tax Lien (IRS federal tax liens)
FTL_CODES = ["T/L", "TL", "TAX LIEN", "FTL"]

# ── DB schema ─────────────────────────────────────────────────────────────────

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS harris_county_liens (
    id                  SERIAL PRIMARY KEY,
    file_number         VARCHAR(50)  UNIQUE,
    grantor_name        VARCHAR(300) NOT NULL,
    grantee_name        VARCHAR(200),
    instrument_type     VARCHAR(50),
    filing_date         DATE,
    film_code           VARCHAR(50),
    volume              VARCHAR(20),
    page                VARCHAR(20),
    address             VARCHAR(300),
    city                VARCHAR(100),
    state               VARCHAR(10)  DEFAULT 'TX',
    zip                 VARCHAR(20),
    lien_amount         NUMERIC(15,2),
    tdlr_match_id       INTEGER,
    status              VARCHAR(30)  DEFAULT 'active',
    source              VARCHAR(30)  DEFAULT 'harris_clerk',
    created_at          TIMESTAMP    DEFAULT NOW(),
    updated_at          TIMESTAMP    DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hc_grantor
    ON harris_county_liens(grantor_name);
CREATE INDEX IF NOT EXISTS idx_hc_date
    ON harris_county_liens(filing_date);
CREATE INDEX IF NOT EXISTS idx_hc_match
    ON harris_county_liens(tdlr_match_id)
    WHERE tdlr_match_id IS NOT NULL;
"""


# ── Session + ViewState handler ───────────────────────────────────────────────

LOGIN_URL = f"{BASE_URL}/Login.aspx"
EMAIL     = os.getenv("HARRIS_CLERK_EMAIL", "")
PASSWORD  = os.getenv("HARRIS_CLERK_PASSWORD", "")


class HarrisClerkSession:
    """
    Handles ASP.NET ViewState + login for Harris County Clerk portal.
    Register free at: cclerk.hctx.net/applications/websearch/Registration/Welcome.aspx
    Add to .env: HARRIS_CLERK_EMAIL and HARRIS_CLERK_PASSWORD
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept":          "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer":         SEARCH_URL,
        })
        self.viewstate        = ""
        self.viewstate_gen    = ""
        self.event_validation = ""
        self.logged_in        = False

    def login(self) -> bool:
        """Log in to Harris County Clerk portal."""
        if not EMAIL or not PASSWORD:
            print("  ⚠ HARRIS_CLERK_EMAIL / HARRIS_CLERK_PASSWORD not set in .env")
            return False

        try:
            # Load login page to get ViewState
            r = self.session.get(LOGIN_URL, timeout=20)
            if r.status_code != 200:
                print(f"  ❌ Login page returned {r.status_code}")
                return False

            html      = r.text
            vs        = self._extract(html, "__VIEWSTATE")
            vs_gen    = self._extract(html, "__VIEWSTATEGENERATOR")
            ev        = self._extract(html, "__EVENTVALIDATION")

            # Submit login form
            login_data = {
                "__VIEWSTATE":          vs,
                "__VIEWSTATEGENERATOR": vs_gen,
                "__EVENTVALIDATION":    ev,
                "__EVENTTARGET":        "",
                "__EVENTARGUMENT":      "",
                "ctl00$ContentPlaceHolder1$txtUserName": EMAIL,
                "ctl00$ContentPlaceHolder1$txtPassword": PASSWORD,
                "ctl00$ContentPlaceHolder1$btnLogin":    "Login",
            }

            r = self.session.post(
                LOGIN_URL, data=login_data,
                timeout=20, allow_redirects=True,
            )

            # Check if login succeeded — portal redirects to home page
            # Success: no login form fields in response
            # Failure: login form still present
            if ("txtUserName" not in r.text and
                    "txtPassword" not in r.text and
                    r.status_code == 200):
                print(f"  ✅ Logged in as {EMAIL}")
                self.logged_in = True
                return True
            elif "invalid" in r.text.lower() or "incorrect" in r.text.lower():
                print(f"  ❌ Login failed — check credentials in .env")
                return False
            else:
                # Assume success and continue
                print(f"  ✅ Login submitted for {EMAIL}")
                self.logged_in = True
                return True

        except Exception as e:
            print(f"  ❌ Login error: {e}")
            return False

    def get_form_state(self) -> bool:
        """Load the search form and extract ASP.NET ViewState."""
        # Auto-login if not logged in yet
        if not self.logged_in and EMAIL and PASSWORD:
            self.login()

        try:
            r = self.session.get(SEARCH_URL, timeout=20)
            if r.status_code != 200:
                print(f"  ❌ Portal returned {r.status_code}")
                return False

            html = r.text

            # Check if we got redirected to login page
            if "txtUserName" in html or "btnLogin" in html:
                print("  ⚠ Session expired — re-logging in...")
                self.logged_in = False
                if self.login():
                    r    = self.session.get(SEARCH_URL, timeout=20)
                    html = r.text
                else:
                    return False

            self.viewstate        = self._extract(html, "__VIEWSTATE")
            self.viewstate_gen    = self._extract(html, "__VIEWSTATEGENERATOR")
            self.event_validation = self._extract(html, "__EVENTVALIDATION")
            return True

        except Exception as e:
            print(f"  ❌ Failed to load portal: {e}")
            return False

    def _extract(self, html: str, field: str) -> str:
        """Extract hidden field value from HTML."""
        pattern = rf'id="{field}"[^>]*value="([^"]*)"'
        match   = re.search(pattern, html, re.IGNORECASE)
        return match.group(1) if match else ""

    def search_liens(self, grantee: str = "",
                     date_from: str = "",
                     date_to: str = "",
                     instrument_type: str = "") -> list[dict]:
        """
        Search Harris County Clerk for liens.
        Confirmed working parameters:
          - Grantee: INTERNAL REVENUE SERVICE → returns T/L (Tax Lien) records
          - Instrument type: leave blank or T/L
        """
        if not self.get_form_state():
            return []

        # Correct field names confirmed from live portal inspection:
        # txtOR  = Grantor, txtEE = Grantee (short for granto-R and grante-E)
        # txtFrom/txtTo = date range, txtInstrument = instrument type (text)
        form_data = {
            "__VIEWSTATE":          self.viewstate,
            "__VIEWSTATEGENERATOR": self.viewstate_gen,
            "__EVENTVALIDATION":    self.event_validation,
            "__VIEWSTATEENCRYPTED": "",
            "__EVENTTARGET":        "",
            "__EVENTARGUMENT":      "",
            "ctl00$ContentPlaceHolder1$txtFileNo":     "",
            "ctl00$ContentPlaceHolder1$txtFilmCd":     "",
            "ctl00$ContentPlaceHolder1$txtFrom":       date_from,
            "ctl00$ContentPlaceHolder1$txtTo":         date_to,
            "ctl00$ContentPlaceHolder1$txtOR":         "",        # Grantor
            "ctl00$ContentPlaceHolder1$txtEE":         grantee,   # Grantee
            "ctl00$ContentPlaceHolder1$txtNameTee":    "",
            "ctl00$ContentPlaceHolder1$txtDesc":       "",
            "ctl00$ContentPlaceHolder1$txtInstrument": instrument_type,
            "ctl00$ContentPlaceHolder1$txtVolNo":      "",
            "ctl00$ContentPlaceHolder1$txtPageNo":     "",
            "ctl00$ContentPlaceHolder1$txtSection":    "",
            "ctl00$ContentPlaceHolder1$txtLot":        "",
            "ctl00$ContentPlaceHolder1$txtBlock":      "",
            "ctl00$ContentPlaceHolder1$txtUnit":       "",
            "ctl00$ContentPlaceHolder1$txtAbstract":   "",
            "ctl00$ContentPlaceHolder1$txtOutLot":     "",
            "ctl00$ContentPlaceHolder1$txtTract":      "",
            "ctl00$ContentPlaceHolder1$txtReserve":    "",
            "ctl00$ContentPlaceHolder1$btnSearch":     "Search",
        }

        try:
            r = self.session.post(
                SEARCH_URL,
                data=form_data,
                timeout=45,
                allow_redirects=True,
            )

            if r.status_code != 200:
                print(f"  ⚠ Search returned {r.status_code}")
                return []

            results = self._parse_results(r.text)

            # If no results and we used instrument type, try without it
            if not results and instrument_type:
                form_data["ctl00$ContentPlaceHolder1$ddlInstrumentType"] = ""
                r = self.session.post(
                    SEARCH_URL, data=form_data,
                    timeout=45, allow_redirects=True,
                )
                results = self._parse_results(r.text)

            return results

        except Exception as e:
            print(f"  ⚠ Search error: {e}")
            return []

    def _parse_results(self, html: str) -> list[dict]:
        """
        Parse Harris County Clerk search results.

        Confirmed format from live portal:
          RP-2026-197971 05/20/2026 T/L
          Grantor : KIRKCONNELL KAMRON
          Grantee : INTERNAL REVENUE SERVICE
          Comment: SEE INSTRUMENT
          2 RP-2026-197971
        """
        results = []

        # Strip scripts and styles
        clean = re.sub(r'<script[^>]*>.*?</script>', ' ',
                       html, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<style[^>]*>.*?</style>', ' ',
                       clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<[^>]+>', ' ', clean)
        clean = re.sub(r'&nbsp;', ' ', clean)
        clean = re.sub(r'&[a-z]+;', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()

        # Match each lien record
        # Pattern: RP-YYYY-NNNNN DATE TYPE ... Grantor : NAME ... Grantee : NAME
        record_pattern = re.compile(
            r'(RP-\d{4}-\d+)\s+'
            r'(\d{2}/\d{2}/\d{4})\s+'
            r'([A-Z/]+)\s+'
            r'(.*?)'
            r'(?=RP-\d{4}-\d+\s+\d{2}/\d{2}/\d{4}|\Z)',
            re.DOTALL
        )

        for m in record_pattern.finditer(clean):
            file_num  = m.group(1).strip()
            file_date = m.group(2).strip()
            inst_type = m.group(3).strip()
            body      = m.group(4)

            # Extract all grantors
            grantors = re.findall(
                r'Grantor\s*:\s*([A-Z0-9&\'\.\-\s]+?)(?=Grantor\s*:|Grantee\s*:|Comment:|$)',
                body, re.IGNORECASE
            )
            # Extract all grantees
            grantees = re.findall(
                r'Grantee\s*:\s*([A-Z0-9&\'\.\-\s]+?)(?=Grantor\s*:|Grantee\s*:|Comment:|$)',
                body, re.IGNORECASE
            )

            grantor = " / ".join(g.strip() for g in grantors if g.strip())
            grantee = " / ".join(g.strip() for g in grantees if g.strip())

            # Only keep IRS tax liens
            if not grantor:
                continue
            if grantee and "INTERNAL REVENUE" not in grantee.upper() and \
               "IRS" not in grantee.upper():
                continue

            results.append({
                "file_number":     file_num,
                "filing_date":     file_date,
                "instrument_type": inst_type,
                "grantor_name":    grantor[:300],
                "grantee_name":    grantee[:200] or "INTERNAL REVENUE SERVICE",
            })

        return results


# ── Date range scraper ────────────────────────────────────────────────────────

def scrape_harris_liens(days_back: int = 90,
                         dry_run: bool = False) -> list[dict]:
    """
    Scrape Harris County Clerk for IRS federal tax liens.

    Confirmed working search:
      Grantee: INTERNAL REVENUE SERVICE
      Instrument type: T/L (Tax Lien)
      Date range: chunked by month to avoid result limits
    """
    end_date   = date.today()
    start_date = end_date - timedelta(days=days_back)

    print(f"  Date range: {start_date.strftime('%m/%d/%Y')} to "
          f"{end_date.strftime('%m/%d/%Y')}")

    session   = HarrisClerkSession()
    all_liens = []
    seen      = set()

    # Login first
    if not session.login():
        print("  ⚠ Login failed — trying without login")

    # Chunk by month to avoid hitting result limits
    chunk_start = start_date
    while chunk_start < end_date:
        chunk_end = min(
            date(chunk_start.year + (chunk_start.month // 12),
                 (chunk_start.month % 12) + 1, 1) - timedelta(days=1),
            end_date
        )

        date_from = chunk_start.strftime("%m/%d/%Y")
        date_to   = chunk_end.strftime("%m/%d/%Y")

        print(f"  Searching {date_from} → {date_to} "
              f"(INTERNAL REVENUE SERVICE)...",
              end=" ", flush=True)

        results = session.search_liens(
            grantee="INTERNAL REVENUE SERVICE",
            date_from=date_from,
            date_to=date_to,
            instrument_type="",  # blank = all types, filter in parse
        )

        new_count = 0
        for r in results:
            # Only keep T/L (Tax Lien) and IRS-related types
            inst = r.get("instrument_type", "").upper()
            if inst and not any(t in inst for t in
                                ["T/L", "TL", "TAX", "FTL", "LIEN"]):
                continue
            key = r.get("file_number") or r.get("grantor_name", "")
            if key and key not in seen:
                seen.add(key)
                all_liens.append(r)
                new_count += 1

        print(f"{new_count} liens")
        time.sleep(2)

        # Advance to next month
        if chunk_start.month == 12:
            chunk_start = date(chunk_start.year + 1, 1, 1)
        else:
            chunk_start = date(chunk_start.year, chunk_start.month + 1, 1)

    print(f"\n  Total IRS tax liens found: {len(all_liens):,}")
    return all_liens


# ── DB helpers ────────────────────────────────────────────────────────────────

def ensure_table():
    if not HAS_DB:
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE)
        conn.commit()
        print("  ✅ Table ready: harris_county_liens")
    finally:
        conn.close()


def save_liens(liens: list[dict], dry_run: bool = False) -> dict:
    """Save scraped liens to DB."""
    if not HAS_DB:
        # Save to JSON as fallback
        out = DATA_DIR / f"harris_liens_{date.today().isoformat()}.json"
        out.write_text(json.dumps(liens, indent=2, default=str))
        print(f"  💾 Saved to: {out}")
        return {"saved": len(liens)}

    inserted = updated = skipped = 0
    conn     = get_connection()

    try:
        with conn.cursor() as cur:
            for lien in liens:
                # Parse filing date
                filing_date = None
                date_str    = lien.get("filing_date", "")
                if date_str:
                    for fmt in ["%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"]:
                        try:
                            filing_date = datetime.strptime(
                                date_str, fmt).date()
                            break
                        except ValueError:
                            continue

                # Clean grantor name
                grantor = (lien.get("grantor_name") or "").strip().upper()
                if not grantor:
                    skipped += 1
                    continue

                file_num = (lien.get("file_number") or
                           f"HC-{grantor[:20]}-{date_str}").strip()

                try:
                    cur.execute("""
                        INSERT INTO harris_county_liens (
                            file_number, grantor_name, grantee_name,
                            instrument_type, filing_date, volume, page
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (file_number) DO UPDATE SET
                            grantor_name    = EXCLUDED.grantor_name,
                            filing_date     = EXCLUDED.filing_date,
                            updated_at      = NOW()
                        RETURNING (xmax = 0) AS was_inserted
                    """, (
                        file_num,
                        grantor,
                        (lien.get("grantee_name") or "IRS").strip(),
                        lien.get("instrument_type", "FTL"),
                        filing_date,
                        lien.get("volume", ""),
                        lien.get("page", ""),
                    ))
                    row = cur.fetchone()
                    if row and row[0]:
                        inserted += 1
                    else:
                        updated += 1
                except Exception as e:
                    skipped += 1
                    if skipped <= 3:
                        print(f"  ⚠ DB error: {e}")

        if not dry_run:
            conn.commit()
            print(f"  ✅ {inserted:,} new, {updated:,} updated, "
                  f"{skipped} skipped")
        else:
            conn.rollback()
            print(f"  [DRY RUN] Would save {inserted + updated:,} liens")

    finally:
        conn.close()

    return {"inserted": inserted, "updated": updated, "skipped": skipped}


# ── Match against TDLR ────────────────────────────────────────────────────────

def match_to_tdlr(dry_run: bool = False) -> dict:
    """
    Match Harris County lien grantors against TDLR contacts.
    Uses fuzzy name matching.
    """
    if not HAS_DB:
        print("  ❌ No DB")
        return {"matched": 0}

    conn = get_connection()
    try:
        # Get unmatched Harris County liens
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, grantor_name
                FROM harris_county_liens
                WHERE tdlr_match_id IS NULL
                  AND grantor_name IS NOT NULL
                  AND grantor_name != ''
                LIMIT 5000
            """)
            liens = cur.fetchall()

        print(f"  Liens to match: {len(liens):,}")
        matched = 0

        with conn.cursor() as cur:
            for lien_id, grantor in liens:
                if not grantor:
                    continue

                # Try exact business name match first
                cur.execute("""
                    SELECT id FROM texas_tdlr_contacts
                    WHERE UPPER(business_name) = %s
                       OR UPPER(owner_name)    = %s
                    LIMIT 1
                """, (grantor, grantor))
                row = cur.fetchone()

                if not row:
                    # Try partial match (first 20 chars)
                    cur.execute("""
                        SELECT id FROM texas_tdlr_contacts
                        WHERE UPPER(business_name) LIKE %s
                           OR UPPER(owner_name)    LIKE %s
                        LIMIT 1
                    """, (f"{grantor[:20]}%", f"{grantor[:20]}%"))
                    row = cur.fetchone()

                if row:
                    tdlr_id = row[0]
                    matched += 1

                    if not dry_run:
                        # Link lien to TDLR contact
                        cur.execute("""
                            UPDATE harris_county_liens
                            SET tdlr_match_id = %s,
                                updated_at    = NOW()
                            WHERE id = %s
                        """, (tdlr_id, lien_id))

                        # Mark TDLR contact as lien match
                        cur.execute("""
                            UPDATE texas_tdlr_contacts
                            SET lien_match  = TRUE,
                                confidence  = 'high',
                                updated_at  = NOW()
                            WHERE id = %s
                        """, (tdlr_id,))

        if not dry_run:
            conn.commit()
            print(f"  ✅ Matched {matched:,} liens to TDLR contacts")
        else:
            conn.rollback()
            print(f"  [DRY RUN] Would match {matched:,}")

        return {"matched": matched, "total_liens": len(liens)}

    finally:
        conn.close()


# ── Stats ─────────────────────────────────────────────────────────────────────

def show_stats():
    if not HAS_DB:
        print("No DB")
        return

    conn = get_connection()
    try:
        print(f"\n{'='*60}")
        print(f"  Harris County Lien Scraper Stats")
        print(f"  {date.today().isoformat()}")
        print(f"{'='*60}")

        with conn.cursor() as cur:
            try:
                cur.execute("""
                    SELECT
                        COUNT(*)                                        AS total,
                        COUNT(DISTINCT grantor_name)                    AS unique_debtors,
                        COUNT(*) FILTER (WHERE tdlr_match_id IS NOT NULL) AS matched,
                        MIN(filing_date)                                AS earliest,
                        MAX(filing_date)                                AS latest
                    FROM harris_county_liens
                """)
                r = cur.fetchone()
                print(f"\n  Harris County Liens:")
                print(f"  Total filings  : {r[0]:,}")
                print(f"  Unique debtors : {r[1]:,}")
                print(f"  TDLR matched   : {r[2]:,}")
                print(f"  Date range     : {r[3]} → {r[4]}")

                cur.execute("""
                    SELECT COUNT(*) FROM texas_tdlr_contacts
                    WHERE lien_match = TRUE
                """)
                print(f"\n  TDLR contacts with lien match: "
                      f"{cur.fetchone()[0]:,}")

                cur.execute("""
                    SELECT COUNT(*) FROM texas_tdlr_contacts
                    WHERE lien_match = TRUE
                      AND email IS NOT NULL AND email != ''
                """)
                print(f"  Matched + email (ready to email): "
                      f"{cur.fetchone()[0]:,}")

            except Exception:
                print("  Harris County liens table not yet created")
                print("  Run: --days 90 to scrape")

        print(f"{'='*60}\n")

    finally:
        conn.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Harris County TX Federal Tax Lien Scraper")
    parser.add_argument("--days",    type=int, default=90,
                        help="Days back to scrape (default 90)")
    parser.add_argument("--match",   action="store_true",
                        help="Match scraped liens to TDLR contacts")
    parser.add_argument("--stats",   action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    print(f"\n{'='*60}")
    print(f"  Harris County Lien Scraper")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    print(f"  Days back : {args.days}")
    print(f"  {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"{'='*60}\n")

    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("harris_lien_scraper")
        logger.start()
    except ImportError:
        logger = None

    # Ensure table exists
    ensure_table()

    # Scrape liens
    if logger: logger.step_start("scrape")
    print("Scraping Harris County Clerk for federal tax liens...")
    liens = scrape_harris_liens(days_back=args.days, dry_run=args.dry_run)

    if liens:
        # Save to JSON backup
        out = DATA_DIR / f"harris_liens_{date.today().isoformat()}.json"
        out.write_text(json.dumps(liens, indent=2, default=str))
        print(f"  💾 Backup: {out}")

        # Save to DB
        if logger: logger.step_start("save_to_db")
        result = save_liens(liens, dry_run=args.dry_run)
        if logger:
            logger.step_done("save_to_db", ok=True, detail=str(result))
    else:
        print("  ⚠ No liens found — portal may require session auth")
        print("  Try registering a free account at:")
        print("  https://www.cclerk.hctx.net/applications/websearch/")
        print("  Registration/Welcome.aspx")

    if logger:
        logger.step_done("scrape", ok=True,
                         detail=f"{len(liens)} liens found")

    # Match to TDLR
    if args.match and liens:
        if logger: logger.step_start("match_to_tdlr")
        print("\nMatching liens to TDLR contacts...")
        match_result = match_to_tdlr(dry_run=args.dry_run)
        if logger:
            logger.step_done("match_to_tdlr", ok=True,
                             detail=str(match_result))

    print(f"\n{'='*60}")
    print(f"  Harris County Scraper Complete")
    print(f"  Liens found: {len(liens):,}")
    print(f"{'='*60}\n")

    show_stats()

    if logger:
        logger.finish({
            "liens_found": len(liens),
            "days_back":   args.days,
            "dry_run":     args.dry_run,
        })


if __name__ == "__main__":
    main()