import psycopg2
conn = psycopg2.connect(host='localhost', port=5434, dbname='leadflow', user='postgres', password='postgres')
cur = conn.cursor()

# Check current state
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='lien_contact_enrichment' ORDER BY ordinal_position")
cols = [row[0] for row in cur.fetchall()]
print("Columns:", cols)

cur.execute("SELECT * FROM lien_contact_enrichment LIMIT 5")
for row in cur.fetchall():
    print(row)

conn.close()