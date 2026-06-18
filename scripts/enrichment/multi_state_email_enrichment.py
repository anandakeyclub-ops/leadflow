"""
multi_state_email_enrichment.py
================================
Email enrichment engine for all 7 states using dual search APIs.

APIs (in priority order):
  1. Google Custom Search API  — 100 free queries/day
  2. ValueSerp API             — 100 free queries/month (fallback)

States supported:
  Florida    → lien_dbpr_contacts        (7,621 records)
  Texas      → texas_tdlr_contacts       (473,039 records)
  Arizona    → arizona_roc_contacts      (pending)
  Georgia    → georgia_sos_contacts      (pending)
  California → california_cslb_contacts  (pending)
  New York   → new_york_dos_contacts     (pending)
  N Carolina → nc_lbgc_contacts          (pending)

Enrichment process per record:
  1. Search: "[business name] [city] [state] contact email"
  2. Filter out directories (Yelp, BBB, Angi, etc.)
  3. Scrape first real business website for email
  4. Save email to source table + multi_state_contacts

Daily quota management:
  - Google: 100/day → used first
  - ValueSerp: 100/month → used as fallback
  - Progress saved — resumes from last position daily
  - Prioritizes FL first (active email sequence)
  - Then TX (largest database)

Usage:
  python scripts/enrichment/multi_state_email_enrichment.py --state fl
  python scripts/enrichment/multi_state_email_enrichment.py --state tx
  python scripts/enrichment/multi_state_email_enrichment.py --all
  python scripts/enrichment/multi_state_email_enrichment.py --all --limit 100
  python scripts/enrichment/multi_state_email_enrichment.py --stats
  python scripts/enrichment/multi_state_email_enrichment.py --resume
  python scripts/enrichment/multi_state_email_enrichment.py --test-apis

Task Scheduler: Daily 7:00 AM
  Arguments: scripts/enrichment/multi_state_email_enrichment.py --all --limit 100 --resume
  Start in: C:\\Users\\Dana\\Desktop\\leadflow

.env required:
  GOOGLE_SEARCH_API_KEY=AIza...   (100 free/day)
  GOOGLE_CSE_ID=abc123...
  VALUESERP_KEY=xxx...            (100 free/month)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

# ── API credentials ───────────────────────────────────────────────────────────
GOOGLE_API_KEY  = os.getenv("GOOGLE_SEARCH_API_KEY", "")
GOOGLE_CSE_ID   = os.getenv("GOOGLE_CSE_ID", "")
VALUESERP_KEY   = os.getenv("VALUESERP_KEY", "")
SERPAPI_KEY     = os.getenv("SERPAPI_KEY", "")

# ── Paths ─────────────────────────────────────────────────────────────────────
DATA_DIR        = LEADFLOW_DIR / "data" / "enrichment"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_FILE   = DATA_DIR / "enrichment_progress.json"
QUOTA_FILE      = DATA_DIR / "api_quota.json"

try:
    from app.core.db import get_connection
    HAS_DB = True
except ImportError:
    HAS_DB = False

# ── State source table configs ────────────────────────────────────────────────
STATE_CONFIGS = {
    "fl": {
        "name":          "Florida",
        "table":         "lien_dbpr_contacts",
        "name_field":    "COALESCE(full_name, debtor_name)",
        "city_field":    "city",
        "state_abbr":    "FL",
        "email_field":   "email",
        "id_field":      "id",
        "where_extra":   "",
        "priority":      1,
    },
    "tx": {
        "name":          "Texas",
        "table":         "texas_tdlr_contacts",
        "name_field":    "COALESCE(business_name, owner_name)",
        "city_field":    "SPLIT_PART(business_city, ' ', 1)",
        "state_abbr":    "TX",
        "email_field":   "email",
        "id_field":      "id",
        "where_extra":   "AND lien_match = TRUE AND business_name NOT LIKE '%%, %%' AND business_name IS NOT NULL AND LENGTH(business_name) > 3",
        "priority":      2,
    },
    "az": {
        "name":          "Arizona",
        "table":         "arizona_roc_contacts",
        "name_field":    "COALESCE(business_name, owner_name)",
        "city_field":    "business_city",
        "state_abbr":    "AZ",
        "email_field":   "email",
        "id_field":      "id",
        "where_extra":   "",
        "priority":      3,
    },
    "ga": {
        "name":          "Georgia",
        "table":         "georgia_sos_contacts",
        "name_field":    "COALESCE(business_name, owner_name)",
        "city_field":    "business_city",
        "state_abbr":    "GA",
        "email_field":   "email",
        "id_field":      "id",
        "where_extra":   "",
        "priority":      4,
    },
    "ca": {
        "name":          "California",
        "table":         "california_cslb_contacts",
        "name_field":    "COALESCE(business_name, owner_name)",
        "city_field":    "business_city",
        "state_abbr":    "CA",
        "email_field":   "email",
        "id_field":      "id",
        "where_extra":   "",
        "priority":      5,
    },
    "ny": {
        "name":          "New York",
        "table":         "new_york_dos_contacts",
        "name_field":    "COALESCE(business_name, owner_name)",
        "city_field":    "business_city",
        "state_abbr":    "NY",
        "email_field":   "email",
        "id_field":      "id",
        "where_extra":   "",
        "priority":      6,
    },
    "nc": {
        "name":          "North Carolina",
        "table":         "nc_lbgc_contacts",
        "name_field":    "COALESCE(business_name, owner_name)",
        "city_field":    "business_city",
        "state_abbr":    "NC",
        "email_field":   "email",
        "id_field":      "id",
        "where_extra":   "",
        "priority":      7,
    },
}

# ── Directories to skip in search results ─────────────────────────────────────
SKIP_DOMAINS = [
    # Directories & aggregators
    "yelp.com", "bbb.org", "facebook.com", "linkedin.com",
    "angi.com", "homeadvisor.com", "thumbtack.com", "manta.com",
    "yellowpages.com", "whitepages.com", "bizapedia.com",
    "opencorporates.com", "bizbuysell.com", "chamberofcommerce.com",
    "houzz.com", "porch.com", "bark.com", "checkatrade.com",
    "angieslist.com", "networksofsolutions.com", "mapquest.com",
    "nextdoor.com", "google.com", "maps.google.com",
    "instagram.com", "twitter.com", "tiktok.com", "pinterest.com",
    "birdeye.com", "rocketreach.co", "bloomberg.com", "prolicensecheck.com",
    "bidbro.com", "blockrenovation.com", "faisalman.com", "bctonline.com",
    "realtor.com", "paci-inc.com", "me.com",
    "youtube.com", "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "buildzoom.com", "contractors.com", "buildingconnected.com",
    "constructionmonitor.com", "procore.com", "buildstats.info",
    "h1bdata.info", "trademarkelite.com", "traction",
    "arlosmanagement.com", "spectorcox.com", "bug-reporting",
    # People/data search
    "spokeo.com", "zoominfo.com", "datanyze.com", "rocketreach.co",
    "fastpeoplesearch.com", "instantcheckmate.com", "beenverified.com",
    "intelius.com", "peoplefinders.com", "truepeoplesearch.com",
    # Education
    "utsouthwestern.edu", "edu",
    # Government
    "tdlr.texas.gov", "texas.gov", "florida.gov", "az.gov",
    "roc.az.gov", "dbpr.fl.gov", "usda.gov", ".gov",
]

# ── Email regex ───────────────────────────────────────────────────────────────
EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)

SKIP_EMAIL_PATTERNS = [
    # System/automated
    "noreply", "no-reply", "donotreply", "unsubscribe",
    "privacy@", "legal@", "dmca@", "abuse@", "spam@",
    "postmaster@", "webmaster@", "hostmaster@", "mailer-daemon",
    # Platform placeholder emails
    "support@wix", "info@wix", "example.com", "test.com",
    "sentry.io", "sentry-next", "wixpress.com", "wordpress.com",
    "godaddy.com", "squarespace.com", "shopify.com",
    # Obvious placeholder patterns
    "first.last@", "firstname.lastname@", "name@company",
    "user@domain", "your@email", "your.name@", "youremail@",
    "someone@", "example@", "sample@", "placeholder@",
    "xx@xx", "test@test", "email@email", "admin@admin",
    # Single char local parts (almost always junk)
    # Handled separately in is_junk_email()
    # Directories & wrong businesses
    "thebluebook.com", "instantcheckmate.com", "claimspages.com",
    "humaneworld.org", "otrucking.com", "spokeo.com",
    "zoominfo.com", "datanyze.com", "courtlistener.com",
    "fastpeoplesearch.com", "tws.edu", "buildzoom.com",
    "h1bdata.info", "trademarkelite.com", "bug-reporting",
    # Generic
    "u003e@", "@mail.", ".gov@", ".edu@",
]

# Domains that are ALWAYS directories/aggregators — never real business emails
SKIP_EMAIL_DOMAINS = {
    # Contractor directories
    "thebluebook.com", "buildzoom.com", "buildstats.info",
    "contractors.com", "constructionmonitor.com", "prolicensecheck.com",
    "blockrenovation.com", "bidbro.com", "dfwprofessionals.com",
    "procore.com", "buildingconnected.com", "bluebook.com",
    # People/background search
    "instantcheckmate.com", "spokeo.com", "zoominfo.com", "datanyze.com",
    "courtlistener.com", "fastpeoplesearch.com", "beenverified.com",
    "intelius.com", "peoplefinders.com", "truepeoplesearch.com",
    "rocketreach.co", "hunter.io", "clearbit.com", "apollo.io",
    # General directories / aggregators
    "claimspages.com", "humaneworld.org", "angieslist.com",
    "homeadvisor.com", "thumbtack.com", "yelp.com", "bbb.org",
    "manta.com", "yellowpages.com", "whitepages.com", "realtor.com",
    "birdeye.com", "bloomberg.com", "chamberofcommerce.com",
    "showmelocal.com", "merchantcircle.com", "hotfrog.com",
    "dexknows.com", "superpages.com", "citysearch.com",
    "cylex.us.com", "local.com", "tupalo.com", "brownbook.net",
    "infobel.com", "cybo.com", "yp.com", "mapquest.com",
    "n2pub.com", "noticeregistry.com", "procurated.com",
    "bebee.com", "windnetwork.com", "piracymonitor.org",
    "linkoutdoor.com", "jadeandcloveraz.com", "anmbf.org",
    "tradekey.com", "alibaba.com", "thomasnet.com",
    # Healthcare / senior care aggregators
    "aplaceformom.com", "seniorcarefinder.com", "carefinder.com",
    "caring.com", "senioradvisor.com", "aila.org",
    "nursa.com", "staffdna.com", "maximstaffing.com",
    "salary.com", "talent.com", "indeed.com", "glassdoor.com",
    "ziprecruiter.com", "careerbuilder.com", "monster.com",
    # Legal / financial aggregators
    "avvo.com", "findlaw.com", "justia.com", "martindale.com",
    "legalmotion.com", "spencerfane.com", "kroll.com",
    "advisoralign.com", "dallasbar.org", "cbre.com",
    "firstgroup.com", "leedsbrownlaw.com",
    # B2B / corporate directories
    "movecars.com", "aerotek.com", "manpowergroup.com",
    "exelatech.com", "seiko.co.jp",
    # Data/HR/visa sites
    "h1bdata.info", "trademarkelite.com", "arlosmanagement.com",
    "spectorcox.com", "embarqmail.com",
    # Gov & edu
    "usda.gov", "utsouthwestern.edu",
    # Platform / website builders
    "godaddy.com", "wix.com", "wixpress.com", "squarespace.com",
    "faisalman.com", "bctonline.com", "weebly.com", "jimdo.com",
    # Generic referral/review platforms
    "giftly.com", "customerlvdg.com", "doineedapro.com",
    "velocitymatch.io", "micahrich.com", "sbcglobal.net",
    "toddflaw.com",
}

# Local parts that are ALWAYS wrong-company indicators
JUNK_LOCAL_PARTS = {
    "investorrelations", "investor.relations", "ir",
    "pressroom", "press", "media", "pr",
    "careers", "jobs", "recruiting", "hr", "humanresources",
    "compliance", "regulatory", "legal", "law",
    "corporate", "corporatecommunications",
    "consumerfeedback", "customerservice", "customerfeedback",
    "bkdata",   # bankruptcy data aggregator
    "dbafrontdesk",
}

# ── Email quality validator ───────────────────────────────────────────────────

def is_junk_email(email: str, business_name: str = "") -> bool:
    """
    Returns True if the email is clearly junk, placeholder, or wrong business.

    v2 improvements:
    - Catches wrong-contact-type local parts (investorrelations, careers, etc.)
    - Enforces domain-business match for single-word businesses too
    - Detects staffing/aggregator emails even for generic local parts
    - Personal email domains (gmail/yahoo/hotmail/outlook/aol/live)
      are valid ONLY when business_name is a sole proprietor (<= 2 words)
    """
    import re as _re

    if not email or "@" not in email:
        return True

    local, domain = email.lower().rsplit("@", 1)

    # Single or double char local part
    if len(local) <= 2:
        return True

    # Numeric-only TLD or garbage TLD
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    if len(tld) < 2 or not tld.isalpha():
        return True

    # Garbage domain (random long string)
    if _re.search(r"[a-z0-9]{20,}", domain.replace(".", "")):
        return True

    # Wrong-contact-type local parts (investor relations, HR, press, etc.)
    if local in JUNK_LOCAL_PARTS:
        return True

    # Known placeholder local parts
    junk_locals = {
        "first.last", "firstname.lastname", "name", "user", "your",
        "someone", "example", "sample", "placeholder", "test",
        "xx", "xxx", "email", "youremail", "your.name", "yourname",
        "info.example", "hello.example", "doe",
    }
    if local in junk_locals:
        return True

    # .edu and .gov domains
    if domain.endswith(".edu") or domain.endswith(".gov"):
        return True

    # Known aggregator/directory domains — always wrong
    if domain in SKIP_EMAIL_DOMAINS:
        return True

    # Personal email domains: only OK for sole props (1-2 word names)
    personal_domains = {
        "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
        "aol.com", "live.com", "icloud.com", "me.com",
        "msn.com", "sbcglobal.net", "embarqmail.com",
        "comcast.net", "att.net", "verizon.net", "cox.net",
    }
    if domain in personal_domains and business_name:
        # Count meaningful business name words
        biz_words = [
            w for w in _re.split(r"\W+", business_name)
            if len(w) > 2 and w.lower() not in {
                "llc", "inc", "corp", "ltd", "pllc", "dba",
                "the", "and", "of", "for",
            }
        ]
        # Business with 3+ words = company, not sole prop — reject personal email
        if len(biz_words) >= 3:
            return True

    # Domain-business name mismatch check
    if business_name:
        biz_words = set(
            w.lower() for w in _re.split(r"\W+", business_name)
            if len(w) > 3 and w.lower() not in {
                "llc", "inc", "corp", "ltd", "pllc", "services",
                "group", "solutions", "management", "enterprises",
                "holdings", "associates", "company", "properties",
                "contractors", "construction", "systems",
            }
        )

        # Apply domain check even for single-word businesses (was >= 2 before)
        if len(biz_words) >= 1:
            domain_clean = domain.replace(".", " ").replace("-", " ")
            if not any(w in domain_clean for w in biz_words):
                # Generic local parts are still OK — they may be real business emails
                generic_locals = {
                    "info", "contact", "hello", "office", "sales",
                    "mail", "support", "team", "billing", "accounting",
                    "customercare", "contactus", "marketing", "care",
                    "service", "services", "inquiry", "inquiries",
                    "reception", "general", "main", "us",
                }
                if local not in generic_locals:
                    return True

    return False


# ── Quota tracker ─────────────────────────────────────────────────────────────

def load_quota() -> dict:
    if QUOTA_FILE.exists():
        try:
            return json.loads(QUOTA_FILE.read_text())
        except Exception:
            pass
    return {
        "google":    {"date": "", "used": 0, "limit": 100},
        "valueserp": {"date": "", "used": 0, "limit": 100},
        "serpapi":   {"date": "", "used": 0, "limit": 250},
    }

def save_quota(quota: dict):
    QUOTA_FILE.write_text(json.dumps(quota, indent=2))

def get_available_api() -> str | None:
    """Returns which API to use next based on quota."""
    quota    = load_quota()
    today    = date.today().isoformat()
    month    = date.today().strftime("%Y-%m")

    # Reset daily Google quota
    if quota["google"]["date"] != today:
        quota["google"]["date"] = today
        quota["google"]["used"] = 0
        save_quota(quota)

    # Reset monthly ValueSerp quota
    if quota["valueserp"]["date"][:7] != month:
        quota["valueserp"]["date"] = today
        quota["valueserp"]["used"] = 0
        save_quota(quota)

    # Reset monthly SerpAPI quota
    if quota.get("serpapi", {}).get("date", "")[:7] != month:
        if "serpapi" not in quota:
            quota["serpapi"] = {"date": today, "used": 0, "limit": 250}
        quota["serpapi"]["date"] = today
        quota["serpapi"]["used"] = 0
        save_quota(quota)

    # SerpAPI — primary (searches real Google, 100/month free)
    serpapi_quota = quota.get("serpapi", {"used": 0, "limit": 250})
    if (SERPAPI_KEY and
            serpapi_quota["used"] < serpapi_quota["limit"]):
        return "serpapi"

    # ValueSerp fallback
    if (VALUESERP_KEY and
            quota["valueserp"]["used"] < quota["valueserp"]["limit"]):
        return "valueserp"

    # Google CSE (site-restricted, last resort)
    if (GOOGLE_API_KEY and GOOGLE_CSE_ID and
            quota["google"]["used"] < quota["google"]["limit"]):
        return "google"

    return None

def record_api_use(api: str):
    quota = load_quota()
    quota[api]["used"] += 1
    save_quota(quota)

def get_quota_status() -> dict:
    quota = load_quota()
    today = date.today().isoformat()
    month = date.today().strftime("%Y-%m")
    return {
        "google_used":       quota["google"]["used"] if quota["google"]["date"] == today else 0,
        "google_limit":      quota["google"]["limit"],
        "google_remaining":  quota["google"]["limit"] - (quota["google"]["used"] if quota["google"]["date"] == today else 0),
        "valueserp_used":    quota["valueserp"]["used"] if quota["valueserp"]["date"][:7] == month else 0,
        "valueserp_limit":   quota["valueserp"]["limit"],
        "valueserp_remaining": quota["valueserp"]["limit"] - (quota["valueserp"]["used"] if quota["valueserp"]["date"][:7] == month else 0),
        "serpapi_used":      quota.get("serpapi", {}).get("used", 0) if quota.get("serpapi", {}).get("date", "")[:7] == month else 0,
        "serpapi_limit":     quota.get("serpapi", {}).get("limit", 250),
        "serpapi_remaining": quota.get("serpapi", {}).get("limit", 250) - (quota.get("serpapi", {}).get("used", 0) if quota.get("serpapi", {}).get("date", "")[:7] == month else 0),
    }


# ── Progress tracker ──────────────────────────────────────────────────────────

def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text())
        except Exception:
            pass
    return {}

def save_progress(state: str, last_id: int,
                  enriched: int, failed: int):
    progress = load_progress()
    progress[state] = {
        "last_id":  last_id,
        "enriched": enriched,
        "failed":   failed,
        "date":     date.today().isoformat(),
    }
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


# ── Search APIs ───────────────────────────────────────────────────────────────

def search_google(query: str) -> list[str]:
    """Search via Google Custom Search API. Returns list of URLs."""
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return []
    try:
        r = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": GOOGLE_API_KEY,
                "cx":  GOOGLE_CSE_ID,
                "q":   query,
                "num": 5,
            },
            timeout=10,
        )
        record_api_use("google")
        if r.status_code == 200:
            return [item["link"]
                    for item in r.json().get("items", [])]
        elif r.status_code == 429:
            # Update quota to limit
            quota = load_quota()
            quota["google"]["used"] = quota["google"]["limit"]
            save_quota(quota)
        return []
    except Exception:
        return []


def search_valueserp(query: str) -> list[str]:
    """Search via ValueSerp API. Returns list of URLs."""
    if not VALUESERP_KEY:
        return []
    try:
        r = requests.get(
            "https://api.valueserp.com/search",
            params={
                "api_key": VALUESERP_KEY,
                "q":       query,
                "num":     5,
            },
            timeout=10,
        )
        record_api_use("valueserp")
        if r.status_code == 200:
            return [item.get("link", "")
                    for item in r.json().get("organic_results", [])
                    if item.get("link")]
        return []
    except Exception:
        return []



def search_serpapi(query: str) -> list[str]:
    """Search via SerpAPI. Returns list of URLs. Searches real Google results."""
    if not SERPAPI_KEY:
        return []
    try:
        r = requests.get(
            "https://serpapi.com/search",
            params={
                "api_key": SERPAPI_KEY,
                "q":       query,
                "num":     5,
                "engine":  "google",
            },
            timeout=10,
        )
        record_api_use("serpapi")
        if r.status_code == 200:
            return [item.get("link", "")
                    for item in r.json().get("organic_results", [])
                    if item.get("link")]
        elif r.status_code == 429:
            quota = load_quota()
            if "serpapi" not in quota:
                quota["serpapi"] = {"date": "", "used": 0, "limit": 250}
            quota["serpapi"]["used"] = quota["serpapi"]["limit"]
            save_quota(quota)
        return []
    except Exception:
        return []


def search_for_website(business_name: str, city: str,
                       state_abbr: str) -> list[str]:
    """Search for business website using available API."""
    if not business_name:
        return []

    query = f'"{business_name}" {city} {state_abbr} contact email'
    api   = get_available_api()

    if api == "serpapi":
        urls = search_serpapi(query)
    elif api == "valueserp":
        urls = search_valueserp(query)
    elif api == "google":
        urls = search_google(query)
    else:
        return []

    # Filter out directories
    filtered = []
    for url in urls:
        domain = urlparse(url).netloc.lower()
        if not any(skip in domain for skip in SKIP_DOMAINS):
            filtered.append(url)

    return filtered[:3]


# ── Website email scraper ─────────────────────────────────────────────────────

def scrape_email_from_url(url: str) -> str | None:
    """Scrape email from a business website."""
    if not url:
        return None

    base     = urlparse(url)
    base_url = f"{base.scheme}://{base.netloc}"

    pages = [
        url,
        urljoin(base_url, "/contact"),
        urljoin(base_url, "/contact-us"),
        urljoin(base_url, "/about"),
        urljoin(base_url, "/about-us"),
    ]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,*/*",
    }

    found = set()

    for page_url in pages[:3]:
        try:
            r = requests.get(page_url, headers=headers,
                             timeout=8, allow_redirects=True)
            if r.status_code != 200:
                continue

            emails = EMAIL_RE.findall(r.text)
            for email in emails:
                email = email.lower().strip()

                # Skip bad patterns
                if any(p in email for p in SKIP_EMAIL_PATTERNS):
                    continue

                # Skip known bad domains
                email_domain = email.split("@")[-1] if "@" in email else ""
                if email_domain in SKIP_EMAIL_DOMAINS:
                    continue

                # Skip image/file extensions
                if email.split(".")[-1] in ("png", "jpg", "gif",
                                             "css", "js", "svg"):
                    continue

                found.add(email)

            if found:
                break

        except Exception:
            continue

        time.sleep(0.3)

    # Filter junk emails
    found = {e for e in found if not is_junk_email(e)}

    if not found:
        return None

    # Prefer business domain emails
    personal_domains = {"gmail.com", "yahoo.com", "hotmail.com",
                        "outlook.com", "aol.com", "icloud.com",
                        "me.com", "live.com"}
    business_emails = [e for e in found
                       if e.split("@")[-1] not in personal_domains]

    if business_emails:
        return sorted(business_emails)[0]
    return sorted(found)[0]


# ── DB helpers ────────────────────────────────────────────────────────────────

def table_exists(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = %s
            )
        """, (table,))
        return cur.fetchone()[0]


