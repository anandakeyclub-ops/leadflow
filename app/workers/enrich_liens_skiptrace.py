"""
enrich_liens_skiptrace.py
=========================
Free skip tracing for individual lien debtors using public people-search sites.

Sources:
1. FastPeopleSearch.com — free, no signup
2. TruePeopleSearch.com — free, no signup

Best for: individual debtors (not businesses)
Skips: LLCs, Corps, government entities

Usage:
  python -m app.workers.enrich_liens_skiptrace --limit 100
  python -m app.workers.enrich_liens_skiptrace --limit 100 --state FL
"""
from __future__ import annotations
import argparse, json, re, time
from datetime import datetime
from pathlib import Path
from typing import Optional
import requests
from bs4 import BeautifulSoup

try:
    from app.core.db import get_connection
except ImportError:
    import sys; sys.exit("Run from leadflow directory")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
})

EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,7}\b')
PHONE_RE = re.compile(r'\(?\d{3}\)?[\s\-\.]\d{3}[\s\-\.]\d{4}')

BUSINESS_INDICATORS = {
    "LLC", "INC", "CORP", "LTD", "CO.", "COMPANY", "ENTERPRISES",
    "GROUP", "SERVICES", "SOLUTIONS", "HOLDINGS", "PARTNERS",
    "PROPERTIES", "REALTY", "CONSTRUCTION", "TRUST", "TR",
    "ASSOCIATION", "ASSOC", "FOUNDATION", "FUND", "INVESTMENTS",
}

SKIP_NAMES = {
    "INTERNAL REVENUE SERVICE", "IRS", "FLORIDA DEPARTMENT",
    "STATE OF FLORIDA", "UNITED STATES", "DEPARTMENT OF REVENUE",
}

def is_individual(name: str) -> bool:
    n = name.upper()
    if any(sw in n for sw in SKIP_NAMES):
        return False
    if any(ind in n for ind in BUSINESS_INDICATORS):
        return False
    # Likely individual if 2 words, no business suffix
    parts = name.strip().split()
    return len(parts) >= 2

def parse_name(name: str) -> tuple[str, str]:
    """Split 'Last First' or 'First Last' into first, last."""
    parts = name.strip().split()
    if len(parts) == 1:
        return parts[0], ""
    # Duval/Martin format is often "Last First" 
    # Try to detect — if last part looks like a first name
    common_first = {"john","mary","james","robert","michael","william","david",
                    "richard","joseph","thomas","charles","patricia","jennifer",
                    "linda","barbara","elizabeth","susan","jessica","sarah",
                    "karen","lisa","nancy","betty","sandra","margaret","ashley",
                    "dorothy","kimberly","emily","donna","carol","michelle",
                    "amanda","melissa","deborah","stephanie","rebecca","sharon",
                    "laura","cynthia","kathleen","amy","angela","shirley",
                    "anna","brenda","pamela","emma","nicole","helen","samantha",
                    "katherine","christine","debra","rachel","carolyn","janet",
                    "catherine","maria","heather","diane","julie","joyce",
                    "victoria","kelly","christina","joan","evelyn","lauren"}
    # If second word is a common first name, format is "Last First"
    if len(parts) >= 2 and parts[-1].lower() in common_first:
        return parts[-1], parts[0]
    # Default: first word = first name
    return parts[0], " ".join(parts[1:])

def fastpeoplesearch(first: str, last: str, state: str = "FL") -> dict:
    """Search FastPeopleSearch for individual contact info."""
    try:
        name_slug = f"{first}-{last}".lower().replace(" ", "-")
        url = f"https://www.fastpeoplesearch.com/name/{name_slug}_{state}"
        r = SESSION.get(url, timeout=12)
        if r.status_code != 200:
            return {}

        soup = BeautifulSoup(r.text, "lxml")
        result = {}

        # First result card
        card = soup.find("div", class_=re.compile("card|result|person"))
        if not card:
            # Try first data block
            card = soup.find("div", {"data-type": "person"})
        if not card:
            card = soup  # search whole page

        # Phone numbers
        phones = PHONE_RE.findall(card.get_text())
        if phones:
            result["phone"] = phones[0]

        # Email (sometimes shown)
        emails = EMAIL_RE.findall(card.get_text())
        if emails:
            result["email"] = emails[0]

        # Address
        addr_el = card.find(class_=re.compile("address|location"))
        if addr_el:
            result["address"] = addr_el.get_text(strip=True)

        # Age
        age_el = card.find(string=re.compile(r'Age\s*\d+'))
        if age_el:
            result["age"] = age_el.strip()

        return result
    except Exception as e:
        return {}

