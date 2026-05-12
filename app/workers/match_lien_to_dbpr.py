"""
match_lien_to_dbpr.py
=====================
Option 3 matching strategy for counties where permit data has no contractor names.
(Hillsborough, Polk, Duval, Pinellas)

Strategy:
  1. For each lien debtor, search DBPR for a contractor match by name
  2. If found, look for permits in the same county that match their trade
  3. Write matched_leads records

This supplements match_and_score.py which requires name overlap between
permit and lien — impossible when permits have no contractor names.

Usage:
  python -m app.workers.match_lien_to_dbpr
  python -m app.workers.match_lien_to_dbpr --county Hillsborough
  python -m app.workers.match_lien_to_dbpr --dry-run
"""
from __future__ import annotations

import argparse
import re
from datetime import date

from app.core.db import get_connection
from app.workers.enrich_palm_beach_from_dbpr import (
    load_dbpr_rows,
    choose_best_match,
    norm_text,
    score_to_confidence,
)
from app.services.scoring import score_lead

# Counties where permit data has no contractor names
TARGET_COUNTIES = ["Hillsborough", "Polk", "Duval", "Pinellas"]

# Min DBPR match score to create a lead
MIN_DBPR_SCORE = 0.45

# Trade keyword map: DBPR license_type keywords → permit description keywords
TRADE_KEYWORDS = {
    "roof":         ["roof", "shingle", "tile roof", "re-roof", "reroof", "metal roof"],
    "electrical":   ["electric", "wiring", "panel", "generator", "solar"],
    "plumbing":     ["plumb", "water heater", "drain", "sewer", "pipe"],
    "hvac":         ["hvac", "ac ", "air condition", "heat", "mechanical", "duct"],
    "pool":         ["pool", "spa", "hot tub"],
    "general":      ["addition", "renovation", "remodel", "construction", "repair",
                     "alteration", "build", "demolish"],
    "solar":        ["solar", "photovoltaic", "pv "],
    "window":       ["window", "door", "opening", "glass"],
    "painting":     ["paint", "stucco", "drywall"],
    "concrete":     ["concrete", "slab", "foundation", "paving"],
}

def get_trade_keywords(license_type: str) -> list[str]:
    """Map a DBPR license type to permit description keywords."""
    lt = (license_type or "").lower()
    keywords = []
    for trade, kws in TRADE_KEYWORDS.items():
        if trade in lt:
            keywords.extend(kws)
    # Always include general as fallback
    if not keywords:
        keywords = TRADE_KEYWORDS["general"]
    return keywords


def description_matches_trade(description: str, keywords: list[str]) -> bool:
    """Check if permit description matches any trade keyword."""
    if not description:
        return True  # No description — accept all
    desc = description.lower()
    return any(kw in desc for kw in keywords)


