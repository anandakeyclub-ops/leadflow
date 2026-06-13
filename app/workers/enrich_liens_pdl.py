"""
enrich_liens_pdl.py
===================
Enriches lien contacts using People Data Labs (PDL) API.
Free tier: 100 lookups/month, no credit card required.

Sign up: https://www.peopledatalabs.com/
Get API key: dashboard.peopledatalabs.com → API Keys

PDL can match by:
- Person name + location (individuals)
- Company name + location (businesses)

Returns: email, phone, LinkedIn, job title, company

Setup:
  Add to .env:
    PDL_API_KEY=your_api_key_here

Usage:
  python -m app.workers.enrich_liens_pdl --limit 50
  python -m app.workers.enrich_liens_pdl --limit 50 --type individual
  python -m app.workers.enrich_liens_pdl --limit 50 --type business
  python -m app.workers.enrich_liens_pdl --dry-run
"""
from __future__ import annotations
import argparse, json, os, time
from datetime import datetime
from pathlib import Path
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv()

try:
    from app.core.db import get_connection
except ImportError:
    import sys; sys.exit("Run from leadflow directory")

PDL_BASE = "https://api.peopledatalabs.com/v5"

BUSINESS_INDICATORS = {
    "LLC", "INC", "CORP", "LTD", "CO.", "COMPANY", "ENTERPRISES",
    "GROUP", "SERVICES", "SOLUTIONS", "HOLDINGS", "PARTNERS",
    "PROPERTIES", "REALTY", "CONSTRUCTION", "CONTRACTING",
    "ASSOCIATION", "ASSOC", "FOUNDATION", "INVESTMENTS", "MGMT",
    "MANAGEMENT", "RESTAURANTS", "RESTAURANT", "SALON", "STUDIO",
    "CONSULTANTS", "CONSULTING", "INDUSTRIES", "INTERNATIONAL",
    "SYSTEMS", "TECHNOLOGIES", "TECH", "FOODS", "FOOD", "AUTO",
    "HOMEOWNERS", "HOA", "BUILDERS", "DEVELOPMENT", "ELECTRIC",
    "PLUMBING", "ROOFING", "LANDSCAPING", "CLEANING", "CARE",
}

# Suffixes to strip from individual names before PDL search
NAME_SUFFIXES = {
    "TR", "TRUST", "JR", "SR", "II", "III", "IV",
    "MD", "DDS", "ESQ", "PHD", "CPA",
}

SKIP_NAMES = {
    "INTERNAL REVENUE SERVICE", "IRS", "FLORIDA DEPARTMENT",
    "STATE OF FLORIDA", "UNITED STATES", "DEPARTMENT OF REVENUE",
}

COUNTY_CITIES = {
    "Miami-Dade": "Miami", "Hillsborough": "Tampa",
    "Pinellas": "St Petersburg", "Duval": "Jacksonville",
    "Polk": "Lakeland", "Sarasota": "Sarasota",
    "Manatee": "Bradenton", "Martin": "Stuart",
    "Lake": "Tavares", "Pasco": "New Port Richey",
    "Osceola": "Kissimmee", "Palm Beach": "West Palm Beach",
}

def is_business(name: str) -> bool:
    n = name.upper()
    return any(ind in n for ind in BUSINESS_INDICATORS)

def parse_person_name(name: str) -> tuple[str, str]:
    """Split debtor name into first/last. Lien format often 'Last First Middle'."""
    # Strip known suffixes
    parts = [p.rstrip(".,") for p in name.strip().split()]
    parts = [p for p in parts if p.upper() not in NAME_SUFFIXES]
    # Strip single-letter middle initials
    parts = [p for p in parts if not (len(p) == 1 and p.isalpha())]

    if len(parts) == 0:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    if len(parts) == 2:
        # FL liens often "Last First"
        return parts[1], parts[0]
    # 3+ parts: "Last First Middle" → first=First, last=Last
    return parts[1], parts[0]

def pdl_person_search(api_key: str, first: str, last: str,
                       city: str, state: str = "FL") -> dict:
    """Search PDL for an individual by name + location."""
    try:
        params = {
            "api_key": api_key,
            "first_name": first.lower(),
            "last_name": last.lower(),
            "locality": city.lower(),
            "region": state.lower(),
            "country": "united states",
            "pretty": True,
        }
        r = requests.get(
            f"{PDL_BASE}/person/enrich",
            params=params,
            timeout=10
        )
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 404:
            return {}  # no match
        elif r.status_code == 402:
            print("    PDL: credit limit reached")
            return {"_limit": True}
        else:
            print(f"    PDL person error: {r.status_code} {r.text[:100]}")
            return {}
    except Exception as e:
        print(f"    PDL person exception: {e}")
        return {}

