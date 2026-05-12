"""
fix_county_ids.py
Repoints all liens to Palm Beach (county_id=1) and ensures permits are correct.
Run once: python fix_county_ids.py
"""
import sys
sys.path.insert(0, ".")
from app.core.db import get_connection

def main():
    conn = get_connection()
    conn.autocommit = False
    try:
        cur = conn.cursor()

        # Show current state
        cur.execute("SELECT county_id, COUNT(*) FROM normalized_permits GROUP BY county_id ORDER BY county_id")
        print("normalized_permits before:", cur.fetchall())
        cur.execute("SELECT county_id, COUNT(*) FROM normalized_liens GROUP BY county_id ORDER BY county_id")
        print("normalized_liens before:  ", cur.fetchall())
        cur.execute("SELECT county_id, COUNT(*) FROM raw_liens GROUP BY county_id ORDER BY county_id")
        print("raw_liens before:         ", cur.fetchall())

        # Repoint all liens to Palm Beach (county_id=1)
        # The Palm Beach Clerk (Landmark) scraper always produces Palm Beach liens
        cur.execute("UPDATE raw_liens SET county_id = 1")
        print(f"\nUpdated {cur.rowcount} raw_liens → county_id=1 (Palm Beach)")

        cur.execute("UPDATE normalized_liens SET county_id = 1")
        print(f"Updated {cur.rowcount} normalized_liens → county_id=1 (Palm Beach)")

        # Fix any stray permits on county_id=4 that belong to Palm Beach
        cur.execute("""
            SELECT id, owner_name, address_1 FROM normalized_permits
            WHERE county_id = 4
        """)
        stray = cur.fetchall()
        if stray:
            print(f"\nStray permits on county_id=4:")
            for r in stray:
                print(f"  {r}")
            cur.execute("UPDATE normalized_permits SET county_id = 1 WHERE county_id = 4")
            cur.execute("UPDATE raw_permits SET county_id = 1 WHERE county_id = 4")
            print(f"Repointed {cur.rowcount} stray permits to county_id=1")

        conn.commit()

        # Verify
        cur.execute("SELECT county_id, COUNT(*) FROM normalized_permits GROUP BY county_id ORDER BY county_id")
        print("\nnormalized_permits after:", cur.fetchall())
        cur.execute("SELECT county_id, COUNT(*) FROM normalized_liens GROUP BY county_id ORDER BY county_id")
        print("normalized_liens after:  ", cur.fetchall())

        print("\nDone.")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
