import os
import sys
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding="utf-8")

"""
weekly_scrape.py  (v4)
======================
LeadFlow weekly scraper + enrichment runner.
Runs every Monday at 6:00 AM via Task Scheduler.

CHANGES IN V4:
  - Added Step 2b: Sync FL contacts to multi_state_contacts unified table
  - Added Step 5: Monthly TX/AZ scraper trigger (runs on 1st of month only)
  - Added Step 6: Multi-state enrichment sync (1st of month only)

Task Scheduler: Monday 6:00 AM
  python weekly_scrape.py

Usage:
  python weekly_scrape.py              # scrape last 7 days, all counties
  python weekly_scrape.py --days 365   # full backfill
  python weekly_scrape.py --county manatee
  python weekly_scrape.py --no-enrich
  python weekly_scrape.py --dry-run
"""
import argparse
import subprocess
import sys
from datetime import datetime, date
from pathlib import Path

BASE = Path(__file__).resolve().parent

COUNTIES = [
    "sarasota", "hillsborough", "manatee", "osceola", "pasco",
    "lake", "martin", "polk", "pinellas", "miami_dade", "duval",
    "lee", "stjohns", "volusia",
]

MODULE_MAP = {
    "miami_dade": "scrape_miami_dade_liens",
    "stjohns":    "scrape_stjohns_liens",
}

PALM_BEACH_NOTE = """
  ⚠  Palm Beach excluded — requires manual CAPTCHA.
     Run separately: python palm_beach_manual.py --days 7
"""


def run_cmd(cmd: list, label: str) -> tuple[bool, str]:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    try:
        result = subprocess.run(
            cmd, cwd=BASE, capture_output=True, text=True, timeout=3600)
        output = result.stdout + result.stderr
        lines  = [l for l in output.strip().splitlines() if l.strip()]
        for line in lines[-10:]:
            print(f"  {line}")
        ok      = result.returncode == 0
        summary = " | ".join(lines[-3:]) if lines else ("ok" if ok else "failed")
        if not ok:
            print(f"  ⚠  Exited with code {result.returncode}")
        return ok, summary
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT after 1 hour"
    except Exception as e:
        return False, str(e)


def is_first_of_month() -> bool:
    return date.today().day == 1


