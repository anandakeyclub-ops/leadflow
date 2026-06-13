"""Check current SerpAPI usage and what's queued for enrichment."""
import psycopg2, os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path("C:/Users/Dana/Desktop/leadflow/.env"))

DB = dict(host="localhost", port=5434, dbname="leadflow",
          user="postgres", password="postgres")

conn = psycopg2.connect(**DB)
try:
    # TX TDLR — matched contacts ready
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(id),
                   COUNT(CASE WHEN email IS NOT NULL AND email != '' THEN 1 END),
                   COUNT(CASE WHEN email IS NULL OR email = '' THEN 1 END)
            FROM texas_tdlr_contacts
            WHERE lien_match = TRUE
        """)
        r = cur.fetchone()
    print(f"TX TDLR matched contacts:")
    print(f"  Total matched   : {r[0]:,}")
    print(f"  Already have email : {r[1]:,}  <- bridge-ready now")
    print(f"  Need enrichment : {r[2]:,}")

    # AZ ROC — matched contacts (after 1825-day scrape will have more)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(id),
                   COUNT(CASE WHEN email IS NOT NULL AND email != '' THEN 1 END),
                   COUNT(CASE WHEN email IS NULL OR email = '' THEN 1 END)
            FROM arizona_roc_contacts
            WHERE lien_match = TRUE
        """)
        r2 = cur.fetchone()
    print(f"\nAZ ROC matched contacts:")
    print(f"  Total matched      : {r2[0]:,}")
    print(f"  Already have email : {r2[1]:,}")
    print(f"  Need enrichment    : {r2[2]:,}")

    # Check SerpAPI key
    key = os.getenv("SERPAPI_KEY", "")
    print(f"\nSerpAPI key present: {'YES (' + key[:8] + '...)' if key else 'NO'}")

    # Check multi_state_email_enrichment for SerpAPI usage tracking
    print("\nTo check remaining SerpAPI searches:")
    print("  curl 'https://serpapi.com/account?api_key=YOUR_KEY'")
    print("  Or: python fix_tdlr_and_enrich.py --stats")

finally:
    conn.close()