def get_records_to_enrich(conn, cfg: dict,
                           last_id: int,
                           limit: int) -> list[dict]:
    """Get records from state table that need email enrichment."""
    if not table_exists(conn, cfg["table"]):
        return []

    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT
                {cfg['id_field']}                    AS id,
                {cfg['name_field']}                  AS business_name,
                {cfg['city_field']}                  AS city,
                '{cfg['state_abbr']}'                AS state_abbr
            FROM {cfg['table']}
            WHERE ({cfg['email_field']} IS NULL
                   OR {cfg['email_field']} = '')
              AND {cfg['id_field']} > %s
              AND {cfg['name_field']} IS NOT NULL
              {cfg['where_extra']}
            ORDER BY {cfg['id_field']}
            LIMIT %s
        """, (last_id, limit))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def save_email_to_db(conn, cfg: dict,
                     record_id: int, email: str,
                     state_code: str):
    """Save found email to source table and multi_state_contacts."""
    with conn.cursor() as cur:
        # Update source table
        cur.execute(f"""
            UPDATE {cfg['table']}
            SET {cfg['email_field']} = %s,
                confidence = CASE
                    WHEN confidence = 'low'    THEN 'medium'
                    WHEN confidence = 'medium' THEN 'high'
                    ELSE 'high'
                END,
                updated_at = NOW()
            WHERE {cfg['id_field']} = %s
        """, (email, record_id))

        # Update multi_state_contacts if table exists
        if table_exists(conn, "multi_state_contacts"):
            cur.execute("""
                UPDATE multi_state_contacts
                SET email      = %s,
                    confidence = CASE
                        WHEN confidence = 'low' THEN 'medium'
                        ELSE 'high'
                    END,
                    updated_at = NOW()
                WHERE state = %s
                  AND (email IS NULL OR email = '')
                  AND license_number IN (
                      SELECT license_number::text
                      FROM """ + cfg['table'] + """
                      WHERE """ + cfg['id_field'] + """ = %s
                  )
            """, (email, state_code.upper(), record_id))

    conn.commit()


# ── Core enrichment loop ──────────────────────────────────────────────────────

def enrich_state(state_code: str,
                 limit: int = 100,
                 resume: bool = False,
                 dry_run: bool = False) -> dict:
    """
    Enrich email addresses for a single state.
    Returns stats dict.
    """
    cfg = STATE_CONFIGS.get(state_code.lower())
    if not cfg:
        print(f"  ❌ Unknown state: {state_code}")
        return {"enriched": 0, "error": "unknown state"}

    if not HAS_DB:
        print("  ❌ No DB connection")
        return {"enriched": 0, "error": "no db"}

    conn = get_connection()
    try:
        if not table_exists(conn, cfg["table"]):
            print(f"  ⏳ {cfg['name']}: table not built yet — skipping")
            return {"enriched": 0, "status": "table_missing"}

        # Load progress
        progress = load_progress()
        state_progress = progress.get(state_code, {})
        last_id  = state_progress.get("last_id", 0) if resume else 0
        enriched = state_progress.get("enriched", 0) if resume else 0
        failed   = state_progress.get("failed", 0) if resume else 0

        # Get records
        records = get_records_to_enrich(conn, cfg, last_id, limit)
        print(f"  {cfg['name']}: {len(records):,} records to enrich "
              f"(resuming from ID {last_id})" if resume else
              f"  {cfg['name']}: {len(records):,} records to enrich")

        if not records:
            print(f"  ✅ {cfg['name']}: all records enriched!")
            return {"enriched": enriched, "failed": failed,
                    "status": "complete"}

        current_id = last_id

        for i, rec in enumerate(records):
            current_id  = rec["id"]
            biz_name    = (rec.get("business_name") or "").strip()
            city        = (rec.get("city") or "").strip()
            state_abbr  = cfg["state_abbr"]

            if not biz_name:
                failed += 1
                continue

            # Check API availability
            api = get_available_api()
            if not api:
                quota = get_quota_status()
                print(f"\n  ⚠ All API quotas exhausted:")
                print(f"    Google   : {quota['google_used']}/{quota['google_limit']} today")
                print(f"    ValueSerp: {quota['valueserp_used']}/{quota['valueserp_limit']} this month")
                print(f"  Run again tomorrow or next month")
                break

            print(f"  [{i+1}/{len(records)}] [{api}] "
                  f"{biz_name[:35]:<35} ({city}, {state_abbr})",
                  end=" ... ", flush=True)

            if dry_run:
                print("[DRY RUN]")
                continue

            # Search for website
            urls = search_for_website(biz_name, city, state_abbr)
            time.sleep(1.0)  # polite delay

            if not urls:
                print("no results")
                failed += 1
                save_progress(state_code, current_id, enriched, failed)
                continue

            # Scrape email from each URL
            email = None
            for url in urls:
                email = scrape_email_from_url(url)
                if email:
                    break

            if email:
                save_email_to_db(conn, cfg, current_id, email, state_code)
                enriched += 1
                print(f"✅ {email}")
            else:
                failed += 1
                domain = urlparse(urls[0]).netloc if urls else "—"
                print(f"no email ({domain})")

            # Save progress every 10 records
            if (i + 1) % 10 == 0:
                save_progress(state_code, current_id, enriched, failed)
                quota = get_quota_status()
                print(f"\n  ── Progress: {enriched} enriched, "
                      f"{failed} failed ──")
                print(f"  Google: {quota['google_remaining']} remaining today | "
                      f"ValueSerp: {quota['valueserp_remaining']} remaining\n")

        # Final save
        save_progress(state_code, current_id, enriched, failed)

        match_rate = round(enriched / max(enriched + failed, 1) * 100, 1)
        return {
            "enriched":   enriched,
            "failed":     failed,
            "match_rate": match_rate,
            "last_id":    current_id,
        }

    finally:
        conn.close()


# ── Stats ─────────────────────────────────────────────────────────────────────

def show_stats():
    print(f"\n{'='*65}")
    print(f"  Multi-State Email Enrichment Stats")
    print(f"  {date.today().isoformat()}")
    print(f"{'='*65}")

    # API quota status
    quota = get_quota_status()
    print(f"\n  API Quota Status:")
    print(f"  {'Google CSE':<20} {quota['google_used']:>4}/{quota['google_limit']:<4} "
          f"used today   ({quota['google_remaining']} remaining)")
    serpapi_q = get_quota_status()
    print(f"  {'SerpAPI':<20} {serpapi_q.get('serpapi_used',0)}/{serpapi_q.get('serpapi_limit',250)}"
          f"  used this month  ({serpapi_q.get('serpapi_remaining',100)} remaining)")
    print(f"  {'ValueSerp':<20} {quota['valueserp_used']:>4}/{quota['valueserp_limit']:<4} "
          f"used this month ({quota['valueserp_remaining']} remaining)")

    # API availability
    print(f"\n  {'Google API key':<20} {'✅ set' if GOOGLE_API_KEY else '❌ not set'}")
    print(f"  {'Google CSE ID':<20} {'✅ set' if GOOGLE_CSE_ID else '❌ not set'}")
    print(f"  {'ValueSerp key':<20} {'✅ set' if VALUESERP_KEY else '❌ not set'}")

    if not HAS_DB:
        print("\n  ❌ No DB connection")
        print(f"{'='*65}\n")
        return

    conn = get_connection()
    try:
        print(f"\n  {'State':<15} {'Table':<30} {'Total':>8} "
              f"{'Email':>8} {'%':>6} {'Status'}")
        print(f"  {'─'*15} {'─'*30} {'─'*8} {'─'*8} {'─'*6} {'─'*10}")

        for code, cfg in sorted(STATE_CONFIGS.items(),
                                 key=lambda x: x[1]["priority"]):
            if not table_exists(conn, cfg["table"]):
                print(f"  {cfg['name']:<15} {cfg['table']:<30} "
                      f"{'—':>8} {'—':>8} {'—':>6} pending")
                continue

            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {cfg['table']}")
                total = cur.fetchone()[0]
                cur.execute(f"""
                    SELECT COUNT(*) FROM {cfg['table']}
                    WHERE {cfg['email_field']} IS NOT NULL
                      AND {cfg['email_field']} != ''
                """)
                with_email = cur.fetchone()[0]

            pct = round(with_email / max(total, 1) * 100, 1)
            print(f"  {cfg['name']:<15} {cfg['table']:<30} "
                  f"{total:>8,} {with_email:>8,} {pct:>5.1f}% active")

        # Progress summary
        progress = load_progress()
        if progress:
            print(f"\n  Enrichment Progress:")
            for state, p in progress.items():
                cfg = STATE_CONFIGS.get(state, {})
                name = cfg.get("name", state.upper())
                print(f"  {name:<15} enriched:{p.get('enriched',0):>6,}  "
                      f"failed:{p.get('failed',0):>6,}  "
                      f"last_id:{p.get('last_id',0):>8}  "
                      f"date:{p.get('date','—')}")

    finally:
        conn.close()

    print(f"{'='*65}\n")


# ── API test ──────────────────────────────────────────────────────────────────

def test_apis():
    print(f"\n{'='*55}")
    print(f"  API Test")
    print(f"{'='*55}\n")

    query = "ABC Plumbing Houston TX contact email"

    # Test Google
    print(f"Testing Google Custom Search...")
    if GOOGLE_API_KEY and GOOGLE_CSE_ID:
        urls = search_google(query)
        if urls:
            print(f"  ✅ Google working — {len(urls)} results")
            for u in urls[:2]:
                print(f"    {u}")
        else:
            print(f"  ❌ Google returned no results — check key/CSE")
    else:
        print(f"  ⚠ Google credentials not set in .env")

    print()

    # Test ValueSerp
    print(f"Testing ValueSerp...")
    if VALUESERP_KEY:
        urls = search_valueserp(query)
        if urls:
            print(f"  ✅ ValueSerp working — {len(urls)} results")
            for u in urls[:2]:
                print(f"    {u}")
        else:
            print(f"  ❌ ValueSerp returned no results")
    else:
        print(f"  ⚠ VALUESERP_KEY not set in .env")

    print()

    # Test website scraper
    print(f"Testing website email scraper...")
    test_url = "https://abetterplumbingllc.com/"
    email = scrape_email_from_url(test_url)
    if email:
        print(f"  ✅ Scraper working — found: {email}")
    else:
        print(f"  ⚠ No email found at test URL (may be normal)")

    print(f"\n{'='*55}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

# ── Normalized Liens Enrichment ───────────────────────────────────────────────

def _save_lien_enrichment_attempt(conn, lien_id: int, debtor: str, county: str):
    """Record search attempt in lien_contact_enrichment (no email found)."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO lien_contact_enrichment
                (normalized_lien_id, debtor_name, county, source, searched_at)
            VALUES (%s, %s, %s, 'google_cse', NOW())
            ON CONFLICT DO NOTHING
        """, (lien_id, debtor[:250], county))
    conn.commit()


def _save_lien_email(conn, lien_id: int, debtor: str,
                      county_name: str, county_id: int,
                      email: str, state: str):
    """Save found email to lien_contact_enrichment and lien_dbpr_contacts."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO lien_contact_enrichment
                (normalized_lien_id, debtor_name, county, email, source, searched_at)
            VALUES (%s, %s, %s, %s, 'google_cse', NOW())
            ON CONFLICT DO NOTHING
        """, (lien_id, debtor[:250], county_name, email.lower().strip()))

        cur.execute("""
            INSERT INTO lien_dbpr_contacts
                (lien_id, county_id, debtor_name, full_name,
                 email, state, confidence, dbpr_score)
            VALUES (%s, %s, %s, %s, %s, %s, 'medium', 65.0)
            ON CONFLICT (lien_id) DO UPDATE SET
                email      = EXCLUDED.email,
                confidence = EXCLUDED.confidence
        """, (lien_id, county_id, debtor[:250], debtor[:250],
              email.lower().strip(), state))
    conn.commit()


def enrich_normalized_liens(county: str = None,
                             state: str = None,
                             limit: int = 100,
                             dry_run: bool = False) -> dict:
    """
    Enrich businesses from normalized_liens using Google CSE.
    Saves emails to lien_contact_enrichment and lien_dbpr_contacts.

    Usage:
      python multi_state_email_enrichment.py --source normalized_liens --county Dallas --limit 99
      python multi_state_email_enrichment.py --source normalized_liens --state TX --limit 99
    """
    if not HAS_DB:
        print("  No DB connection")
        return {"enriched": 0, "error": "no db"}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            where_parts = [
                "nl.business_name IS NOT NULL",
                "nl.business_name != ''",
                """NOT EXISTS (
                    SELECT 1 FROM lien_contact_enrichment lce
                    WHERE lce.normalized_lien_id = nl.id
                      AND lce.email IS NOT NULL
                )""",
                """NOT EXISTS (
                    SELECT 1 FROM lien_contact_enrichment lce
                    WHERE lce.normalized_lien_id = nl.id
                      AND lce.searched_at IS NOT NULL
                )""",
            ]
            params = []

            if county:
                where_parts.append("c.county_name ILIKE %s")
                params.append(county)
            if state:
                where_parts.append("nl.state = %s")
                params.append(state.upper())

            where_sql = " AND ".join(where_parts)
            params.append(limit)

            cur.execute(f"""
                SELECT
                    nl.id,
                    nl.business_name,
                    nl.state,
                    nl.county_id,
                    c.county_name
                FROM normalized_liens nl
                JOIN counties c ON c.id = nl.county_id
                WHERE {where_sql}
                ORDER BY nl.id
                LIMIT %s
            """, params)
            rows = cur.fetchall()

        if not rows:
            print("  No unenriched business liens found.")
            return {"enriched": 0, "skipped": 0}

        print(f"  Found {len(rows):,} business liens to enrich")
        enriched = 0
        failed   = 0

        for i, (lien_id, biz_name, state_abbr, county_id, county_name) in enumerate(rows):
            city = county_name.replace(" County", "").strip()
            api  = get_available_api()
            if not api:
                quota = get_quota_status()
                print(f"\n  All API quotas exhausted:")
                print(f"    Google   : {quota['google_used']}/{quota['google_limit']} today")
                print(f"    ValueSerp: {quota['valueserp_used']}/{quota['valueserp_limit']} this month")
                break

            print(f"  [{i+1}/{len(rows)}] [{api}] {biz_name[:40]:<40} ({city}, {state_abbr})",
                  end=" ... ", flush=True)

            if dry_run:
                print("[DRY RUN]")
                continue

            urls = search_for_website(biz_name, city, state_abbr)
            time.sleep(1.0)

            if not urls:
                print("no results")
                failed += 1
                _save_lien_enrichment_attempt(conn, lien_id, biz_name, county_name)
                continue

            email = None
            for url in urls:
                email = scrape_email_from_url(url)
                if email:
                    break

            if email and not is_junk_email(email, biz_name):
                _save_lien_email(conn, lien_id, biz_name, county_name,
                                 county_id, email, state_abbr)
                enriched += 1
                print(f"✅ {email}")
            elif email:
                print(f"⚠ junk filtered: {email}")
                email = None
                failed += 1
                _save_lien_enrichment_attempt(conn, lien_id, biz_name, county_name)
            else:
                print("no email found")
                failed += 1
                _save_lien_enrichment_attempt(conn, lien_id, biz_name, county_name)

        print(f"\n  Enriched : {enriched:,}")
        print(f"  Failed   : {failed:,}")
        if enriched + failed:
            print(f"  Rate     : {enriched/(enriched+failed)*100:.1f}%")
        return {"enriched": enriched, "failed": failed}

    finally:
        conn.close()



# ── Hardened email acceptance (lien-matched company enrichment) ─────────────────
DIRECTORY_EMAIL_DOMAINS = {
    "yelp.com", "yellowpages.com", "bbb.org", "manta.com", "linkedin.com",
    "facebook.com", "instagram.com", "angi.com", "homeadvisor.com",
    "thumbtack.com", "houzz.com",
    # B2B data / SaaS aggregators that surface as the top result and whose
    # on-page emails are NOT the business's (caught in the first TX run):
    "zippia.com", "seamless.ai", "rocketreach.co", "apollo.io", "crunchbase.com",
    "dispatchcore.io", "hvacservice.io", "geothermalfinder.com", "heartwork.com",
    "buildzoom.com", "nsnlookup.com", "pacermonitor.com", "trellis.law",
}
GENERIC_EMAIL_PROVIDERS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com",
    "icloud.com", "live.com", "msn.com", "comcast.net", "att.net", "verizon.net",
}
ROLE_LOCALPARTS = {"info", "contact", "admin", "webmaster", "noreply", "no-reply"}
MIN_EMAIL_CONFIDENCE = 0.7
_BIZ_STOP = {"the", "and", "llc", "inc", "corp", "co", "company", "services",
             "service", "solutions", "group", "of"}


def registrable_domain(host: str) -> str:
    host = (host or "").lower().split(":")[0].strip(".")
    host = re.sub(r"^www\.", "", host)
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def evaluate_email(email: str, business_name: str, site_url: str) -> tuple[float, str]:
    """Hardened acceptance. Returns (confidence 0-1, reason).
    Rejects directory/generic-provider/role addresses and any email whose domain
    doesn't match the business website domain; scores the rest 0-1."""
    email = (email or "").lower().strip()
    if "@" not in email:
        return 0.0, "no_email"
    local, _, edom = email.partition("@")
    e_reg    = registrable_domain(edom)
    site_dom = registrable_domain(urlparse(site_url).netloc)
    if e_reg in DIRECTORY_EMAIL_DOMAINS:
        return 0.0, "directory_email"
    if e_reg in GENERIC_EMAIL_PROVIDERS:
        return 0.0, "generic_provider"   # rejects info@gmail.com, contact@yahoo.com, etc.
    if site_dom and e_reg != site_dom:
        return 0.0, "domain_mismatch"
    # Require the business name to appear in the email domain. This is what
    # separates a real owned domain (blazeair.com for BLAZE AIR) from a SaaS/
    # aggregator domain that merely surfaced as the top result (dispatchcore.io,
    # zippia.com, ...). Without this, an aggregator's own email matches its own
    # site domain and slips through.
    btoks = [t for t in re.split(r"[^a-z0-9]+", (business_name or "").lower())
             if len(t) > 2 and t not in _BIZ_STOP]
    dom_squashed = e_reg.replace(".", "")
    if not any(t in dom_squashed for t in btoks):
        return 0.0, "unrelated_domain"
    # Role addresses (info@/contact@/admin@...) are ACCEPTED on the business's own
    # name-matching domain — generic-provider role addresses were already rejected
    # above. They just score slightly below a personal local part.
    is_role = local in ROLE_LOCALPARTS
    conf = 0.8 if is_role else 0.9
    if not is_role and re.search(r"[a-z]", local):
        conf += 0.1                  # personal-looking local part
    return min(conf, 1.0), ("ok_role" if is_role else "ok")


