"""
enrich_liens_from_sunbiz.py
===========================
Enriches normalized_liens with contact data from Florida Sunbiz
(search.sunbiz.org) — the official FL Division of Corporations registry.

For each unmatched business lien:
1. Search Sunbiz by debtor name
2. Find best matching entity
3. Extract: registered agent name, principal address, officer names/addresses
4. Store in lien_sunbiz_contacts table

Sunbiz gives us: registered agent, principal address, officer names.
No email directly, but gives us a name + address for skip tracing.

Usage:
  python -m app.workers.enrich_liens_from_sunbiz
  python -m app.workers.enrich_liens_from_sunbiz --limit 100
  python -m app.workers.enrich_liens_from_sunbiz --force
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

SEARCH_URL = "https://search.sunbiz.org/Inquiry/corporationsearch/SearchResults"
DETAIL_URL = "https://search.sunbiz.org/Inquiry/corporationsearch/GetListOfBusinessEntities"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Referer": "https://search.sunbiz.org/",
})

SKIP_WORDS = {
    "INTERNAL REVENUE SERVICE", "IRS", "FLORIDA DEPARTMENT OF REVENUE",
    "FLORIDA DEPT OF REVENUE", "DEPARTMENT OF REVENUE", "STATE OF FLORIDA",
    "UNITED STATES", "US TREASURY", "OSCEOLA COUNTY", "PASCO COUNTY",
    "HILLSBOROUGH COUNTY", "PINELLAS COUNTY", "MANATEE COUNTY",
}

BUSINESS_INDICATORS = {
    "LLC", "INC", "CORP", "LTD", "CO.", "COMPANY", "ENTERPRISES",
    "GROUP", "SERVICES", "SOLUTIONS", "HOLDINGS", "PARTNERS",
    "PROPERTIES", "REALTY", "CONSTRUCTION", "CONTRACTING",
}

def is_business(name: str) -> bool:
    n = name.upper()
    return any(ind in n for ind in BUSINESS_INDICATORS)

def clean_name(name: str) -> str:
    """Strip lien suffixes for better search match."""
    name = re.sub(r'\s+(LLC|INC|CORP|LTD|CO\.?)[\s,]*$', '', name, flags=re.I)
    return name.strip()

def search_sunbiz(name: str) -> list[dict]:
    """Search Sunbiz by entity name, return list of matches."""
    try:
        r = SESSION.get(SEARCH_URL, params={
            "inquiryType": "EntityName",
            "inquiryDirectionType": "ForwardList",
            "searchTerm": name,
            "listNameOrder": name.upper(),
        }, timeout=15)
        if r.status_code != 200:
            return []

        soup = BeautifulSoup(r.text, "lxml")
        results = []

        # Results table has class 'searchResultGrid'
        table = soup.find("table", {"class": "searchResultGrid"})
        if not table:
            return []

        for row in table.find_all("tr")[1:]:  # skip header
            cols = row.find_all("td")
            if len(cols) < 3:
                continue
            link = cols[0].find("a")
            entity_name = cols[0].get_text(strip=True)
            doc_num     = cols[1].get_text(strip=True)
            status      = cols[2].get_text(strip=True)
            detail_url  = f"https://search.sunbiz.org{link['href']}" if link else ""
            results.append({
                "name": entity_name,
                "doc_num": doc_num,
                "status": status,
                "detail_url": detail_url,
            })

        return results[:5]  # top 5 matches
    except Exception as e:
        print(f"    Search error: {e}")
        return []

def get_detail(detail_url: str) -> dict:
    """Fetch entity detail page and extract contact info."""
    try:
        r = SESSION.get(detail_url, timeout=15)
        if r.status_code != 200:
            return {}

        soup = BeautifulSoup(r.text, "lxml")
        data = {}

        # Principal address
        pa = soup.find("span", string=re.compile("Principal Address", re.I))
        if pa:
            addr_div = pa.find_next("span", {"class": "p"})
            if addr_div:
                data["principal_address"] = addr_div.get_text(" ", strip=True)

        # Registered agent
        ra = soup.find("span", string=re.compile("Registered Agent", re.I))
        if ra:
            ra_name = ra.find_next("span", {"class": "p"})
            if ra_name:
                data["registered_agent_name"] = ra_name.get_text(" ", strip=True)
            ra_addr = ra.find_next("span", string=re.compile("Address", re.I))
            if ra_addr:
                addr = ra_addr.find_next("span", {"class": "p"})
                if addr:
                    data["registered_agent_address"] = addr.get_text(" ", strip=True)

        # Officers/Directors
        officers = []
        for title_span in soup.find_all("span", string=re.compile("Officer/Director", re.I)):
            officer = {}
            name_span = title_span.find_next("span", {"class": "p"})
            if name_span:
                officer["name"] = name_span.get_text(" ", strip=True)
            addr_span = name_span.find_next("span", {"class": "p"}) if name_span else None
            if addr_span:
                officer["address"] = addr_span.get_text(" ", strip=True)
            if officer.get("name"):
                officers.append(officer)
        if officers:
            data["officers"] = officers

        # Status
        status_span = soup.find("span", string=re.compile("^Status$", re.I))
        if status_span:
            val = status_span.find_next("span")
            if val:
                data["status"] = val.get_text(strip=True)

        return data
    except Exception as e:
        print(f"    Detail error: {e}")
        return {}

def best_match(debtor: str, results: list[dict]) -> Optional[dict]:
    """Find best matching entity from search results."""
    if not results:
        return None

    debtor_upper = debtor.upper().strip()

    # Exact match first
    for r in results:
        if r["name"].upper().strip() == debtor_upper:
            return r

    # Active status preferred
    active = [r for r in results if r["status"].upper() in ("ACTIVE", "A")]

    # Name similarity — starts with same words
    debtor_words = set(debtor_upper.split())
    for r in (active or results):
        result_words = set(r["name"].upper().split())
        overlap = len(debtor_words & result_words)
        if overlap >= 2 or (len(debtor_words) == 1 and overlap == 1):
            return r

    # Return first active result if any
    return active[0] if active else results[0]

def ensure_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lien_sunbiz_contacts (
            id                      SERIAL PRIMARY KEY,
            normalized_lien_id      INTEGER REFERENCES normalized_liens(id) ON DELETE CASCADE,
            debtor_name             TEXT,
            entity_name             TEXT,
            document_number         TEXT,
            entity_status           TEXT,
            principal_address       TEXT,
            registered_agent_name   TEXT,
            registered_agent_address TEXT,
            officers                JSONB,
            detail_url              TEXT,
            matched_at              TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_sunbiz_lien_id
        ON lien_sunbiz_contacts(normalized_lien_id)
    """)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true",
                        help="Re-enrich already processed liens")
    args = parser.parse_args()

    conn = get_connection(); conn.autocommit = False

    try:
        with conn.cursor() as cur:
            ensure_table(cur)
            conn.commit()

            # Get unmatched business liens
            already_done = "" if args.force else """
                AND nl.id NOT IN (SELECT normalized_lien_id FROM lien_sunbiz_contacts)
            """
            # Also skip liens already matched by DBPR
            dbpr_done = """
                AND nl.id NOT IN (
                    SELECT lien_id FROM lien_dbpr_contacts
                    WHERE email IS NOT NULL
                )
            """ if not args.force else ""

            limit_clause = f"LIMIT {args.limit}" if args.limit else ""

            cur.execute(f"""
                SELECT nl.id, nl.debtor_name, c.county_name
                FROM normalized_liens nl
                JOIN counties c ON c.id = nl.county_id
                WHERE nl.debtor_name IS NOT NULL
                AND nl.debtor_name != ''
                {already_done}
                {dbpr_done}
                ORDER BY nl.filed_date DESC NULLS LAST
                {limit_clause}
            """)
            liens = cur.fetchall()

        print(f"\n[Sunbiz Enrichment] {len(liens)} liens to process")

        matched = skipped = errors = 0

        for i, (lien_id, debtor, county) in enumerate(liens):
            # Skip non-businesses and government creditors
            if any(sw in debtor.upper() for sw in SKIP_WORDS):
                skipped += 1
                continue
            if not is_business(debtor):
                skipped += 1  # individuals — skip for now
                continue

            search_name = clean_name(debtor)
            results = search_sunbiz(search_name)
            time.sleep(0.5)  # be polite to Sunbiz

            match = best_match(debtor, results)
            if not match:
                skipped += 1
                if i % 50 == 0:
                    print(f"  [{i+1}/{len(liens)}] No match: {debtor[:50]}")
                continue

            # Get detail page
            detail = {}
            if match.get("detail_url"):
                detail = get_detail(match["detail_url"])
                time.sleep(0.5)

            with conn.cursor() as cur:
                if args.force:
                    cur.execute(
                        "DELETE FROM lien_sunbiz_contacts WHERE normalized_lien_id=%s",
                        (lien_id,))
                cur.execute("""
                    INSERT INTO lien_sunbiz_contacts
                        (normalized_lien_id, debtor_name, entity_name,
                         document_number, entity_status, principal_address,
                         registered_agent_name, registered_agent_address,
                         officers, detail_url)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                """, (
                    lien_id, debtor, match["name"], match["doc_num"],
                    match["status"],
                    detail.get("principal_address"),
                    detail.get("registered_agent_name"),
                    detail.get("registered_agent_address"),
                    json.dumps(detail.get("officers", [])),
                    match["detail_url"],
                ))
            conn.commit()
            matched += 1

            if i % 20 == 0 or matched % 10 == 0:
                print(f"  [{i+1}/{len(liens)}] ✓ {debtor[:40]} → {match['name'][:40]} ({match['status']})")

        print(f"\n{'='*60}")
        print(f"  Processed : {len(liens)}")
        print(f"  Matched   : {matched}")
        print(f"  Skipped   : {skipped}")
        print(f"  Errors    : {errors}")

        # Export summary
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE entity_status ILIKE '%active%') as active,
                    COUNT(*) FILTER (WHERE registered_agent_name IS NOT NULL) as has_agent,
                    COUNT(*) FILTER (WHERE principal_address IS NOT NULL) as has_address
                FROM lien_sunbiz_contacts
            """)
            stats = cur.fetchone()
            print(f"\n  Sunbiz contacts DB:")
            print(f"    Total matched : {stats[0]}")
            print(f"    Active status : {stats[1]}")
            print(f"    Has reg agent : {stats[2]}")
            print(f"    Has address   : {stats[3]}")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        import traceback; traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    main()