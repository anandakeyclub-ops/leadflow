"""
enrich_liens_from_dbpr.py
=========================
Matches every lien debtor DIRECTLY against the DBPR contractor database.
No permit matching required.

Logic:
  For each lien debtor name:
    1. Search DBPR by business_name OR owner_name
    2. If score >= threshold → write contact with real email + phone
    3. If no match → skip (don't create placeholder)

This is the highest-leverage enrichment step:
  - Works on ALL counties regardless of permit data quality
  - DBPR has real emails for 99%+ of its 84k records
  - Contractors with IRS liens + active licenses = best prospects

Usage:
  python -m app.workers.enrich_liens_from_dbpr
  python -m app.workers.enrich_liens_from_dbpr --county Miami-Dade
  python -m app.workers.enrich_liens_from_dbpr --min-score 0.40
  python -m app.workers.enrich_liens_from_dbpr --dry-run
  python -m app.workers.enrich_liens_from_dbpr --force   # re-enrich all
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import date
from typing import Optional

from app.core.db import get_connection
from app.workers.enrich_palm_beach_from_dbpr import (
    load_dbpr_rows,
    norm_text,
    token_overlap,
    build_placeholder_email,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_MIN_SCORE = 0.45   # Lower than permit matching since we match name→name directly
HIGH_SCORE        = 0.75   # Treat as high confidence
MEDIUM_SCORE      = 0.55   # Treat as medium confidence

NOISE_TOKENS = {
    "llc", "inc", "corp", "ltd", "co", "company", "lp", "llp", "pa", "pl",
    "the", "and", "of", "at", "in", "for", "a", "an",
    "construction", "services", "service", "contractors", "contractor",
    "builders", "builder", "group", "holdings", "enterprises", "solutions",
    "management", "properties", "property", "realty", "homes", "home",
    "roofing", "electric", "electrical", "plumbing", "mechanical", "hvac",
    "air", "conditioning", "heating", "cooling", "solar", "energy",
}


def meaningful_tokens(name: str) -> set:
    """Extract meaningful tokens — strip noise words."""
    tokens = set(norm_text(name).split())
    return tokens - NOISE_TOKENS


def score_lien_vs_dbpr(lien_debtor: str, dbpr_row: dict) -> float:
    """
    Score a lien debtor name against a DBPR record.
    Tries both business_name and owner_name from DBPR.
    Returns 0.0-1.0.
    """
    t_debtor = norm_text(lien_debtor)
    if not t_debtor or len(t_debtor) < 3:
        return 0.0

    nb = dbpr_row.get("norm_biz", "")
    no = dbpr_row.get("norm_owner", "")

    # Direct token overlap scores
    biz_score   = token_overlap(t_debtor, nb) if nb else 0.0
    owner_score = token_overlap(t_debtor, no) if no else 0.0
    raw_score   = max(biz_score, owner_score)

    if raw_score < 0.15:
        return 0.0

    # Boost if meaningful tokens overlap strongly
    debtor_tokens = meaningful_tokens(lien_debtor)
    biz_tokens    = meaningful_tokens(dbpr_row.get("business_name", ""))
    owner_tokens  = meaningful_tokens(dbpr_row.get("owner_name", ""))

    if debtor_tokens:
        biz_overlap   = len(debtor_tokens & biz_tokens)   / len(debtor_tokens | biz_tokens)   if (debtor_tokens | biz_tokens)   else 0
        owner_overlap = len(debtor_tokens & owner_tokens) / len(debtor_tokens | owner_tokens) if (debtor_tokens | owner_tokens) else 0
        meaningful = max(biz_overlap, owner_overlap)

        # Require at least 1 meaningful shared token for multi-word names
        if len(debtor_tokens) >= 2 and len(debtor_tokens & (biz_tokens | owner_tokens)) == 0:
            return 0.0

        # Blend raw overlap with meaningful token overlap
        score = (raw_score * 0.5) + (meaningful * 0.5)
    else:
        score = raw_score

    return round(score, 4)


def find_best_dbpr_match(
    lien_debtor: str,
    dbpr_rows: list,
    min_score: float = DEFAULT_MIN_SCORE,
) -> Optional[tuple]:
    """
    Find the best DBPR match for a lien debtor name.
    Returns (dbpr_row, score) or None.

    Optimization: pre-filter candidates by first meaningful token
    before doing full scoring on all 84k rows.
    """
    if not lien_debtor or len(lien_debtor.strip()) < 3:
        return None

    debtor_tokens = set(norm_text(lien_debtor).split())
    if not debtor_tokens:
        return None

    # Pre-filter: candidates must share at least one token with the debtor
    # This cuts 84k → ~500-2000 candidates per lien
    candidates = []
    for row in dbpr_rows:
        nb = row.get("norm_biz", "")
        no = row.get("norm_owner", "")
        combined = set((nb + " " + no).split())
        if debtor_tokens & combined:
            candidates.append(row)

    if not candidates:
        return None

    best_row   = None
    best_score = 0.0

    for row in candidates:
        score = score_lien_vs_dbpr(lien_debtor, row)
        if score > best_score:
            best_score = score
            best_row   = row

    if best_score >= min_score and best_row:
        return (best_row, best_score)
    return None


def confidence_label(score: float) -> str:
    if score >= HIGH_SCORE:
        return "high"
    if score >= MEDIUM_SCORE:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Main enrichment
# ---------------------------------------------------------------------------
def enrich_liens(
    county_filter: Optional[str],
    min_score: float,
    force: bool,
    dry_run: bool,
    dbpr_rows: list,
) -> dict:

    conn = get_connection()
    stats = {
        "total_liens": 0,
        "already_enriched": 0,
        "dbpr_matched": 0,
        "no_match": 0,
        "contacts_written": 0,
    }

    # Create table FIRST before any queries reference it
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS lien_dbpr_contacts (
                    id              SERIAL PRIMARY KEY,
                    lien_id         INTEGER NOT NULL REFERENCES normalized_liens(id),
                    county_id       INTEGER NOT NULL,
                    debtor_name     TEXT,
                    full_name       TEXT,
                    email           TEXT,
                    phone           TEXT,
                    mailing_address TEXT,
                    city            TEXT,
                    state           TEXT,
                    zip             TEXT,
                    license_number  TEXT,
                    trade           TEXT,
                    dbpr_score      NUMERIC(5,2),
                    confidence      TEXT,
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (lien_id)
                );
                CREATE INDEX IF NOT EXISTS idx_lien_dbpr_email
                    ON lien_dbpr_contacts (email);
                CREATE INDEX IF NOT EXISTS idx_lien_dbpr_county
                    ON lien_dbpr_contacts (county_id);
            """)
        conn.commit()
        print("  Table lien_dbpr_contacts ready")
    except Exception as e:
        print(f"  Table setup error: {e}")
        conn.rollback()

    county_clause = "AND c.county_name = %(county)s" if county_filter else ""
    force_clause  = "" if force else """
        AND nl.id NOT IN (
            SELECT lien_id FROM lien_dbpr_contacts
        )
    """

    try:
        with conn.cursor() as cur:
            # Load all liens
            query = f"""
                SELECT nl.id, nl.debtor_name, nl.lien_type, nl.filed_date,
                       c.id as county_id, c.county_name
                FROM normalized_liens nl
                JOIN counties c ON nl.county_id = c.id
                WHERE nl.debtor_name IS NOT NULL
                  AND length(trim(nl.debtor_name)) >= 3
                  {county_clause}
                  {force_clause if not force else ''}
                ORDER BY nl.filed_date DESC NULLS LAST
            """
            params = {"county": county_filter} if county_filter else {}
            cur.execute(query, params)
            liens = cur.fetchall()
            stats["total_liens"] = len(liens)
            print(f"  Liens to process: {len(liens)}")

            matched = 0
            no_match = 0

            for lien_id, debtor_name, lien_type, filed_date, county_id, county_name in liens:

                result = find_best_dbpr_match(debtor_name, dbpr_rows, min_score)

                if result is None:
                    no_match += 1
                    continue

                dbpr_row, score = result
                confidence = confidence_label(score)
                full_name  = dbpr_row.get("business_name") or dbpr_row.get("owner_name") or debtor_name
                email      = dbpr_row.get("email", "").strip()
                phone      = dbpr_row.get("phone", "").strip()

                if not email:
                    no_match += 1
                    continue

                matched += 1

                if dry_run:
                    print(f"  [DRY] {debtor_name!r:45} → {full_name!r:45} "
                          f"score={score:.2f} email={email}")
                    continue

                # Write to lien_dbpr_contacts
                try:
                    cur.execute("""
                        INSERT INTO lien_dbpr_contacts (
                            lien_id, county_id, debtor_name, full_name,
                            email, phone, mailing_address, city, state, zip,
                            license_number, trade, dbpr_score, confidence
                        ) VALUES (
                            %(lien_id)s, %(county_id)s, %(debtor)s, %(full_name)s,
                            %(email)s, %(phone)s, %(addr)s, %(city)s, %(state)s, %(zip)s,
                            %(lic)s, %(trade)s, %(score)s, %(conf)s
                        )
                        ON CONFLICT (lien_id) DO UPDATE SET
                            full_name    = EXCLUDED.full_name,
                            email        = EXCLUDED.email,
                            phone        = EXCLUDED.phone,
                            dbpr_score   = EXCLUDED.dbpr_score,
                            confidence   = EXCLUDED.confidence
                    """, {
                        "lien_id":   lien_id,
                        "county_id": county_id,
                        "debtor":    debtor_name[:250],
                        "full_name": full_name[:250],
                        "email":     email[:200],
                        "phone":     phone[:50],
                        "addr":      (dbpr_row.get("mailing_address_1") or "")[:200],
                        "city":      (dbpr_row.get("city") or "")[:100],
                        "state":     (dbpr_row.get("state") or "FL")[:10],
                        "zip":       (dbpr_row.get("zip") or "")[:20],
                        "lic":       (dbpr_row.get("license_number") or "")[:50],
                        "trade":     (dbpr_row.get("license_type") or dbpr_row.get("trade") or "")[:100],
                        "score":     round(score * 100, 1),
                        "conf":      confidence,
                    })
                    stats["contacts_written"] += 1
                except Exception as e:
                    conn.rollback()
                    print(f"  DB error for lien {lien_id}: {e}")
                    continue

            stats["dbpr_matched"] = matched
            stats["no_match"]     = no_match

            if not dry_run:
                conn.commit()

    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

    return stats


