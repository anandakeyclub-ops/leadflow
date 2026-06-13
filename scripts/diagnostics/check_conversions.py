from app.core.db import get_connection
conn = get_connection()
cur  = conn.cursor()
cur.execute("""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'conversions'
    ORDER BY ordinal_position
""")
rows = cur.fetchall()
if rows:
    for r in rows:
        print(f"  {r[0]:30} {r[1]}")
else:
    print("  Table 'conversions' has no columns or does not exist")
conn.close()
