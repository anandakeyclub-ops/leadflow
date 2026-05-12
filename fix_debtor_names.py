"""
fix_debtor_names.py
Fixes normalized_liens debtor_name from raw_payload reverse_names JSON.
Flips "LAST,FIRST" to "First Last" and prefers person names over institutions.
Run once: python fix_debtor_names.py
"""
import sys
sys.path.insert(0, ".")
from app.core.db import get_connection

SQL = """
UPDATE normalized_liens nl
SET debtor_name = INITCAP(LOWER(
    CASE
        WHEN EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(rl.raw_payload->'reverse_names') n
            WHERE n LIKE '%,%'
              AND n !~ '(LLC|INC|CORP|LTD|BANK|ASSN|ASSOCIATION|SERVICES|FUNDING)'
        )
        THEN (
            SELECT
                TRIM(split_part(n, ',', 2)) || ' ' || TRIM(split_part(n, ',', 1))
            FROM jsonb_array_elements_text(rl.raw_payload->'reverse_names') n
            WHERE n LIKE '%,%'
              AND n !~ '(LLC|INC|CORP|LTD|BANK|ASSN|ASSOCIATION|SERVICES|FUNDING)'
            LIMIT 1
        )
        ELSE rl.raw_payload->'reverse_names'->>0
    END
))
FROM raw_liens rl
WHERE nl.raw_lien_id = rl.id
  AND rl.raw_payload->>'source' = 'broward_official_records_bulk'
  AND rl.raw_payload->'reverse_names' IS NOT NULL
  AND jsonb_array_length(rl.raw_payload->'reverse_names') > 0
"""

def main():
    conn = get_connection()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(SQL)
            print(f"Updated {cur.rowcount} normalized_liens debtor_name rows")

            # Verify sample
            cur.execute("""
                SELECT nl.debtor_name, nl.filing_type, nl.filed_date
                FROM normalized_liens nl
                JOIN raw_liens rl ON nl.raw_lien_id = rl.id
                WHERE rl.raw_payload->>'source' = 'broward_official_records_bulk'
                ORDER BY nl.id DESC
                LIMIT 10
            """)
            print("\nSample after fix:")
            for row in cur.fetchall():
                print(f"  {str(row[0] or ''):<30} | {str(row[1] or ''):<25} | {row[2]}")

        conn.commit()
        print("\nCommitted. Run: python -m app.workers.match_and_score")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
