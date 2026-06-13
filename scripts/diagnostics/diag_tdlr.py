from app.core.db import get_connection
conn = get_connection()
cur = conn.cursor()

print('=== TDLR unmatched with emails ===')
cur.execute("""SELECT id, business_name, owner_name, email, business_county
               FROM texas_tdlr_contacts
               WHERE lien_match = FALSE AND email IS NOT NULL AND email != ''
               AND business_state = 'TX' LIMIT 12""")
for r in cur.fetchall(): print(r)

print()
print('=== normalized_liens TX sample ===')
cur.execute("""SELECT nl.id, nl.business_name, nl.debtor_name, nl.state, c.county_name
               FROM normalized_liens nl
               JOIN counties c ON c.id = nl.county_id
               WHERE nl.state = 'TX' LIMIT 10""")
for r in cur.fetchall(): print(r)

print()
print('=== TX normalized_liens count ===')
cur.execute("SELECT COUNT(id) FROM normalized_liens WHERE state = 'TX'")
print('TX liens:', cur.fetchone()[0])

cur.execute("SELECT COUNT(id) FROM normalized_liens WHERE state IS NULL OR state = ''")
print('Null/empty state:', cur.fetchone()[0])

print()
print('=== TDLR totals ===')
cur.execute("""SELECT COUNT(id) total, COUNT(email) with_email,
               COUNT(CASE WHEN lien_match THEN 1 END) matched
               FROM texas_tdlr_contacts""")
print(cur.fetchone())

conn.close()
