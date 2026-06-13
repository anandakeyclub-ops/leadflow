"""
fix_tdlr_and_enrich.py
======================
Step 1: Null out all junk emails in texas_tdlr_contacts
Step 2: Show the 581 lien_match=TRUE contacts that need enrichment
Step 3: Run SerpAPI enrichment against matched BUSINESS contacts without emails
        (individuals in LAST, FIRST format are skipped)

Run:
  python fix_tdlr_and_enrich.py --clean        # null out junk emails
  python fix_tdlr_and_enrich.py --stats        # show current state
  python fix_tdlr_and_enrich.py --enrich --limit 562  # enrich businesses only
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

import os
from app.core.db import get_connection

SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
QUOTA_FILE  = LEADFLOW_DIR / "data" / "enrichment" / "api_quota.json"

import json
from datetime import date

sys.path.insert(0, str(LEADFLOW_DIR / "scripts" / "enrichment"))
try:
    from multi_state_email_enrichment import (
        is_junk_email, SKIP_EMAIL_DOMAINS, search_for_website,
        scrape_email_from_url, get_available_api, record_api_use,
        get_quota_status
    )
    print("Imported enrichment functions OK")
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)

TDLR_JUNK_DOMAINS = {
    "sentry-next.wixpress.com", "wixpress.com",
    "humaneworld.org", "claimspages.com", "thebluebook.com",
    "instantcheckmate.com", "otrucking.com", "tdlr.texas.gov",
    "rosetrucksales.com",
} | SKIP_EMAIL_DOMAINS

TDLR_JUNK_PATTERNS = [
    "u003e",
    "sentry",
    "wixpress",
    "@mail.",
]


def clean_tdlr_junk_emails(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, email, business_name
            FROM texas_tdlr_contacts
            WHERE email IS NOT NULL AND email != ''
        """)
        rows = cur.fetchall()

    nulled = 0
    for tdlr_id, email, biz_name in rows:
        if not email or "@" not in email:
            continue
        domain = email.split("@")[-1].lower()
        is_junk = (
            domain in TDLR_JUNK_DOMAINS
            or any(p in email.lower() for p in TDLR_JUNK_PATTERNS)
            or domain.endswith(".gov")
            or domain.endswith(".edu")
            or is_junk_email(email, biz_name or "")
        )
        if is_junk:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE texas_tdlr_contacts SET email = NULL WHERE id = %s",
                    (tdlr_id,)
                )
            nulled += 1
            print(f"  Nulled: {email} ({biz_name})")

    conn.commit()
    return nulled


def show_stats(conn):
    print(f"\n{'='*60}")
    print("  TDLR Enrichment Status")
    print(f"{'='*60}")

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(id)                                                AS total,
                COUNT(email)                                             AS with_email,
                COUNT(CASE WHEN lien_match = TRUE THEN 1 END)            AS lien_matched,
                COUNT(CASE WHEN lien_match = TRUE
                      AND email IS NOT NULL THEN 1 END)                  AS matched_with_email,
                COUNT(CASE WHEN lien_match = TRUE
                      AND (email IS NULL OR email = '') THEN 1 END)      AS matched_no_email
            FROM texas_tdlr_contacts
        """)
        r = cur.fetchone()
        print(f"  Total TDLR contacts    : {r[0]:,}")
        print(f"  With email             : {r[1]:,}")
        print(f"  lien_match = TRUE      : {r[2]:,}")
        print(f"  Matched + has email    : {r[3]:,}  <- ready for bridge")
        print(f"  Matched + no email     : {r[4]:,}  <- needs SerpAPI enrichment")

    # Business vs individual breakdown
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(id)                                               AS total_needing,
                COUNT(CASE WHEN business_name !~ '^[A-Z][A-Z .&-]+,[[:space:]]+[A-Z]'
                           THEN 1 END)                                  AS businesses,
                COUNT(CASE WHEN business_name ~ '^[A-Z][A-Z .&-]+,[[:space:]]+[A-Z]'
                           THEN 1 END)                                  AS individuals
            FROM texas_tdlr_contacts
            WHERE lien_match = TRUE
              AND (email IS NULL OR email = '')
              AND business_name IS NOT NULL
              AND business_name != ''
        """)
        r2 = cur.fetchone()
        print(f"\n  Of matched no-email contacts:")
        print(f"    Businesses  (SerpAPI target)      : {r2[1] or 0:,}")
        print(f"    Individuals (LAST, FIRST — skipped): {r2[2] or 0:,}")

    with conn.cursor() as cur:
        cur.execute("""
            SELECT license_type, COUNT(id)
            FROM texas_tdlr_contacts
            WHERE lien_match = TRUE
              AND (email IS NULL OR email = '')
              AND business_name !~ '^[A-Z][A-Z .&-]+,[[:space:]]+[A-Z]'
            GROUP BY license_type
            ORDER BY 2 DESC
            LIMIT 10
        """)
        rows = cur.fetchall()
        if rows:
            print(f"\n  Top business trades needing enrichment:")
            for lic_type, cnt in rows:
                print(f"    {(lic_type or 'Unknown'):<40} {cnt:>6,}")

    print(f"\n  SerpAPI quota:")
    q = get_quota_status()
    print(f"    Used      : {q['serpapi_used']}/{q['serpapi_limit']}")
    print(f"    Remaining : {q['serpapi_remaining']}")
    print(f"{'='*60}\n")


