"""Check AZ ROC schema and find real strong matches."""
import psycopg2

DB = dict(host="localhost", port=5434, dbname="leadflow",
          user="postgres", password="postgres")

conn = psycopg2.connect(**DB)
try:
    # Check arizona_roc_contacts columns
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'arizona_roc_contacts'
            ORDER BY ordinal_position
        """)
        print("arizona_roc_contacts columns:")
        cols = [r[0] for r in cur.fetchall()]
        for c in cols: print(f"  {c}")

    # Sample some ROC contacts to understand the data
    with conn.cursor() as cur:
        cur.execute("""
            SELECT business_name, owner_name
            FROM arizona_roc_contacts LIMIT 5
        """)
        print("\nSample ROC contacts:")
        for r in cur.fetchall():
            print(f"  biz={r[0]!r}  owner={r[1]!r}")

    # Strong matches > 0.70
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                arc.business_name, nl.debtor_name,
                similarity(UPPER(arc.business_name), UPPER(nl.debtor_name)) AS sim,
                nl.filed_date
            FROM arizona_roc_contacts arc
            CROSS JOIN normalized_liens nl
            WHERE nl.state = 'AZ'
              AND arc.business_name IS NOT NULL
              AND LENGTH(arc.business_name) > 5
              AND similarity(UPPER(arc.business_name), UPPER(nl.debtor_name)) > 0.7
            ORDER BY sim DESC LIMIT 20
        """)
        rows = cur.fetchall()
    print(f"\nStrong matches (sim > 0.70): {len(rows)}")
    for biz, debtor, sim, filed in rows:
        print(f"  {sim:.2f}  {biz[:38]:<38} <-> {debtor[:38]}")

    # How many AZ lien debtors look like individuals vs businesses?
    with conn.cursor() as cur:
        cur.execute("""
            SELECT debtor_name FROM normalized_liens
            WHERE state = 'AZ' LIMIT 30
        """)
        names = [r[0] for r in cur.fetchall()]

    import re
    biz_indicators = ['LLC','INC','CORP','LTD','CO ','COMPANY',
                      'SERVICES','GROUP','ENTERPRISE','SOLUTIONS']
    businesses = [n for n in names
                  if any(w in n.upper() for w in biz_indicators)]
    individuals = [n for n in names if n not in businesses]
    print(f"\nOf first 30 AZ liens:")
    print(f"  Likely businesses  : {len(businesses)}")
    print(f"  Likely individuals : {len(individuals)}")
    print("  Individual samples:", individuals[:5])

finally:
    conn.close()
