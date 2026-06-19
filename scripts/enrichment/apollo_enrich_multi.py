#!/usr/bin/env python3
"""
apollo_enrich_multi.py
======================
Enrich unmatched TX / AZ / GA liens with business contacts via the Apollo.io
people search API, then insert the results into lien_dbpr_contacts so the rest
of the pipeline (scoring → email pool → sequence) can pick them up.

"Unmatched" means a normalized_liens row whose county is in TX/AZ/GA and that
has no corresponding lien_dbpr_contacts record yet. lien_dbpr_contacts.lien_id
is a NOT NULL UNIQUE FK to normalized_liens.id, so every contact we insert is
anchored to its lien — this mirrors _save_lien_email() in
multi_state_email_enrichment.py.

Liens are pulled highest-amount-first ACROSS all three states so the limited
free Apollo credits are spent on the highest-value cases regardless of state.
Default cap is 90 (the free credit grant).

Note on schema: lien_dbpr_contacts has no first_name/last_name columns. Apollo's
first + last name are combined into full_name (the matched contact's name),
while debtor_name keeps the lien's original taxpayer name; phone is stored in
the phone column. state is taken from the lien's county (counties.state).

Env:
  APOLLO_API_KEY  — required (Apollo.io API key)

Usage:
  python scripts/enrichment/apollo_enrich_multi.py
  python scripts/enrichment/apollo_enrich_multi.py --state AZ
  python scripts/enrichment/apollo_enrich_multi.py --limit 25
  python scripts/enrichment/apollo_enrich_multi.py --dry-run   # calls Apollo, no DB writes
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

# Windows consoles are often cp1252 — never let an emoji crash the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

from app.core.db import get_connection  # noqa: E402

APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "")
APOLLO_URL     = "https://api.apollo.io/v1/people/search"

# Apollo free tier is rate-limited; be polite between calls.
REQUEST_DELAY  = 1.0
MIN_CONFIDENCE = 0.7
DEFAULT_LIMIT  = 90        # full free credit allocation

# States this importer covers, and the Apollo organization_locations value for
# each (Apollo expects a full state name, not the abbreviation).
STATE_LOCATIONS = {
    "TX": "Texas",
    "AZ": "Arizona",
    "GA": "Georgia",
}

# Emails Apollo returns when the address is locked behind credits — not usable.
_LOCKED_EMAIL_MARKERS = ("email_not_unlocked", "@domain.com")

# email_status -> confidence mapping, used when Apollo doesn't return a numeric
# confidence field for the person.
_STATUS_CONFIDENCE = {
    "verified":     0.95,
    "likely":       0.80,
    "extrapolated": 0.75,
    "guessed":      0.50,
    "unverified":   0.40,
    "unavailable":  0.0,
}


def get_unmatched_liens(conn, states: list[str], limit: int) -> list[dict]:
    """normalized_liens rows whose county is in `states` and that have no
    lien_dbpr_contacts record yet, highest lien amount first ACROSS states so
    the free credits go to the highest-value cases regardless of state."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                nl.id                                          AS lien_id,
                nl.county_id                                   AS county_id,
                c.county_name                                  AS county_name,
                c.state                                        AS state,
                nl.business_name                               AS business_name,
                nl.debtor_name                                 AS debtor_name,
                nl.amount                                      AS amount,
                COALESCE(NULLIF(nl.business_name, ''), nl.debtor_name) AS search_name
            FROM normalized_liens nl
            JOIN counties c ON c.id = nl.county_id
            WHERE c.state = ANY(%s)
              AND COALESCE(NULLIF(nl.business_name, ''), nl.debtor_name) IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM lien_dbpr_contacts d
                  WHERE d.lien_id = nl.id
              )
            ORDER BY nl.amount DESC NULLS LAST, nl.id
            LIMIT %s
            """,
            (states, limit),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def apollo_people_search(query_name: str, location: str) -> dict | None:
    """Call Apollo people search for a single organization name, scoped to the
    given state location. Returns the parsed JSON, or None on transport/HTTP
    error (logged, never raised)."""
    try:
        r = requests.post(
            APOLLO_URL,
            headers={
                "x-api-key":     APOLLO_API_KEY,
                "Content-Type":  "application/json",
                "Cache-Control": "no-cache",
                "Accept":        "application/json",
            },
            json={
                "q_organization_name":    query_name,
                "organization_locations": [location],
                "per_page":               1,
            },
            timeout=20,
        )
        if r.status_code == 429:
            print("  ⚠ Apollo rate limit (429) — stopping early")
            return {"_rate_limited": True}
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        print(f"  ⚠ Apollo request failed: {e}")
        return None


def _extract_phone(person: dict) -> str | None:
    for ph in (person.get("phone_numbers") or []):
        num = ph.get("sanitized_number") or ph.get("raw_number")
        if num:
            return str(num).strip()
    org = person.get("organization") or {}
    return (org.get("phone") or org.get("sanitized_phone") or None)


def extract_person(resp: dict) -> dict | None:
    """Pull the matched person from an Apollo response. Returns a dict with
    email/confidence/first_name/last_name/phone/matched_name, or None when Apollo
    returned no person at all. `email` is None when it's missing or locked behind
    credits (so confidence can still be reported)."""
    people = resp.get("people") or resp.get("contacts") or []
    if not people:
        return None
    person = people[0]

    # Confidence: numeric field if Apollo provides one, else map email_status.
    conf = person.get("extrapolated_email_confidence")
    if conf is None:
        conf = person.get("email_confidence")
    if conf is None:
        status = (person.get("email_status") or "").lower()
        conf = _STATUS_CONFIDENCE.get(status, 0.0)
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = 0.0

    email = (person.get("email") or "").strip().lower()
    if not email or "@" not in email or any(m in email for m in _LOCKED_EMAIL_MARKERS):
        email = None

    first = (person.get("first_name") or "").strip() or None
    last  = (person.get("last_name") or "").strip() or None
    matched_name = (person.get("name") or " ".join(p for p in (first, last) if p)).strip()

    return {
        "email":        email,
        "confidence":   conf,
        "first_name":   first,
        "last_name":    last,
        "phone":        _extract_phone(person),
        "matched_name": matched_name or None,
    }


def insert_contact(conn, lien: dict, person: dict) -> None:
    """Insert/update the enriched contact in lien_dbpr_contacts, anchored to its
    lien. On conflict, only overwrite email/phone/full_name when Apollo supplied
    a (non-null) value — existing data is preserved otherwise. state comes from
    the lien's county."""
    debtor    = (lien.get("debtor_name") or lien.get("business_name") or "")[:250]
    # full_name carries the matched contact's name (first + last); fall back to
    # the lien's own name when Apollo didn't give one.
    full_name = (person.get("matched_name") or debtor)[:250]
    email     = person.get("email")
    phone     = (person.get("phone") or None)
    if phone:
        phone = phone[:50]

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO lien_dbpr_contacts
                (lien_id, county_id, debtor_name, full_name,
                 email, phone, state, confidence, dbpr_score)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'medium', 65)
            ON CONFLICT (lien_id) DO UPDATE SET
                email      = COALESCE(EXCLUDED.email,     lien_dbpr_contacts.email),
                phone      = COALESCE(EXCLUDED.phone,     lien_dbpr_contacts.phone),
                full_name  = COALESCE(EXCLUDED.full_name, lien_dbpr_contacts.full_name),
                confidence = EXCLUDED.confidence,
                dbpr_score = EXCLUDED.dbpr_score
            """,
            (lien["lien_id"], lien["county_id"], debtor, full_name,
             email, phone, lien["state"]),
        )
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Apollo.io contact enrichment for unmatched TX/AZ/GA liens")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"Max liens to process across all states (default {DEFAULT_LIMIT})")
    parser.add_argument("--state", default=None, choices=list(STATE_LOCATIONS.keys()),
                        help="Limit to one state (TX/AZ/GA). Default: all three")
    parser.add_argument("--dry-run", action="store_true",
                        help="Call Apollo and report, but do not write to the DB")
    args = parser.parse_args()

    limit  = max(1, args.limit)
    states = [args.state] if args.state else list(STATE_LOCATIONS.keys())

    print(f"\n{'='*64}")
    print(f"  Apollo.io Multi-State Lien Contact Enrichment")
    print(f"  States : {', '.join(states)}")
    print(f"  Limit  : {limit} (highest lien amount first, across states)")
    print(f"  {'DRY RUN — no DB writes' if args.dry_run else 'LIVE — inserting into lien_dbpr_contacts'}")
    print(f"{'='*64}\n")

    if not APOLLO_API_KEY:
        print("  ❌ APOLLO_API_KEY is not set in .env — cannot call Apollo.io.")
        print("     Add APOLLO_API_KEY=... to .env and re-run.")
        sys.exit(1)

    # Per-state tallies for the summary.
    by_state = {s: {"searched": 0, "found_email": 0, "inserted": 0} for s in states}
    total = {"searched": 0, "found_email": 0, "inserted": 0}

    conn = get_connection()
    try:
        liens = get_unmatched_liens(conn, states, limit)
        if not liens:
            print("  ✅ No unmatched liens to enrich.")
            return

        print(f"  {len(liens)} unmatched liens to process\n")

        for i, lien in enumerate(liens):
            query_name = (lien.get("search_name") or "").strip()
            st = lien["state"]
            if not query_name or st not in STATE_LOCATIONS:
                continue
            debtor   = (lien.get("debtor_name") or lien.get("business_name") or "?").strip()
            location = STATE_LOCATIONS[st]

            by_state[st]["searched"] += 1
            total["searched"] += 1
            resp = apollo_people_search(query_name, location)
            time.sleep(REQUEST_DELAY)

            prefix = (f"  [{i+1}/{len(liens)}] [{st}] {debtor[:30]:<30} "
                      f"({lien.get('county_name', '?')}) →")

            if resp is None:
                print(f"{prefix} request error")
                continue
            if resp.get("_rate_limited"):
                break

            person = extract_person(resp)
            if not person or not person.get("matched_name"):
                print(f"{prefix} no match")
                continue

            matched = person["matched_name"]
            email   = person.get("email")
            conf    = person.get("confidence", 0.0)

            if email and conf >= MIN_CONFIDENCE:
                by_state[st]["found_email"] += 1
                total["found_email"] += 1
                if args.dry_run:
                    print(f"{prefix} {matched} | {email} (conf {conf:.2f}) [DRY RUN]")
                else:
                    insert_contact(conn, lien, person)
                    by_state[st]["inserted"] += 1
                    total["inserted"] += 1
                    print(f"{prefix} {matched} | ✅ {email} (conf {conf:.2f})")
            elif email:
                print(f"{prefix} {matched} | skip — low confidence ({conf:.2f})")
            else:
                print(f"{prefix} {matched} | no email")

        # ── Summary (broken down by state) ──
        print(f"\n{'─'*64}")
        for st in states:
            s = by_state[st]
            print(f"  {st}: searched {s['searched']} / found {s['found_email']} emails")
        verb = "would insert" if args.dry_run else "inserted"
        printed = total["found_email"] if args.dry_run else total["inserted"]
        print(f"  TOTAL: searched {total['searched']} / found {total['found_email']} "
              f"emails / {verb} {printed}")
        print(f"{'─'*64}\n")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