def enrich_tdlr_matched(conn, limit: int = 100, dry_run: bool = False) -> dict:
    """
    Enrich texas_tdlr_contacts where lien_match=TRUE but no email yet.
    Skips individuals stored as LASTNAME, FIRSTNAME format.
    Uses SerpAPI -> website scrape -> junk filter -> save to DB.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, business_name, owner_name, business_city,
                   business_county, business_state, license_type
            FROM texas_tdlr_contacts
            WHERE lien_match = TRUE
              AND (email IS NULL OR email = '')
              AND business_name IS NOT NULL
              AND business_name != ''
              AND business_name !~ '^[A-Z][A-Z .&-]+,[[:space:]]+[A-Z]'
            ORDER BY id
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()

    if not rows:
        print("  No matched TDLR business contacts without email found.")
        return {"enriched": 0, "failed": 0}

    print(f"  Found {len(rows):,} matched TDLR business contacts to enrich")
    enriched = 0
    failed   = 0

    for i, (tdlr_id, biz_name, owner_name, city, county, state, lic_type) in enumerate(rows):
        city_clean = (city or county or "Texas").strip()
        state_abbr = (state or "TX").strip()

        api = get_available_api()
        if not api:
            q = get_quota_status()
            print(f"\n  All API quotas exhausted.")
            print(f"    SerpAPI: {q['serpapi_used']}/{q['serpapi_limit']}")
            break

        print(f"  [{i+1}/{len(rows)}] [{api}] {biz_name[:40]:<40} ({city_clean}, {state_abbr})",
              end=" ... ", flush=True)

        if dry_run:
            print("[DRY RUN]")
            continue

        urls = search_for_website(biz_name, city_clean, state_abbr)

        if not urls and owner_name and owner_name.lower() != biz_name.lower():
            urls = search_for_website(owner_name, city_clean, state_abbr)

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
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE texas_tdlr_contacts
                    SET email = %s
                    WHERE id = %s
                """, (email.lower().strip(), tdlr_id))
            conn.commit()
            enriched += 1
            print(f"OK {email}")
        elif email:
            print(f"junk: {email}")
            failed += 1
        else:
            print("no email found")
            failed += 1

    print(f"\n  Enriched : {enriched:,}")
    print(f"  Failed   : {failed:,}")
    if enriched + failed:
        print(f"  Rate     : {enriched/(enriched+failed)*100:.1f}%")
    return {"enriched": enriched, "failed": failed}


def main():
    parser = argparse.ArgumentParser(description="Fix and enrich TDLR contacts")
    parser.add_argument("--clean",   action="store_true",
                        help="Null out all junk emails in texas_tdlr_contacts")
    parser.add_argument("--stats",   action="store_true",
                        help="Show enrichment status")
    parser.add_argument("--enrich",  action="store_true",
                        help="Enrich lien_match=TRUE business contacts via SerpAPI")
    parser.add_argument("--limit",   type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_connection()
    try:
        if args.clean:
            print("\nCleaning junk emails from texas_tdlr_contacts...")
            nulled = clean_tdlr_junk_emails(conn)
            print(f"\nNulled {nulled:,} junk emails.")
            show_stats(conn)
        elif args.stats:
            show_stats(conn)
        elif args.enrich:
            print(f"\nEnriching TDLR matched business contacts (limit={args.limit})...\n")
            result = enrich_tdlr_matched(conn, limit=args.limit, dry_run=args.dry_run)
            show_stats(conn)
        else:
            parser.print_help()
    finally:
        conn.close()


if __name__ == "__main__":
    main()