"""
fix_db_constraints.py
=====================
Adds missing unique constraints and columns needed by LeadFlow scrapers.
Run once before importing permits.

Usage:
  python fix_db_constraints.py
"""
from app.core.db import get_connection

def main():
    conn = get_connection()
    conn.autocommit = True

    fixes = [
        # Unique constraints for ON CONFLICT to work
        ("normalized_permits unique hash",
         "CREATE UNIQUE INDEX IF NOT EXISTS idx_norm_permits_hash "
         "ON normalized_permits (normalized_hash)"),

        ("normalized_liens unique hash",
         "CREATE UNIQUE INDEX IF NOT EXISTS idx_norm_liens_hash "
         "ON normalized_liens (normalized_hash)"),

        ("raw_permits unique county+source",
         "CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_permits_county_source "
         "ON raw_permits (county_id, source_record_id)"),

        ("raw_liens unique county+source",
         "CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_liens_county_source "
         "ON raw_liens (county_id, source_record_id)"),

        # Extra columns some scrapers use
        ("normalized_liens pdf_path",
         "ALTER TABLE normalized_liens ADD COLUMN IF NOT EXISTS pdf_path TEXT"),

        ("normalized_liens amount",
         "ALTER TABLE normalized_liens ADD COLUMN IF NOT EXISTS amount NUMERIC"),

        ("normalized_liens lien_source",
         "ALTER TABLE normalized_liens ADD COLUMN IF NOT EXISTS lien_source TEXT"),

        ("normalized_permits updated_at",
         "ALTER TABLE normalized_permits ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()"),
    ]

    with conn.cursor() as cur:
        for label, sql in fixes:
            try:
                cur.execute(sql)
                print(f"  ✓ {label}")
            except Exception as e:
                print(f"  - {label}: {e}")

    conn.close()
    print("\nDone — run your permit scrapers now.")

if __name__ == "__main__":
    main()