def truepeoplesearch(first: str, last: str, state: str = "FL") -> dict:
    """Search TruePeopleSearch as backup."""
    try:
        url = "https://www.truepeoplesearch.com/results"
        r = SESSION.get(url, params={
            "name": f"{first} {last}",
            "rid": "0x0",
            "state": state,
        }, timeout=12)
        if r.status_code != 200:
            return {}

        soup = BeautifulSoup(r.text, "lxml")
        result = {}

        card = soup.find("div", class_=re.compile("card|result"))
        if not card:
            return {}

        phones = PHONE_RE.findall(card.get_text())
        if phones:
            result["phone"] = phones[0]

        emails = EMAIL_RE.findall(card.get_text())
        if emails:
            result["email"] = emails[0]

        return result
    except Exception:
        return {}

def ensure_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lien_skiptrace_contacts (
            id                  SERIAL PRIMARY KEY,
            normalized_lien_id  INTEGER REFERENCES normalized_liens(id)
                                ON DELETE CASCADE UNIQUE,
            debtor_name         TEXT,
            first_name          TEXT,
            last_name           TEXT,
            email               TEXT,
            phone               TEXT,
            address             TEXT,
            age                 TEXT,
            source              TEXT,
            raw_data            JSONB,
            searched_at         TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_skiptrace_lien
        ON lien_skiptrace_contacts(normalized_lien_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_skiptrace_email
        ON lien_skiptrace_contacts(email) WHERE email IS NOT NULL
    """)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--state", default="FL")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    conn = get_connection(); conn.autocommit = False

    try:
        with conn.cursor() as cur:
            ensure_table(cur)
            conn.commit()

            already_done = "" if args.force else \
                "AND nl.id NOT IN (SELECT normalized_lien_id FROM lien_skiptrace_contacts)"
            
            cur.execute(f"""
                SELECT nl.id, nl.debtor_name, c.county_name
                FROM normalized_liens nl
                JOIN counties c ON c.id = nl.county_id
                WHERE nl.debtor_name IS NOT NULL
                AND nl.debtor_name != ''
                AND nl.id NOT IN (
                    SELECT lien_id FROM lien_dbpr_contacts WHERE email IS NOT NULL
                )
                {already_done}
                ORDER BY nl.filed_date DESC NULLS LAST
                LIMIT {args.limit}
            """)
            liens = cur.fetchall()

        # Filter to individuals only
        individuals = [
            (lid, name, county) for lid, name, county in liens
            if is_individual(name)
        ]
        businesses = len(liens) - len(individuals)

        print(f"\n[Skip Trace] {len(liens)} liens to check")
        print(f"  Individuals: {len(individuals)}")
        print(f"  Businesses skipped: {businesses}")

        found_email = found_phone = 0

        for i, (lien_id, debtor, county) in enumerate(individuals):
            first, last = parse_name(debtor)
            if not last:
                continue

            print(f"\n  [{i+1}/{len(individuals)}] {debtor} ({county})")

            # Try FastPeopleSearch first
            result = fastpeoplesearch(first, last, args.state)
            source = "fastpeoplesearch"
            time.sleep(2)

            # Fallback to TruePeopleSearch if no phone found
            if not result.get("phone") and not result.get("email"):
                result = truepeoplesearch(first, last, args.state)
                source = "truepeoplesearch"
                time.sleep(2)

            with conn.cursor() as cur:
                if args.force:
                    cur.execute(
                        "DELETE FROM lien_skiptrace_contacts WHERE normalized_lien_id=%s",
                        (lien_id,))
                cur.execute("""
                    INSERT INTO lien_skiptrace_contacts
                        (normalized_lien_id, debtor_name, first_name, last_name,
                         email, phone, address, age, source, raw_data)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (normalized_lien_id) DO UPDATE SET
                        email  = COALESCE(EXCLUDED.email, lien_skiptrace_contacts.email),
                        phone  = COALESCE(EXCLUDED.phone, lien_skiptrace_contacts.phone),
                        source = EXCLUDED.source,
                        searched_at = NOW()
                """, (
                    lien_id, debtor, first, last,
                    result.get("email"), result.get("phone"),
                    result.get("address"), result.get("age"),
                    source, json.dumps(result)
                ))
            conn.commit()

            if result.get("email"):
                found_email += 1
                print(f"    ✓ email: {result['email']}")
            if result.get("phone"):
                found_phone += 1
                print(f"    ☎ phone: {result['phone']}")
            if not result:
                print(f"    ✗ not found")

        print(f"\n{'='*60}")
        print(f"  Individuals processed : {len(individuals)}")
        print(f"  Emails found          : {found_email}")
        print(f"  Phones found          : {found_phone}")
        print(f"  Email rate            : {found_email/max(len(individuals),1)*100:.1f}%")
        print(f"  Phone rate            : {found_phone/max(len(individuals),1)*100:.1f}%")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        import traceback; traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    main()