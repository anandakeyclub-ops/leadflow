import psycopg2
conn = psycopg2.connect(host='localhost', port=5434, dbname='leadflow', user='postgres', password='postgres')
cur = conn.cursor()

# Fix match for Tarrant and Collin using correct column name
cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
conn.commit()

cur.execute("""
    UPDATE texas_liens tl
    SET tdlr_match_id = t.id,
        updated_at = NOW()
    FROM texas_tdlr_contacts t
    WHERE tl.tdlr_match_id IS NULL
    AND tl.status = 'active'
    AND tl.county IN ('Tarrant', 'Collin')
    AND (
        similarity(UPPER(tl.debtor_name), UPPER(t.owner_name)) > 0.5
        OR similarity(UPPER(tl.debtor_name), UPPER(COALESCE(t.business_name,''))) > 0.5
    )
""")
matched = cur.rowcount
conn.commit()
print(f'Newly matched Tarrant+Collin: {matched}')

# Also update lien_match flag on TDLR contacts
cur.execute("""
    UPDATE texas_tdlr_contacts t
    SET lien_match = TRUE
    FROM texas_liens tl
    WHERE tl.tdlr_match_id = t.id
    AND t.lien_match IS NOT TRUE
""")
print(f'Updated lien_match flag: {cur.rowcount}')
conn.commit()

# Final counts
cur.execute("""
    SELECT county, COUNT(*) 
    FROM texas_liens 
    WHERE tdlr_match_id IS NOT NULL 
    AND status='active'
    GROUP BY county ORDER BY county
""")
print('\nMatched active by county:')
for row in cur.fetchall():
    print(f'  {row[0]:12} {row[1]}')

cur.execute("SELECT COUNT(*) FROM texas_tdlr_contacts WHERE lien_match=TRUE")
print(f'\nTotal TDLR with lien match: {cur.fetchone()[0]}')

cur.execute("SELECT COUNT(*) FROM texas_tdlr_contacts WHERE lien_match=TRUE AND email IS NOT NULL")
print(f'TDLR matched with email: {cur.fetchone()[0]}')

conn.close()