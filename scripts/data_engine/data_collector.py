r"""
data_collector.py
=================
Centralized data collection + enrichment engine for TaxCase Review.

Pipeline per state:
    collect_liens   -> normalized_liens          (state field set)
    collect_licenses-> normalized_contacts        (license universe)
    match_liens_to_licenses                       (link contacts <-> liens)
    enrich_emails_pdl                             (People Data Labs)
    enrich_emails_cse                             (Google CSE / ValueSerp fallback)
    sync_to_email_pipeline                        (-> lien_dbpr_contacts)

HARD RULES honored here:
  1. We NEVER ALTER the schema of normalized_liens, lien_dbpr_contacts,
     email_sends, email_opens, email_clicks. We only INSERT rows
     (sync writes new lien_dbpr_contacts rows, exactly like the existing
     arizona_roc enrichment already does).
  2. We REUSE existing functions by import — scoring, PDL, search/scrape.
  3. 1s min delay per network request, exponential backoff on HTTP 429.
  4. Progress checkpoints — reruns skip already-collected records.
  5. Max 500 records per county per run for live scrapers.
  6. HTTP first, Selenium only as fallback.
  7. All credentials come from .env — never hardcoded.

The existing email sequence reads from lien_dbpr_contacts / normalized_liens.
This engine feeds that sequence through sync_to_email_pipeline(); it does NOT
touch multi_state_contacts or send_email_sequence.py.

Usage:
  python scripts/data_engine/data_collector.py --state fl
  python scripts/data_engine/data_collector.py --state ny --county "New York"
  python scripts/data_engine/data_collector.py --licenses fl
  python scripts/data_engine/data_collector.py --match az
  python scripts/data_engine/data_collector.py --pdl az --limit 50
  python scripts/data_engine/data_collector.py --cse az --limit 30
  python scripts/data_engine/data_collector.py --sync
  python scripts/data_engine/data_collector.py --stats
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# Force UTF-8 stdout/stderr so box-drawing chars / ✓ never crash a run on a
# non-UTF-8 Windows console (cp1252) under Task Scheduler. No-op on UTF-8
# streams. Matches the guard in pipeline_log.py.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

# ── Make the leadflow root importable regardless of cwd ───────────────────────
LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

from app.core.db import get_connection, release_connection  # noqa: E402

# ── REUSED functions (imported, never rewritten) ──────────────────────────────
from app.workers.enrich_liens_from_dbpr import (  # noqa: E402
    score_lien_vs_dbpr,
    find_best_dbpr_match,
    NOISE_TOKENS,
)
from app.workers.enrich_palm_beach_from_dbpr import norm_text  # noqa: E402
from app.workers.enrich_liens_pdl import (  # noqa: E402
    pdl_person_search,
    pdl_company_search,
    extract_person_contact,
    extract_company_contact,
    parse_person_name,
    is_business,
)
from scripts.enrichment.multi_state_email_enrichment import (  # noqa: E402
    search_for_website,
    scrape_email_from_url,
    is_junk_email,
    load_quota,
    save_quota,
    get_available_api,
)

# ── Config ────────────────────────────────────────────────────────────────────
CAMPAIGN_ID = os.getenv("CAMPAIGN_ID", "lien_outreach_2026")
MATCH_THRESHOLD = 0.55          # score >= 55 (score_lien_vs_dbpr returns 0..1)
# Per-state overrides — TX name formats diverge more (lien=business names,
# TDLR=person names in "LAST, FIRST"), so it needs a looser gate.
STATE_MATCH_THRESHOLD = {"tx": 0.45}
MAX_PER_COUNTY = 500            # hard rule #5
MIN_REQUEST_DELAY = 1.0         # hard rule #3
PDL_API_KEY = os.getenv("PDL_API_KEY", "")

DATA_DIR = LEADFLOW_DIR / "data" / "data_engine"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CHECKPOINT_FILE = DATA_DIR / "collection_checkpoints.json"

STATE_NAMES = {
    "fl": "Florida", "tx": "Texas", "az": "Arizona", "ga": "Georgia",
    "ca": "California", "ny": "New York", "nc": "North Carolina",
    "il": "Illinois", "oh": "Ohio", "pa": "Pennsylvania",
}

# Default city used for enrichment when a contact has no business_city.
STATE_DEFAULT_CITY = {
    "az": "Phoenix", "tx": "Houston", "ny": "New York", "ga": "Atlanta",
    "ca": "Los Angeles", "nc": "Charlotte", "il": "Chicago",
    "oh": "Columbus", "pa": "Philadelphia", "fl": "Miami",
}


# ─────────────────────────────────────────────────────────────────────────────
# Checkpoint helpers (hard rule #4)
# ─────────────────────────────────────────────────────────────────────────────

def _load_checkpoints() -> dict:
    if CHECKPOINT_FILE.exists():
        try:
            return json.loads(CHECKPOINT_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_checkpoint(key: str, value) -> None:
    cp = _load_checkpoints()
    cp[key] = value
    CHECKPOINT_FILE.write_text(json.dumps(cp, indent=2, default=str))


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helper with polite delay + exponential backoff on 429 (hard rule #3)
# ─────────────────────────────────────────────────────────────────────────────

_UA = {"User-Agent": "Mozilla/5.0 (compatible; LeadFlowDataEngine/1.0)"}


def http_get(url: str, params: dict | None = None, timeout: int = 30,
             max_retries: int = 4, headers: dict | None = None):
    """GET with 1s polite delay and exponential backoff on 429/5xx."""
    delay = MIN_REQUEST_DELAY
    h = dict(_UA)
    if headers:
        h.update(headers)
    for attempt in range(max_retries):
        time.sleep(MIN_REQUEST_DELAY)
        try:
            r = requests.get(url, params=params, headers=h, timeout=timeout)
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                print(f"    HTTP error (final): {e}")
                return None
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code == 429 or r.status_code >= 500:
            print(f"    HTTP {r.status_code} — backing off {delay:.0f}s")
            time.sleep(delay)
            delay *= 2
            continue
        return r
    return None


# ─────────────────────────────────────────────────────────────────────────────
# County resolution (reused pattern from the existing AZ enrichment)
# ─────────────────────────────────────────────────────────────────────────────

def get_or_create_county(cur, county_name: str, state_abbr: str) -> int:
    cur.execute(
        "SELECT id FROM counties WHERE county_name ILIKE %s AND state = %s",
        (county_name, state_abbr.upper()),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO counties (county_name, state, active, created_at) "
        "VALUES (%s, %s, TRUE, NOW()) RETURNING id",
        (county_name, state_abbr.upper()),
    )
    return cur.fetchone()[0]


# ═════════════════════════════════════════════════════════════════════════════
# STEP A — collect_liens
# ═════════════════════════════════════════════════════════════════════════════

def collect_liens(state_code: str, county: Optional[str] = None) -> int:
    """
    Route to the correct lien source per state. Writes to normalized_liens
    with state set. Returns count of NEW liens added.
    """
    s = state_code.lower()
    print(f"\n  [collect_liens] {STATE_NAMES.get(s, s.upper())}"
          f"{' / ' + county if county else ''}")

    if s == "ny":
        return collect_liens_ny_acris(county=county)
    if s == "az":
        return collect_liens_az_maricopa()
    if s == "ga":
        # GSCCCA gates automated login behind a CAPTCHA, so reuse a saved browser
        # session instead of logging in fresh each run. One-time setup:
        #   python scripts/scrapers/georgia_scraper.py --save-session
        # (re-run when the session expires). Skip launching a browser entirely
        # until a session exists, so the unattended daily run stays fast.
        from scripts.scrapers.georgia_scraper import collect_ga_liens, GA_SESSION_FILE
        if not GA_SESSION_FILE.exists():
            print("    GA/GSCCCA: no saved session — run "
                  "`python scripts/scrapers/georgia_scraper.py --save-session` "
                  "once to enable GA collection. (0 liens)")
            return 0
        return collect_ga_liens(use_session=True)
    if s == "il":
        # Prefer CourtListener (free federal docket API, no WAF) when a token is
        # configured; otherwise fall back to the SOS UCC Selenium scraper.
        if os.getenv("COURTLISTENER_TOKEN"):
            from scripts.scrapers.illinois_scraper import collect_il_liens_courtlistener
            return collect_il_liens_courtlistener()
        from scripts.scrapers.illinois_scraper import collect_il_liens
        print("    IL: COURTLISTENER_TOKEN not set — using SOS UCC scraper fallback.")
        return collect_il_liens()
    if s in ("fl", "tx"):
        # FL (9 county portals) and TX (Harris) liens are produced by the
        # existing, credentialed Selenium scrapers that already write to
        # normalized_liens (app/workers/scrape_*_liens.py, weekly_scrape.py,
        # scripts/scrapers/selenium_tx_scraper.py). The data engine does not
        # duplicate those logins; for FL/TX it focuses on licenses -> match ->
        # enrich -> sync. Run the dedicated scrapers to add new liens.
        print(f"    {s.upper()} liens come from the existing dedicated "
              f"scrapers — skipping duplicate scrape here.")
        return 0

    # GA / IL / NC / OH / PA recorder scrapers — HTTP-first.
    # These public-recorder portals require live, per-site reverse engineering
    # (session tokens / CAPTCHAs vary by county vendor). Scaffolded here so the
    # daily runner never crashes; implement per county against the live site.
    return collect_liens_recorder_pending(s, county)


# ── NYC ACRIS (Socrata open data) ─────────────────────────────────────────────
# Federal tax liens are PERSONAL PROPERTY records in ACRIS (class "UCC AND
# FEDERAL LIENS"; party1=DEBTOR, party2=SECURED PARTY/IRS) — NOT real property.
# Verified live: Personal Property Master sv7x-dduq has doc_type 'FTL' (Federal
# Tax Lien) and fedtax_* fields; Personal Property Parties is nbbg-wtuz.
# (The spec's 8h5j-fqxa is the Real Property Legals dataset and has no doc_type.)
ACRIS_MASTER   = "https://data.cityofnewyork.us/resource/sv7x-dduq.json"
ACRIS_PARTIES  = "https://data.cityofnewyork.us/resource/nbbg-wtuz.json"
# Verified federal-tax-lien doc_type code in the PP master. Releases/withdrawals
# (RFTL, DPFTL, NAFTL, ...) are intentionally excluded — we want active liens.
ACRIS_FTL_CODES = ["FTL"]
# Optional Socrata app token raises the anonymous rate limit. Either name works.
NYC_APP_TOKEN  = os.getenv("NYC_APP_TOKEN") or os.getenv("SOCRATA_APP_TOKEN") or ""
ACRIS_BOROUGH  = {"1": "New York", "2": "Bronx", "3": "Kings",
                  "4": "Queens", "5": "Richmond"}
# Party names that are the lienholder (the IRS / govt), never the debtor.
GOV_PARTY_MARKERS = (
    "INTERNAL REVENUE", "UNITED STATES", "U S A", "U.S.A", "USA",
    "DEPARTMENT OF TREASURY", "DEPT OF TREASURY", "SECRETARY OF",
    "STATE OF NEW YORK", "DEPARTMENT OF TAXATION", "CITY OF NEW YORK",
    "NYS ", "NYC ",
)


def _socrata_headers() -> dict:
    return {"X-App-Token": NYC_APP_TOKEN} if NYC_APP_TOKEN else {}


def _acris_in_clause(values) -> str:
    """SoQL IN list with single-quote escaping: ('A','B')."""
    return "(" + ",".join("'" + str(v).replace("'", "''") + "'"
                          for v in values) + ")"


def _acris_borough_county(code) -> str:
    return ACRIS_BOROUGH.get(str(code).strip(), "New York")


def _acris_is_gov(name: str) -> bool:
    u = (name or "").upper()
    return any(m in u for m in GOV_PARTY_MARKERS)


def _acris_pick_debtor(parties: list) -> dict:
    """
    From a document's ACRIS parties pick the taxpayer/debtor: the non-government
    party. On ACRIS Personal Property federal tax liens party_type '1' is the
    DEBTOR and '2' is the SECURED PARTY (the IRS), so prefer '1', then '2'. The
    IRS party is also dropped by the government-name filter. Returns the party
    dict (name/address/city/state/zip) or {} if none usable.
    """
    named = [p for p in parties if (p.get("name") or "").strip()]
    nongov = [p for p in named if not _acris_is_gov(p.get("name", ""))]
    pool = nongov or named
    if not pool:
        return {}
    pool.sort(key=lambda p: (str(p.get("party_type")) != "1",
                             str(p.get("party_type")) != "2"))
    return pool[0]


def _resolve_acris_doc_types() -> list:
    """
    ACRIS doc_type code(s) for federal tax liens. NY_ACRIS_DOC_TYPES env var
    (comma list) overrides; otherwise the verified default ACRIS_FTL_CODES.
    """
    env = os.getenv("NY_ACRIS_DOC_TYPES", "").strip()
    if env:
        return [c.strip() for c in env.split(",") if c.strip()]
    return list(ACRIS_FTL_CODES)


def _acris_fetch_parties(doc_ids: list, headers: dict, chunk: int = 75) -> dict:
    """Bulk-fetch parties for many documents at once. Returns doc_id -> [party]."""
    out: dict[str, list] = {}
    for i in range(0, len(doc_ids), chunk):
        sub = doc_ids[i:i + chunk]
        r = http_get(ACRIS_PARTIES, headers=headers, params={
            "$where": f"document_id in {_acris_in_clause(sub)}",
            "$limit": chunk * 25,
        })
        if r is None or r.status_code != 200:
            continue
        try:
            for p in r.json():
                out.setdefault(p.get("document_id"), []).append(p)
        except ValueError:
            continue
    return out


def collect_liens_ny_acris(county: Optional[str] = None,
                           doc_type: Optional[str] = None) -> int:
    """
    NYC ACRIS open data (Socrata, free; optional app token).
      Master:  8h5j-fqxa   Parties: 636b-3b5g   Doc codes: 7isb-wh4c
    Pulls federal-tax-lien master records, bulk-joins party names, and writes
    debtor + address to normalized_liens (state='NY').

    Incremental: a recorded_datetime high-watermark (checkpointed) means each
    run only pulls documents recorded since the last run — no gaps, no drift.
    First run defaults to the last 365 days. Idempotent via normalized_hash.
    Max 500 records per run (hard rule #5).
    """
    import hashlib

    headers = _socrata_headers()
    codes = [doc_type] if doc_type else _resolve_acris_doc_types()
    code_in = _acris_in_clause(codes)

    wm_key = "ny_acris_recorded_watermark"
    default_wm = date.today().replace(year=date.today().year - 1).isoformat() \
        + "T00:00:00.000"
    watermark = _load_checkpoints().get(wm_key) or default_wm

    r = http_get(ACRIS_MASTER, headers=headers, params={
        "$where": f"doc_type in {code_in} AND recorded_datetime > '{watermark}'",
        "$order": "recorded_datetime",
        "$limit": MAX_PER_COUNTY,
    })
    if r is None or r.status_code != 200:
        print(f"    ACRIS master fetch failed "
              f"(status={getattr(r, 'status_code', 'n/a')}, codes={codes})")
        return 0

    try:
        masters = r.json()
    except ValueError:
        print("    ACRIS: master response was not JSON")
        return 0
    if not masters:
        print(f"    ACRIS: nothing new since {watermark} (codes={codes})")
        return 0

    doc_ids = [m.get("document_id") for m in masters if m.get("document_id")]
    parties_by_doc = _acris_fetch_parties(doc_ids, headers)

    conn = get_connection()
    conn.autocommit = False
    added = 0
    max_seen = watermark
    try:
        for m in masters:
            doc_id = m.get("document_id")
            if not doc_id:
                continue
            rec_dt = m.get("recorded_datetime") or ""
            if rec_dt > max_seen:
                max_seen = rec_dt

            county_name = _acris_borough_county(m.get("recorded_borough"))
            if county and county.lower() not in county_name.lower():
                continue

            party = _acris_pick_debtor(parties_by_doc.get(doc_id, []))
            debtor = (party.get("name") or "").strip()
            if not debtor:
                continue

            amount = m.get("document_amt")
            try:
                amount = float(amount) if amount not in (None, "") else None
            except (TypeError, ValueError):
                amount = None
            filed = (m.get("fedtax_assessment_date")
                     or m.get("recorded_datetime") or "")[:10] or None

            h = hashlib.md5(f"acris|{doc_id}".encode()).hexdigest()
            with conn.cursor() as cur:
                county_id = get_or_create_county(cur, county_name, "NY")
                cur.execute("""
                    INSERT INTO normalized_liens
                        (county_id, debtor_name, business_name, filing_type,
                         lien_type, lien_source, normalized_hash, state,
                         amount, filed_date, address_1, city, zip, created_at)
                    VALUES (%s,%s,%s,'FEDERAL TAX LIEN','FEDERAL TAX LIEN',
                            'nyc_acris',%s,'NY',%s,%s,%s,%s,%s,NOW())
                    ON CONFLICT (normalized_hash) DO NOTHING
                    RETURNING id
                """, (county_id, debtor[:250],
                      debtor[:250] if is_business(debtor) else None,
                      h, amount, filed,
                      (party.get("address_1") or "")[:250] or None,
                      (party.get("city") or "")[:100] or None,
                      (party.get("zip") or "")[:20] or None))
                if cur.fetchone():
                    added += 1
            conn.commit()

        if max_seen > watermark:
            _save_checkpoint(wm_key, max_seen)
        print(f"    ACRIS: +{added} new NY liens "
              f"(scanned {len(masters)}, watermark -> {max_seen})")
    except Exception as e:
        conn.rollback()
        print(f"    ACRIS error: {e}")
    finally:
        release_connection(conn)
    return added


def collect_liens_az_maricopa() -> int:
    """Reuse the existing Maricopa HTTP scraper (writes to normalized_liens)."""
    try:
        import importlib.util
        # maricopa_lien_scraper.py lives in scripts/archive/ (moved out of root
        # during the cleanup). Fall back to the old root location if present.
        candidates = [
            LEADFLOW_DIR / "scripts" / "archive" / "maricopa_lien_scraper.py",
            LEADFLOW_DIR / "maricopa_lien_scraper.py",
        ]
        path = next((p for p in candidates if p.exists()), candidates[0])
        spec = importlib.util.spec_from_file_location(
            "maricopa_lien_scraper", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"    Could not load maricopa_lien_scraper: {e}")
        return 0
    try:
        records = mod.scrape_maricopa(days_back=30)
        result = mod.import_to_db(records)
        added = result.get("imported", 0) if isinstance(result, dict) else 0
        print(f"    Maricopa: +{added} new AZ liens")
        return added
    except Exception as e:
        print(f"    Maricopa scrape error: {e}")
        return 0


def collect_liens_recorder_pending(state_code: str,
                                   county: Optional[str]) -> int:
    """
    HTTP-first scaffold for county-recorder lien scrapers
    (GA Fulton, NC Mecklenburg/Wake, IL Cook, OH Hamilton/Franklin,
    PA Philadelphia). These public portals need per-site reverse engineering
    against the live endpoint; until that is wired they log and return 0 so the
    daily runner is never blocked.
    """
    print(f"    {state_code.upper()} recorder scraper not yet wired to a live "
          f"endpoint — pending. (0 liens)")
    return 0


# ═════════════════════════════════════════════════════════════════════════════
# STEP B — collect_licenses
# ═════════════════════════════════════════════════════════════════════════════

def collect_licenses(state_code: str) -> int:
    """
    Populate normalized_contacts with the license universe for a state.
    FL/TX/AZ copy from the existing source tables (set-based, idempotent).
    Other states' license lookups are pending live integration.
    Returns count written.
    """
    s = state_code.lower()
    print(f"\n  [collect_licenses] {STATE_NAMES.get(s, s.upper())}")

    if s == "fl":
        return _copy_fl_licenses()
    if s == "tx":
        return _copy_tx_licenses()
    if s == "az":
        return _copy_az_licenses()
    if s == "ga":
        from scripts.scrapers.georgia_scraper import collect_ga_licenses
        return collect_ga_licenses()
    if s == "il":
        from scripts.scrapers.illinois_scraper import collect_il_licenses
        return collect_il_licenses()

    print(f"    {s.upper()} license source not yet wired "
          f"(CA CSLB / NY DOB / NC LBGC / OH / PA) — pending. (0 licenses)")
    return 0


def _copy_fl_licenses() -> int:
    """lien_dbpr_contacts -> normalized_contacts (FL). Idempotent by lien_id."""
    conn = get_connection()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO normalized_contacts
                    (state, state_name, county, license_number, license_type,
                     license_source, owner_name, business_name,
                     business_address, business_city, business_zip, phone,
                     email, has_lien_match, lien_id, email_source,
                     email_confidence, data_source)
                SELECT 'FL', 'Florida', c.county_name,
                       NULLIF(ldc.license_number, ''), ldc.trade, 'dbpr',
                       ldc.debtor_name, ldc.full_name,
                       ldc.mailing_address, ldc.city, ldc.zip, ldc.phone,
                       NULLIF(ldc.email, ''), TRUE, ldc.lien_id,
                       CASE WHEN NULLIF(ldc.email,'') IS NOT NULL
                            THEN 'dbpr' END,
                       CASE WHEN NULLIF(ldc.email,'') IS NOT NULL
                            THEN COALESCE(ldc.confidence,'medium') ELSE 'low' END,
                       'lien_dbpr_contacts'
                FROM lien_dbpr_contacts ldc
                LEFT JOIN counties c ON c.id = ldc.county_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM normalized_contacts nc
                    WHERE nc.data_source = 'lien_dbpr_contacts'
                      AND nc.lien_id = ldc.lien_id
                )
                ON CONFLICT (state, license_number) DO NOTHING
            """)
            n = cur.rowcount
        conn.commit()
        print(f"    FL: +{n} contacts copied from lien_dbpr_contacts")
        return n
    except Exception as e:
        conn.rollback()
        print(f"    FL copy error: {e}")
        return 0
    finally:
        release_connection(conn)


