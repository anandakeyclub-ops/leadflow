from app.core.db import get_connection

conn = get_connection()
cur  = conn.cursor()

# Check name fields for contacts showing 'there'
cur.execute("""
    SELECT email, full_name, debtor_name
    FROM lien_dbpr_contacts
    WHERE email IN ('fabioslegacy@gmail.com', 'angelscooling@yahoo.com')
""")
print("Name check:")
for r in cur.fetchall():
    print(f"  email     : {r[0]}")
    print(f"  full_name : {r[1]}")
    print(f"  debtor_name: {r[2]}")
    print()

# Check overall how many contacts have no parseable first name
cur.execute("""
    SELECT COUNT(*) FROM lien_dbpr_contacts
    WHERE full_name IS NULL AND debtor_name IS NULL
""")
print(f"Contacts with no name at all: {cur.fetchone()[0]:,}")

cur.execute("""
    SELECT COUNT(*) FROM lien_dbpr_contacts
    WHERE (full_name IS NOT NULL OR debtor_name IS NOT NULL)
    AND email IS NOT NULL
""")
print(f"Contacts with name + email: {cur.fetchone()[0]:,}")

# Sample of what names look like
cur.execute("""
    SELECT full_name, debtor_name, email
    FROM lien_dbpr_contacts
    WHERE email IS NOT NULL
    LIMIT 10
""")
print("\nSample name formats:")
for r in cur.fetchall():
    print(f"  full_name='{r[0]}' debtor_name='{r[1]}'")

conn.close()
