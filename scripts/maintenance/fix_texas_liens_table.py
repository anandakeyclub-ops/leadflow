"""
fix_texas_liens_table.py
========================
Adds missing columns to texas_liens table for PublicSearch scraper.
Run once: python fix_texas_liens_table.py
"""
from app.core.db import get_connection

conn = get_connection()
cur  = conn.cursor()

# Check existing columns
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'texas_liens'
    ORDER BY ordinal_position
""")
existing = [r[0] for r in cur.fetchall()]
print(f"Existing columns: {existing}")

# Add missing columns
additions = [
    ("grantee_name",     "VARCHAR(300)"),
    ("county",           "VARCHAR(100)"),
    ("town",             "VARCHAR(100)"),
    ("legal_description","VARCHAR(500)"),
    ("source",           "VARCHAR(50) DEFAULT 'publicsearch'"),
]

for col, dtype in additions:
    if col not in existing:
        cur.execute(f"ALTER TABLE texas_liens ADD COLUMN {col} {dtype}")
        print(f"  Added: {col}")
    else:
        print(f"  Already exists: {col}")

# Add indexes if missing
indexes = [
    "CREATE INDEX IF NOT EXISTS idx_tx_liens_county ON texas_liens(county)",
    "CREATE INDEX IF NOT EXISTS idx_tx_liens_grantee ON texas_liens(grantee_name)",
]
for idx in indexes:
    cur.execute(idx)
    print(f"  Index: OK")

conn.commit()
print("\nDone — texas_liens table updated")

# Show final columns
cur.execute("""
    SELECT column_name, data_type FROM information_schema.columns
    WHERE table_name = 'texas_liens'
    ORDER BY ordinal_position
""")
print("\nFinal columns:")
for r in cur.fetchall():
    print(f"  {r[0]:<25} {r[1]}")

conn.close()
