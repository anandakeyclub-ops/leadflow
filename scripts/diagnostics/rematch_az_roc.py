"""
Fix AZ ROC matching:
1. Reset all AZ false-positive lien_match flags
2. Re-match with tighter rules:
   - Raise similarity threshold to 0.6
   - Remove the first-word LIKE (causes "Enterprises" false matches)
   - Add minimum word count check so generic words don't match
"""
import psycopg2

DB = dict(host="localhost", port=5434, dbname="leadflow",
          user="postgres", password="postgres")

conn = psycopg2.connect(**DB)
try:
    # Step 1: Reset ALL lien_match on AZ ROC contacts
    # (They were matched against AZ liens just now — reset all)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE arizona_roc_contacts arc
            SET lien_match = FALSE
            WHERE lien_match = TRUE
              AND EXISTS (
                SELECT 1 FROM normalized_liens nl
                WHERE nl.state = 'AZ'
                  AND (
                    similarity(UPPER(COALESCE(arc.business_name,'')),
                               UPPER(nl.debtor_name)) > 0.0
                    OR similarity(UPPER(COALESCE(arc.owner_name,'')),
                                  UPPER(nl.debtor_name)) > 0.0
                  )
              )
        """)
        reset_count = cur.rowcount
    conn.commit()
    print(f"Reset {reset_count:,} AZ lien_match flags")

    # Step 2: Re-match with tighter rules
    # - Similarity > 0.6 (was 0.45)
    # - NO first-word LIKE match (was causing generic word false positives)
    # - Require business_name has at least 2 meaningful words (length > 10)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE arizona_roc_contacts arc
            SET lien_match = TRUE
            FROM normalized_liens nl
            WHERE nl.state = 'AZ'
              AND arc.lien_match IS NOT TRUE
              AND arc.business_name IS NOT NULL
              AND LENGTH(arc.business_name) > 10
              AND nl.business_name IS NOT NULL
              AND LENGTH(nl.debtor_name) > 10
              AND similarity(
                    UPPER(arc.business_name),
                    UPPER(nl.debtor_name)
                  ) > 0.6
        """)
        biz_matched = cur.rowcount
    conn.commit()

    # Owner name match (tighter too)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE arizona_roc_contacts arc
            SET lien_match = TRUE
            FROM normalized_liens nl
            WHERE nl.state = 'AZ'
              AND arc.lien_match IS NOT TRUE
              AND arc.owner_name IS NOT NULL
              AND LENGTH(arc.owner_name) > 6
              AND similarity(
                    UPPER(arc.owner_name),
                    UPPER(nl.debtor_name)
                  ) > 0.65
        """)
        owner_matched = cur.rowcount
    conn.commit()

    total = biz_matched + owner_matched
    print(f"Business matches (>0.60): {biz_matched:,}")
    print(f"Owner matches   (>0.65): {owner_matched:,}")
    print(f"Total new matches       : {total:,}")

    # Show sample matches
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                arc.business_name, arc.owner_name,
                nl.debtor_name,
                similarity(UPPER(COALESCE(arc.business_name,'')),
                           UPPER(nl.debtor_name)) AS sim,
                nl.filed_date
            FROM arizona_roc_contacts arc
            JOIN normalized_liens nl ON (
                nl.state = 'AZ'
                AND (
                    similarity(UPPER(COALESCE(arc.business_name,'')),
                               UPPER(nl.debtor_name)) > 0.6
                    OR similarity(UPPER(COALESCE(arc.owner_name,'')),
                                  UPPER(nl.debtor_name)) > 0.65
                )
            )
            WHERE arc.lien_match = TRUE
            ORDER BY sim DESC
            LIMIT 20
        """)
        rows = cur.fetchall()

    print(f"\nTop matches (sorted by similarity):")
    for biz, owner, debtor, sim, filed in rows:
        roc = (biz or owner or "?")[:35]
        print(f"  {sim:.2f}  {roc:<35} <-> {debtor[:35]}")

    # Final stats
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(id),
                COUNT(CASE WHEN lien_match=TRUE THEN 1 END)
            FROM arizona_roc_contacts
        """)
        r = cur.fetchone()
    print(f"\nFinal: {r[1]:,} matched out of {r[0]:,} total ROC contacts")

finally:
    conn.close()
