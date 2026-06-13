from dotenv import load_dotenv
load_dotenv()
import psycopg2
conn = psycopg2.connect(host='localhost', port=5434, dbname='leadflow', user='postgres', password='postgres')
cur = conn.cursor()
cur.execute("SELECT COUNT(*), MIN(updated_at), MAX(updated_at) FROM texas_tdlr_contacts WHERE email IS NOT NULL AND email != ''")
print('TX enriched:', cur.fetchone())
cur.execute("SELECT email, confidence, updated_at FROM texas_tdlr_contacts WHERE email IS NOT NULL LIMIT 10")
for r in cur.fetchall(): print(r)