"""
reset_db.py
===========
Clears all scraped data from LeadFlow DB and deactivates Broward.
Run ONCE before the 180-day initial pull.

Usage:
  python reset_db.py
  python reset_db.py --confirm   # skip the confirmation prompt
"""
import argparse
from app.core.db import get_connection

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true",
                        help="Skip confirmation prompt")
    args = parser.parse_args()

    if not args.confirm:
        print("\n⚠  This will DELETE all liens, permits, matches, contacts,")
        print("   and outreach events from the database.")
        print("   PDFs on disk will NOT be deleted.\n")
        ans = input("Type 'yes' to continue: ").strip().lower()
        if ans != "yes":
            print("Aborted.")
            return

    conn = get_connection()
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            print("\nClearing data...")

            # Clear in dependency order
            cur.execute("TRUNCATE TABLE outreach_events    RESTART IDENTITY CASCADE")
            print("  ✓ outreach_events cleared")

            cur.execute("TRUNCATE TABLE contacts           RESTART IDENTITY CASCADE")
            print("  ✓ contacts cleared")

            cur.execute("TRUNCATE TABLE matched_leads      RESTART IDENTITY CASCADE")
            print("  ✓ matched_leads cleared")

            cur.execute("TRUNCATE TABLE normalized_liens   RESTART IDENTITY CASCADE")
            print("  ✓ normalized_liens cleared")

            cur.execute("TRUNCATE TABLE normalized_permits RESTART IDENTITY CASCADE")
            print("  ✓ normalized_permits cleared")

            cur.execute("TRUNCATE TABLE raw_liens          RESTART IDENTITY CASCADE")
            print("  ✓ raw_liens cleared")

            cur.execute("TRUNCATE TABLE raw_permits        RESTART IDENTITY CASCADE")
            print("  ✓ raw_permits cleared")

            # Deactivate Broward
            cur.execute("""
                UPDATE counties SET active = false
                WHERE county_name = 'Broward'
            """)
            print("  ✓ Broward deactivated")

            # Ensure all 6 active counties exist and are active
            for county in ["Miami-Dade", "Hillsborough", "Pinellas",
                           "Polk", "Duval", "Lee"]:
                cur.execute("""
                    INSERT INTO counties (county_name, state, active, created_at)
                    VALUES (%s, 'FL', true, NOW())
                    ON CONFLICT (county_name) DO UPDATE SET active = true
                """, (county,))
            print("  ✓ 6 active counties confirmed")

        conn.commit()
        print("\n✓ DB reset complete. Ready for 180-day pull.\n")

    except Exception as e:
        conn.rollback()
        print(f"\n✗ Error: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
