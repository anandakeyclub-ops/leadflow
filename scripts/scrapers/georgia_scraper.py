r"""
georgia_scraper.py
==================
Georgia data sources for the TaxCase Review data engine.

LIENS  — GSCCCA statewide lien index (Georgia Superior Court Clerks'
         Cooperative Authority), federal tax lien filings.
         https://www.gsccca.org/search  /  https://search.gsccca.org
LICENSES — Georgia Secretary of State business search.
         https://ecorp.sos.ga.gov/BusinessSearch

HTTP-first (hard rule #6): both functions attempt a plain `requests` flow.
The GSCCCA lien index requires a (free) registered login and has anti-bot
protection, so an unauthenticated GET returns the login page rather than
results — when that wall is detected the scraper logs it and returns 0 instead
of crashing the daily runner. The GA SOS business search is an ASP.NET app
guarded by an antiforgery token; we fetch the token + cookies, POST the search,
and parse the results table. If the live response can't be parsed (markup
change / block) we return 0.

DB writes:
  liens    -> normalized_liens     (state='GA', lien_source='gsccca')
  licenses -> normalized_contacts  (state='GA', license_source='GA_SOS')

Credentials (optional, for a future authenticated/Selenium path) come from .env:
  GSCCCA_USERNAME / GSCCCA_PASSWORD
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from app.core.db import get_connection, release_connection  # noqa: E402
from scripts.data_engine.data_collector import (  # noqa: E402
    http_get, get_or_create_county, MAX_PER_COUNTY, is_business,
)

GSCCCA_BASE   = "https://search.gsccca.org"
GSCCCA_LIEN   = "https://search.gsccca.org/Lien/namesearch.asp"
GA_SOS_SEARCH = "https://ecorp.sos.ga.gov/BusinessSearch"

# Contractor entity types we care about (search keywords).
GA_CONTRACTOR_KEYWORDS = [
    "roofing", "hvac", "heating and air", "general contractor",
    "electrical", "plumbing",
]


# ── LIENS — GSCCCA ─────────────────────────────────────────────────────────────
def collect_ga_liens(limit: int = MAX_PER_COUNTY) -> int:
    """Attempt the GSCCCA statewide federal-tax-lien index over HTTP.

    GSCCCA gates lien searches behind a registered login; an anonymous request
    is redirected to / returns the sign-in page. We detect that and return 0
    (a credentialed Selenium path is the documented next step). Returns count of
    NEW liens written to normalized_liens (state='GA')."""
    r = http_get(GSCCCA_LIEN, params={"bsearch": "Federal Tax Lien"})
    if r is None:
        print("    GA/GSCCCA: unreachable — 0 liens.")
        return 0

    body = r.text or ""
    if (r.status_code in (301, 302, 401, 403)
            or "login" in body.lower() or "sign in" in body.lower()
            or "username" in body.lower()):
        print("    GA/GSCCCA: login wall detected (free registration + anti-bot)"
              " — HTTP path blocked. Pending GSCCCA_USERNAME/PASSWORD + Selenium."
              " (0 liens)")
        return 0

    rows = _parse_gsccca_results(body)
    if not rows:
        print("    GA/GSCCCA: no parseable federal-tax-lien rows in response"
              " (markup change or empty) — 0 liens.")
        return 0

    return _store_ga_liens(rows[:limit])


def _parse_gsccca_results(html: str) -> list[dict]:
    """Best-effort parse of a GSCCCA lien results table -> [{name, county, date}]."""
    out: list[dict] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        for tr in soup.select("table tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) < 2:
                continue
            name = cells[0]
            if not name or name.lower() in ("name", "debtor"):
                continue
            out.append({
                "name":   name,
                "county": cells[1] if len(cells) > 1 else "",
                "date":   cells[2] if len(cells) > 2 else None,
            })
    except Exception:
        pass
    return out


def _store_ga_liens(rows: list[dict]) -> int:
    conn = get_connection()
    conn.autocommit = False
    added = 0
    try:
        for rec in rows:
            name = (rec.get("name") or "").strip()
            if not name:
                continue
            county_name = (rec.get("county") or "Fulton").strip() or "Fulton"
            h = hashlib.md5(f"gsccca|{name}|{county_name}|{rec.get('date')}"
                            .encode()).hexdigest()
            with conn.cursor() as cur:
                county_id = get_or_create_county(cur, county_name, "GA")
                cur.execute("""
                    INSERT INTO normalized_liens
                        (county_id, debtor_name, business_name, filing_type,
                         lien_type, lien_source, normalized_hash, state,
                         filed_date, created_at)
                    VALUES (%s,%s,%s,'FEDERAL TAX LIEN','FEDERAL TAX LIEN',
                            'gsccca',%s,'GA',%s,NOW())
                    ON CONFLICT (normalized_hash) DO NOTHING
                    RETURNING id
                """, (county_id, name[:250],
                      name[:250] if is_business(name) else None,
                      h, (rec.get("date") or None)))
                if cur.fetchone():
                    added += 1
            conn.commit()
        print(f"    GA/GSCCCA: +{added} new GA liens")
    except Exception as e:
        conn.rollback()
        print(f"    GA/GSCCCA store error: {e}")
    finally:
        release_connection(conn)
    return added


# ── LICENSES — GA Secretary of State business search ───────────────────────────
def collect_ga_licenses(limit: int = MAX_PER_COUNTY) -> int:
    """Search GA SOS business records for contractor entity types and store them
    in normalized_contacts (state='GA', license_source='GA_SOS'). Paginates by
    keyword. Returns count written."""
    session = requests.Session()
    session.headers.update({"User-Agent":
                            "Mozilla/5.0 (compatible; LeadFlowDataEngine/1.0)"})
    token = _ga_sos_token(session)
    if token is None:
        print("    GA/SOS: could not obtain search page/token — pending. (0 licenses)")
        return 0

    all_rows: list[dict] = []
    for kw in GA_CONTRACTOR_KEYWORDS:
        if len(all_rows) >= limit:
            break
        rows = _ga_sos_search(session, token, kw)
        all_rows.extend(rows)
    # de-dupe by control number / name
    seen, deduped = set(), []
    for r in all_rows:
        key = (r.get("control_number") or r.get("name", "")).lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(r)

    if not deduped:
        print("    GA/SOS: no parseable business rows returned (ASP.NET markup "
              "change or block) — pending. (0 licenses)")
        return 0
    return _store_ga_licenses(deduped[:limit])


def _ga_sos_token(session: requests.Session):
    """Fetch the search page and extract the ASP.NET antiforgery token."""
    try:
        r = session.get(GA_SOS_SEARCH, timeout=30)
        if r.status_code != 200:
            return None
        m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"',
                      r.text)
        return m.group(1) if m else ""
    except requests.RequestException:
        return None


def _ga_sos_search(session: requests.Session, token: str,
                   keyword: str) -> list[dict]:
    """POST one keyword search and parse the results grid. Best-effort."""
    out: list[dict] = []
    try:
        data = {
            "BusinessName":  keyword,
            "searchType":    "Contains",
        }
        if token:
            data["__RequestVerificationToken"] = token
        r = session.post(GA_SOS_SEARCH, data=data, timeout=30)
        if r.status_code != 200:
            return out
        soup = BeautifulSoup(r.text, "lxml")
        for tr in soup.select("table tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) < 2:
                continue
            name = cells[0]
            if not name or name.lower() in ("business name", "name"):
                continue
            out.append({
                "name":           name,
                "control_number": cells[1] if len(cells) > 1 else "",
                "status":         cells[2] if len(cells) > 2 else "",
                "keyword":        keyword,
            })
    except Exception:
        pass
    return out


def _looks_like_control(v: str) -> bool:
    """GA SOS control numbers are 6-12 digits. Anything else is page chrome
    (labels, nav) the naive HTML parse picked up — reject it."""
    return bool(re.fullmatch(r"\d{6,12}", (v or "").strip()))


def _store_ga_licenses(rows: list[dict]) -> int:
    conn = get_connection()
    conn.autocommit = False
    added = 0
    try:
        for rec in rows:
            name = (rec.get("name") or "").strip()
            lic = (rec.get("control_number") or "").strip()
            # Only real business rows: a valid control number + a plausible name.
            if not name or len(name) < 3 or not _looks_like_control(lic):
                continue
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO normalized_contacts
                        (state, state_name, license_number, license_type,
                         license_status, license_source, business_name,
                         owner_name, data_source)
                    VALUES ('GA','Georgia',%s,%s,%s,'GA_SOS',%s,%s,'georgia_scraper')
                    ON CONFLICT (state, license_number) DO NOTHING
                """, (lic[:100], (rec.get("keyword", "") or "")[:100],
                      (rec.get("status", "") or "")[:50],
                      name[:200], name[:200]))
                if cur.rowcount:
                    added += 1
            conn.commit()
        print(f"    GA/SOS: +{added} contractor businesses")
    except Exception as e:
        conn.rollback()
        print(f"    GA/SOS store error: {e}")
    finally:
        release_connection(conn)
    return added
