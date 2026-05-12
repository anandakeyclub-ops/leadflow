"""
enrich_dbpr.py
==============
Unified DBPR enrichment for ALL counties.
Reuses the matching logic from enrich_palm_beach_from_dbpr.py.

Usage:
  python -m app.workers.enrich_dbpr
  python -m app.workers.enrich_dbpr --county "Miami-Dade"
  python -m app.workers.enrich_dbpr --force   # re-enrich already matched
"""
from __future__ import annotations

import argparse
import os

from app.core.db import get_connection
from app.workers.enrich_palm_beach_from_dbpr import (
    load_dbpr_rows,
    choose_best_match,
    build_placeholder_email,
    score_to_confidence,
)

FORCE_REENRICH = os.getenv("FORCE_REENRICH", "0") == "1"


def enrich_county(cur, dbpr_rows: list, county_name: str, force: bool = False) -> dict:
    status_filter = "" if force else \
        "AND (ml.enrichment_status IS NULL OR ml.enrichment_status NOT LIKE 'matched_dbpr%')"

    cur.execute(f"""
        SELECT ml.id, np.business_name, np.owner_name, np.address_1,
               ct.id, ct.email, nl.debtor_name
        FROM matched_leads ml
        JOIN normalized_permits np ON ml.permit_id = np.id
        JOIN normalized_liens nl   ON ml.lien_id  = nl.id
        JOIN counties c            ON ml.county_id = c.id
        LEFT JOIN contacts ct      ON ml.id = ct.lead_id
        WHERE c.county_name = %s
        {status_filter}
        ORDER BY ml.id
    """, (county_name,))

    rows = cur.fetchall()
    if not rows:
        return {"county": county_name, "processed": 0, "matched": 0, "unmatched": 0}

    # Debug: show data quality for this county
    has_biz  = sum(1 for r in rows if r[1])
    has_own  = sum(1 for r in rows if r[2])
    has_addr = sum(1 for r in rows if r[3])
    has_deb  = sum(1 for r in rows if r[6])
    print(f"    {county_name}: {len(rows)} leads — "
          f"biz={has_biz} own={has_own} addr={has_addr} debtor={has_deb}")

    matched = 0
    unmatched = 0

    for lead_id, biz, own, addr, contact_id, existing_email, debtor_name in rows:
        # Use debtor_name as fallback when permit has no contractor info
        # This covers Hillsborough, Polk, Duval, Pinellas where permits
        # don't always have contractor names populated
        effective_biz = biz or debtor_name or ""
        effective_own = own or debtor_name or ""

        result = choose_best_match(
            dbpr_rows,
            business_name = effective_biz,
            owner_name    = effective_own,
            address_1     = addr or "",
            debtor_name   = debtor_name or "",
        )

        if result is None:
            unmatched += 1
            best_name = biz or own or debtor_name or "Unknown"
            placeholder = build_placeholder_email(best_name, lead_id)
            cur.execute("""
                INSERT INTO contacts (lead_id, full_name, email, enrichment_status, last_enriched_at)
                VALUES (%s, %s, %s, 'no_dbpr_match', NOW())
                ON CONFLICT (lead_id) DO UPDATE SET
                    full_name         = EXCLUDED.full_name,
                    enrichment_status = 'no_dbpr_match',
                    last_enriched_at  = NOW()
            """, (lead_id, best_name, placeholder))

            cur.execute("""
                UPDATE matched_leads SET enrichment_status = 'no_dbpr_match', updated_at = NOW()
                WHERE id = %s
            """, (lead_id,))
            continue

        match, score = result
        confidence = score_to_confidence(score)
        full_name  = biz or own or debtor_name or "Unknown"
        email      = match["email"] or existing_email or build_placeholder_email(full_name, lead_id)

        cur.execute("""
            INSERT INTO contacts (
                lead_id, full_name, primary_phone, secondary_phone,
                email, mailing_address_1, city, state, zip,
                enrichment_vendor, enrichment_score, enrichment_status,
                last_enriched_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (lead_id) DO UPDATE SET
                full_name          = EXCLUDED.full_name,
                primary_phone      = COALESCE(EXCLUDED.primary_phone,      contacts.primary_phone),
                email              = COALESCE(EXCLUDED.email,              contacts.email),
                mailing_address_1  = COALESCE(EXCLUDED.mailing_address_1,  contacts.mailing_address_1),
                city               = COALESCE(EXCLUDED.city,               contacts.city),
                state              = COALESCE(EXCLUDED.state,              contacts.state),
                zip                = COALESCE(EXCLUDED.zip,               contacts.zip),
                enrichment_vendor  = EXCLUDED.enrichment_vendor,
                enrichment_score   = EXCLUDED.enrichment_score,
                enrichment_status  = EXCLUDED.enrichment_status,
                last_enriched_at   = NOW()
        """, (
            lead_id, full_name,
            match["phone"] or None, None,
            email,
            match["mailing_address_1"] or addr or "",
            match["city"] or "",
            match["state"] or "FL",
            match["zip"] or "",
            "dbpr_csv",
            round(score * 100, 1),
            f"matched_dbpr_{confidence}",
        ))

        cur.execute("""
            UPDATE matched_leads
            SET enrichment_status = %s, updated_at = NOW()
            WHERE id = %s
        """, (f"matched_dbpr_{confidence}", lead_id))

        matched += 1

    return {
        "county":    county_name,
        "processed": len(rows),
        "matched":   matched,
        "unmatched": unmatched,
    }


def get_active_counties(cur) -> list[str]:
    cur.execute("""
        SELECT DISTINCT c.county_name
        FROM matched_leads ml
        JOIN counties c ON ml.county_id = c.id
        ORDER BY c.county_name
    """)
    return [row[0] for row in cur.fetchall()]


def main():
    parser = argparse.ArgumentParser(description="DBPR enrichment for all counties")
    parser.add_argument("--county", help="Only enrich a specific county")
    parser.add_argument("--force",  action="store_true", help="Re-enrich already matched leads")
    args = parser.parse_args()

    force = args.force or FORCE_REENRICH

    print("Loading DBPR data...")
    dbpr_rows = load_dbpr_rows()
    print(f"  DBPR rows loaded: {len(dbpr_rows):,}")

    conn = get_connection()
    total_matched = 0
    total_unmatched = 0

    try:
        with conn:
            with conn.cursor() as cur:
                counties = [args.county] if args.county else get_active_counties(cur)
                print(f"  Counties to process: {counties}\n")

                for county in counties:
                    result = enrich_county(cur, dbpr_rows, county, force=force)
                    print(f"  {result['county']:15} → "
                          f"processed: {result['processed']:4} | "
                          f"matched: {result['matched']:4} | "
                          f"unmatched: {result['unmatched']:4}")
                    total_matched   += result["matched"]
                    total_unmatched += result["unmatched"]

        print(f"\n--- DBPR enrichment summary ---")
        print(f"  Total matched  : {total_matched}")
        print(f"  Total unmatched: {total_unmatched}")
        print(f"\nNext: python -m app.workers.send_email_campaign")

    finally:
        conn.close()


if __name__ == "__main__":
    main()