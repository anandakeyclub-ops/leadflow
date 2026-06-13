"""
import_dallas_federal_liens.py
==============================
Imports Dallas County federal tax liens from Excel into normalized_liens.

After this runs:
  - enrich_liens_pdl.py    picks up new liens automatically (individual + business)
  - bridge_to_email_pool.py feeds enriched contacts into lien_dbpr_contacts
  - send_email_sequence.py  emails them

Usage:
  python import_dallas_federal_liens.py --file "path/to/Federal Tax Lien - January 2024 - Present Day.xlsx"
  python import_dallas_federal_liens.py --file "..." --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

from app.core.db import get_connection

STATE       = "TX"
COUNTY_NAME = "Dallas"
LIEN_SOURCE = "dallas_county_clerk"
LIEN_TYPE   = "FEDERAL TAX LIEN"

BUSINESS_KEYWORDS = {
    "LLC", "INC", "CORP", "LTD", "CO", "LP", "LLP", "PLLC", "PC", "PA",
    "GROUP", "SERVICES", "SERVICE", "ENTERPRISES", "PROPERTIES", "COMPANY",
    "TRUST", "SOLUTIONS", "MANAGEMENT", "HOLDINGS", "ASSOCIATES", "REALTY",
    "TRANSPORT", "TECHNOLOGIES", "RESTAURANT", "FENCE", "ENERGY", "CENTER",
    "SCHOOL", "HEALTH", "CARE", "FOUNDATION", "PARTNERS", "VENTURES",
    "SYSTEMS", "INDUSTRIES", "NETWORKS", "CONSTRUCTION", "CONSULTING",
    "STAFFING", "MEDIA", "STUDIO", "AGENCY", "INTERNATIONAL",
    "FRANCHISES", "INCORPORATED",
}


def is_business(name: str) -> bool:
    return any(kw in name.upper().split() for kw in BUSINESS_KEYWORDS)


def make_hash(doc_number: str, state: str, county: str) -> str:
    raw = f"{doc_number}|{state}|{county}".lower()
    return hashlib.md5(raw.encode()).hexdigest()


def get_or_create_county(conn, county_name: str, state: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM counties WHERE county_name = %s AND state = %s",
            (county_name, state)
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "INSERT INTO counties (county_name, state, active, created_at) "
            "VALUES (%s, %s, TRUE, NOW()) RETURNING id",
            (county_name, state)
        )
        county_id = cur.fetchone()[0]
        conn.commit()
        print(f"  Created county: {county_name}, {state} -> id={county_id}")
        return county_id


def get_existing_hashes(conn, county_id: int) -> set:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT normalized_hash FROM normalized_liens WHERE county_id = %s",
            (county_id,)
        )
        return {r[0] for r in cur.fetchall()}


def load_excel(filepath: str) -> pd.DataFrame:
    print(f"  Loading {filepath}")
    df = pd.read_excel(filepath)
    df.columns = [c.strip() for c in df.columns]
    df["Recorded Date/Time"] = pd.to_datetime(df["Recorded Date/Time"])
    df["doc_number"] = df["Document Number"].astype(str).str.strip()
    df["grantor"]    = df["First Grantor"].astype(str).str.strip().str.upper()
    print(f"  {len(df):,} rows | {df['Recorded Date/Time'].min().date()} -> {df['Recorded Date/Time'].max().date()}")
    return df


def main():
    parser = argparse.ArgumentParser(description="Import Dallas County Federal Tax Liens")
    parser.add_argument("--file",    required=True, help="Path to Excel file")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    df = load_excel(args.file)

    conn = get_connection()
    conn.autocommit = False

    try:
        # 1. Ensure Dallas County exists
        county_id = get_or_create_county(conn, COUNTY_NAME, STATE)
        print(f"  Dallas County id = {county_id}")

        # 2. Dedupe against existing
        existing = get_existing_hashes(conn, county_id)
        print(f"  Existing liens in DB for Dallas: {len(existing):,}")

        # 3. Build insert list
        to_insert = []
        skipped   = 0

        for _, row in df.iterrows():
            h = make_hash(row["doc_number"], STATE, COUNTY_NAME)
            if h in existing:
                skipped += 1
                continue

            biz      = is_business(row["grantor"])
            debtor   = row["grantor"]

            to_insert.append({
                "county_id":       county_id,
                "debtor_name":     debtor,
                "business_name":   debtor if biz else None,
                "filing_type":     LIEN_TYPE,
                "lien_type":       LIEN_TYPE,
                "filed_date":      row["Recorded Date/Time"].date(),
                "normalized_hash": h,
                "lien_source":     LIEN_SOURCE,
                "state":           STATE,
            })

        individuals = sum(1 for r in to_insert if not r["business_name"])
        businesses  = sum(1 for r in to_insert if r["business_name"])

        print(f"  Skipped (already in DB) : {skipped:,}")
        print(f"  New to insert           : {len(to_insert):,}")
        print(f"    Individuals           : {individuals:,}")
        print(f"    Businesses            : {businesses:,}")

        if args.dry_run:
            print("\n  [DRY RUN] No changes written.")
            print("\nWould run next:")
            print("  python -m app.workers.enrich_liens_pdl --county Dallas --limit 100")
            print("  python bridge_to_email_pool.py --source pdl")
            return

        if not to_insert:
            print("  Nothing new to insert.")
            return

        # 4. Insert
        with conn.cursor() as cur:
            inserted = 0
            for r in to_insert:
                cur.execute("""
                    INSERT INTO normalized_liens
                        (county_id, debtor_name, business_name, filing_type,
                         lien_type, filed_date, normalized_hash, lien_source, state)
                    VALUES
                        (%(county_id)s, %(debtor_name)s, %(business_name)s,
                         %(filing_type)s, %(lien_type)s, %(filed_date)s,
                         %(normalized_hash)s, %(lien_source)s, %(state)s)
                    ON CONFLICT (normalized_hash) DO NOTHING
                """, r)
                inserted += cur.rowcount

        conn.commit()

        print(f"\n{'='*55}")
        print(f"  DALLAS IMPORT COMPLETE")
        print(f"{'='*55}")
        print(f"  Inserted into normalized_liens : {inserted:,}")
        print(f"  Already existed (skipped)      : {skipped:,}")
        print(f"\nNext steps:")
        print(f"  python -m app.workers.enrich_liens_pdl --county Dallas --limit 100")
        print(f"  python bridge_to_email_pool.py --source pdl")

    except Exception as e:
        conn.rollback()
        print(f"\nERROR: {e}")
        import traceback; traceback.print_exc()
    finally:
        conn.close()


if __name__ == "__main__":
    main()