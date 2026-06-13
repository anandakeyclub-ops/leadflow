import psycopg2
conn = psycopg2.connect(host='localhost', port=5434, dbname='leadflow', user='postgres', password='postgres')
cur = conn.cursor()

# Add missing license_type column to arizona_roc_contacts
cur.execute("ALTER TABLE arizona_roc_contacts ADD COLUMN IF NOT EXISTS license_type VARCHAR(100)")
conn.commit()
print("Added license_type column")

# Fix county_name issue - check normalized_liens columns
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='normalized_liens' ORDER BY ordinal_position")
cols = [r[0] for r in cur.fetchall()]
print(f"normalized_liens columns: {cols}")

conn.close()