def _copy_tx_licenses() -> int:
    """texas_tdlr_contacts -> normalized_contacts (TX)."""
    conn = get_connection()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO normalized_contacts
                    (state, state_name, county, license_number, license_type,
                     license_status, license_source, owner_name, business_name,
                     business_address, business_city, business_zip, phone,
                     email, has_lien_match, email_source, email_confidence,
                     data_source)
                SELECT 'TX', 'Texas', t.business_county,
                       t.license_number, t.license_type, t.status, 'tdlr',
                       t.owner_name, t.business_name, t.business_address,
                       t.business_city, t.business_zip,
                       COALESCE(t.business_phone, t.owner_phone),
                       NULLIF(t.email, ''),
                       COALESCE(t.lien_match, FALSE),
                       CASE WHEN NULLIF(t.email,'') IS NOT NULL
                            THEN 'tdlr' END,
                       CASE WHEN NULLIF(t.email,'') IS NOT NULL
                            THEN 'medium' ELSE 'low' END,
                       'texas_tdlr_contacts'
                FROM texas_tdlr_contacts t
                WHERE t.license_number IS NOT NULL AND t.license_number <> ''
                ON CONFLICT (state, license_number) DO NOTHING
            """)
            n = cur.rowcount
        conn.commit()
        print(f"    TX: +{n} contacts copied from texas_tdlr_contacts")
        return n
    except Exception as e:
        conn.rollback()
        print(f"    TX copy error: {e}")
        return 0
    finally:
        release_connection(conn)


def _copy_az_licenses() -> int:
    """arizona_roc_contacts -> normalized_contacts (AZ)."""
    conn = get_connection()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO normalized_contacts
                    (state, state_name, county, license_number, license_type,
                     license_status, license_source, owner_name, business_name,
                     business_city, business_zip, phone, email,
                     has_lien_match, email_source, email_confidence,
                     data_source)
                SELECT 'AZ', 'Arizona', a.county,
                       a.license_number,
                       COALESCE(a.license_type, a.license_class), a.status,
                       'roc', a.owner_name, a.business_name,
                       a.business_city, a.business_zip, a.phone,
                       NULLIF(a.email, ''),
                       COALESCE(a.lien_match, FALSE),
                       CASE WHEN NULLIF(a.email,'') IS NOT NULL
                            THEN 'roc' END,
                       CASE WHEN NULLIF(a.email,'') IS NOT NULL
                            THEN 'medium' ELSE 'low' END,
                       'arizona_roc_contacts'
                FROM arizona_roc_contacts a
                WHERE a.license_number IS NOT NULL AND a.license_number <> ''
                ON CONFLICT (state, license_number) DO NOTHING
            """)
            n = cur.rowcount
        conn.commit()
        print(f"    AZ: +{n} contacts copied from arizona_roc_contacts")
        return n
    except Exception as e:
        conn.rollback()
        print(f"    AZ copy error: {e}")
        return 0
    finally:
        release_connection(conn)


