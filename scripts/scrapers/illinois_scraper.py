r"""
illinois_scraper.py
===================
Illinois data sources for the TaxCase Review data engine.

LIENS  — Cook County (Chicago) federal tax lien filings.
         Recorder of Deeds: https://ccrd.cookcountyil.gov
         Open data (if a recorder dataset is published): data.cookcountyil.gov
LICENSES — Illinois Dept. of Financial & Professional Regulation (IDFPR).
         https://www.idfpr.com/LicenseLookUp/licenselookup.asp
         Target license types: roofing (058), HVAC (004),
         general contractor (016), electrician (017).

HTTP-first (hard rule #6). Cook County's recorder search is an ASP/anti-bot
portal with no documented anonymous JSON API; if a Socrata dataset id is
provided via COOK_LIENS_DATASET we query it, otherwise we probe the recorder
and return 0 (pending Selenium) rather than crash. IDFPR's lookup is a classic
ASP.NET form — we fetch the page (viewstate + token), POST per license type, and
parse the results table; unparseable responses return 0.

DB writes:
  liens    -> normalized_liens     (state='IL', lien_source='cook_recorder')
  licenses -> normalized_contacts  (state='IL', license_source='IL_DPR')
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

IDFPR_LOOKUP = "https://www.idfpr.com/LicenseLookUp/licenselookup.asp"
COOK_RECORDER = "https://ccrd.cookcountyil.gov"
# Optional Socrata recorder dataset (id) on data.cookcountyil.gov.
COOK_LIENS_DATASET = os.getenv("COOK_LIENS_DATASET", "")

# IDFPR license type codes from the task.
IL_LICENSE_TYPES = {
    "058": "roofing",
    "004": "hvac",
    "016": "general contractor",
    "017": "electrician",
}


# ── LIENS — Cook County ────────────────────────────────────────────────────────
def collect_il_liens(limit: int = MAX_PER_COUNTY) -> int:
    """Federal tax liens for Cook County. Uses a Socrata dataset when configured
    (COOK_LIENS_DATASET), otherwise probes the recorder portal and reports the
    auth/anti-bot wall. Returns count of NEW liens (state='IL')."""
    if COOK_LIENS_DATASET:
        return _collect_il_liens_socrata(limit)

    r = http_get(COOK_RECORDER)
    reachable = r is not None and r.status_code < 400
    print("    IL/Cook recorder: "
          + ("reachable but no anonymous lien API/dataset"
             if reachable else "unreachable")
          + " — set COOK_LIENS_DATASET or wire Selenium. (0 liens)")
    return 0


def _collect_il_liens_socrata(limit: int) -> int:
    url = f"https://data.cookcountyil.gov/resource/{COOK_LIENS_DATASET}.json"
    r = http_get(url, params={"$limit": min(limit, MAX_PER_COUNTY)})
    if r is None or r.status_code != 200:
        print(f"    IL/Cook Socrata fetch failed "
              f"(status={getattr(r,'status_code','n/a')}) — 0 liens.")
        return 0
    try:
        records = r.json()
    except ValueError:
        return 0

    conn = get_connection()
    conn.autocommit = False
    added = 0
    try:
        for rec in records:
            # Field names vary by dataset; try common ones.
            name = (rec.get("debtor") or rec.get("grantor")
                    or rec.get("name") or "").strip()
            if not name:
                continue
            doc = (rec.get("document_number") or rec.get("doc_number")
                   or rec.get("id") or name)
            filed = (rec.get("recorded_date") or rec.get("filing_date")
                     or rec.get("date") or "")[:10] or None
            h = hashlib.md5(f"cook|{doc}".encode()).hexdigest()
            with conn.cursor() as cur:
                county_id = get_or_create_county(cur, "Cook", "IL")
                cur.execute("""
                    INSERT INTO normalized_liens
                        (county_id, debtor_name, business_name, filing_type,
                         lien_type, lien_source, normalized_hash, state,
                         filed_date, created_at)
                    VALUES (%s,%s,%s,'FEDERAL TAX LIEN','FEDERAL TAX LIEN',
                            'cook_recorder',%s,'IL',%s,NOW())
                    ON CONFLICT (normalized_hash) DO NOTHING
                    RETURNING id
                """, (county_id, name[:250],
                      name[:250] if is_business(name) else None, h, filed))
                if cur.fetchone():
                    added += 1
            conn.commit()
        print(f"    IL/Cook Socrata: +{added} new IL liens")
    except Exception as e:
        conn.rollback()
        print(f"    IL/Cook store error: {e}")
    finally:
        release_connection(conn)
    return added


# ── LICENSES — IDFPR ───────────────────────────────────────────────────────────
def collect_il_licenses(limit: int = MAX_PER_COUNTY) -> int:
    """Look up IL contractor licenses (roofing/HVAC/GC/electrician) via IDFPR and
    store them in normalized_contacts (state='IL', license_source='IL_DPR').
    Returns count written."""
    session = requests.Session()
    session.headers.update({"User-Agent":
                            "Mozilla/5.0 (compatible; LeadFlowDataEngine/1.0)"})
    form = _idfpr_form_state(session)
    if form is None:
        print("    IL/IDFPR: could not load lookup page — pending. (0 licenses)")
        return 0

    all_rows: list[dict] = []
    for code, label in IL_LICENSE_TYPES.items():
        if len(all_rows) >= limit:
            break
        all_rows.extend(_idfpr_search(session, form, code, label))

    if not all_rows:
        print("    IL/IDFPR: no parseable license rows (ASP.NET viewstate/markup "
              "change or block) — pending. (0 licenses)")
        return 0
    return _store_il_licenses(all_rows[:limit])


def _idfpr_form_state(session: requests.Session):
    """Fetch the IDFPR lookup page and capture ASP.NET viewstate fields."""
    try:
        r = session.get(IDFPR_LOOKUP, timeout=30)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "lxml")
        state = {}
        for fid in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
            el = soup.find("input", {"name": fid})
            if el and el.get("value"):
                state[fid] = el["value"]
        return state
    except requests.RequestException:
        return None


def _idfpr_search(session: requests.Session, form: dict,
                  type_code: str, label: str) -> list[dict]:
    """POST one license-type search and parse the results table. Best-effort —
    IDFPR field names vary by page version, so we post viewstate + a license
    type and parse whatever tabular rows come back."""
    out: list[dict] = []
    try:
        data = dict(form)
        data.update({"LicenseType": type_code, "txtLicenseType": type_code})
        r = session.post(IDFPR_LOOKUP, data=data, timeout=30)
        if r.status_code != 200:
            return out
        soup = BeautifulSoup(r.text, "lxml")
        for tr in soup.select("table tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) < 2:
                continue
            name = cells[0]
            if not name or name.lower() in ("name", "licensee name"):
                continue
            out.append({
                "name":     name,
                "license":  cells[1] if len(cells) > 1 else "",
                "status":   cells[2] if len(cells) > 2 else "",
                "city":     cells[3] if len(cells) > 3 else "",
                "type":     label,
            })
    except Exception:
        pass
    return out


def _looks_like_license(v: str) -> bool:
    """IDFPR license numbers contain digits and are reasonably long. Reject UI
    labels / page chrome the naive HTML parse may have picked up."""
    v = (v or "").strip()
    return len(v) >= 6 and any(ch.isdigit() for ch in v)


def _store_il_licenses(rows: list[dict]) -> int:
    conn = get_connection()
    conn.autocommit = False
    added = 0
    try:
        for rec in rows:
            name = (rec.get("name") or "").strip()
            lic = (rec.get("license") or "").strip()
            if not name or len(name) < 3 or not _looks_like_license(lic):
                continue
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO normalized_contacts
                        (state, state_name, license_number, license_type,
                         license_status, license_source, business_name,
                         owner_name, business_city, data_source)
                    VALUES ('IL','Illinois',%s,%s,%s,'IL_DPR',%s,%s,%s,'illinois_scraper')
                    ON CONFLICT (state, license_number) DO NOTHING
                """, (lic[:100], (rec.get("type", "") or "")[:100],
                      (rec.get("status", "") or "")[:50],
                      name[:200], name[:200], (rec.get("city") or "")[:100]))
                if cur.rowcount:
                    added += 1
            conn.commit()
        print(f"    IL/IDFPR: +{added} contractor licenses")
    except Exception as e:
        conn.rollback()
        print(f"    IL/IDFPR store error: {e}")
    finally:
        release_connection(conn)
    return added
