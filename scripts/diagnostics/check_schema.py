import psycopg2
conn = psycopg2.connect(host="localhost", port=5434, dbname="leadflow",
                        user="postgres", password="postgres")
with conn.cursor() as cur:
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'normalized_liens'
        ORDER BY ordinal_position
    """)
    cols = [r[0] for r in cur.fetchall()]
    print("normalized_liens columns:")
    for c in cols:
        print(f"  {c}")
conn.close()