def pdl_company_search(api_key: str, company: str,
                        city: str, state: str = "FL") -> dict:
    """Search PDL for a company by name + location."""
    try:
        params = {
            "api_key": api_key,
            "name": company,
            "locality": city.lower(),
            "region": state.lower(),
            "pretty": True,
        }
        r = requests.get(
            f"{PDL_BASE}/company/enrich",
            params=params,
            timeout=10
        )
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 404:
            return {}
        elif r.status_code == 402:
            print("    PDL: credit limit reached")
            return {"_limit": True}
        else:
            return {}
    except Exception as e:
        print(f"    PDL company exception: {e}")
        return {}

def extract_person_contact(data: dict) -> dict:
    """Extract email/phone from PDL person response."""
    if not data or data.get("_limit"):
        return data if data.get("_limit") else {}

    result = {}

    # Emails — PDL returns list, take first work/personal
    emails = data.get("emails", [])
    for e in emails:
        addr = e.get("address", "")
        if addr and "@" in addr:
            result["email"] = addr
            break

    # Phone
    phones = data.get("phone_numbers", [])
    if phones:
        result["phone"] = phones[0]

    # LinkedIn
    profiles = data.get("profiles", [])
    for p in profiles:
        if "linkedin" in p.get("network", ""):
            result["linkedin"] = p.get("url", "")
            break

    # Job title + company (useful context)
    result["job_title"]   = data.get("job_title", "")
    result["job_company"] = data.get("job_company_name", "")
    result["full_name"]   = (
        f"{data.get('first_name','')} {data.get('last_name','')}".strip()
    )
    result["city"]  = data.get("location_locality", "")
    result["state"] = data.get("location_region", "")

    return result

def extract_company_contact(data: dict) -> dict:
    """Extract email/phone from PDL company response."""
    if not data or data.get("_limit"):
        return data if data.get("_limit") else {}

    result = {}
    result["website"]   = data.get("website", "")
    result["linkedin"]  = data.get("linkedin_url", "")
    result["size"]      = data.get("size", "")
    result["industry"]  = data.get("industry", "")
    result["founded"]   = data.get("founded", "")

    # PDL company doesn't always have direct email
    # but website gives us something to scrape
    return result

