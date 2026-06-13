# cleanup_stale_queued_emails.py
# Safely marks old queued email_sends rows as stale_queued so they stop blocking the live sender.
#
# Usage:
#   cd C:\Users\Dana\Desktop\leadflow
#   python scripts\maintenance\cleanup_stale_queued_emails.py --dry-run
#   python scripts\maintenance\cleanup_stale_queued_emails.py
#
# Optional:
#   python scripts\maintenance\cleanup_stale_queued_emails.py --hours 6
#   python scripts\maintenance\cleanup_stale_queued_emails.py --campaign lien_outreach_2026

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import datetime

# Make direct script execution work from scripts/maintenance/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.db import get_connection


DEFAULT_CAMPAIGN = "lien_outreach_2026"


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean stale queued email_sends rows.")
    parser.add_argument("--campaign", default=DEFAULT_CAMPAIGN)
    parser.add_argument("--hours", type=int, default=6, help="Queued rows older than this are stale.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("\n" + "=" * 72)
    print("  Cleanup Stale Queued Emails")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Campaign : {args.campaign}")
    print(f"  Older than: {args.hours} hours")
    print(f"  Mode     : {'DRY RUN' if args.dry_run else 'LIVE'}")
    print("=" * 72 + "\n")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Ensure helper columns exist. These are harmless if already present.
            cur.execute("""
                ALTER TABLE email_sends
                ADD COLUMN IF NOT EXISTS stale_reason TEXT;

                ALTER TABLE email_sends
                ADD COLUMN IF NOT EXISTS stale_marked_at TIMESTAMPTZ;
            """)
            conn.commit()

            cur.execute("""
                SELECT sequence_step, COUNT(*), MIN(sent_at), MAX(sent_at)
                FROM email_sends
                WHERE campaign_id = %s
                  AND status = 'queued'
                  AND sent_at <= NOW() - (%s || ' hours')::interval
                GROUP BY sequence_step
                ORDER BY sequence_step
            """, (args.campaign, args.hours))
            rows = cur.fetchall()

            total = sum(r[1] for r in rows) if rows else 0

            if not rows:
                print("No stale queued rows found.")
                return

            print("Stale queued rows found:")
            for step, count, oldest, newest in rows:
                print(f"  Step {step}: {count:,} rows | oldest={oldest} | newest={newest}")

            print(f"\nTotal stale queued rows: {total:,}")

            if args.dry_run:
                print("\nDRY RUN ONLY — no rows changed.")
                print("\nRun without --dry-run to mark these as stale_queued.")
                return

            cur.execute("""
                UPDATE email_sends
                SET status = 'stale_queued',
                    stale_reason = 'Queued row older than cleanup threshold; marked stale to unblock sender.',
                    stale_marked_at = NOW()
                WHERE campaign_id = %s
                  AND status = 'queued'
                  AND sent_at <= NOW() - (%s || ' hours')::interval
            """, (args.campaign, args.hours))

            changed = cur.rowcount
            conn.commit()

            print(f"\n✅ Marked {changed:,} queued rows as stale_queued.")
            print("These rows are preserved for history but no longer block new sends.")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
