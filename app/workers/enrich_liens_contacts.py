"""
enrich_liens_contacts.py
========================
Multi-source free email enrichment for FL tax liens.

Sources (in order):
1. Google Maps Places API (free $200/month = ~40k searches)
   → Gets business website, phone, address
2. Website scraping → extract email from contact/about pages
3. YellowPages.com → business listings with email
4. Manta.com → small business directory
5. BBB.org → business profiles

Setup:
  Get free Google Maps API key at console.cloud.google.com
  Enable "Places API" — $200 free credit/month

Usage:
  python -m app.workers.enrich_liens_contacts
  python -m app.workers.enrich_liens_contacts --limit 100
  python -m app.workers.enrich_liens_contacts --source yellowpages
  python -m app.workers.enrich_liens_contacts --gmaps-key YOUR_KEY
"""
from __future__ import annotations

import argparse, json, re, time
from datetime import datetime
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
SKIP_EMAIL_DOMAINS = {
    "example.com", "test.com", "domain.com", "email.com",
    "sentry.io", "wixpress.com", "squarespace.com", "wordpress.com",
    "shopify.com", "godaddy.com", "amazonaws.com", "cloudflare.com",
}
SKIP_NAMES = {
    "INTERNAL REVENUE SERVICE", "IRS", "FLORIDA DEPARTMENT OF REVENUE",
    "FLORIDA DEPT OF REVENUE", "DEPARTMENT OF REVENUE", "STATE OF FLORIDA",
    "UNITED STATES", "US TREASURY",
}
COUNTY_CITIES = {
    "Miami-Dade": "Miami", "Broward": "Fort Lauderdale",
    "Palm Beach": "West Palm Beach", "Hillsborough": "Tampa",
    "Pinellas": "St Petersburg", "Orange": "Orlando",
    "Duval": "Jacksonville", "Lee": "Fort Myers",
    "Polk": "Lakeland", "Sarasota": "Sarasota",
    "Manatee": "Bradenton", "Martin": "Stuart",
    "Lake": "Tavares", "Pasco": "New Port Richey",
    "Osceola": "Kissimmee", "Volusia": "Daytona Beach",
    "St. Johns": "St Augustine",
}


def clean_email(email: str) -> Optional[str]:
    email = email.lower().strip()
    if not email or "@" not in email:
        return None
    domain = email.split("@")[1]
    if domain in SKIP_EMAIL_DOMAINS:
        return None
    if any(x in email for x in [".png", ".jpg", ".gif", ".css", ".js"]):
        return None
    if len(email) > 100 or len(email) < 6:
        return None
    return email


def extract_emails_from_html(html: str) -> list[str]:
    raw = EMAIL_RE.findall(html)
    emails = []
    seen = set()
    for e in raw:
        c = clean_email(e)
        if c and c not in seen:
            emails.append(c)
            seen.add(c)
    return emails


def scrape_website_email(url: str) -> list[str]:
    """Fetch website and scrape for emails on main + contact pages."""
    emails = []
    try:
        r = SESSION.get(url, timeout=10, allow_redirects=True)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")

        # Get emails from main page
        emails.extend(extract_emails_from_html(r.text))

        # Also check mailto: links directly
        for a in soup.find_all("a", href=True):
            if a["href"].startswith("mailto:"):
                email = a["href"].replace("mailto:", "").split("?")[0]
                c = clean_email(email)
                if c:
                    emails.append(c)

        # Find contact/about page links
        contact_urls = []
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            txt  = a.get_text().lower()
            if any(w in href or w in txt for w in
                   ["contact", "about", "reach-us", "get-in-touch", "email-us"]):
                if href.startswith("http"):
                    contact_urls.append(href)
                elif href.startswith("/"):
                    base = "/".join(url.split("/")[:3])
                    contact_urls.append(base + href)

        for cu in contact_urls[:2]:
            try:
                r2 = SESSION.get(cu, timeout=8, allow_redirects=True)
                if r2.status_code == 200:
                    emails.extend(extract_emails_from_html(r2.text))
                    # mailto links
                    soup2 = BeautifulSoup(r2.text, "lxml")
                    for a in soup2.find_all("a", href=True):
                        if a["href"].startswith("mailto:"):
                            c = clean_email(
                                a["href"].replace("mailto:", "").split("?")[0])
                            if c:
                                emails.append(c)
                time.sleep(0.5)
            except Exception:
                pass

    except Exception:
        pass

    # Deduplicate
    seen = set()
    result = []
    for e in emails:
        if e not in seen:
            seen.add(e); result.append(e)
    return result[:5]


