import psycopg2
from datetime import datetime, timezone
conn = psycopg2.connect(host='localhost', port=5434, dbname='leadflow', user='postgres', password='postgres')
with conn.cursor() as cur:
    cur.execute("SELECT COUNT(*), MIN(sent_at), MAX(sent_at) FROM email_sends WHERE sent_at > NOW() - INTERVAL '24 hours' AND status = 'sent'")
    r = cur.fetchone()
    print(f"Sent in last 24h: {r[0]}")
    print(f"First send: {r[1]}")
    print(f"Last send:  {r[2]}")
    if r[2]:
        from datetime import timedelta
        reset_time = r[1] + timedelta(hours=24)
        print(f"Gmail window resets at: {reset_time} (Eastern = subtract 4h)")
conn.close()