# ═════════════════════════════════════════════════════════════════════════════
# STEP C — match_liens_to_licenses  (REUSES score_lien_vs_dbpr)
# ═════════════════════════════════════════════════════════════════════════════

import re as _re

# Strip business suffixes so they don't pad token-overlap scores.
_BIZ_SUFFIX_RE = _re.compile(
    r"\b(l\.?l\.?c|inc|incorporated|corp|corporation|co|company|ltd|limited|"
    r"lp|llp|pllc|p\.?c|p\.?a|dba)\.?\b", _re.I)


def normalize_match_name(name: str) -> str:
    """
    Normalize a name for fuzzy matching, consistently on both sides:
      - invert "LAST, FIRST [MIDDLE]" -> "FIRST [MIDDLE] LAST"
      - strip LLC/INC/CO/... business suffixes
      - collapse whitespace
    (Lowercasing + punctuation removal happen downstream in norm_text. Because
    score_lien_vs_dbpr is token-set based, inversion is order-neutral but keeps
    names readable; suffix stripping is what actually tightens the match.)
    """
    n = (name or "").strip()
    if "," in n:
        last, rest = n.split(",", 1)
        n = f"{rest.strip()} {last.strip()}".strip()
    n = _BIZ_SUFFIX_RE.sub(" ", n)
    n = _re.sub(r"\s+", " ", n).strip()
    return n


