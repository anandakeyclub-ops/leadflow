"""
enrich_property_appraiser.py
============================
Enriches matched leads using free public property records.

Primary source: Palm Beach County Property Appraiser (PAPA)
  Search: pbcpao.gov/MasterSearch/SearchResults?propertyType=RE&searchvalue=LAST+FIRST
  Auto-redirects to detail page. Confirmed response format:
    "Mailing Address Actions BROSEN ALICIA 19666 BLACK OLIVE LN BOCA RATON FL 33498 4828"

Secondary source: Florida Sunbiz (for LLC/Corp debtors)
  search.sunbiz.org → registered agent name + principal address

Tertiary source: Broward County Property Appraiser (BCPA)
  bcpa.net for Broward leads

Usage:
  python -m app.workers.enrich_property_appraiser
  python -m app.workers.enrich_property_appraiser --force
  python -m app.workers.enrich_property_appraiser --dry-run --limit 10
  python -m app.workers.enrich_property_appraiser --county palm_beach
"""
from __future__ import annotations

import argparse
import re
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.db import get_connection

# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://search.sunbiz.org/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
})
_retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
SESSION.mount("https://", HTTPAdapter(max_retries=_retry))
SESSION.mount("http://",  HTTPAdapter(max_retries=_retry))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PAPA_SEARCH  = "https://pbcpao.gov/MasterSearch/SearchResults"
SUNBIZ_URL   = "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResults"
BCPA_URL     = "https://www.bcpa.net/RecInfo.asp"

BIZ_MARKERS  = {"LLC", "INC", "CORP", "LTD", "LP", "LLP", "ASSN", "ASSOCIATION", "PA", "PL"}