# ---------------------------------------------------------------------------
# Export to CSV
# ---------------------------------------------------------------------------
def export_contacts(output_path: Optional[str] = None) -> str:
    """Export lien_dbpr_contacts to a campaign-ready CSV."""
    import csv
    from pathlib import Path
    from datetime import datetime

    if not output_path:
        base = Path(__file__).resolve().parents[2]
        out_dir = base / "data" / "exports" / "email_lists"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / f"lien_dbpr_campaign_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ldc.email,
                    ldc.full_name,
                    ldc.phone,
                    ldc.debtor_name,
                    ldc.trade,
                    ldc.license_number,
                    ldc.confidence,
                    ldc.dbpr_score,
                    c.county_name,
                    nl.lien_type,
                    nl.filed_date,
                    nl.amount
                FROM lien_dbpr_contacts ldc
                JOIN normalized_liens nl  ON ldc.lien_id   = nl.id
                JOIN counties c           ON ldc.county_id = c.id
                WHERE ldc.email IS NOT NULL
                  AND ldc.email != ''
                ORDER BY ldc.dbpr_score DESC, nl.filed_date DESC
            """)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    finally:
        conn.close()

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(rows)

    print(f"  Exported {len(rows)} contacts → {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Match all lien debtors directly to DBPR — no permit required"
    )
    parser.add_argument("--county",    help="Only process a specific county")
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE,
                        help=f"Min match score (default {DEFAULT_MIN_SCORE})")
    parser.add_argument("--force",     action="store_true",
                        help="Re-enrich liens already processed")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Print matches without writing to DB")
    parser.add_argument("--export",    action="store_true",
                        help="Export contacts to CSV after enrichment")
    args = parser.parse_args()

    print(f"\n[enrich_liens_from_dbpr]")
    print(f"  Min score : {args.min_score}")
    print(f"  County    : {args.county or 'ALL'}")
    print(f"  Dry run   : {args.dry_run}")
    print(f"  Force     : {args.force}")

    print(f"\n  Loading DBPR data...")
    dbpr_rows = load_dbpr_rows()
    print(f"  DBPR rows : {len(dbpr_rows):,}")

    stats = enrich_liens(
        county_filter = args.county,
        min_score     = args.min_score,
        force         = args.force,
        dry_run       = args.dry_run,
        dbpr_rows     = dbpr_rows,
    )

    print(f"\n--- Results ---")
    print(f"  Liens processed  : {stats['total_liens']:,}")
    print(f"  DBPR matched     : {stats['dbpr_matched']:,}")
    print(f"  No match         : {stats['no_match']:,}")
    print(f"  Contacts written : {stats['contacts_written']:,}")
    if stats['total_liens'] > 0:
        rate = stats['dbpr_matched'] / stats['total_liens'] * 100
        print(f"  Match rate       : {rate:.1f}%")

    if args.export and not args.dry_run and stats['contacts_written'] > 0:
        print(f"\n  Exporting campaign CSV...")
        export_contacts()

    print(f"\nNext steps:")
    print(f"  python -m app.workers.enrich_liens_from_dbpr --export")
    print(f"  python -m app.workers.enrich_liens_from_dbpr --dry-run  (preview)")


if __name__ == "__main__":
    main()