def match_liens_to_licenses(state_code: str, threshold: float | None = None) -> int:
    """
    Match normalized_liens -> normalized_contacts for the same state using the
    existing DBPR scorer. Threshold defaults to MATCH_THRESHOLD (0.55) or the
    per-state override in STATE_MATCH_THRESHOLD (TX = 0.45). Names are normalized
    on both sides (LAST,FIRST inversion + suffix strip) before scoring.
    Updates has_lien_match, lien_id, lien_amount, lien_filed_date, lien_county,
    match_score, match_method. Returns count of contacts matched.
    """
    s = state_code.upper()
    if threshold is None:
        threshold = STATE_MATCH_THRESHOLD.get(s.lower(), MATCH_THRESHOLD)
    print(f"\n  [match_liens_to_licenses] {STATE_NAMES.get(s.lower(), s)} "
          f"(threshold {int(threshold * 100)})")
    conn = get_connection()
    conn.autocommit = False
    try:
        # Build dbpr-style rows from normalized_contacts (REUSE contract:
        # score_lien_vs_dbpr expects norm_biz / norm_owner / business_name /
        # owner_name keys). Names normalized on both sides for fair matching.
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, business_name, owner_name
                FROM normalized_contacts WHERE state = %s
            """, (s,))
            contact_rows = []
            for cid, biz, owner in cur.fetchall():
                nb = normalize_match_name(biz or "")
                no = normalize_match_name(owner or "")
                contact_rows.append({
                    "id": cid,
                    "business_name": nb,
                    "owner_name": no,
                    "norm_biz": norm_text(nb),
                    "norm_owner": norm_text(no),
                })

        if not contact_rows:
            print("    No contacts for this state yet — run collect_licenses.")
            return 0

        with conn.cursor() as cur:
            cur.execute("""
                SELECT nl.id, nl.debtor_name, nl.business_name,
                       nl.amount, nl.filed_date, c.county_name
                FROM normalized_liens nl
                LEFT JOIN counties c ON c.id = nl.county_id
                WHERE nl.state = %s
                  AND COALESCE(nl.debtor_name, nl.business_name) IS NOT NULL
            """, (s,))
            liens = cur.fetchall()

        print(f"    Scoring {len(liens):,} liens against "
              f"{len(contact_rows):,} contacts...")

        # Inverted token index: token -> contact rows containing it. This is a
        # pre-filter so each lien is only scored against contacts that share a
        # meaningful token, instead of all contacts. The actual scoring still
        # uses the REUSED score_lien_vs_dbpr() — only the candidate selection is
        # local (mirrors the pre-filter already inside find_best_dbpr_match,
        # but computed once instead of per-lien).
        from collections import defaultdict
        token_index: dict[str, list] = defaultdict(list)
        for row in contact_rows:
            toks = set((row["norm_biz"] + " " + row["norm_owner"]).split())
            for t in toks:
                # Index only meaningful tokens. score_lien_vs_dbpr already
                # requires >=1 shared meaningful token for multi-word names, so
                # skipping noise words (llc, construction, services, ...) here
                # cannot drop a true match — it only shrinks candidate buckets.
                if len(t) > 1 and t not in NOISE_TOKENS:
                    token_index[t].append(row)

        # best match per contact id
        best: dict[int, dict] = {}
        for lien_id, debtor, biz, amount, filed, county_name in liens:
            lien_name = normalize_match_name(debtor or biz or "")
            if len(lien_name) < 3:
                continue
            lien_tokens = set(norm_text(lien_name).split())
            if not lien_tokens:
                continue

            # candidate contacts sharing >=1 token (dedup by id)
            seen_ids = set()
            best_row = None
            best_score = 0.0
            for t in lien_tokens:
                for row in token_index.get(t, ()):
                    if row["id"] in seen_ids:
                        continue
                    seen_ids.add(row["id"])
                    score = score_lien_vs_dbpr(lien_name, row)  # REUSED
                    if score > best_score:
                        best_score = score
                        best_row = row
            if best_row is None or best_score < threshold:
                continue

            cid = best_row["id"]
            prev = best.get(cid)
            if prev is None or best_score > prev["score"]:
                best[cid] = {
                    "score": best_score, "lien_id": lien_id,
                    "amount": amount, "filed": filed,
                    "county": county_name,
                }

        matched = 0
        with conn.cursor() as cur:
            for cid, info in best.items():
                cur.execute("""
                    UPDATE normalized_contacts
                    SET has_lien_match  = TRUE,
                        lien_id         = %s,
                        lien_amount     = %s,
                        lien_filed_date = %s,
                        lien_county     = %s,
                        match_score     = %s,
                        match_method    = 'dbpr_token_score',
                        updated_at      = NOW()
                    WHERE id = %s
                      AND (match_score IS NULL OR match_score <= %s)
                """, (info["lien_id"], info["amount"], info["filed"],
                      info["county"], int(round(info["score"] * 100)),
                      cid, int(round(info["score"] * 100))))
                matched += cur.rowcount
        conn.commit()
        print(f"    Matched {matched:,} contacts (threshold "
              f"{int(threshold * 100)})")
        return matched
    except Exception as e:
        conn.rollback()
        print(f"    Match error: {e}")
        import traceback
        traceback.print_exc()
        return 0
    finally:
        release_connection(conn)


# ═════════════════════════════════════════════════════════════════════════════
# STEP D — enrich_emails_pdl  (REUSES pdl_person_search / pdl_company_search)
# ═════════════════════════════════════════════════════════════════════════════

def enrich_emails_pdl(state_code: str, limit: int = 100) -> int:
    """
    People Data Labs enrichment for lien-matched contacts missing an email.
    Tries company search first, then person search. Returns count enriched.
    """
    s = state_code.upper()
    print(f"\n  [enrich_emails_pdl] {STATE_NAMES.get(s.lower(), s)} "
          f"(limit {limit})")
    if not PDL_API_KEY:
        print("    PDL_API_KEY not set — skipping PDL enrichment.")
        return 0

    conn = get_connection()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, business_name, owner_name, business_city, county
                FROM normalized_contacts
                WHERE state = %s AND has_lien_match = TRUE
                  AND (email IS NULL OR email = '')
                ORDER BY match_score DESC NULLS LAST
                LIMIT %s
            """, (s, limit))
            rows = cur.fetchall()

        if not rows:
            print("    Nothing to enrich (no lien-matched rows missing email).")
            return 0

        enriched = 0
        default_city = STATE_DEFAULT_CITY.get(s.lower(), "")
        for cid, biz, owner, city, county in rows:
            name = (biz or owner or "").strip()
            if not name:
                continue
            search_city = (city or county or default_city or "").strip()
            email = None

            # 1) company first
            if is_business(name):
                data = pdl_company_search(PDL_API_KEY, name, search_city, s)
                if data.get("_limit"):
                    print("    PDL credit limit reached — stopping.")
                    break
                email = extract_company_contact(data).get("email")

            # 2) person fallback (or primary for individuals)
            if not email:
                first, last = parse_person_name(owner or name)
                if last:
                    data = pdl_person_search(PDL_API_KEY, first, last,
                                             search_city, s)
                    if data.get("_limit"):
                        print("    PDL credit limit reached — stopping.")
                        break
                    email = extract_person_contact(data).get("email")

            if email and not is_junk_email(email, name):
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE normalized_contacts
                        SET email = %s, email_source = 'pdl',
                            email_confidence = 'high', updated_at = NOW()
                        WHERE id = %s
                    """, (email.lower().strip(), cid))
                conn.commit()
                enriched += 1
                print(f"    ✓ {name[:40]} -> {email}")
            time.sleep(MIN_REQUEST_DELAY)

        print(f"    PDL enriched: {enriched}")
        return enriched
    except Exception as e:
        conn.rollback()
        print(f"    PDL error: {e}")
        return 0
    finally:
        release_connection(conn)


# ═════════════════════════════════════════════════════════════════════════════
# STEP E — enrich_emails_cse  (REUSES search_for_website + quota helpers)
# ═════════════════════════════════════════════════════════════════════════════

def enrich_emails_cse(state_code: str, limit: int = 50) -> int:
    """
    Web-search + website-scrape enrichment for contacts still missing an email
    after the PDL pass. Uses the shared multi-API search (SerpAPI / ValueSerp /
    Google CSE picked by get_available_api), with ValueSerp fallback handled
    inside search_for_website(). Returns count enriched.
    """
    s = state_code.upper()
    print(f"\n  [enrich_emails_cse] {STATE_NAMES.get(s.lower(), s)} "
          f"(limit {limit})")

    conn = get_connection()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, business_name, owner_name, business_city, county
                FROM normalized_contacts
                WHERE state = %s AND has_lien_match = TRUE
                  AND (email IS NULL OR email = '')
                ORDER BY match_score DESC NULLS LAST
                LIMIT %s
            """, (s, limit))
            rows = cur.fetchall()

        if not rows:
            print("    Nothing to enrich.")
            return 0

        enriched = 0
        default_city = STATE_DEFAULT_CITY.get(s.lower(), "")
        for cid, biz, owner, city, county in rows:
            if not get_available_api():
                print("    All search-API quotas exhausted — stopping.")
                break
            name = (biz or owner or "").strip()
            if not name:
                continue
            search_city = (city or county or default_city or "").strip()

            urls = search_for_website(name, search_city, s)
            time.sleep(MIN_REQUEST_DELAY)
            email = None
            for url in urls:
                email = scrape_email_from_url(url)
                if email:
                    break

            if email and not is_junk_email(email, name):
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE normalized_contacts
                        SET email = %s, email_source = 'cse',
                            email_confidence = 'medium', updated_at = NOW()
                        WHERE id = %s
                    """, (email.lower().strip(), cid))
                conn.commit()
                enriched += 1
                print(f"    ✓ {name[:40]} -> {email}")

        print(f"    CSE enriched: {enriched}")
        return enriched
    except Exception as e:
        conn.rollback()
        print(f"    CSE error: {e}")
        return 0
    finally:
        release_connection(conn)


# ═════════════════════════════════════════════════════════════════════════════
# STEP F — sync_to_email_pipeline  (-> lien_dbpr_contacts, INSERT-only)
# ═════════════════════════════════════════════════════════════════════════════

def sync_to_email_pipeline(state_code: Optional[str] = None) -> int:
    """
    Copy email-ready, lien-matched normalized_contacts into lien_dbpr_contacts
    so they flow through the existing 7-touch email sequence.

    Only copies rows that are: has_lien_match=TRUE, email present, email_step=0,
    whose matched lien has a county_id (required by the sequence's JOIN), and
    whose email is not already present in lien_dbpr_contacts.

    INSERT-only — never alters lien_dbpr_contacts structure. ON CONFLICT(lien_id)
    DO NOTHING protects the table's UNIQUE(lien_id) constraint and leaves any
    pre-existing contact for that lien untouched.
    """
    print(f"\n  [sync_to_email_pipeline] "
          f"{STATE_NAMES.get((state_code or '').lower(), state_code or 'ALL')}")
    conn = get_connection()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO lien_dbpr_contacts
                    (lien_id, county_id, debtor_name, full_name, email, phone,
                     license_number, trade, state, dbpr_score, confidence)
                SELECT nc.lien_id, nl.county_id,
                       COALESCE(nc.owner_name, nc.business_name),
                       COALESCE(nc.business_name, nc.owner_name),
                       LOWER(nc.email), nc.phone,
                       nc.license_number, nc.license_type, nc.state,
                       COALESCE(nc.match_score, 65),
                       CASE WHEN nc.email_confidence IN ('high','medium')
                            THEN nc.email_confidence ELSE 'medium' END
                FROM normalized_contacts nc
                JOIN normalized_liens nl ON nl.id = nc.lien_id
                WHERE nc.has_lien_match = TRUE
                  AND nc.email IS NOT NULL AND nc.email <> ''
                  AND nc.email_step = 0
                  AND nl.county_id IS NOT NULL
                  AND (%(state)s IS NULL OR nc.state = %(state)s)
                  AND NOT EXISTS (
                      SELECT 1 FROM lien_dbpr_contacts ldc
                      WHERE LOWER(ldc.email) = LOWER(nc.email)
                  )
                ON CONFLICT (lien_id) DO NOTHING
            """, {"state": state_code.upper() if state_code else None})
            n = cur.rowcount
        conn.commit()
        print(f"    Synced {n} new contacts into lien_dbpr_contacts")
        return n
    except Exception as e:
        conn.rollback()
        print(f"    Sync error: {e}")
        return 0
    finally:
        release_connection(conn)


