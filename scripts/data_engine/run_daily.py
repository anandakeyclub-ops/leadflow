r"""
run_daily.py
============
Daily driver for the TaxCase Review data engine.

Runs today's scheduled states through the full collection + enrichment
pipeline, then syncs every email-ready, lien-matched contact into
lien_dbpr_contacts so it flows through the existing 7-touch email sequence.

Scheduled (Windows Task Scheduler) daily at 6:30 AM — before the email
enrichment job at 7:00 AM.

Usage:
  python scripts/data_engine/run_daily.py
  python scripts/data_engine/run_daily.py --states fl,ga   # override
  python scripts/data_engine/run_daily.py --weekday 0      # force Monday set
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))

from scripts.data_engine.data_collector import (  # noqa: E402
    run_state_collection,
    sync_to_email_pipeline,
    show_collection_stats,
)
from app.core.db import close_all  # noqa: E402

# Monday=0 ... Sunday=6
# Only states with a working collection source are in the rotation. CA/NC/OH/PA
# were removed (no scraper wired — they showed as ❌ in the weekly calendar).
# Working: FL, TX (dedicated scrapers), AZ (Maricopa), IL (CourtListener),
# GA (GSCCCA via saved session), NY (ACRIS open data).
DAILY_STATES = {
    0: ["fl", "ga"],   # Monday
    1: ["tx", "il"],   # Tuesday
    2: ["az", "ny"],   # Wednesday
    3: ["fl", "tx"],   # Thursday
    4: ["ga", "il"],   # Friday
    5: ["az", "ny"],   # Saturday
    6: [],             # Sunday
}


def main():
    ap = argparse.ArgumentParser(description="Data engine daily runner")
    ap.add_argument("--states", help="Comma list overriding today's schedule")
    ap.add_argument("--weekday", type=int, choices=range(0, 7),
                    help="Force a specific weekday's state set (0=Mon)")
    args = ap.parse_args()

    if args.states:
        states = [s.strip().lower() for s in args.states.split(",") if s.strip()]
    else:
        wd = args.weekday if args.weekday is not None else date.today().weekday()
        states = DAILY_STATES.get(wd, [])

    print(f"\n{'#'*64}\n  DATA ENGINE DAILY RUN — {date.today().isoformat()}")
    print(f"  States today: {', '.join(s.upper() for s in states) or '(none)'}")
    print(f"{'#'*64}")

    try:
        from pipeline_log import PipelineLogger
    except Exception:
        PipelineLogger = None

    all_stats = []
    for state in states:
        logger = PipelineLogger(f"data_collection_{state}") if PipelineLogger else None
        if logger:
            logger.start()
            logger.step_start(f"collect_{state}")
        try:
            stats = run_state_collection(state)
            all_stats.append(stats)
            if logger:
                logger.step_done(
                    f"collect_{state}", ok=True,
                    detail=(f"liens+{stats['liens']} lic+{stats['licenses']} "
                            f"match+{stats['matched']} pdl+{stats['pdl']} "
                            f"cse+{stats['cse']}"))
        except Exception as e:
            if logger:
                logger.step_done(f"collect_{state}", ok=False, error=str(e))
            print(f"  ERROR collecting {state}: {e}")
            stats = {"state": state, "error": str(e)}
            all_stats.append(stats)

        # Sync this state into the email pipeline immediately.
        synced = 0
        if logger:
            logger.step_start(f"sync_{state}")
        try:
            synced = sync_to_email_pipeline(state)
            if logger:
                logger.step_done(f"sync_{state}", ok=True,
                                 detail=f"{synced} synced")
        except Exception as e:
            if logger:
                logger.step_done(f"sync_{state}", ok=False, error=str(e))

        if logger:
            logger.finish({**stats, "synced": synced})

    # Final safety sync across everything (catches anything left at email_step=0).
    print("\n  Final cross-state sync...")
    total_synced = sync_to_email_pipeline()
    print(f"  Total final sync: {total_synced}")

    show_collection_stats()
    close_all()


if __name__ == "__main__":
    main()
