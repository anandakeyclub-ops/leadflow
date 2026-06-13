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


def collect_liens_ny_acris(county: Optional[str] = None,
                           doc_type: str = "FTLIEN") -> int:
    """
    NYC ACRIS open data (Socrata, free, no auth).
      Master:  https://data.cityofnewyork.us/resource/8h5j-fqxa.json
      Parties: https://data.cityofnewyork.us/resource/636b-3b5g.json
    Pulls federal-tax-lien master records, joins party names, writes to
    normalized_liens (state='NY'). Idempotent via normalized_hash + checkpoint.
    Max 500 new records per run (hard rule #5).
    """
    import hashlib

    MASTER = "https://data.cityofnewyork.us/resource/8h5j-fqxa.json"
    PARTIES = "https://data.cityofnewyork.us/resource/636b-3b5g.json"
    BOROUGH = {  # recorded_borough code -> county name
        "1": "New York", "2": "Bronx", "3": "Kings",
        "4": "Queens", "5": "Richmond",
    }

    cp_key = f"ny_acris_offset_{doc_type}"
    offset = _load_checkpoints().get(cp_key, 0)

    r = http_get(MASTER, params={
        "$where": f"doc_type='{doc_type}'",
        "$order": "document_date DESC",
        "$limit": MAX_PER_COUNTY,
        "$offset": offset,
    })
    if r is None or r.status_code != 200:
        print(f"    ACRIS master fetch failed "
              f"(status={getattr(r, 'status_code', 'n/a')})")
        return 0

    masters = r.json()
    if not masters:
        print("    ACRIS: no more master records — resetting offset.")
        _save_checkpoint(cp_key, 0)
        return 0

    conn = get_connection()
    conn.autocommit = False
    added = 0
    try:
        for m in masters:
            doc_id = m.get("document_id")
            if not doc_id:
                continue
            borough = str(m.get("recorded_borough", "")).strip()
            county_name = BOROUGH.get(borough, "New York")
            if county and county.lower() not in county_name.lower():
                continue

            # Fetch the debtor party (party_type 2 = grantee/borrower in ACRIS;
            # for liens the taxpayer is typically the party of interest).
            pr = http_get(PARTIES, params={
                "$where": f"document_id='{doc_id}'",
                "$limit": 10,
            })
            debtor = ""
            if pr is not None and pr.status_code == 200:
                parties = pr.json()
                # Prefer the non-government party as the debtor.
                for p in parties:
                    nm = (p.get("name") or "").strip()
                    if nm and "INTERNAL REVENUE" not in nm.upper() \
                            and "UNITED STATES" not in nm.upper():
                        debtor = nm
                        break
                if not debtor and parties:
                    debtor = (parties[0].get("name") or "").strip()
            if not debtor:
                continue

            amount = m.get("document_amt")
            try:
                amount = float(amount) if amount not in (None, "") else None
            except (TypeError, ValueError):
                amount = None
            filed = (m.get("document_date") or "")[:10] or None

            h = hashlib.md5(f"acris|{doc_id}".encode()).hexdigest()
            with conn.cursor() as cur:
                county_id = get_or_create_county(cur, county_name, "NY")
                cur.execute("""
                    INSERT INTO normalized_liens
                        (county_id, debtor_name, business_name, filing_type,
                         lien_type, lien_source, normalized_hash, state,
                         amount, filed_date, created_at)
                    VALUES (%s,%s,%s,'FEDERAL TAX LIEN','FEDERAL TAX LIEN',
                            'nyc_acris',%s,'NY',%s,%s,NOW())
                    ON CONFLICT (normalized_hash) DO NOTHING
                    RETURNING id
                """, (county_id, debtor[:250],
                      debtor[:250] if is_business(debtor) else None,
                      h, amount, filed))
                if cur.fetchone():
                    added += 1
            conn.commit()

        _save_checkpoint(cp_key, offset + len(masters))
        print(f"    ACRIS: +{added} new NY liens "
              f"(scanned {len(masters)}, offset now {offset + len(masters)})")
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
        spec = importlib.util.spec_from_file_location(
            "maricopa_lien_scraper", str(LEADFLOW_DIR / "maricopa_lien_scraper.py"))
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

    print(f"    {s.upper()} license source not yet wired "
          f"(GA SOS / CA CSLB / NY DOB / NC LBGC / IL IDFPR / OH / PA) "
          f"— pending. (0 licenses)")
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

