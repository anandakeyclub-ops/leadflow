"""
enrich_liens_from_web.py
========================
Free web-based email enrichment for liens not matched by DBPR or Sunbiz.

Pipeline per lien:
1. Google search: "{business name} {city} FL email contact"
2. Parse top results for email addresses + website URLs
3. If website found: scrape contact/about page for email
4. Store found emails in lien_web_contacts table

Uses:
- requests + BeautifulSoup (free)
- Google search via googlesearch-python (free, ~100/day)
- No API keys required

Usage:
  python -m app.workers.enrich_liens_from_web
  python -m app.workers.enrich_liens_from_web --limit 50
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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
})

EMAIL_RE = re.compile(
    r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'
)
SKIP_DOMAINS = {
    "example.com", "sentry.io", "email.com", "domain.com",
    "youremail.com", "company.com", "test.com", "gmail.com",
    "yahoo.com", "hotmail.com", "aol.com", "icloud.com",
}
SKIP_WORDS = {
    "INTERNAL REVENUE", "IRS", "FLORIDA DEPARTMENT", "STATE OF FLORIDA",
    "UNITED STATES", "DEPARTMENT OF REVENUE",
}


def google_search(query: str, num: int = 5) -> list[str]:
    """Search Google and return result URLs. Uses googlesearch-python."""
    try:
        from googlesearch import search
        urls = []
        for url in search(query, num_results=num, sleep_interval=2):
            if url and not any(skip in url for skip in
                               ['google.', 'facebook.com', 'linkedin.com',
                                'youtube.com', 'twitter.com', 'yelp.com']):
                urls.append(url)
        return urls[:3]
    except ImportError:
        # Fallback: use requests to hit Google directly
        return google_search_requests(query, num)
    except Exception as e:
        print(f"    Google error: {e}")
        return []


def google_search_requests(query: str, num: int = 5) -> list[str]:
    """Fallback Google search using requests."""
    try:
        r = SESSION.get(
            "https://www.google.com/search",
            params={"q": query, "num": num},
            timeout=10
        )
        soup = BeautifulSoup(r.text, "lxml")
        urls = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/url?q="):
                url = href.split("/url?q=")[1].split("&")[0]
                if url.startswith("http") and "google" not in url:
                    urls.append(url)
        return urls[:3]
    except Exception:
        return []


def scrape_emails_from_url(url: str) -> list[str]:
    """Fetch URL and extract email addresses."""
    try:
        r = SESSION.get(url, timeout=10, allow_redirects=True)
        if r.status_code != 200:
            return []
        text = r.text

        # Find emails in page
        emails = EMAIL_RE.findall(text)

        # Also check contact/about pages
        soup = BeautifulSoup(text, "lxml")
        contact_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            text_lower = a.get_text().lower()
            if any(w in href or w in text_lower
                   for w in ["contact", "about", "email", "reach"]):
                full = href if href.startswith("http") else \
                    f"{url.rstrip('/')}/{href.lstrip('/')}"
                contact_links.append(full)

        # Scrape first contact page
        for link in contact_links[:2]:
            try:
                r2 = SESSION.get(link, timeout=8)
                emails.extend(EMAIL_RE.findall(r2.text))
            except Exception:
                pass

        # Filter emails
        clean = []
        seen = set()
        for email in emails:
            email = email.lower().strip()
            domain = email.split("@")[1] if "@" in email else ""
            if (email not in seen and
                    domain not in SKIP_DOMAINS and
                    not email.endswith(".png") and
                    not email.endswith(".jpg") and
                    len(email) < 100):
                clean.append(email)
                seen.add(email)
        return clean[:5]
    except Exception:
        return []


def hunter_lookup(first: str, last: str, domain: str,
                  api_key: str) -> Optional[str]:
    """Hunter.io email finder — 25 free/month."""
    try:
        r = SESSION.get(
            "https://api.hunter.io/v2/email-finder",
            params={
                "domain": domain,
                "first_name": first,
                "last_name": last,
                "api_key": api_key,
            },
            timeout=10
        )
        data = r.json()
        email = data.get("data", {}).get("email")
        confidence = data.get("data", {}).get("score", 0)
        return email if confidence >= 50 else None
    except Exception:
        return None


def ensure_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lien_web_contacts (
            id                  SERIAL PRIMARY KEY,
            normalized_lien_id  INTEGER REFERENCES normalized_liens(id)
                                ON DELETE CASCADE,
            debtor_name         TEXT,
            county              TEXT,
            emails_found        JSONB,
            sources             JSONB,
            best_email          TEXT,
            searched_at         TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_web_lien_id
        ON lien_web_contacts(normalized_lien_id)
    """)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--hunter-key", default="",
                        help="Hunter.io API key (optional)")
    args = parser.parse_args()

    # Try to install googlesearch if not present
    try:
        import googlesearch
    except ImportError:
        import subprocess
        subprocess.run(["pip", "install", "googlesearch-python", "-q"])

    conn = get_connection(); conn.autocommit = False

    try:
        with conn.cursor() as cur:
            ensure_table(cur)
            conn.commit()

            already_done = "" if args.force else \
                "AND nl.id NOT IN (SELECT normalized_lien_id FROM lien_web_contacts)"

            # Get liens not yet matched by DBPR or Sunbiz email
            cur.execute(f"""
                SELECT nl.id, nl.debtor_name, c.county_name
                FROM normalized_liens nl
                JOIN counties c ON c.id = nl.county_id
                WHERE nl.debtor_name IS NOT NULL
                AND nl.debtor_name != ''
                AND nl.id NOT IN (
                    SELECT normalized_lien_id FROM lien_dbpr_contacts
                    WHERE email IS NOT NULL
                )
                {already_done}
                ORDER BY nl.filed_date DESC NULLS LAST
                LIMIT {args.limit}
            """)
            liens = cur.fetchall()

        print(f"\n[Web Enrichment] {len(liens)} liens to process")
        print(f"  Google search limit: ~100/day (free)")
        if args.hunter_key:
            print(f"  Hunter.io: enabled")

        found_email = 0
        no_email = 0

        for i, (lien_id, debtor, county) in enumerate(liens):
            if any(sw in debtor.upper() for sw in SKIP_WORDS):
                continue

            # Build search query
            city = county.replace(" County", "").replace("-", " ")
            query = f'"{debtor}" {city} Florida email contact'
            print(f"\n  [{i+1}/{len(liens)}] {debtor[:50]} ({county})")
            print(f"    Query: {query}")

            urls = google_search(query)
            time.sleep(2)  # rate limit

            all_emails = []
            sources = []

            for url in urls:
                print(f"    Checking: {url[:60]}")
                emails = scrape_emails_from_url(url)
                if emails:
                    all_emails.extend(emails)
                    sources.append({"url": url, "emails": emails})
                time.sleep(1)

            best_email = all_emails[0] if all_emails else None

            with conn.cursor() as cur:
                if args.force:
                    cur.execute(
                        "DELETE FROM lien_web_contacts WHERE normalized_lien_id=%s",
                        (lien_id,))
                cur.execute("""
                    INSERT INTO lien_web_contacts
                        (normalized_lien_id, debtor_name, county,
                         emails_found, sources, best_email)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                """, (
                    lien_id, debtor, county,
                    json.dumps(all_emails),
                    json.dumps(sources),
                    best_email,
                ))
            conn.commit()

            if best_email:
                found_email += 1
                print(f"    ✓ Found: {best_email}")
            else:
                no_email += 1
                print(f"    ✗ No email found")

        print(f"\n{'='*60}")
        print(f"  Processed   : {len(liens)}")
        print(f"  Found email : {found_email}")
        print(f"  No email    : {no_email}")
        print(f"  Match rate  : {found_email/max(len(liens),1)*100:.1f}%")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        import traceback; traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    main()