def ensure_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lien_pdl_contacts (
            id                  SERIAL PRIMARY KEY,
            normalized_lien_id  INTEGER REFERENCES normalized_liens(id)
                                ON DELETE CASCADE UNIQUE,
            debtor_name         TEXT,
            record_type         TEXT,
            email               TEXT,
            phone               TEXT,
            full_name           TEXT,
            job_title           TEXT,
            job_company         TEXT,
            linkedin_url        TEXT,
            website             TEXT,
            industry            TEXT,
            pdl_status          TEXT,
            raw_data            JSONB,
            searched_at         TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_pdl_lien
        ON lien_pdl_contacts(normalized_lien_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_pdl_email
        ON lien_pdl_contacts(email) WHERE email IS NOT NULL
    """)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int, default=50,
                        help="Max lookups (free tier: 100/month)")
    parser.add_argument("--type",    default="all",
                        choices=["all", "individual", "business"])
    parser.add_argument("--county",  default=None)
    parser.add_argument("--force",   action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be searched without calling API")
    args = parser.parse_args()

    api_key = os.getenv("PDL_API_KEY", "")
    if not api_key and not args.dry_run:
        print("ERROR: Set PDL_API_KEY in .env")
        print("Sign up free: https://www.peopledatalabs.com/")
        return

    conn = get_connection(); conn.autocommit = False

    try:
        with conn.cursor() as cur:
            ensure_table(cur)
            conn.commit()

            already_done = "" if args.force else \
                "AND nl.id NOT IN (SELECT normalized_lien_id FROM lien_pdl_contacts)"
            county_filter = f"AND c.county_name ILIKE '%{args.county}%'" \
                if args.county else ""

            cur.execute(f"""
                SELECT DISTINCT ON (UPPER(nl.debtor_name), c.county_name)
                    nl.id, nl.debtor_name, c.county_name
                FROM normalized_liens nl
                JOIN counties c ON c.id = nl.county_id
                WHERE nl.debtor_name IS NOT NULL
                AND nl.debtor_name != ''
                -- Skip records starting with numbers (addresses, street names)
                AND nl.debtor_name !~ '^[0-9]'
                -- Skip land trusts, revocable trusts, addresses
                AND nl.debtor_name NOT ILIKE '%land trust%'
                AND nl.debtor_name NOT ILIKE '%revocable trust%'
                AND nl.debtor_name NOT ILIKE '%living trust%'
                AND nl.debtor_name NOT ILIKE '% trust %'
                AND nl.debtor_name NOT ILIKE '%homestead%'
                AND nl.debtor_name NOT ILIKE '%
%'
                -- Skip very short names
                AND LENGTH(nl.debtor_name) > 5
                AND nl.id NOT IN (
                    SELECT lien_id FROM lien_dbpr_contacts
                    WHERE email IS NOT NULL
                )
                {already_done}
                {county_filter}
                ORDER BY UPPER(nl.debtor_name), c.county_name,
                         nl.filed_date DESC NULLS LAST
                LIMIT {args.limit}
            """)
            liens = cur.fetchall()

        print(f"\n[PDL Enrichment]")
        print(f"  API key   : {'set' if api_key else 'NOT SET'}")
        print(f"  Mode      : {'DRY RUN' if args.dry_run else 'LIVE'}")
        print(f"  Liens     : {len(liens)}")
        print(f"  Type      : {args.type}")
        print(f"  Free tier : 100 lookups/month\n")

        found_email = found_phone = credit_limit = 0

        for i, (lien_id, debtor, county) in enumerate(liens):
            if any(sw in debtor.upper() for sw in SKIP_NAMES):
                continue
            # Skip HOAs and associations - never in PDL
            if any(x in debtor.upper() for x in
                   ["HOMEOWNERS", "HOA", "CONDO ASSOC", "PROPERTY OWNERS"]):
                continue

            city = COUNTY_CITIES.get(county, county.replace(" County", ""))
            biz  = is_business(debtor)

            # Filter by type
            if args.type == "individual" and biz:
                continue
            if args.type == "business" and not biz:
                continue

            record_type = "business" if biz else "individual"
            print(f"  [{i+1}/{len(liens)}] [{record_type}] {debtor[:45]} ({city})")

            if args.dry_run:
                if biz:
                    print(f"    Would search company: {debtor!r} in {city}, FL")
                else:
                    first, last = parse_person_name(debtor)
                    print(f"    Would search person: {first!r} {last!r} in {city}, FL")
                continue

            # Make PDL API call
            raw_data = {}
            contact  = {}

            if biz:
                raw_data = pdl_company_search(api_key, debtor, city)
                contact  = extract_company_contact(raw_data)
            else:
                first, last = parse_person_name(debtor)
                if not last:
                    continue
                raw_data = pdl_person_search(api_key, first, last, city)
                contact  = extract_person_contact(raw_data)

            # Check credit limit
            if contact.get("_limit"):
                credit_limit += 1
                print(f"    ⚠ Credit limit reached — stopping")
                break

            time.sleep(0.5)  # rate limit

            # Store result
            with conn.cursor() as cur:
                if args.force:
                    cur.execute(
                        "DELETE FROM lien_pdl_contacts WHERE normalized_lien_id=%s",
                        (lien_id,))
                cur.execute("""
                    INSERT INTO lien_pdl_contacts
                        (normalized_lien_id, debtor_name, record_type,
                         email, phone, full_name, job_title, job_company,
                         linkedin_url, website, industry,
                         pdl_status, raw_data)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (normalized_lien_id) DO UPDATE SET
                        email    = COALESCE(EXCLUDED.email, lien_pdl_contacts.email),
                        phone    = COALESCE(EXCLUDED.phone, lien_pdl_contacts.phone),
                        pdl_status = EXCLUDED.pdl_status,
                        searched_at = NOW()
                """, (
                    lien_id, debtor, record_type,
                    contact.get("email"),
                    contact.get("phone"),
                    contact.get("full_name"),
                    contact.get("job_title"),
                    contact.get("job_company"),
                    contact.get("linkedin"),
                    contact.get("website"),
                    contact.get("industry"),
                    "found" if (contact.get("email") or contact.get("phone")) else "not_found",
                    json.dumps(raw_data, default=str)
                ))
            conn.commit()

            if contact.get("email"):
                found_email += 1
                print(f"    ✓ email: {contact['email']}")
                if contact.get("job_title"):
                    print(f"      title: {contact['job_title']} @ {contact.get('job_company','')}")
            elif contact.get("phone"):
                found_phone += 1
                print(f"    ☎ phone: {contact['phone']} (no email)")
            elif contact.get("website"):
                print(f"    🌐 website: {contact['website']} (no email)")
            else:
                print(f"    ✗ not found in PDL")

        print(f"\n{'='*60}")
        print(f"  Processed     : {len(liens)}")
        print(f"  Emails found  : {found_email}")
        print(f"  Phones only   : {found_phone}")
        print(f"  Email rate    : {found_email/max(len(liens),1)*100:.1f}%")
        if credit_limit:
            print(f"  ⚠ Hit credit limit after {i} lookups")

        # Summary from DB
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(email) as with_email,
                    COUNT(phone) as with_phone
                FROM lien_pdl_contacts
            """)
            row = cur.fetchone()
            print(f"\n  PDL contacts in DB:")
            print(f"    Total searched : {row[0]}")
            print(f"    With email     : {row[1]}")
            print(f"    With phone     : {row[2]}")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        import traceback; traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    main()