def match_liens_to_licenses(state_code: str) -> int:
    """
    Match normalized_liens -> normalized_contacts for the same state using the
    existing DBPR scorer. Threshold score >= 0.55 (stored as match_score 0-100).
    Updates has_lien_match, lien_id, lien_amount, lien_filed_date, lien_county,
    match_score, match_method. Returns count of contacts matched.
    """
    s = state_code.upper()
    print(f"\n  [match_liens_to_licenses] {STATE_NAMES.get(s.lower(), s)}")
    conn = get_connection()
    conn.autocommit = False
    try:
        # Build dbpr-style rows from normalized_contacts (REUSE contract:
        # score_lien_vs_dbpr expects norm_biz / norm_owner / business_name /
        # owner_name keys).
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, business_name, owner_name
                FROM normalized_contacts WHERE state = %s
            """, (s,))
            contact_rows = []
            for cid, biz, owner in cur.fetchall():
                contact_rows.append({
                    "id": cid,
                    "business_name": biz or "",
                    "owner_name": owner or "",
                    "norm_biz": norm_text(biz or ""),
                    "norm_owner": norm_text(owner or ""),
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
            lien_name = (debtor or biz or "").strip()
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
            if best_row is None or best_score < MATCH_THRESHOLD:
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
              f"{int(MATCH_THRESHOLD * 100)})")
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


def show_collection_stats() -> None:
    """Print a per-state summary across the engine."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT state,
                       COUNT(*) AS contacts,
                       COUNT(*) FILTER (WHERE has_lien_match) AS matched,
                       COUNT(*) FILTER (
                           WHERE email IS NOT NULL AND email <> '') AS with_email
                FROM normalized_contacts
                GROUP BY state
            """)
            contacts = {r[0]: {"contacts": r[1], "matched": r[2],
                               "with_email": r[3]} for r in cur.fetchall()}

            cur.execute("""
                SELECT COALESCE(state,'?'), COUNT(*)
                FROM normalized_liens GROUP BY state
            """)
            liens = {r[0]: r[1] for r in cur.fetchall()}

            # in pipeline = normalized_contacts emails present in lien_dbpr_contacts
            cur.execute("""
                SELECT nc.state, COUNT(DISTINCT LOWER(nc.email))
                FROM normalized_contacts nc
                JOIN lien_dbpr_contacts ldc
                  ON LOWER(ldc.email) = LOWER(nc.email)
                WHERE nc.email IS NOT NULL AND nc.email <> ''
                GROUP BY nc.state
            """)
            in_pipe = {r[0]: r[1] for r in cur.fetchall()}

        all_states = sorted(set(list(contacts) + list(liens)))
        print(f"\n{'='*86}")
        print(f"  DATA ENGINE — COLLECTION STATS  ({date.today().isoformat()})")
        print(f"{'='*86}")
        print(f"  {'State':<6}{'Liens':>9}{'Licenses':>10}{'Matched':>9}"
              f"{'Emails':>9}{'InPipe':>8}{'Match%':>9}{'Email%':>9}")
        print(f"  {'-'*5:<6}{'-'*8:>9}{'-'*9:>10}{'-'*8:>9}"
              f"{'-'*8:>9}{'-'*7:>8}{'-'*8:>9}{'-'*8:>9}")
        for st in all_states:
            label = "??" if st in (None, "?") else st
            cc = contacts.get(st, {})
            lic = cc.get("contacts", 0)
            matched = cc.get("matched", 0)
            emails = cc.get("with_email", 0)
            ln = liens.get(st, 0)
            pipe = in_pipe.get(st, 0)
            match_pct = round(matched / lic * 100, 1) if lic else 0.0
            email_pct = round(emails / matched * 100, 1) if matched else 0.0
            print(f"  {label:<6}{ln:>9,}{lic:>10,}{matched:>9,}"
                  f"{emails:>9,}{pipe:>8,}{match_pct:>8.1f}%{email_pct:>8.1f}%")
        print(f"{'='*86}\n")
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