def enrich_normalized_contacts(state: str = "TX", limit: int = 100,
                               dry_run: bool = False, min_score: int = 65) -> dict:
    """
    Enrich emails for lien-MATCHED normalized_contacts via Google CSE + website
    scraping, then write the email back to normalized_contacts so
    sync_to_email_pipeline() can forward it into the 7-touch sequence.

    Targets rows that are: state=<state>, has_lien_match=TRUE, no email yet,
    match_score >= min_score (default 65 — drops the threshold-floor/surname
    spurious matches), company-style name (person-format 'LAST, FIRST' rows are
    skipped: they're individual licensees with no business website to scrape, and
    searching them just burns the 100/day Google quota), and not previously
    attempted (email_source IS NULL).

    --dry-run still performs the real search/scrape and prints what it finds, but
    does not write to the DB.

    Usage:
      python multi_state_email_enrichment.py --source normalized_contacts --state tx --dry-run --limit 20
    """
    if not HAS_DB:
        print("  No DB connection")
        return {"enriched": 0, "error": "no db"}

    st = state.upper()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, business_name, business_city, match_score
                FROM normalized_contacts
                WHERE state = %s
                  AND has_lien_match = TRUE
                  AND (email IS NULL OR email = '')
                  AND match_score >= %s
                  AND business_name IS NOT NULL
                  AND LENGTH(business_name) > 3
                  AND business_name NOT LIKE '%%, %%'   -- exclude "LAST, FIRST"
                  AND business_name LIKE '%% %%'        -- multi-token (exclude single-token names)
                  -- require a business indicator (also drops "Firstname Lastname"
                  -- personal names, which carry no indicator):
                  AND business_name ~* '\\y(LLC|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|SERVICES?|SOLUTIONS|GROUP|CONTRACTORS?|HVAC|ROOFING|PLUMBING|ELECTRIC|ELECTRICAL|CONSTRUCTION|TRUCKING|TRANSPORT|LOGISTICS|RESTAURANT|MANAGEMENT|PROPERTIES|REALTY|CONSULTING)\\y'
                  AND email_source IS NULL              -- skip already-attempted
                ORDER BY match_score DESC, id
                LIMIT %s
            """, (st, min_score, limit))
            rows = cur.fetchall()

        if not rows:
            print("  No unenriched lien-matched contacts found.")
            return {"enriched": 0, "skipped": 0}

        print(f"  Found {len(rows):,} lien-matched {st} contacts to enrich "
              f"(score>={min_score}, company-format)")
        enriched = 0
        failed   = 0
        stats = {"searched": 0, "with_website": 0, "valid_email": 0,
                 "rejected": {}, "found_domains": []}

        for i, (cid, biz, city, score) in enumerate(rows):
            # business_city is often "HOUSTON TX" — drop a trailing state token.
            parts = (city or "").split()
            if parts and parts[-1].upper() == st:
                parts = parts[:-1]
            cty = " ".join(parts)

            api = get_available_api()
            if not api:
                quota = get_quota_status()
                print(f"\n  All API quotas exhausted: "
                      f"Google {quota['google_used']}/{quota['google_limit']}, "
                      f"ValueSerp {quota['valueserp_used']}/{quota['valueserp_limit']}")
                break

            print(f"  [{i+1}/{len(rows)}] [{api}] {biz[:38]:<38} "
                  f"({cty or '-'}, {st}) score={score}", end=" ... ", flush=True)

            stats["searched"] += 1
            urls = search_for_website(biz, cty, st)
            time.sleep(1.0)
            # drop directory results before scraping
            site_urls = [u for u in (urls or [])
                         if registrable_domain(urlparse(u).netloc) not in DIRECTORY_EMAIL_DOMAINS]
            if site_urls:
                stats["with_website"] += 1

            chosen, chosen_conf, reason = None, 0.0, ("no_results" if not urls else "no_email")
            for url in site_urls[:3]:
                em = scrape_email_from_url(url)
                if not em:
                    continue
                conf, why = evaluate_email(em, biz, url)
                if conf >= MIN_EMAIL_CONFIDENCE:
                    chosen, chosen_conf, reason = em.lower().strip(), conf, "ok"
                    chosen_dom = registrable_domain(em.partition("@")[2])
                    break
                reason = why
                stats["rejected"][why] = stats["rejected"].get(why, 0) + 1

            if dry_run:
                if chosen:
                    print(f"[DRY RUN] would save (conf {chosen_conf:.2f}) @{chosen.partition('@')[2]}")
                else:
                    print(f"[DRY RUN] no valid email ({reason})")
                continue

            if chosen:
                conf_label = "high" if chosen_conf >= 0.85 else "medium"
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE normalized_contacts
                        SET email = %s, email_source = 'serpapi_scrape',
                            email_confidence = %s, updated_at = NOW()
                        WHERE id = %s
                    """, (chosen, conf_label, cid))
                conn.commit()
                enriched += 1
                stats["valid_email"] += 1
                stats["found_domains"].append(chosen_dom)
                print(f"OK conf {chosen_conf:.2f} @{chosen_dom}")
            else:
                with conn.cursor() as cur:   # mark attempted so re-runs skip it
                    cur.execute("""
                        UPDATE normalized_contacts
                        SET email_source = 'serpapi_attempted', updated_at = NOW()
                        WHERE id = %s
                    """, (cid,))
                conn.commit()
                failed += 1
                print(f"rejected ({reason})")

        print(f"\n  -- TX enrichment results --")
        print(f"  Searched          : {stats['searched']}")
        print(f"  Returned a website: {stats['with_website']}")
        print(f"  Valid email saved : {stats['valid_email']}")
        print(f"  Rejected by filter: {dict(sorted(stats['rejected'].items()))}")
        print(f"  Sample domains    : {stats['found_domains'][:3]}")
        return {"enriched": enriched, "failed": failed, **stats}

    finally:
        conn.close()


