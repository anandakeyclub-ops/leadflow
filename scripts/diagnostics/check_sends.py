import psycopg2
conn = psycopg2.connect(host='localhost', port=5434, dbname='leadflow', user='postgres', password='postgres')
with conn.cursor() as cur:
    cur.execute("SELECT sent_at, to_email, status FROM email_sends WHERE sent_at > NOW() - INTERVAL '24 hours' ORDER BY id DESC LIMIT 10")
    for r in cur.fetchall():
        print(r)
conn.close()