def main():
    parser = argparse.ArgumentParser(
        description="Match liens to DBPR contractors, then find matching permits by trade")
    parser.add_argument("--county",  help="Only process a specific county")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    parser.add_argument("--min-score", type=float, default=MIN_DBPR_SCORE)
    args = parser.parse_args()

    counties = [args.county] if args.county else TARGET_COUNTIES
    min_score = args.min_score

    print(f"[match_lien_to_dbpr] Loading DBPR data...")
    dbpr_rows = load_dbpr_rows()
    print(f"  DBPR rows: {len(dbpr_rows):,}")

    conn = get_connection()
    cur  = conn.cursor()

    total_written = 0
    total_skipped = 0

    for county_name in counties:
        print(f"\n  [{county_name}]")

        # Get county_id
        cur.execute("SELECT id FROM counties WHERE county_name = %s AND active = true",
                    (county_name,))
        row = cur.fetchone()
        if not row:
            print(f"    County not found or inactive — skipping")
            continue
        county_id = row[0]

        # Load all liens for this county
        cur.execute("""
            SELECT id, debtor_name, business_name, address_1, filed_date, amount
            FROM normalized_liens
            WHERE county_id = %s AND debtor_name IS NOT NULL
            ORDER BY filed_date DESC
        """, (county_id,))
        liens = cur.fetchall()
        print(f"    Liens: {len(liens)}")

        # Load all permits for this county
        cur.execute("""
            SELECT id, project_description, issued_date, address_1
            FROM normalized_permits
            WHERE county_id = %s
        """, (county_id,))
        permits = cur.fetchall()
        print(f"    Permits: {len(permits)}")

        if not liens or not permits:
            print(f"    No data — skipping")
            continue

        written = 0
        skipped = 0

        for lien_id, debtor_name, lien_biz, lien_addr, filed_date, amount in liens:

            # Match lien debtor to DBPR
            result = choose_best_match(
                dbpr_rows,
                business_name = lien_biz   or "",
                owner_name    = debtor_name or "",
                address_1     = lien_addr   or "",
                debtor_name   = debtor_name or "",
                min_score     = min_score,
            )

            if result is None:
                skipped += 1
                continue

            dbpr_match, dbpr_score = result
            license_type = dbpr_match.get("license_type", "")
            trade_kws    = get_trade_keywords(license_type)

            # Find best matching permit by trade keyword
            best_permit   = None
            best_lead_score = -1

            for permit_id, description, issued_date, permit_addr in permits:
                if not description_matches_trade(description, trade_kws):
                    continue

                lead_score = score_lead(
                    permit_date       = issued_date,
                    lien_date         = filed_date,
                    permit_description= description,
                    match_confidence  = score_to_confidence(dbpr_score),
                    lien_amount       = amount,
                )

                if lead_score > best_lead_score:
                    best_lead_score = lead_score
                    best_permit     = permit_id

            if best_permit is None:
                skipped += 1
                continue

            # Write matched lead
            confidence = score_to_confidence(dbpr_score)
            match_score = round(dbpr_score * 100, 1)

            if not args.dry_run:
                try:
                    cur.execute("""
                        INSERT INTO matched_leads (
                            county_id, permit_id, lien_id,
                            match_score, match_confidence,
                            lead_score, lead_status, enrichment_status
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,'new','pending')
                        ON CONFLICT (county_id, permit_id, lien_id) DO UPDATE SET
                            match_score      = GREATEST(matched_leads.match_score, EXCLUDED.match_score),
                            match_confidence = EXCLUDED.match_confidence,
                            lead_score       = GREATEST(matched_leads.lead_score, EXCLUDED.lead_score),
                            updated_at       = NOW()
                    """, (county_id, best_permit, lien_id,
                          match_score, confidence, best_lead_score))

                    # Also write contact directly from DBPR match
                    full_name = lien_biz or debtor_name or "Unknown"
                    email     = dbpr_match.get("email") or ""
                    if email:
                        # Get the lead_id we just inserted
                        cur.execute("""
                            SELECT id FROM matched_leads
                            WHERE county_id=%s AND permit_id=%s AND lien_id=%s
                        """, (county_id, best_permit, lien_id))
                        lead_row = cur.fetchone()
                        if lead_row:
                            lead_id_new = lead_row[0]
                            cur.execute("""
                                INSERT INTO contacts (
                                    lead_id, full_name, primary_phone, email,
                                    mailing_address_1, city, state, zip,
                                    enrichment_vendor, enrichment_score,
                                    enrichment_status, last_enriched_at
                                )
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                                ON CONFLICT (lead_id) DO UPDATE SET
                                    email             = COALESCE(EXCLUDED.email, contacts.email),
                                    enrichment_status = EXCLUDED.enrichment_status,
                                    last_enriched_at  = NOW()
                            """, (
                                lead_id_new, full_name,
                                dbpr_match.get("phone") or None,
                                email,
                                dbpr_match.get("mailing_address_1") or "",
                                dbpr_match.get("city") or "",
                                dbpr_match.get("state") or "FL",
                                dbpr_match.get("zip") or "",
                                "dbpr_csv",
                                round(dbpr_score * 100, 1),
                                f"matched_dbpr_{confidence}",
                            ))
                    written += 1
                except Exception as e:
                    print(f"    DB error: {e}")
                    conn.rollback()
            else:
                written += 1
                print(f"    [DRY] {debtor_name} → {license_type} score={dbpr_score:.2f} "
                      f"permit={best_permit} lead={best_lead_score}")

        if not args.dry_run:
            conn.commit()

        print(f"    Written: {written} | Skipped: {skipped}")
        total_written += written
        total_skipped += skipped

    cur.close()
    conn.close()

    print(f"\n--- match_lien_to_dbpr summary ---")
    print(f"  Counties   : {counties}")
    print(f"  Leads added: {total_written}")
    print(f"  Skipped    : {total_skipped}")
    print(f"\nNext: python -m app.workers.enrich_dbpr --force")


if __name__ == "__main__":
    main()
