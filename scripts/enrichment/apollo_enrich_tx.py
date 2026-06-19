#!/usr/bin/env python3
"""
apollo_enrich_tx.py
===================
Enrich unmatched Texas liens with business contacts via the Apollo.io people
search API, then insert the results into lien_dbpr_contacts so the rest of the
pipeline (scoring → email pool → sequence) can pick them up.

"Unmatched" means a normalized_liens row (state='TX') that has no corresponding
lien_dbpr_contacts record yet. lien_dbpr_contacts.lien_id is a NOT NULL UNIQUE
FK to normalized_liens.id, so every contact we insert is anchored to its lien —
this mirrors _save_lien_email() in multi_state_email_enrichment.py.

Liens are pulled highest-amount-first so the limited free Apollo credits are
spent on the highest-value cases. Default cap is 90 (the free credit grant).

Note on schema: lien_dbpr_contacts has no first_name/last_name columns. Apollo's
first + last name are combined into full_name (the matched contact's name),
while debtor_name keeps the lien's original taxpayer name; phone is stored in
the phone column.

Env:
  APOLLO_API_KEY  — required (Apollo.io API key)

Usage:
  python scripts/enrichment/apollo_enrich_tx.py
  python scripts/enrichment/apollo_enrich_tx.py --limit 25
  python scripts/enrichment/apollo_enrich_tx.py --dry-run    # calls Apollo, no DB writes
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


def get_unmatched_tx_liens(conn, limit: int) -> list[dict]:
    """normalized_liens rows (TX) with no lien_dbpr_contacts record yet, highest
    lien amount first so the free credits go to the highest-value cases."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                nl.id                                          AS lien_id,
                nl.county_id                                   AS county_id,
                c.county_name                                  AS county_name,
                nl.business_name                               AS business_name,
                nl.debtor_name                                 AS debtor_name,
                nl.amount                                      AS amount,
                COALESCE(NULLIF(nl.business_name, ''), nl.debtor_name) AS search_name
            FROM normalized_liens nl
            JOIN counties c ON c.id = nl.county_id
            WHERE nl.state = 'TX'
              AND COALESCE(NULLIF(nl.business_name, ''), nl.debtor_name) IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM lien_dbpr_contacts d
                  WHERE d.lien_id = nl.id
              )
            ORDER BY nl.amount DESC NULLS LAST, nl.id
            LIMIT %s
            """,
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def apollo_people_search(query_name: str) -> dict | None:
    """Call Apollo people search for a single organization name. Returns the
    parsed JSON, or None on transport/HTTP error (logged, never raised)."""
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
                "organization_locations": ["Texas"],
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
    a (non-null) value — existing data is preserved otherwise."""
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
            VALUES (%s, %s, %s, %s, %s, %s, 'TX', 'medium', 65)
            ON CONFLICT (lien_id) DO UPDATE SET
                email      = COALESCE(EXCLUDED.email,     lien_dbpr_contacts.email),
                phone      = COALESCE(EXCLUDED.phone,     lien_dbpr_contacts.phone),
                full_name  = COALESCE(EXCLUDED.full_name, lien_dbpr_contacts.full_name),
                confidence = EXCLUDED.confidence,
                dbpr_score = EXCLUDED.dbpr_score
            """,
            (lien["lien_id"], lien["county_id"], debtor, full_name, email, phone),
        )
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Apollo.io contact enrichment for unmatched TX liens")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"Max liens to process (default {DEFAULT_LIMIT}, the free credit grant)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Call Apollo and report, but do not write to the DB")
    args = parser.parse_args()

    limit = max(1, args.limit)

    print(f"\n{'='*64}")
    print(f"  Apollo.io TX Lien Contact Enrichment")
    print(f"  Limit  : {limit} (highest lien amount first)")
    print(f"  {'DRY RUN — no DB writes' if args.dry_run else 'LIVE — inserting into lien_dbpr_contacts'}")
    print(f"{'='*64}\n")

    if not APOLLO_API_KEY:
        print("  ❌ APOLLO_API_KEY is not set in .env — cannot call Apollo.io.")
        print("     Add APOLLO_API_KEY=... to .env and re-run.")
        sys.exit(1)

    stats = {"searched": 0, "matched": 0, "found_email": 0, "inserted": 0}

    conn = get_connection()
    try:
        liens = get_unmatched_tx_liens(conn, limit)
        if not liens:
            print("  ✅ No unmatched TX liens to enrich.")
            return

        print(f"  {len(liens)} unmatched TX liens to process\n")

        for i, lien in enumerate(liens):
            query_name = (lien.get("search_name") or "").strip()
            if not query_name:
                continue
            debtor = (lien.get("debtor_name") or lien.get("business_name") or "?").strip()

            stats["searched"] += 1
            resp = apollo_people_search(query_name)
            time.sleep(REQUEST_DELAY)

            prefix = f"  [{i+1}/{len(liens)}] {debtor[:32]:<32} ({lien.get('county_name', '?')}, TX) →"

            if resp is None:
                print(f"{prefix} request error")
                continue
            if resp.get("_rate_limited"):
                break

            person = extract_person(resp)
            if not person or not person.get("matched_name"):
                print(f"{prefix} no match")
                continue

            stats["matched"] += 1
            matched = person["matched_name"]
            email   = person.get("email")
            conf    = person.get("confidence", 0.0)

            if email and conf >= MIN_CONFIDENCE:
                stats["found_email"] += 1
                if args.dry_run:
                    print(f"{prefix} {matched} | {email} (conf {conf:.2f}) [DRY RUN]")
                else:
                    insert_contact(conn, lien, person)
                    stats["inserted"] += 1
                    print(f"{prefix} {matched} | ✅ {email} (conf {conf:.2f})")
            elif email:
                print(f"{prefix} {matched} | skip — low confidence ({conf:.2f})")
            else:
                print(f"{prefix} {matched} | no email")

        # ── Summary ──
        print(f"\n{'─'*64}")
        print(f"  Searched     : {stats['searched']}")
        print(f"  Apollo match : {stats['matched']}")
        print(f"  Found email  : {stats['found_email']}  (confidence >= {MIN_CONFIDENCE})")
        print(f"  {'Would insert' if args.dry_run else 'Inserted'}    : "
              f"{stats['found_email'] if args.dry_run else stats['inserted']}")
        print(f"{'─'*64}\n")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