def rematch_tdlr_against_normalized_liens(limit: int = 500) -> int:
    """
    Improved TDLR matching: matches texas_tdlr_contacts against
    normalized_liens by fuzzy business name comparison.
    Sets lien_match=TRUE so bridge_to_email_pool picks them up.

    Runs fast name normalization:
      - strips LLC/INC/CORP/CO suffixes
      - compares first 2 significant words
      - county must match (TX)

    Returns count of newly matched records.
    """
    if not HAS_DB:
        return 0

    conn = get_connection()
    try:
        # Get unmatched TDLR contacts that have emails
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, business_name, owner_name, email,
                       business_county, business_state
                FROM texas_tdlr_contacts
                WHERE lien_match = FALSE
                  AND email IS NOT NULL
                  AND email != ''
                  AND business_state = 'TX'
                LIMIT %s
            """, (limit,))
            tdlr_rows = cur.fetchall()

        if not tdlr_rows:
            print("  No unmatched TDLR contacts with emails found.")
            return 0

        print(f"  Checking {len(tdlr_rows):,} TDLR contacts against normalized_liens...")

        import re as _re

        def normalize_name(name: str) -> str:
            """Strip legal suffixes and normalize for comparison."""
            if not name:
                return ""
            name = name.upper().strip()
            for suffix in [" LLC", " INC", " CORP", " LTD", " CO",
                           " PLLC", " DBA", " L.L.C.", " INC.", " CORP."]:
                name = name.replace(suffix, "")
            name = _re.sub(r"[^A-Z0-9 ]", " ", name)
            name = _re.sub(r"\s+", " ", name).strip()
            return name

        def name_key(name: str) -> str:
            """First 2 significant words as match key."""
            words = [w for w in normalize_name(name).split()
                     if len(w) > 1]
            return " ".join(words[:2])

        matched = 0
        for tdlr_id, biz_name, owner_name, email, county, state in tdlr_rows:
            search_name = biz_name or owner_name or ""
            if not search_name:
                continue
            key = name_key(search_name)
            if len(key) < 4:
                continue

            # Search normalized_liens for TX records with similar name
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT nl.id
                    FROM normalized_liens nl
                    JOIN counties c ON c.id = nl.county_id
                    WHERE nl.state = 'TX'
                      AND (
                          UPPER(nl.business_name) LIKE %s
                          OR UPPER(nl.debtor_name) LIKE %s
                      )
                    LIMIT 1
                """, (f"%{key}%", f"%{key}%"))
                nl_row = cur.fetchone()

            if nl_row:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE texas_tdlr_contacts
                        SET lien_match = TRUE
                        WHERE id = %s
                    """, (tdlr_id,))
                conn.commit()
                matched += 1

        print(f"  TDLR re-match: {matched:,} new lien_match=TRUE records")
        return matched

    finally:
        conn.close()


def enrich_arizona_roc(limit: int = 100, dry_run: bool = False) -> dict:
    """
    Enrich arizona_roc_contacts using SerpAPI website search.
    Searches: business_name + business_city + AZ -> scrapes email from website.
    Saves email directly to arizona_roc_contacts.email.
    Also saves to lien_dbpr_contacts so send_email_sequence picks them up.

    Usage:
      python multi_state_email_enrichment.py --source arizona_roc --limit 100
    """
    if not HAS_DB:
        print("  No DB connection")
        return {"enriched": 0}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, business_name, owner_name, business_city, county, phone
                FROM arizona_roc_contacts
                WHERE (email IS NULL OR email = '')
                  AND lien_match = TRUE
                  AND status = 'Active'
                  AND business_name IS NOT NULL
                  AND business_name != ''
                ORDER BY id
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()

        if not rows:
            print("  No unenriched AZ ROC contacts found.")
            return {"enriched": 0}

        print(f"  Found {len(rows):,} AZ ROC contacts to enrich")
        enriched = 0
        failed   = 0

        for i, (cid, biz_name, owner_name, city, county, phone) in enumerate(rows):
            city_clean = (city or "Phoenix").strip()
            api = get_available_api()
            if not api:
                print(f"\n  All API quotas exhausted.")
                break

            print(f"  [{i+1}/{len(rows)}] [{api}] {biz_name[:40]:<40} ({city_clean}, AZ)",
                  end=" ... ", flush=True)

            if dry_run:
                print("[DRY RUN]")
                continue

            # For AZ ROC: search with license type context for better results
            urls = search_for_website(biz_name, city_clean, "AZ")
            if not urls:
                # Fallback: search with owner name if different
                if owner_name and owner_name.lower() != biz_name.lower():
                    urls = search_for_website(owner_name, city_clean, "AZ")
            time.sleep(1.0)

            if not urls:
                print("no results")
                failed += 1
                continue

            email = None
            for url in urls:
                email = scrape_email_from_url(url)
                if email:
                    break

            if email and not is_junk_email(email, biz_name):
                # Save to arizona_roc_contacts
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE arizona_roc_contacts
                        SET email = %s, updated_at = NOW()
                        WHERE id = %s
                    """, (email.lower().strip(), cid))

                # Also save to lien_contact_enrichment + lien_dbpr_contacts
                # Get county_id for this AZ county
                county_name = (county or "Maricopa").strip()
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM counties WHERE county_name ILIKE %s AND state = 'AZ'",
                        (county_name,)
                    )
                    row = cur.fetchone()
                    if row:
                        county_id = row[0]
                    else:
                        cur.execute(
                            "INSERT INTO counties (county_name, state, active, created_at) "
                            "VALUES (%s, 'AZ', TRUE, NOW()) RETURNING id",
                            (county_name,)
                        )
                        county_id = cur.fetchone()[0]

                # Create normalized_lien placeholder if needed
                import hashlib
                h = hashlib.md5(f"roc|{cid}|AZ|{county_name}".encode()).hexdigest()
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO normalized_liens
                            (county_id, debtor_name, business_name, filing_type,
                             lien_type, lien_source, normalized_hash, state)
                        VALUES (%s, %s, %s, 'TAX LIEN', 'TAX LIEN', 'arizona_roc', %s, 'AZ')
                        ON CONFLICT (normalized_hash) DO NOTHING
                        RETURNING id
                    """, (county_id, (biz_name or owner_name or "")[:250],
                          biz_name[:250], h))
                    ret = cur.fetchone()
                    if ret:
                        lien_id = ret[0]
                    else:
                        cur.execute(
                            "SELECT id FROM normalized_liens WHERE normalized_hash = %s", (h,)
                        )
                        lien_id = cur.fetchone()[0]

                # Insert into lien_dbpr_contacts
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO lien_dbpr_contacts
                            (lien_id, county_id, debtor_name, full_name,
                             email, phone, state, confidence, dbpr_score)
                        VALUES (%s, %s, %s, %s, %s, %s, 'AZ', 'medium', 65.0)
                        ON CONFLICT (lien_id) DO UPDATE SET
                            email = EXCLUDED.email
                    """, (lien_id, county_id,
                          (biz_name or owner_name or "")[:250],
                          (biz_name or owner_name or "")[:250],
                          email.lower().strip(),
                          (phone or "")[:50]))

                conn.commit()
                enriched += 1
                print(f"✅ {email}")
            elif email:
                print(f"⚠ junk filtered: {email}")
                failed += 1
            else:
                print("no email found")
                failed += 1

        print(f"\n  Enriched : {enriched:,}")
        print(f"  Failed   : {failed:,}")
        if enriched + failed:
            print(f"  Rate     : {enriched/(enriched+failed)*100:.1f}%")
        return {"enriched": enriched, "failed": failed}

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Multi-State Email Enrichment Engine")
    parser.add_argument("--state",    default=None,
                        choices=list(STATE_CONFIGS.keys()),
                        help="Enrich specific state")
    parser.add_argument("--all",      action="store_true",
                        help="Enrich all available states")
    parser.add_argument("--limit",    type=int, default=100,
                        help="Max records per state (default: 100)")
    parser.add_argument("--rematch-tdlr", action="store_true",
                        help="Re-match TDLR contacts against normalized_liens")
    parser.add_argument("--resume",   action="store_true",
                        help="Resume from last position")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Search only — don't save emails")
    parser.add_argument("--stats",    action="store_true",
                        help="Show enrichment stats")
    parser.add_argument("--test-apis", action="store_true",
                        help="Test API connections")
    parser.add_argument("--source",   default=None,
                        choices=["normalized_liens", "normalized_contacts", "arizona_roc"],
                        help="Enrich from an alternate source table via Google CSE")
    parser.add_argument("--min-score", type=int, default=65,
                        help="Min match_score for --source normalized_contacts (default: 65)")
    parser.add_argument("--county",   default=None,
                        help="Filter by county (use with --source normalized_liens)")
    args = parser.parse_args()

    # Arizona ROC mode
    if getattr(args, "rematch_tdlr", False):
        print("\nRe-matching TDLR contacts against normalized_liens...")
        matched = rematch_tdlr_against_normalized_liens(limit=args.limit * 5)
        print(f"Done — {matched:,} newly matched")
        return

    if args.source == "arizona_roc":
        quota = get_quota_status()
        print(f"\n{'='*65}")
        print(f"  Arizona ROC Enrichment (SerpAPI)")
        print(f"  Limit  : {args.limit}")
        print(f"  SerpAPI: {quota.get('serpapi_remaining', 0)} queries remaining")
        print(f"{'='*65}\n")
        enrich_arizona_roc(
            limit=args.limit,
            dry_run=args.dry_run,
        )
        return

    # normalized_contacts mode (lien-matched, score-gated contacts)
    if args.source == "normalized_contacts":
        st = (args.state or "tx").upper()
        quota = get_quota_status()
        print(f"\n{'='*65}")
        print(f"  Normalized Contacts Enrichment (SerpAPI) — lien-matched")
        print(f"  State    : {st}")
        print(f"  Min score: {args.min_score}")
        print(f"  Limit    : {args.limit}")
        print(f"  SerpAPI  : {quota['serpapi_used']} searches used today")
        print(f"  {'DRY RUN' if args.dry_run else 'LIVE'}")
        print(f"{'='*65}\n")
        enrich_normalized_contacts(
            state=st,
            limit=args.limit,
            dry_run=args.dry_run,
            min_score=args.min_score,
        )
        return

    # normalized_liens mode
    if args.source == "normalized_liens":
        quota = get_quota_status()
        print(f"\n{'='*65}")
        print(f"  Normalized Liens Enrichment (Google CSE)")
        print(f"  County : {args.county or 'all'}")
        print(f"  State  : {args.state or 'all'}")
        print(f"  Limit  : {args.limit}")
        print(f"  Google : {quota['google_remaining']} queries remaining today")
        print(f"{'='*65}\n")
        enrich_normalized_liens(
            county=args.county,
            state=args.state,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        return

    if args.stats:
        show_stats()
        return

    if args.test_apis:
        test_apis()
        return

    if not args.state and not args.all:
        parser.print_help()
        return

    # Determine states to run
    if args.all:
        # Sort by priority — FL first, then TX, etc.
        states = sorted(STATE_CONFIGS.keys(),
                        key=lambda s: STATE_CONFIGS[s]["priority"])
    else:
        states = [args.state]

    print(f"\n{'='*65}")
    print(f"  Multi-State Email Enrichment")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    print(f"  States  : {', '.join(s.upper() for s in states)}")
    print(f"  Limit   : {args.limit} per state")
    print(f"  Resume  : {args.resume}")
    print(f"  {'DRY RUN' if args.dry_run else 'LIVE'}")

    quota = get_quota_status()
    print(f"\n  Google   : {quota['google_remaining']} queries remaining today")
    print(f"  ValueSerp: {quota['valueserp_remaining']} queries remaining this month")
    print(f"{'='*65}\n")

    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("multi_state_email_enrichment")
        logger.start()
    except ImportError:
        logger = None

    results = {}
    total_enriched = 0

    for state_code in states:
        cfg = STATE_CONFIGS[state_code]
        print(f"\n── {cfg['name']} ({state_code.upper()}) ──")

        # Check quota before each state
        api = get_available_api()
        if not api:
            print(f"  ⚠ All quotas exhausted — stopping")
            break

        if logger: logger.step_start(f"enrich_{state_code}")

        result = enrich_state(
            state_code,
            limit=args.limit,
            resume=args.resume,
            dry_run=args.dry_run,
        )
        results[state_code] = result
        total_enriched += result.get("enriched", 0)

        if logger:
            logger.step_done(f"enrich_{state_code}",
                             ok="error" not in result,
                             detail=str(result))

        # Check quota after each state
        quota = get_quota_status()
        if quota["google_remaining"] == 0 and quota["valueserp_remaining"] == 0:
            print(f"\n  ⚠ All quotas exhausted after {cfg['name']}")
            break

    # Summary
    print(f"\n{'='*65}")
    print(f"  Enrichment Complete")
    print(f"  Total emails found: {total_enriched:,}")
    print()
    for state_code, result in results.items():
        name = STATE_CONFIGS[state_code]["name"]
        enriched = result.get("enriched", 0)
        failed   = result.get("failed", 0)
        rate     = result.get("match_rate", 0)
        status   = result.get("status", "")
        if status in ("table_missing", "pending"):
            print(f"  ⏳ {name:<15} — table not built yet")
        else:
            print(f"  {'✅' if enriched > 0 else '—'} "
                  f"{name:<15} {enriched:>5} enriched  "
                  f"{failed:>5} failed  {rate:>5.1f}% match rate")
    print(f"{'='*65}\n")

    if logger:
        logger.finish({
            "states":        list(results.keys()),
            "total_enriched": total_enriched,
            "results":       results,
            "dry_run":       args.dry_run,
        })

    show_stats()


if __name__ == "__main__":
    main()