# ── Source 1: Google Maps Places API ─────────────────────────────────────────

def gmaps_search(name: str, city: str, api_key: str) -> dict:
    """Search Google Places API for business. Returns website + phone."""
    try:
        # Text search
        r = SESSION.get(
            "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
            params={
                "input": f"{name} {city} FL",
                "inputtype": "textquery",
                "fields": "name,formatted_address,website,formatted_phone_number,place_id",
                "key": api_key,
            }, timeout=10
        )
        data = r.json()
        candidates = data.get("candidates", [])
        if candidates:
            return candidates[0]
        return {}
    except Exception:
        return {}


# ── Source 2: YellowPages ────────────────────────────────────────────────────

def yellowpages_search(name: str, city: str) -> dict:
    """Search YellowPages for business listing."""
    try:
        query = name.replace(" ", "+")
        loc   = city.replace(" ", "+") + "+FL"
        url   = f"https://www.yellowpages.com/search?search_terms={query}&geo_location_terms={loc}"
        r = SESSION.get(url, timeout=12)
        if r.status_code != 200:
            return {}

        soup = BeautifulSoup(r.text, "lxml")

        # First result
        result = soup.find("div", class_=re.compile("result"))
        if not result:
            return {}

        data = {}

        # Business name
        name_el = result.find(["h2", "a"], class_=re.compile("business-name|listing-name"))
        if name_el:
            data["name"] = name_el.get_text(strip=True)

        # Phone
        phone_el = result.find(class_=re.compile("phones|phone"))
        if phone_el:
            data["phone"] = phone_el.get_text(strip=True)

        # Website link
        web_el = result.find("a", class_=re.compile("track-visit-website|website"))
        if web_el and web_el.get("href"):
            data["website"] = web_el["href"]

        # Email sometimes in listing
        emails = extract_emails_from_html(str(result))
        if emails:
            data["email"] = emails[0]

        return data
    except Exception as e:
        return {}


# ── Source 3: Manta ──────────────────────────────────────────────────────────

def manta_search(name: str, city: str) -> dict:
    """Search Manta.com business directory."""
    try:
        query = f"{name} {city} FL"
        r = SESSION.get(
            "https://www.manta.com/search",
            params={"search_source": "nav", "search[name]": query},
            timeout=12
        )
        if r.status_code != 200:
            return {}

        soup = BeautifulSoup(r.text, "lxml")
        result = soup.find("div", class_=re.compile("search-result|listing"))
        if not result:
            return {}

        data = {}
        link = result.find("a", href=True)
        if link and link["href"].startswith("/"):
            detail_url = "https://www.manta.com" + link["href"]
            r2 = SESSION.get(detail_url, timeout=10)
            if r2.status_code == 200:
                emails = extract_emails_from_html(r2.text)
                if emails:
                    data["email"] = emails[0]
                phone = re.search(r'\(?\d{3}\)?[\s\-\.]\d{3}[\s\-\.]\d{4}',
                                  r2.text)
                if phone:
                    data["phone"] = phone.group()
                web = re.search(r'href=["\']?(https?://[^"\'>\s]+)["\']?',
                                r2.text)
                if web:
                    data["website"] = web.group(1)
        return data
    except Exception:
        return {}