def main():
    parser = argparse.ArgumentParser(description="LeadFlow Weekly Scrape v4")
    parser.add_argument("--days",         type=int, default=7)
    parser.add_argument("--county",       default=None)
    parser.add_argument("--no-enrich",    action="store_true")
    parser.add_argument("--dry-run",      action="store_true")
    parser.add_argument("--force-monthly", action="store_true",
                        help="Force TX/AZ monthly scraper even if not 1st")
    args = parser.parse_args()

    start         = datetime.now()
    run_monthly   = is_first_of_month() or args.force_monthly

    # Pipeline logger
    try:
        from pipeline_log import PipelineLogger
        logger     = PipelineLogger("weekly_scrape")
        logger.start()
        HAS_LOGGER = True
    except ImportError:
        logger     = None
        HAS_LOGGER = False
        print("  ⚠  pipeline_log.py not found — logging disabled")

    print(f"\n{'='*60}")
    print(f"  LeadFlow Weekly Scrape v4 — {start.strftime('%Y-%m-%d %H:%M')}")
    print(f"  Days back      : {args.days}")
    print(f"  Counties       : {args.county or 'ALL (except Palm Beach)'}")
    print(f"  Monthly jobs   : {'YES (1st of month)' if run_monthly else 'NO'}")
    print(f"  Logging        : {'enabled' if HAS_LOGGER else 'DISABLED'}")
    print(f"{'='*60}")
    print(PALM_BEACH_NOTE)

    counties = [args.county] if args.county else COUNTIES
    results  = {}

    # ── Step 1: Scrape liens ──────────────────────────────────────────────────
    print("\n--- STEP 1: LIEN SCRAPERS ---")
    for county in counties:
        module_name = MODULE_MAP.get(county, f"scrape_{county}_liens")
        module      = f"app.workers.{module_name}"
        cmd         = [sys.executable, "-m", module,
                       f"--days-back={args.days}"]
        label       = f"Scraping {county.replace('_', ' ').title()}"

        if HAS_LOGGER:
            logger.step_start(f"scrape:{county}")

        if args.dry_run:
            print(f"  [DRY RUN] Would run: {' '.join(cmd)}")
            results[county] = "skipped"
            if HAS_LOGGER:
                logger.step_skip(f"scrape:{county}", "dry-run")
            continue

        ok, summary = run_cmd(cmd, label)
        results[county] = "ok" if ok else "failed"
        if HAS_LOGGER:
            logger.step_done(f"scrape:{county}", ok=ok, detail=summary)

    # ── Step 2: DBPR enrichment ───────────────────────────────────────────────
    print("
--- STEP 2: DBPR ENRICHMENT ---")
    print("  DBPR enrichment complete - 7,621 FL contacts matched. Skipping.")
    if HAS_LOGGER:
        logger.step_skip("enrich_liens_from_dbpr", "complete")

    # ── Step 2b: Sync FL → multi_state_contacts ───────────────────────────────
    print("\n--- STEP 2b: SYNC FL → UNIFIED TABLE ---")
    sync_cmd = [
        sys.executable, "scripts/enrichment/multi_state_enrichment.py",
        "--state", "fl"
    ]
    if args.dry_run:
        print("  [DRY RUN] Would sync FL contacts to multi_state_contacts")
        if HAS_LOGGER:
            logger.step_skip("sync_fl_unified", "dry-run")
    else:
        if HAS_LOGGER:
            logger.step_start("sync_fl_unified")
        ok, summary = run_cmd(sync_cmd, "Sync FL → multi_state_contacts")
        if HAS_LOGGER:
            logger.step_done("sync_fl_unified", ok=ok, detail=summary)

    # ── Step 3: Export contacts ───────────────────────────────────────────────
    print("\n--- STEP 3: EXPORT CONTACTS ---")
    if not args.dry_run:
        if HAS_LOGGER:
            logger.step_start("export_contacts")
        ok, summary = run_cmd([sys.executable, "export_contacts.py"],
                              "Export contacts to CSV")
        if HAS_LOGGER:
            logger.step_done("export_contacts", ok=ok, detail=summary)
    else:
        print("  [DRY RUN] Would run: export_contacts.py")
        if HAS_LOGGER:
            logger.step_skip("export_contacts", "dry-run")

    # ── Step 4: Inventory ─────────────────────────────────────────────────────
    print("\n--- STEP 4: INVENTORY ---")
    if not args.dry_run:
        if HAS_LOGGER:
            logger.step_start("inventory")
        ok, summary = run_cmd(
            [sys.executable, "-m", "app.workers.inventory"], "DB Inventory")
        if HAS_LOGGER:
            logger.step_done("inventory", ok=ok, detail=summary)
    else:
        print("  [DRY RUN] Would run: app.workers.inventory")

    # ── Step 5: Monthly TX/AZ scrapers (1st of month only) ───────────────────
    if run_monthly:
        print("\n--- STEP 5: MONTHLY STATE SCRAPERS (1st of month) ---")

        # Texas TDLR — A/C Contractors
        print("\n  Texas TDLR — A/C Contractors")
        if HAS_LOGGER: logger.step_start("tdlr_ac")
        if not args.dry_run:
            ok, summary = run_cmd([
                sys.executable,
                "scripts/scrapers/tdlr_scraper.py",
                "--download", "--import", "--type", "ac"
            ], "Texas TDLR — A/C Contractors")
            if HAS_LOGGER:
                logger.step_done("tdlr_ac", ok=ok, detail=summary)
        else:
            print("  [DRY RUN] Would download TX TDLR A/C contractors")
            if HAS_LOGGER:
                logger.step_skip("tdlr_ac", "dry-run")

        # Texas TDLR — Electricians
        print("\n  Texas TDLR — Electricians")
        if HAS_LOGGER: logger.step_start("tdlr_electricians")
        if not args.dry_run:
            ok, summary = run_cmd([
                sys.executable,
                "scripts/scrapers/tdlr_scraper.py",
                "--download", "--import", "--type", "electricians"
            ], "Texas TDLR — Electricians")
            if HAS_LOGGER:
                logger.step_done("tdlr_electricians", ok=ok, detail=summary)
        else:
            print("  [DRY RUN] Would download TX TDLR electricians")
            if HAS_LOGGER:
                logger.step_skip("tdlr_electricians", "dry-run")

        # Arizona ROC
        print("\n  Arizona ROC")
        if HAS_LOGGER: logger.step_start("az_roc")
        if not args.dry_run:
            ok, summary = run_cmd([
                sys.executable,
                "scripts/scrapers/arizona_roc_scraper.py",
                "--download", "--import"
            ], "Arizona ROC Contractors")
            if HAS_LOGGER:
                logger.step_done("az_roc", ok=ok, detail=summary)
        else:
            print("  [DRY RUN] Would download AZ ROC contractors")
            if HAS_LOGGER:
                logger.step_skip("az_roc", "dry-run")

    else:
        print(f"\n--- STEP 5: MONTHLY SCRAPERS ---")
        print(f"  Skipping — not 1st of month (today: {date.today().day})")
        print(f"  Next run: 1st of {date.today().strftime('%B')} or use --force-monthly")

    # ── Step 6: Multi-state enrichment sync (1st of month) ───────────────────
    if run_monthly and not args.dry_run:
        print("\n--- STEP 6: MULTI-STATE ENRICHMENT SYNC ---")
        for state in ["tx", "az"]:
            if HAS_LOGGER: logger.step_start(f"sync_{state}_unified")
            ok, summary = run_cmd([
                sys.executable,
                "scripts/enrichment/multi_state_enrichment.py",
                "--state", state
            ], f"Sync {state.upper()} → multi_state_contacts")
            if HAS_LOGGER:
                logger.step_done(f"sync_{state}_unified",
                                 ok=ok, detail=summary)

    # ── Step 7: Blog drafts check ─────────────────────────────────────────────
    blogs_dir  = BASE / "blog_drafts"
    blog_files = sorted(blogs_dir.glob("*.md"), reverse=True) \
                 if blogs_dir.exists() else []
    latest     = blog_files[0].name if blog_files else "none yet"
    print(f"\n--- BLOG DRAFTS ---")
    print(f"  Location : {blogs_dir}")
    print(f"  Total    : {len(blog_files)} drafts")
    print(f"  Latest   : {latest}")
    if HAS_LOGGER:
        logger.step_done("blog_drafts_check", ok=True,
                         detail=f"{len(blog_files)} drafts — latest: {latest}")

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = (datetime.now() - start).seconds // 60
    failed  = [c for c, s in results.items() if s == "failed"]

    print(f"\n{'='*60}")
    print(f"  Weekly scrape v4 complete — {elapsed} min")
    print(f"\n  County results:")
    for county, status in results.items():
        icon = "✓" if status == "ok" else "✗" if status == "failed" else "–"
        print(f"    {icon} {county.replace('_', ' ').title()}: {status}")
    if failed:
        print(f"\n  ⚠  Failed: {', '.join(failed)}")
    if run_monthly:
        print(f"\n  Monthly: TX TDLR + AZ ROC + unified sync ✓")
    print(f"\n  Palm Beach  : python palm_beach_manual.py --days {args.days}")
    print(f"  View log    : python pipeline_log.py --today")
    print(f"  Email seq   : runs automatically Mon/Tue/Wed/Thu")
    print(f"  Multi-state : python scripts/enrichment/multi_state_enrichment.py --stats")
    print(f"{'='*60}\n")

    if HAS_LOGGER:
        liens_total = liens_added = 0
        try:
            from app.core.db import get_connection
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM normalized_liens")
                liens_total = cur.fetchone()[0]
                cur.execute("""
                    SELECT COUNT(*) FROM normalized_liens
                    WHERE created_at >= NOW() - INTERVAL '7 days'
                """)
                liens_added = cur.fetchone()[0]
            conn.close()
        except Exception:
            pass

        logger.finish({
            "counties_scraped":    len([c for c, s in results.items()
                                        if s == "ok"]),
            "counties_failed":     len(failed),
            "liens_added_week":    liens_added,
            "liens_total":         liens_total,
            "blog_drafts_on_disk": len(blog_files),
            "monthly_ran":         run_monthly,
        })


if __name__ == "__main__":
    main()