# ═════════════════════════════════════════════════════════════════════════════
# Orchestration + stats
# ═════════════════════════════════════════════════════════════════════════════

def run_state_collection(state_code: str,
                         county: Optional[str] = None) -> dict:
    """Run the full per-state pipeline. Returns a stats dict."""
    s = state_code.lower()
    print(f"\n{'='*64}\n  DATA ENGINE — {STATE_NAMES.get(s, s.upper())} "
          f"({s.upper()})\n{'='*64}")
    stats = {"state": s, "liens": 0, "licenses": 0, "matched": 0,
             "pdl": 0, "cse": 0}

    def _safe(fn, *a):
        try:
            return fn(*a)
        except Exception as e:  # never let one step kill the run
            print(f"    step error: {e}")
            return 0

    stats["liens"]    = _safe(collect_liens, s, county)
    stats["licenses"] = _safe(collect_licenses, s)
    stats["matched"]  = _safe(match_liens_to_licenses, s)
    stats["pdl"]      = _safe(enrich_emails_pdl, s, 100)
    stats["cse"]      = _safe(enrich_emails_cse, s, 50)
    return stats


# Engine states shown (in order) even when not built yet.
ENGINE_STATES = ["FL", "TX", "AZ", "NY", "GA", "IL", "CA", "NC", "OH", "PA"]


