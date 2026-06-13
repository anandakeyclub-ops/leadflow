from app.core.db import get_connection

conn = get_connection()
cur  = conn.cursor()

for table in ["email_opens", "email_clicks", "email_sends"]:
    print(f"\n── {table} ──")
    try:
        cur.execute(f"SELECT * FROM {table} LIMIT 0")
        cols = [d[0] for d in cur.description]
        for c in cols:
            print(f"  {c}")
    except Exception as e:
        print(f"  NOT FOUND: {e}")
        conn.rollback()

conn.close()
print("\nDone.")