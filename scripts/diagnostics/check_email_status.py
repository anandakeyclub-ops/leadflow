from app.core.db import get_connection

conn = get_connection()
cur  = conn.cursor()

cur.execute("""
    SELECT status, COUNT(*), MIN(sent_at), MAX(sent_at)
    FROM email_sends
    WHERE campaign_id = 'lien_outreach_2026'
    GROUP BY status
    ORDER BY COUNT(*) DESC
""")

print("\nEmail Send Status Breakdown:")
print(f"  {'Status':<15} {'Count':>7}  {'First':>16}  {'Last':>16}")
print(f"  {'─'*15} {'─'*7}  {'─'*16}  {'─'*16}")
for r in cur.fetchall():
    status    = r[0] or "None"
    count     = r[1]
    first     = str(r[2])[:16] if r[2] else "—"
    last      = str(r[3])[:16] if r[3] else "—"
    print(f"  {status:<15} {count:>7,}  {first:>16}  {last:>16}")

# Total
cur.execute("SELECT COUNT(*) FROM email_sends WHERE campaign_id = 'lien_outreach_2026'")
total = cur.fetchone()[0]
print(f"\n  Total: {total:,}")

# Check throttled specifically
cur.execute("""
    SELECT COUNT(*) FROM email_sends
    WHERE campaign_id = 'lien_outreach_2026'
    AND status IN ('throttled', 'failed')
""")
retryable = cur.fetchone()[0]
print(f"  Retryable (throttled+failed): {retryable:,}")

conn.close()
