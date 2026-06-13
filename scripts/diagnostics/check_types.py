import psycopg2
conn = psycopg2.connect(host='localhost', port=5434, dbname='leadflow', user='postgres', password='postgres')
cur = conn.cursor()

# Reset SMTP error failures back to queued for retry
cur.execute("""
    UPDATE email_sends
    SET status = 'queued', error_message = NULL
    WHERE status = 'failed'
    AND error_message LIKE '%SMTP_SSL%users%'
""")
reset1 = cur.rowcount

cur.execute("""
    UPDATE email_sends
    SET status = 'queued', error_message = NULL
    WHERE status = 'failed'
    AND error_message LIKE '%connect()%'
""")
reset2 = cur.rowcount

conn.commit()
print(f'Reset for retry: {reset1} SMTP errors + {reset2} connect errors = {reset1+reset2} total')

# Remove duplicate queued emails (keep lowest id)
cur.execute("""
    DELETE FROM email_sends
    WHERE id NOT IN (
        SELECT MIN(id) FROM email_sends
        WHERE status = 'queued'
        GROUP BY to_email, sequence_step
    )
    AND status = 'queued'
    AND to_email IN (
        SELECT to_email FROM email_sends
        WHERE status = 'queued'
        GROUP BY to_email HAVING COUNT(*) > 1
    )
""")
dupes = cur.rowcount
conn.commit()
print(f'Removed {dupes} duplicate queued emails')

cur.execute("SELECT COUNT(*) FROM email_sends WHERE status='queued'")
print(f'Total queued now: {cur.fetchone()[0]:,}')
conn.close()