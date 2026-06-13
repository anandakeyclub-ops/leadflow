"""
fix_queued.py
=============
Contacts marked 'queued' from dry runs are blocking real emails.
This resets them so they get picked up in the next real send.
"""
from app.core.db import get_connection
from datetime import datetime

conn = get_connection()
cur  = conn.cursor()

# Show what queued looks like
cur.execute("""
    SELECT to_email, sent_at, sequence_step
    FROM email_sends
    WHERE status = 'queued'
    AND campaign_id = 'lien_outreach_2026'
    ORDER BY sent_at
    LIMIT 10
""")
rows = cur.fetchall()
print(f"Sample queued records ({len(rows)} shown):")
for r in rows:
    print(f"  {r[0]:<40} step {r[2]}  {str(r[1])[:16]}")

# Delete queued records so contacts get real emails
cur.execute("""
    DELETE FROM email_sends
    WHERE status = 'queued'
    AND campaign_id = 'lien_outreach_2026'
""")
deleted = cur.rowcount
conn.commit()
print(f"\nDeleted {deleted} queued (dry-run) records")
print(f"These {deleted} contacts will now receive real emails in tomorrow's run")

# Verify
cur.execute("""
    SELECT status, COUNT(*) FROM email_sends
    WHERE campaign_id = 'lien_outreach_2026'
    GROUP BY status ORDER BY COUNT(*) DESC
""")
print("\nUpdated status breakdown:")
for r in cur.fetchall():
    print(f"  {r[0]:<15} {r[1]:>6,}")

conn.close()