def _state_label(st) -> str:
    """NULL/empty state -> 'UNKNOWN' (so it never shows as '??')."""
    return "UNKNOWN" if st in (None, "", "?") else st


def show_collection_stats() -> None:
    """Per-state engine summary: liens, licenses, matches, the unmatched gaps on
    both sides, enriched emails, what's in the email pipeline, the pipeline lag,
    and the last scrape date. Flags any UNKNOWN-state liens."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Licenses (normalized_contacts) per state.
            cur.execute("""
                SELECT COALESCE(NULLIF(state,''),'UNKNOWN') AS st,
                       COUNT(*) AS contacts,
                       COUNT(*) FILTER (WHERE has_lien_match) AS matched,
                       COUNT(*) FILTER (WHERE email IS NOT NULL AND email <> '') AS with_email
                FROM normalized_contacts
                GROUP BY 1
            """)
            contacts = {r[0]: {"contacts": r[1], "matched": r[2],
                               "with_email": r[3]} for r in cur.fetchall()}

            # Liens per state + last scrape (max created_at).
            cur.execute("""
                SELECT COALESCE(NULLIF(state,''),'UNKNOWN') AS st,
                       COUNT(*), MAX(created_at)
                FROM normalized_liens GROUP BY 1
            """)
            liens = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute("""
                SELECT COALESCE(NULLIF(state,''),'UNKNOWN') AS st, MAX(created_at)
                FROM normalized_liens GROUP BY 1
            """)
            last_run = {r[0]: (r[1].date().isoformat() if r[1] else "—")
                        for r in cur.fetchall()}

            # Matched liens per state (liens referenced by a contact) -> unmatched liens.
            cur.execute("""
                SELECT COALESCE(NULLIF(nl.state,''),'UNKNOWN') AS st,
                       COUNT(*) AS matched_liens
                FROM normalized_liens nl
                WHERE nl.id IN (SELECT lien_id FROM normalized_contacts
                                WHERE lien_id IS NOT NULL)
                GROUP BY 1
            """)
            matched_liens = {r[0]: r[1] for r in cur.fetchall()}

            # In pipeline = normalized_contacts emails present in lien_dbpr_contacts.
            cur.execute("""
                SELECT nc.state, COUNT(DISTINCT LOWER(nc.email))
                FROM normalized_contacts nc
                JOIN lien_dbpr_contacts ldc ON LOWER(ldc.email) = LOWER(nc.email)
                WHERE nc.email IS NOT NULL AND nc.email <> ''
                GROUP BY nc.state
            """)
            in_pipe = {_state_label(r[0]): r[1] for r in cur.fetchall()}

            # Addressable = matched contacts with a deliverable email that have
            # not yet entered the sequence (email_step=0), excluding spam traps
            # and unsubscribes.
            cur.execute("""
                SELECT COALESCE(NULLIF(nc.state,''),'UNKNOWN') AS st, COUNT(*)
                FROM normalized_contacts nc
                WHERE nc.has_lien_match = TRUE
                  AND nc.email IS NOT NULL AND nc.email <> ''
                  AND nc.email_step = 0
                  AND nc.is_spam_trap = FALSE
                  AND nc.unsubscribed = FALSE
                GROUP BY 1
            """)
            lag = {r[0]: r[1] for r in cur.fetchall()}

        # Order: known engine states first, then any extras (e.g. UNKNOWN).
        extras = [s for s in (set(contacts) | set(liens)) if s not in ENGINE_STATES]
        all_states = ENGINE_STATES + sorted(extras)

        print(f"\n{'='*108}")
        print(f"  DATA ENGINE — COLLECTION STATS  ({date.today().isoformat()})")
        print(f"{'='*108}")
        hdr = (f"  {'State':<8}{'Liens':>9}{'Licenses':>10}{'Matched':>9}"
               f"{'UnmtchL':>9}{'UnmtchLic':>10}{'Emails':>9}{'InPipe':>8}"
               f"{'Lag':>7}  {'LastRun':<10}")
        print(hdr)
        print(f"  {'-'*104}")
        total_addressable = 0
        for st in all_states:
            cc = contacts.get(st, {})
            ln = liens.get(st, 0)
            lic = cc.get("contacts", 0)
            if ln == 0 and lic == 0:
                print(f"  {st:<8}{'(not built)':>56}")
                continue
            matched = cc.get("matched", 0)
            emails = cc.get("with_email", 0)
            unm_liens = max(ln - matched_liens.get(st, 0), 0)
            unm_lic = max(lic - matched, 0)
            pipe = in_pipe.get(st, 0)
            st_lag = lag.get(st, 0)
            total_addressable += st_lag
            print(f"  {st:<8}{ln:>9,}{lic:>10,}{matched:>9,}{unm_liens:>9,}"
                  f"{unm_lic:>10,}{emails:>9,}{pipe:>8,}{st_lag:>7,}  "
                  f"{last_run.get(st,'—'):<10}")
        print(f"  {'-'*104}")
        print(f"  Total addressable: {total_addressable:,} matched contacts "
              f"with email, not yet sequenced (email_step=0)")
        if liens.get("UNKNOWN"):
            print(f"  ⚠ {liens['UNKNOWN']:,} liens have UNKNOWN (NULL/empty) state "
                  f"— backfill state from county/data_source.")
        print(f"{'='*108}\n")
    finally:
        release_connection(conn)


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="TaxCase Review data engine")
    p.add_argument("--state", help="Run full pipeline for a state code")
    p.add_argument("--county", default=None)
    p.add_argument("--liens", help="Run only collect_liens for a state")
    p.add_argument("--licenses", help="Run only collect_licenses for a state")
    p.add_argument("--match", help="Run only match_liens_to_licenses")
    p.add_argument("--pdl", help="Run only PDL enrichment for a state")
    p.add_argument("--cse", help="Run only CSE enrichment for a state")
    p.add_argument("--sync", nargs="?", const="__ALL__",
                   help="Sync to email pipeline (optional state code)")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    if args.stats:
        show_collection_stats()
        return
    if args.state:
        print(run_state_collection(args.state, args.county))
    if args.liens:
        print(f"new liens: {collect_liens(args.liens, args.county)}")
    if args.licenses:
        print(f"licenses: {collect_licenses(args.licenses)}")
    if args.match:
        print(f"matched: {match_liens_to_licenses(args.match)}")
    if args.pdl:
        print(f"pdl: {enrich_emails_pdl(args.pdl, args.limit)}")
    if args.cse:
        print(f"cse: {enrich_emails_cse(args.cse, args.limit)}")
    if args.sync:
        st = None if args.sync == "__ALL__" else args.sync
        print(f"synced: {sync_to_email_pipeline(st)}")

    if not any([args.state, args.liens, args.licenses, args.match,
                args.pdl, args.cse, args.sync, args.stats]):
        p.print_help()


if __name__ == "__main__":
    main()
