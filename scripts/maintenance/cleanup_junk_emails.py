"""
cleanup_junk_emails.py
======================
Removes junk/placeholder/wrong-business emails from lien_dbpr_contacts
that were saved before the quality filter was added.

Usage:
    python cleanup_junk_emails.py --dry-run   # preview what will be deleted
    python cleanup_junk_emails.py             # actually clean
"""
import argparse
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.core.db import get_connection

# ── Junk detection ─────────────────────────────────────────────────────────────

JUNK_EMAIL_PATTERNS = [
    r"^.{1,2}@",                          # single/double char local
    r"first\.last@", r"firstname\.lastname@",
    r"^name@", r"^user@", r"^your@",
    r"^someone@", r"^example@", r"^sample@",
    r"^placeholder@", r"^test@", r"^xx@",
    r"^email@", r"^youremail@", r"^your\.name@",
]

JUNK_DOMAINS = {
    "buildzoom.com", "h1bdata.info", "trademarkelite.com",
    "arlosmanagement.com", "spectorcox.com", "usda.gov",
    "utsouthwestern.edu", "godaddy.com", "faisalman.com",
    "bug-reporting-xalgha6.m-w.com", "fe73oqfa.rfcq",
    "ll.an", "h-ftp1h.udo", "company.com", "domain.com",
    "yourdomain.com", "theuptownagency.com",  # first.last@ placeholder
    "jadeandcloveraz.com",  # Arizona company matched to Dallas TX
    "traction", "proviser.net",
}

JUNK_DOMAIN_SUFFIXES = (".edu", ".gov")

JUNK_EXACT = {
    "6@h-ftp1h.udo", "doe@company.com", "user@domain.com",
    "name@company.com", "example@yourdomain.com", "xx@xxxx.xx",
    "-@fe73oqfa.rfcq", "r@ll.an", "first.last@theuptownagency.com",
    "a@exelatech.com",   # single char local
    "j@dfwcpg.com",      # single char local
    "f@faisalman.com",   # single char local + wrong business
}


def is_junk(email: str) -> bool:
    if not email or "@" not in email:
        return True

    email = email.lower().strip()

    # Exact matches
    if email in JUNK_EXACT:
        return True

    local, domain = email.rsplit("@", 1)

    # Short local part
    if len(local) <= 2:
        return True

    # Garbage TLD
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    if len(tld) < 2 or not tld.isalpha():
        return True

    # Junk domains
    if domain in JUNK_DOMAINS:
        return True
    if any(domain.endswith(s) for s in JUNK_DOMAIN_SUFFIXES):
        return True

    # Pattern matches
    for pattern in JUNK_EMAIL_PATTERNS:
        if re.search(pattern, email):
            return True

    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_connection()
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, lien_id, debtor_name, email
                FROM lien_dbpr_contacts
                WHERE email IS NOT NULL
                  AND state = 'TX'
                ORDER BY id
            """)
            rows = cur.fetchall()

        print(f"\n{'='*60}")
        print(f"  Email Quality Cleanup — lien_dbpr_contacts (TX)")
        print(f"{'='*60}")
        print(f"  Total TX rows with email: {len(rows):,}\n")

        to_delete = []
        to_keep   = []

        for row_id, lien_id, debtor, email in rows:
            if is_junk(email):
                to_delete.append((row_id, lien_id, debtor, email))
                print(f"  🗑  JUNK  : {email:<45} | {debtor[:35]}")
            else:
                to_keep.append((row_id, lien_id, debtor, email))

        print(f"\n  Total junk  : {len(to_delete):,}")
        print(f"  Total clean : {len(to_keep):,}")

        if args.dry_run:
            print(f"\n  [DRY RUN] No changes written.")
            return

        if not to_delete:
            print(f"\n  Nothing to clean up.")
            return

        # Null out junk emails (keep the row, just clear email)
        # so we can re-enrich these businesses later
        ids_to_null = [r[0] for r in to_delete]  # lien_dbpr_contacts.id
        with conn.cursor() as cur:
            cur.execute(f"""
                UPDATE lien_dbpr_contacts
                SET email = NULL, confidence = 'low'
                WHERE id = ANY(%s)
            """, (ids_to_null,))

        # Also clear from lien_contact_enrichment so re-enrichment picks them up
        lien_ids = [r[1] for r in to_delete if r[1] is not None]  # normalized_lien_id
        with conn.cursor() as cur:
            cur.execute(f"""
                DELETE FROM lien_contact_enrichment
                WHERE normalized_lien_id = ANY(%s)
                  AND source = 'google_cse'
            """, (lien_ids,))

        conn.commit()

        print(f"\n  ✅ Nulled {len(to_delete):,} junk emails")
        print(f"  ✅ Cleared enrichment records so they can be re-enriched")
        print(f"\n  Clean emails remaining: {len(to_keep):,}")
        print(f"{'='*60}\n")

    except Exception as e:
        conn.rollback()
        print(f"\nERROR: {e}")
        import traceback; traceback.print_exc()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
