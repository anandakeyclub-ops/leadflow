"""Fix and test TDLR enrichment individual filter."""
import psycopg2

DB = dict(host="localhost", port=5434, dbname="leadflow",
          user="postgres", password="postgres")

conn = psycopg2.connect(**DB)
try:
    # Check what the current query actually returns
    with conn.cursor() as cur:
        cur.execute("""
            SELECT business_name,
                   business_name ~ '^[A-Z]+,\s+[A-Z]' AS looks_individual
            FROM texas_tdlr_contacts
            WHERE lien_match = TRUE
              AND (email IS NULL OR email = '')
              AND business_name IS NOT NULL
              AND business_name != ''
            ORDER BY id
            LIMIT 30
        """)
        rows = cur.fetchall()

    individuals = sum(1 for _, is_indv in rows if is_indv)
    businesses  = sum(1 for _, is_indv in rows if not is_indv)
    print(f"Of first 30 matched no-email TDLR contacts:")
    print(f"  Individuals (LAST, FIRST): {individuals}")
    print(f"  Businesses              : {businesses}")
    print()
    for name, is_indv in rows:
        tag = "INDV" if is_indv else "BIZ "
        print(f"  [{tag}] {name}")

    # Correct count using proper regex
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(id)
            FROM texas_tdlr_contacts
            WHERE lien_match = TRUE
              AND (email IS NULL OR email = '')
              AND business_name IS NOT NULL
              AND business_name != ''
              AND business_name !~ '^[A-Z]+[\s-][A-Z]+,\s*[A-Z]'
        """)
        biz_count = cur.fetchone()[0]
    print(f"\nWith correct regex filter — businesses to enrich: {biz_count}")

finally:
    conn.close()
