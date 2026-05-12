"""
fix_raw_liens.py - Full FK chain dedup for raw_liens and all dependents.
Usage: python fix_raw_liens.py
"""
import sys
sys.path.insert(0, ".")
from app.core.db import get_connection

def main():
    conn = get_connection()
    conn.autocommit = False
    try:
        cur = conn.cursor()

        # ----------------------------------------------------------------
        # Step 1: Repoint normalized_liens → keeper raw_lien
        # ----------------------------------------------------------------
        print("Step 1: Repointing normalized_liens to keeper raw_lien ids...")
        cur.execute("""
            UPDATE normalized_liens nl
            SET raw_lien_id = keepers.keeper_id
            FROM (
                SELECT id,
                       MIN(id) OVER (PARTITION BY county_id, source_record_id) AS keeper_id
                FROM raw_liens
            ) keepers
            WHERE nl.raw_lien_id = keepers.id
              AND keepers.id <> keepers.keeper_id
        """)
        print(f"  Repointed {cur.rowcount} rows")

        # ----------------------------------------------------------------
        # Step 2: Build the final keeper lien map
        # (after repoint, partition normalized_liens by county+raw_lien_id)
        # Then find which matched_leads will collide when we repoint lien_id
        # ----------------------------------------------------------------
        print("Step 2: Computing final keeper lead map (permit+lien aware)...")
        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT ml.county_id, ml.permit_id, nl_keepers.keeper_lien_id, COUNT(*) as cnt
                FROM matched_leads ml
                JOIN (
                    SELECT id,
                           MIN(id) OVER (PARTITION BY county_id, raw_lien_id) AS keeper_lien_id
                    FROM normalized_liens
                ) nl_keepers ON ml.lien_id = nl_keepers.id
                GROUP BY ml.county_id, ml.permit_id, nl_keepers.keeper_lien_id
                HAVING COUNT(*) > 1
            ) collisions
        """)
        collision_count = cur.fetchone()[0]
        print(f"  Collision groups that need pre-deletion: {collision_count}")

        # ----------------------------------------------------------------
        # Step 3: Delete children of leads that will be deleted
        # (use final keeper lien to compute which leads survive)
        # ----------------------------------------------------------------
        print("Step 3: Deleting bookings pointing at leads-to-be-deleted...")
        cur.execute("""
            DELETE FROM bookings
            WHERE lead_id IN (
                SELECT id FROM matched_leads
                WHERE id NOT IN (
                    SELECT MIN(ml.id)
                    FROM matched_leads ml
                    JOIN (
                        SELECT id,
                               MIN(id) OVER (PARTITION BY county_id, raw_lien_id) AS keeper_lien_id
                        FROM normalized_liens
                    ) nl_keepers ON ml.lien_id = nl_keepers.id
                    GROUP BY ml.county_id, ml.permit_id, nl_keepers.keeper_lien_id
                )
            )
        """)
        print(f"  Deleted {cur.rowcount} bookings rows")

        print("Step 4: Deleting contacts pointing at leads-to-be-deleted...")
        cur.execute("""
            DELETE FROM contacts
            WHERE lead_id IN (
                SELECT id FROM matched_leads
                WHERE id NOT IN (
                    SELECT MIN(ml.id)
                    FROM matched_leads ml
                    JOIN (
                        SELECT id,
                               MIN(id) OVER (PARTITION BY county_id, raw_lien_id) AS keeper_lien_id
                        FROM normalized_liens
                    ) nl_keepers ON ml.lien_id = nl_keepers.id
                    GROUP BY ml.county_id, ml.permit_id, nl_keepers.keeper_lien_id
                )
            )
        """)
        print(f"  Deleted {cur.rowcount} contacts rows")

        print("Step 5: Deleting outreach_events pointing at leads-to-be-deleted...")
        cur.execute("""
            DELETE FROM outreach_events
            WHERE lead_id IN (
                SELECT id FROM matched_leads
                WHERE id NOT IN (
                    SELECT MIN(ml.id)
                    FROM matched_leads ml
                    JOIN (
                        SELECT id,
                               MIN(id) OVER (PARTITION BY county_id, raw_lien_id) AS keeper_lien_id
                        FROM normalized_liens
                    ) nl_keepers ON ml.lien_id = nl_keepers.id
                    GROUP BY ml.county_id, ml.permit_id, nl_keepers.keeper_lien_id
                )
            )
        """)
        print(f"  Deleted {cur.rowcount} outreach_events rows")

        # ----------------------------------------------------------------
        # Step 6: Delete duplicate matched_leads (children are clear)
        # ----------------------------------------------------------------
        print("Step 6: Deleting duplicate matched_leads...")
        cur.execute("""
            DELETE FROM matched_leads
            WHERE id NOT IN (
                SELECT MIN(ml.id)
                FROM matched_leads ml
                JOIN (
                    SELECT id,
                           MIN(id) OVER (PARTITION BY county_id, raw_lien_id) AS keeper_lien_id
                    FROM normalized_liens
                ) nl_keepers ON ml.lien_id = nl_keepers.id
                GROUP BY ml.county_id, ml.permit_id, nl_keepers.keeper_lien_id
            )
        """)
        print(f"  Deleted {cur.rowcount} matched_leads rows")

        # ----------------------------------------------------------------
        # Step 7: Repoint surviving matched_leads to keeper lien ids
        # (no collisions now — duplicates are gone)
        # ----------------------------------------------------------------
        print("Step 7: Repointing matched_leads to keeper normalized_lien ids...")
        cur.execute("""
            UPDATE matched_leads ml
            SET lien_id = keepers.keeper_id
            FROM (
                SELECT id,
                       MIN(id) OVER (PARTITION BY county_id, raw_lien_id) AS keeper_id
                FROM normalized_liens
            ) keepers
            WHERE ml.lien_id = keepers.id
              AND keepers.id <> keepers.keeper_id
        """)
        print(f"  Repointed {cur.rowcount} matched_leads rows")

        # ----------------------------------------------------------------
        # Step 8: Delete duplicate normalized_liens (FK now clear)
        # ----------------------------------------------------------------
        print("Step 8: Deleting duplicate normalized_liens...")
        cur.execute("""
            DELETE FROM normalized_liens
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM normalized_liens
                GROUP BY county_id, raw_lien_id
            )
        """)
        print(f"  Deleted {cur.rowcount} normalized_liens rows")

        # ----------------------------------------------------------------
        # Step 9: Delete duplicate raw_liens (FK now clear)
        # ----------------------------------------------------------------
        print("Step 9: Deleting duplicate raw_liens...")
        cur.execute("""
            DELETE FROM raw_liens
            WHERE id NOT IN (
                SELECT MIN(id) FROM raw_liens GROUP BY county_id, source_record_id
            )
        """)
        print(f"  Deleted {cur.rowcount} raw_liens rows")

        # ----------------------------------------------------------------
        # Step 10: Add unique constraint
        # ----------------------------------------------------------------
        print("Step 10: Adding unique constraint on raw_liens...")
        cur.execute("""
            ALTER TABLE raw_liens
            ADD CONSTRAINT uq_raw_liens_county_record
            UNIQUE (county_id, source_record_id)
        """)
        print("  Constraint added")

        # ----------------------------------------------------------------
        # Verify
        # ----------------------------------------------------------------
        print("\nVerifying...")
        for table in ["raw_liens", "normalized_liens", "matched_leads",
                       "contacts", "bookings", "outreach_events"]:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            print(f"  {table:25s}: {cur.fetchone()[0]}")

        cur.execute("""
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'raw_liens'::regclass AND contype = 'u'
        """)
        print(f"  raw_liens constraints : {[r[0] for r in cur.fetchall()]}")

        cur.execute("""
            SELECT COUNT(*) FROM normalized_liens nl
            LEFT JOIN raw_liens rl ON nl.raw_lien_id = rl.id
            WHERE rl.id IS NULL
        """)
        orphaned_liens = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM matched_leads ml
            LEFT JOIN normalized_liens nl ON ml.lien_id = nl.id
            WHERE ml.lien_id IS NOT NULL AND nl.id IS NULL
        """)
        orphaned_leads = cur.fetchone()[0]

        print(f"  orphaned normalized_liens : {orphaned_liens}")
        print(f"  orphaned matched_leads    : {orphaned_leads}")

        if orphaned_liens > 0 or orphaned_leads > 0:
            print("\nERROR: Orphaned rows found — rolling back")
            conn.rollback()
            sys.exit(1)

        conn.commit()
        print("\nDone. All steps committed successfully.")

    except Exception as e:
        conn.rollback()
        print(f"\nERROR: {e}")
        print("Rolled back — nothing was changed.")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()