# Street suffix pattern for city/street splitting
STREET_SUFFIX = (
    r"(?:AVE|BLVD|ST|DR|LN|RD|WAY|CT|PL|TER|CIR|LOOP|PKWY|HWY|TRL|RUN|PT|PTE|"
    r"PKWY|CRK|CRST|CV|GN|GROVE|PARK|PATH|PIKE|RIDGE|ROW|SQ|TRCE|VIS|VLG|WALK|XING)"
)

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def clean(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()

def title(v: str) -> str:
    return clean(v).title()

def is_business(name: str) -> bool:
    return any(m in name.upper() for m in BIZ_MARKERS)

def to_last_first(name: str) -> str:
    """Convert 'First Last' to 'LAST FIRST' for PAPA searches.
    For business names (LLC, INC etc) — return as-is, no reordering."""
    BIZ = {"LLC", "INC", "CORP", "LTD", "LP", "LLP", "PA", "PL", "ASSN"}
    parts = clean(name).split()
    if not parts:
        return name.upper()
    # If any token is a business suffix, don't reorder
    if any(p.upper().rstrip(".") in BIZ for p in parts):
        return name.upper()
    if len(parts) >= 2:
        return f"{parts[-1]} {' '.join(parts[:-1])}".upper()
    return name.upper()

def strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()

# ---------------------------------------------------------------------------
# PAPA mailing address parser
# Confirmed text format:
#   "Mailing Address Actions BROSEN ALICIA 19666 BLACK OLIVE LN BOCA RATON FL 33498 4828"
# ---------------------------------------------------------------------------

def parse_papa_mailing(text: str) -> dict:
    """
    Parse mailing address from PAPA detail page plain text.
    Returns dict with mail_addr, mail_city, mail_state, mail_zip, mail_owner.
    """
    result = {}

    # Extract block between "Mailing Address Actions" and "Change of Mailing Address"
    block_m = re.search(
        r"Mailing\s+Address\s+Actions\s+(.+?)\s+Change\s+of\s+Mailing",
        text, re.I
    )
    if not block_m:
        # Fallback: any FL address block near "Mailing"
        block_m = re.search(
            r"Mailing.{0,50}?\s+(\d+\s+[A-Z].+?FL\s+\d{5})",
            text, re.I
        )
        if not block_m:
            return result
        block = block_m.group(1).strip()
    else:
        block = block_m.group(1).strip()

    # Split on FL zip anchor
    fl_m = re.search(r"^(.+?)\s+FL\s+(\d{5})", block, re.I)
    if not fl_m:
        return result

    before_fl = fl_m.group(1).strip()
    result["mail_state"] = "FL"
    result["mail_zip"]   = fl_m.group(2)

    # Find street start (first digit sequence = street number)
    street_m = re.search(r"\d+\s+\S", before_fl)
    if not street_m:
        return result

    owner_raw = before_fl[:street_m.start()].strip()
    street_and_city = before_fl[street_m.start():]

    # Split street from city using street suffix
    sfx_m = re.search(
        rf"^(.+?\b{STREET_SUFFIX}\b)\s+(.+)$",
        street_and_city, re.I
    )
    if sfx_m:
        result["mail_addr"]  = title(sfx_m.group(1))
        result["mail_city"]  = title(sfx_m.group(2).strip())
    else:
        # No recognizable suffix — treat last 2 words as city
        words = street_and_city.split()
        if len(words) >= 3:
            result["mail_addr"] = title(" ".join(words[:-2]))
            result["mail_city"] = title(" ".join(words[-2:]))
        else:
            result["mail_addr"] = title(street_and_city)

    if owner_raw:
        result["mail_owner"] = title(owner_raw)

    return result


def parse_papa_pcn(html: str, url: str) -> Optional[str]:
    """Extract parcel control number from PAPA URL or page."""
    # From URL: ?parcelId=00414712150100260
    m = re.search(r"parcelId=(\d{17})", url)
    if not m:
        m = re.search(r"parcelId=(\d{17})", html)
    if m:
        raw = m.group(1)
        return f"{raw[0:2]}-{raw[2:4]}-{raw[4:6]}-{raw[6:8]}-{raw[8:10]}-{raw[10:13]}-{raw[13:17]}"
    # Also look for formatted PCN in page
    m2 = re.search(r"(\d{2}-\d{2}-\d{2}-\d{2}-\d{3}-\d{4})", html)
    return m2.group(1) if m2 else None

# ---------------------------------------------------------------------------
# PAPA search — primary enrichment method
# ---------------------------------------------------------------------------

def search_papa(owner_name: str, permit_address: str = "") -> dict:
    """
    Search PAPA by owner name (LAST FIRST format).
    Returns enriched dict with mailing address if found.
    """
    search_term = to_last_first(owner_name)
    print(f"  [PAPA] Searching: {search_term!r}")

    try:
        resp = SESSION.get(
            PAPA_SEARCH,
            params={"propertyType": "RE", "searchvalue": search_term},
            timeout=20,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"  [PAPA] Request failed: {e}")
        return {}

    html = resp.text
    text = strip_html(html)

    result = {"source": "papa"}

    # Extract PCN
    pcn = parse_papa_pcn(html, resp.url)
    if pcn:
        result["pcn"] = pcn
        print(f"  [PAPA] PCN: {pcn}")

    # Parse mailing address
    addr = parse_papa_mailing(text)
    if addr:
        result.update(addr)
        print(f"  [PAPA] Mailing: {addr.get('mail_addr')}, {addr.get('mail_city')} FL {addr.get('mail_zip')}")
    else:
        # If no direct redirect, try with permit address
        if permit_address and not addr:
            street_only = re.sub(r"\b(FL|FLORIDA|PALM BEACH|BOCA RATON|DELRAY|WEST PALM|WELLINGTON)\b.*", "", permit_address, flags=re.I).strip()
            try:
                resp2 = SESSION.get(
                    PAPA_SEARCH,
                    params={"propertyType": "RE", "searchvalue": street_only},
                    timeout=15,
                )
                html2  = resp2.text
                text2  = strip_html(html2)
                addr2  = parse_papa_mailing(text2)
                if addr2:
                    # Only accept if returned address shares same street number
                    # (prevents matching wrong property)
                    permit_num = re.match(r"\d+", address_1 or "")
                    result_num = re.match(r"\d+", addr2.get("mail_addr",""))
                    same_number = (
                        permit_num and result_num and
                        permit_num.group(0) == result_num.group(0)
                    )
                    if same_number:
                        result.update(addr2)
                        pcn2 = parse_papa_pcn(html2, resp2.url)
                        if pcn2:
                            result["pcn"] = pcn2
                        print(f"  [PAPA] Address fallback: {addr2.get('mail_addr')}")
                    else:
                        print(f"  [PAPA] Rejected fallback (wrong property): {addr2.get('mail_addr')}")
            except Exception:
                pass

    return result if (result.get("mail_addr") or result.get("pcn")) else {}


# ---------------------------------------------------------------------------
# Sunbiz — Florida Secretary of State (for LLC/Corp debtors)
# ---------------------------------------------------------------------------

def search_sunbiz(entity_name: str) -> dict:
    """Search Sunbiz for business entity — returns officer name + principal address."""
    clean_name = re.sub(
        r"\b(LLC|INC|CORP|LTD|LP|LLP|ASSN|ASSOCIATION|PA|PL)\b\.?",
        "", entity_name, flags=re.I
    ).strip()
    print(f"  [Sunbiz] Searching: {clean_name!r}")
    import time as _time; _time.sleep(1)  # avoid rate limiting

    try:
        resp = SESSION.get(
            SUNBIZ_URL,
            params={
                "SearchTerm":        clean_name,
                "SearchType":        "EntityName",
                "SearchNameOrder":   "CONTAINS",
                "SearchCriteria":    "Active",
                "ListNameOrderPage": 1,
            },
            timeout=15,
        )
        resp.raise_for_status()
        html = resp.text
        text = strip_html(html)
    except Exception as e:
        print(f"  [Sunbiz] Request failed: {e}")
        return {}

    # Find first detail link
    detail_links = re.findall(
        r'href="(/Inquiry/CorporationSearch/SearchResultDetail\?[^"]+)"', html
    )
    if not detail_links:
        return {}

    try:
        detail_resp = SESSION.get(
            f"https://search.sunbiz.org{detail_links[0]}", timeout=15
        )
        detail_text = strip_html(detail_resp.text)
        result = {"source": "sunbiz", "entity_name": entity_name}

        # Principal address
        addr_m = re.search(
            r"Principal\s+Address\s+(\d+.+?FL\s+\d{5})",
            detail_text, re.I
        )
        if addr_m:
            addr_block = addr_m.group(1)
            fl_m = re.search(r"^(.+?)\s+FL\s+(\d{5})", addr_block, re.I)
            if fl_m:
                result["mail_addr"] = title(fl_m.group(1))
                result["mail_state"] = "FL"
                result["mail_zip"]   = fl_m.group(2)

        # Officer/manager name
        officer_m = re.search(
            r"(?:President|Manager|Director|Member|Officer|Managing Member)\s+([A-Z][A-Za-z\s]{4,40})",
            detail_text
        )
        if officer_m:
            result["officer_name"] = title(officer_m.group(1))
            print(f"  [Sunbiz] Officer: {result['officer_name']}")

        return result if result.get("mail_addr") else {}
    except Exception as e:
        print(f"  [Sunbiz] Detail fetch failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# BCPA — Broward County Property Appraiser
# ---------------------------------------------------------------------------

def search_bcpa(owner_name: str) -> dict:
    """Search Broward County Property Appraiser by owner name."""
    search_term = to_last_first(owner_name)
    print(f"  [BCPA] Searching: {search_term!r}")
    try:
        resp = SESSION.get(
            BCPA_URL,
            params={"URL_Folio": "", "SearchType": "name", "SearchValue": search_term},
            timeout=15,
        )
        resp.raise_for_status()
        text = strip_html(resp.text)
        # Look for FL address in response
        fl_m = re.search(
            r"(\d+\s+[A-Z].+?\b" + STREET_SUFFIX + r"\b.+?)\s+FL\s+(\d{5})",
            text, re.I
        )
        if fl_m:
            return {
                "source":     "bcpa",
                "mail_addr":  title(fl_m.group(1)),
                "mail_state": "FL",
                "mail_zip":   fl_m.group(2),
            }
    except Exception as e:
        print(f"  [BCPA] Request failed: {e}")
    return {}


# ---------------------------------------------------------------------------
# Main enrichment logic
# ---------------------------------------------------------------------------

def enrich_lead(lead: dict) -> dict:
    """Try all enrichment sources for a single lead."""
    owner_name  = clean(lead.get("owner_name") or "")
    debtor_name = clean(lead.get("debtor_name") or "")
    address_1   = clean(lead.get("address_1") or "")
    county      = lead.get("county_name", "Palm Beach")

    result = {
        "lead_id":      lead["id"],
        "source":       None,
        "mail_addr":    None,
        "mail_city":    None,
        "mail_state":   "FL",
        "mail_zip":     None,
        "mail_owner":   owner_name or debtor_name,
        "officer_name": None,
        "pcn":          None,
        "enriched":     False,
    }

    primary = owner_name or debtor_name
    if not primary:
        return result

    # Strategy 1: Business entity → Sunbiz
    if is_business(debtor_name or primary):
        data = search_sunbiz(debtor_name or primary)
        if data:
            result.update(data)
            result["enriched"] = True
            time.sleep(1.5)
            return result

    # Strategy 2: PAPA by owner name (Palm Beach)
    if county == "Palm Beach":
        data = search_papa(primary, address_1)
        if data:
            result.update(data)
            result["enriched"] = True
            time.sleep(1)
            return result

    # Strategy 3: BCPA by owner name (Broward)
    if county == "Broward":
        data = search_bcpa(primary)
        if data:
            result.update(data)
            result["enriched"] = True
            time.sleep(1)
            return result

    return result


def upsert_contact(cur, result: dict, dry_run: bool = False) -> None:
    if dry_run:
        print(f"  [dry-run] contact: {result.get('mail_addr')} {result.get('mail_city')} {result.get('mail_zip')}")
        return

    owner = result.get("mail_owner") or result.get("officer_name") or "Unknown"

    cur.execute("""
        INSERT INTO contacts (
            lead_id, full_name,
            mailing_address_1, city, state, zip,
            enrichment_vendor, enrichment_score, enrichment_status,
            last_enriched_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (lead_id) DO UPDATE SET
            mailing_address_1 = CASE
                WHEN EXCLUDED.mailing_address_1 IS NOT NULL
                 AND EXCLUDED.mailing_address_1 != ''
                THEN EXCLUDED.mailing_address_1
                ELSE contacts.mailing_address_1
            END,
            city              = COALESCE(EXCLUDED.city,  contacts.city),
            state             = COALESCE(EXCLUDED.state, contacts.state),
            zip               = COALESCE(EXCLUDED.zip,   contacts.zip),
            full_name         = CASE
                WHEN contacts.full_name IS NULL OR contacts.full_name = 'Unknown'
                THEN EXCLUDED.full_name
                ELSE contacts.full_name
            END,
            enrichment_vendor  = EXCLUDED.enrichment_vendor,
            enrichment_status  = EXCLUDED.enrichment_status,
            last_enriched_at   = NOW()
        WHERE contacts.email IS NULL
           OR contacts.email LIKE '%%leadflow.invalid%%'
           OR contacts.mailing_address_1 IS NULL
    """, (
        result["lead_id"],
        owner,
        result.get("mail_addr"),
        result.get("mail_city"),
        result.get("mail_state", "FL"),
        result.get("mail_zip"),
        result.get("source"),
        75 if result["enriched"] else 0,
        "matched_property_appraiser" if result["enriched"] else "no_pa_match",
    ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Enrich leads via Property Appraiser + Sunbiz")
    parser.add_argument("--county",  default="all", choices=["all", "palm_beach", "broward"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit",   type=int, default=0)
    parser.add_argument("--force",   action="store_true", help="Re-enrich already enriched leads")
    args = parser.parse_args()

    county_filter = {
        "palm_beach": "AND c.county_name = 'Palm Beach'",
        "broward":    "AND c.county_name = 'Broward'",
        "all":        "",
    }[args.county]

    # Only process leads without mailing address (or force re-enrich)
    addr_filter = "" if args.force else (
        "AND (ct.mailing_address_1 IS NULL OR ct.mailing_address_1 = '')"
    )
    limit_clause = f"LIMIT {args.limit}" if args.limit else ""

    conn = get_connection()
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    ml.id,
                    c.county_name,
                    np.owner_name,
                    np.address_1,
                    np.city,
                    nl.debtor_name,
                    ct.email            AS current_email,
                    ct.mailing_address_1 AS current_mail
                FROM matched_leads ml
                JOIN counties c            ON ml.county_id = c.id
                JOIN normalized_permits np ON ml.permit_id = np.id
                JOIN normalized_liens nl   ON ml.lien_id   = nl.id
                LEFT JOIN contacts ct      ON ct.lead_id   = ml.id
                WHERE 1=1
                  {county_filter}
                  {addr_filter}
                ORDER BY ml.lead_score DESC
                {limit_clause}
            """)
            leads = [
                {
                    "id":          row[0],
                    "county_name": row[1],
                    "owner_name":  row[2],
                    "address_1":   row[3],
                    "city":        row[4],
                    "debtor_name": row[5],
                }
                for row in cur.fetchall()
            ]

        print(f"\nProperty Appraiser enrichment")
        print(f"  Leads to process : {len(leads)}")
        print(f"  County filter    : {args.county}")
        print(f"  Dry run          : {args.dry_run}")

        enriched = 0
        not_found = 0

        for i, lead in enumerate(leads, 1):
            name = lead.get("owner_name") or lead.get("debtor_name") or "?"
            print(f"\n[{i}/{len(leads)}] Lead {lead['id']}: {name} | {lead.get('address_1','')}")

            result = enrich_lead(lead)

            with conn.cursor() as cur:
                upsert_contact(cur, result, dry_run=args.dry_run)

            if result["enriched"]:
                enriched += 1
                print(f"  ✓ Enriched: {result.get('mail_addr')} {result.get('mail_city')} {result.get('mail_zip')}")
            else:
                not_found += 1
                print(f"  ✗ Not found")

            if not args.dry_run:
                conn.commit()

            time.sleep(0.75)  # polite rate limiting

        print(f"\n--- Property Appraiser enrichment summary ---")
        print(f"  Processed : {len(leads)}")
        print(f"  Enriched  : {enriched}")
        print(f"  Not found : {not_found}")
        if enriched:
            print(f"\nNext: $env:MIN_LEAD_SCORE='40'; python -m app.workers.generate_email_list")

    except Exception as e:
        conn.rollback()
        print(f"\nERROR: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()