from app.core.db import get_connection
conn = get_connection()
cur  = conn.cursor()
cols = [
    "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS grantor_name VARCHAR(300)",
    "ALTER TABLE texas_liens ADD COLUMN IF NOT EXISTS file_number VARCHAR(50)",
    "CREATE INDEX IF NOT EXISTS idx_tx_liens_grantor ON texas_liens(grantor_name)",
    "CREATE INDEX IF NOT EXISTS idx_tx_liens_file ON texas_liens(file_number)",
]
for sql in cols:
    try:
        cur.execute(sql)
        print(f"OK: {sql[:60]}")
    except Exception as e:
        print(f"Skip: {e}")
conn.commit()
conn.close()
print("Done")
