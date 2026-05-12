"""
fix_normalized_liens.py
Clears normalized_liens and all child rows safely, then re-adds the unique
constraint on normalized_hash so the scraper can insert correctly.
Run once: python fix_normalized_liens.py
"""
import sys
sys.path.insert(0, ".")
from app.core.db import get_connection

def main():
    conn = get_connection()
    conn.autocommit = False
    try:
        cur = conn.cursor()

        print("Step 1: Deleting outreach_events referencing matched_leads with lien_id...")
        cur.execute("""
            DELETE FROM outreach_events
            WHERE lead_id IN (
                SELECT id FROM matched_leads WHERE lien_id IS NOT NULL
            )
        """)
        print(f"  Deleted {cur.rowcount} outreach_events")

        print("Step 2: Deleting contacts referencing matched_leads with lien_id...")
        cur.execute("""
            DELETE FROM contacts
            WHERE lead_id IN (
                SELECT id FROM matched_leads WHERE lien_id IS NOT NULL
            )
        """)
        print(f"  Deleted {cur.rowcount} contacts")

        print("Step 3: Deleting bookings referencing matched_leads with lien_id...")
        cur.execute("""
            DELETE FROM bookings
            WHERE lead_id IN (
                SELECT id FROM matched_leads WHERE lien_id IS NOT NULL
            )
        """)
        print(f"  Deleted {cur.rowcount} bookings")

        print("Step 4: Deleting matched_leads with lien_id...")
        cur.execute("DELETE FROM matched_leads WHERE lien_id IS NOT NULL")
        print(f"  Deleted {cur.rowcount} matched_leads")

        print("Step 5: Deleting all normalized_liens...")
        cur.execute("DELETE FROM normalized_liens")
        print(f"  Deleted {cur.rowcount} normalized_liens")

        print("Step 6: Adding UNIQUE constraint on normalized_hash...")
        # Drop first if exists
        cur.execute("""
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'normalized_liens'::regclass
              AND conname = 'uq_normalized_liens_hash'
        """)
        if cur.fetchone():
            cur.execute("ALTER TABLE normalized_liens DROP CONSTRAINT uq_normalized_liens_hash")
            print("  Dropped existing constraint")
        cur.execute("""
            ALTER TABLE normalized_liens
            ADD CONSTRAINT uq_normalized_liens_hash
            UNIQUE (normalized_hash)
        """)
        print("  Constraint added")

        print("\nVerifying...")
        cur.execute("SELECT COUNT(*) FROM normalized_liens")
        print(f"  normalized_liens : {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM matched_leads WHERE lien_id IS NOT NULL")
        print(f"  matched_leads (with lien) : {cur.fetchone()[0]}")
        cur.execute("""
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'normalized_liens'::regclass AND contype = 'u'
        """)
        print(f"  constraints : {[r[0] for r in cur.fetchall()]}")

        conn.commit()
        print("\nDone. Re-run the scraper to repopulate normalized_liens correctly.")

    except Exception as e:
        conn.rollback()
        print(f"\nERROR: {e}")
        print("Rolled back.")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
