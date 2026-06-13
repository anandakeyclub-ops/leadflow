"""
Diagnose AZ ROC matching situation:
- Do we have AZ lien data in normalized_liens?
- Is there a matching step between arizona_roc_contacts and normalized_liens?
- How many ROC contacts are actually lien-matched?
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.core.db import get_connection

conn = get_connection()
cur = conn.cursor()

print("=== AZ normalized_liens ===")
cur.execute("SELECT COUNT(id) FROM normalized_liens WHERE state = 'AZ'")
print(f"  AZ liens in normalized_liens: {cur.fetchone()[0]:,}")

print()
print("=== arizona_roc_contacts schema ===")
cur.execute("""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'arizona_roc_contacts'
    ORDER BY ordinal_position
""")
for col, dtype in cur.fetchall():
    print(f"  {col:<30} {dtype}")

print()
print("=== arizona_roc_contacts lien_match column? ===")
cur.execute("""
    SELECT COUNT(id) FROM arizona_roc_contacts
""")
print(f"  Total ROC contacts: {cur.fetchone()[0]:,}")

# Check if lien_match column exists
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'arizona_roc_contacts'
    AND column_name IN ('lien_match', 'matched', 'lien_id', 'normalized_lien_id')
""")
match_cols = cur.fetchall()
print(f"  Match columns: {[r[0] for r in match_cols] or 'NONE — no matching done yet'}")

print()
print("=== AZ county data ===")
cur.execute("""
    SELECT county_name, COUNT(id)
    FROM counties WHERE state = 'AZ'
    GROUP BY county_name ORDER BY 2 DESC
""")
rows = cur.fetchall()
if rows:
    for county, cnt in rows:
        print(f"  {county:<30} {cnt:,}")
else:
    print("  No AZ counties in counties table")

print()
print("=== Sample AZ normalized_liens ===")
cur.execute("""
    SELECT nl.id, nl.business_name, nl.debtor_name, nl.lien_source, c.county_name
    FROM normalized_liens nl
    JOIN counties c ON c.id = nl.county_id
    WHERE nl.state = 'AZ'
    LIMIT 10
""")
rows = cur.fetchall()
if rows:
    for r in rows: print(f"  {r}")
else:
    print("  No AZ normalized_liens found")

conn.close()