# ── Source 4: BBB ────────────────────────────────────────────────────────────

def bbb_search(name: str, city: str) -> dict:
    """Search BBB.org for business profile."""
    try:
        r = SESSION.get(
            "https://www.bbb.org/search",
            params={"find_text": name, "find_loc": f"{city}, FL"},
            timeout=12
        )
        if r.status_code != 200:
            return {}

        soup = BeautifulSoup(r.text, "lxml")
        result = soup.find("div", class_=re.compile("result-card|business-card"))
        if not result:
            return {}

        data = {}
        emails = extract_emails_from_html(str(result))
        if emails:
            data["email"] = emails[0]

        phone = result.find(class_=re.compile("phone"))
        if phone:
            data["phone"] = phone.get_text(strip=True)

        website = result.find("a", class_=re.compile("website"))
        if website:
            data["website"] = website.get("href", "")

        return data
    except Exception:
        return {}


# ── Main enrichment loop ──────────────────────────────────────────────────────

def ensure_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lien_contact_enrichment (
            id                  SERIAL PRIMARY KEY,
            normalized_lien_id  INTEGER REFERENCES normalized_liens(id)
                                ON DELETE CASCADE UNIQUE,
            debtor_name         TEXT,
            county              TEXT,
            email               TEXT,
            phone               TEXT,
            website             TEXT,
            address             TEXT,
            source              TEXT,
            all_emails          JSONB,
            raw_data            JSONB,
            searched_at         TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_contact_enrich_lien
        ON lien_contact_enrichment(normalized_lien_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_contact_enrich_email
        ON lien_contact_enrichment(email)
        WHERE email IS NOT NULL
    """)


def enrich_one(name: str, city: str, gmaps_key: str,
               sources: list[str]) -> dict:
    """Try each source in order, return first hit with email."""
    result = {"email": None, "phone": None, "website": None,
              "source": None, "all_emails": [], "raw": {}}

    for source in sources:
        data = {}
        if source == "gmaps" and gmaps_key:
            data = gmaps_search(name, city, gmaps_key)
            time.sleep(0.2)
        elif source == "yellowpages":
            data = yellowpages_search(name, city)
            time.sleep(1.5)
        elif source == "manta":
            data = manta_search(name, city)
            time.sleep(1.5)
        elif source == "bbb":
            data = bbb_search(name, city)
            time.sleep(1.5)

        if not data:
            continue

        # If we have a website, scrape it for email
        website = data.get("website") or data.get("url", "")
        if website and not data.get("email"):
            emails = scrape_website_email(website)
            if emails:
                data["email"] = emails[0]
                result["all_emails"].extend(emails)
            time.sleep(0.5)

        if data.get("email"):
            result["email"]   = data["email"]
            result["phone"]   = data.get("phone") or result["phone"]
            result["website"] = website or result["website"]
            result["source"]  = source
            result["raw"]     = data
            break  # found email — stop searching

        # Even without email, capture phone/website
        if data.get("phone") and not result["phone"]:
            result["phone"] = data["phone"]
        if website and not result["website"]:
            result["website"] = website

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",      type=int,  default=100)
    parser.add_argument("--force",      action="store_true")
    parser.add_argument("--gmaps-key",  default="",
                        help="Google Maps API key (optional, free $200/mo)")
    parser.add_argument("--source",     default="all",
                        choices=["all", "gmaps", "yellowpages", "manta", "bbb"])
    args = parser.parse_args()

    sources = (["gmaps", "yellowpages", "manta", "bbb"]
               if args.source == "all" else [args.source])
    if args.gmaps_key:
        print(f"  Google Maps API: enabled")
    else:
        sources = [s for s in sources if s != "gmaps"]
        print(f"  Google Maps API: disabled (no key)")
    print(f"  Sources: {sources}")

    conn = get_connection(); conn.autocommit = False

    try:
        with conn.cursor() as cur:
            ensure_table(cur)
            conn.commit()

            already_done = "" if args.force else \
                "AND nl.id NOT IN (SELECT normalized_lien_id FROM lien_contact_enrichment)"
            no_dbpr = """
                AND nl.id NOT IN (
                    SELECT normalized_lien_id FROM lien_dbpr_contacts
                    WHERE email IS NOT NULL
                )
            """

            cur.execute(f"""
                SELECT nl.id, nl.debtor_name, c.county_name
                FROM normalized_liens nl
                JOIN counties c ON c.id = nl.county_id
                WHERE nl.debtor_name IS NOT NULL
                AND nl.debtor_name != ''
                {no_dbpr}
                {already_done}
                ORDER BY nl.filed_date DESC NULLS LAST
                LIMIT {args.limit}
            """)
            liens = cur.fetchall()

        print(f"\n[Contact Enrichment] {len(liens)} liens to process")

        found = 0
        no_email = 0

        for i, (lien_id, debtor, county) in enumerate(liens):
            if any(sw in debtor.upper() for sw in SKIP_NAMES):
                continue

            city = COUNTY_CITIES.get(county, county.replace(" County", ""))
            print(f"\n  [{i+1}/{len(liens)}] {debtor[:50]!r} ({city})")

            result = enrich_one(debtor, city, args.gmaps_key, sources)

            with conn.cursor() as cur:
                if args.force:
                    cur.execute(
                        "DELETE FROM lien_contact_enrichment "
                        "WHERE normalized_lien_id=%s", (lien_id,))
                cur.execute("""
                    INSERT INTO lien_contact_enrichment
                        (normalized_lien_id, debtor_name, county,
                         email, phone, website, source,
                         all_emails, raw_data)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (normalized_lien_id) DO UPDATE SET
                        email   = COALESCE(EXCLUDED.email, lien_contact_enrichment.email),
                        phone   = COALESCE(EXCLUDED.phone, lien_contact_enrichment.phone),
                        website = COALESCE(EXCLUDED.website, lien_contact_enrichment.website),
                        source  = EXCLUDED.source,
                        searched_at = NOW()
                """, (
                    lien_id, debtor, county,
                    result["email"], result["phone"], result["website"],
                    result["source"],
                    json.dumps(result["all_emails"]),
                    json.dumps(result["raw"], default=str),
                ))
            conn.commit()

            if result["email"]:
                found += 1
                print(f"    ✓ {result['email']} (via {result['source']})")
                if result["phone"]:
                    print(f"    ☎ {result['phone']}")
            else:
                no_email += 1
                print(f"    ✗ no email"
                      + (f" | website: {result['website'][:40]}"
                         if result["website"] else ""))

        print(f"\n{'='*60}")
        print(f"  Processed : {len(liens)}")
        print(f"  Emails    : {found}")
        print(f"  No email  : {no_email}")
        print(f"  Rate      : {found/max(len(liens),1)*100:.1f}%")

        # Export CSV
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    nl.debtor_name, ce.email, ce.phone,
                    ce.website, ce.source, c.county_name,
                    nl.filed_date, nl.lien_type
                FROM lien_contact_enrichment ce
                JOIN normalized_liens nl ON nl.id = ce.normalized_lien_id
                JOIN counties c ON c.id = nl.county_id
                WHERE ce.email IS NOT NULL
                ORDER BY nl.filed_date DESC
            """)
            rows = cur.fetchall()

        if rows:
            import csv
            from pathlib import Path
            out = Path("data") / "exports" / \
                f"web_contacts_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["Name", "Email", "Phone", "Website",
                            "Source", "County", "Filed", "Type"])
                w.writerows(rows)
            print(f"\n  Exported: {out}  ({len(rows)} contacts)")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        import traceback; traceback.print_exc()
    finally:
        conn.close()


if __name__ == "__main__":
    main()