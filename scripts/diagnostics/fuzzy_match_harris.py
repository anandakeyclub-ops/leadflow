"""
fuzzy_match_harris.py
=====================
Improved name matching for Harris County liens vs TDLR contacts.
Uses PostgreSQL pg_trgm trigram similarity for fuzzy matching.

Run: python fuzzy_match_harris.py
"""
from app.core.db import get_connection

conn = get_connection()
cur  = conn.cursor()

print("Setting up pg_trgm extension...")
cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
conn.commit()
print("OK")

print("\nRunning fuzzy match (this may take 1-2 minutes)...")

cur.execute("""
    UPDATE harris_county_liens h
    SET tdlr_match_id = t.id
    FROM texas_tdlr_contacts t
    WHERE h.tdlr_match_id IS NULL
      AND (
        similarity(
            regexp_replace(UPPER(h.grantor_name),
                           '(LLC|INC|CORP|LTD|CO|LP|THE )', '', 'g'),
            regexp_replace(UPPER(COALESCE(t.business_name, '')),
                           '(LLC|INC|CORP|LTD|CO|LP|THE )', '', 'g')
        ) > 0.5
        OR
        similarity(
            UPPER(h.grantor_name),
            UPPER(COALESCE(t.owner_name, ''))
        ) > 0.5
      )
    RETURNING h.id, h.grantor_name, t.business_name, t.owner_name,
              t.license_type, t.business_county
""")

rows = cur.fetchall()
conn.commit()

print(f"\nNew fuzzy matches: {len(rows)}")
for r in rows[:30]:
    lien_name = (r[1] or "")[:40]
    tdlr_name = (r[2] or r[3] or "")[:40]
    print(f"  Lien: {lien_name:<40} TDLR: {tdlr_name:<40} ({r[4]}, {r[5]})")

# Also mark those TDLR contacts as lien_match=TRUE
if rows:
    tdlr_ids = [r[0] for r in rows]
    cur.execute("""
        UPDATE texas_tdlr_contacts
        SET lien_match  = TRUE,
            confidence  = 'high',
            updated_at  = NOW()
        WHERE id IN (
            SELECT tdlr_match_id FROM harris_county_liens
            WHERE tdlr_match_id IS NOT NULL
        )
    """)
    conn.commit()
    print(f"\nMarked TDLR contacts as lien_match=TRUE")

# Final stats
cur.execute("SELECT COUNT(*) FROM harris_county_liens WHERE tdlr_match_id IS NOT NULL")
total_matched = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM texas_tdlr_contacts WHERE lien_match = TRUE")
tdlr_matched  = cur.fetchone()[0]

print(f"\nFinal stats:")
print(f"  Harris liens matched  : {total_matched}")
print(f"  TDLR contacts matched : {tdlr_matched}")

conn.close()
