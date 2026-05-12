"""
run_broward_pipeline.py
=======================
Orchestrates the full Broward permit + lien pipeline.

Sources:
  - Weston (Accela, confirmed working)
  - Fort Lauderdale (report-based, separate scraper)
  - Hollywood, Cooper City (Accela, expand as needed)

Usage:
  python -m app.workers.run_broward_pipeline
  python -m app.workers.run_broward_pipeline --source weston --days-back 30
  python -m app.workers.run_broward_pipeline --source ftl
  python -m app.workers.run_broward_pipeline --all
"""
import argparse
import subprocess
import sys
from datetime import date, timedelta


def run_weston(days_back: int, visible: bool, debug: bool) -> bool:
    print(f"\n[1] Scraping Weston permits ({days_back} days back)...")
    cmd = [
        sys.executable, "-m", "app.workers.scrape_broward_permits",
        "--source", "weston_accela",
        "--days-back", str(days_back),
        "--limit", "0",
        "--pages", "50",
    ]
    if visible:
        cmd.append("--visible")
    if debug:
        cmd.append("--debug-pages")
    result = subprocess.run(cmd)
    return result.returncode == 0


def run_fort_lauderdale(visible: bool, debug: bool) -> bool:
    print(f"\n[2] Scraping Fort Lauderdale permits (report-based)...")
    cmd = [
        sys.executable, "-m", "app.workers.scrape_fort_lauderdale_reports",
    ]
    if visible:
        cmd.append("--visible")
    if debug:
        cmd.append("--debug")
    result = subprocess.run(cmd)
    return result.returncode == 0


def run_match_and_score() -> bool:
    print(f"\n[3] Running match_and_score...")
    result = subprocess.run([sys.executable, "-m", "app.workers.match_and_score"])
    return result.returncode == 0


def run_enrich() -> bool:
    print(f"\n[4] Running Broward DBPR enrichment...")
    result = subprocess.run([sys.executable, "-m", "app.workers.enrich_broward_from_dbpr"])
    return result.returncode == 0


def run_email_list() -> bool:
    print(f"\n[5] Generating email list...")
    result = subprocess.run([sys.executable, "-m", "app.workers.generate_email_list"])
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Run Broward pipeline")
    parser.add_argument("--source",    choices=["weston", "ftl", "all"], default="all")
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--visible",   action="store_true", help="Show browser windows")
    parser.add_argument("--debug",     action="store_true", help="Save debug screenshots")
    parser.add_argument("--permits-only", action="store_true", help="Skip matching/email steps")
    args = parser.parse_args()

    print(f"Broward pipeline | source={args.source} | days_back={args.days_back}")

    permit_ok = False

    if args.source in ("weston", "all"):
        permit_ok |= run_weston(args.days_back, args.visible, args.debug)

    if args.source in ("ftl", "all"):
        permit_ok |= run_fort_lauderdale(args.visible, args.debug)

    if args.permits_only:
        print("\nPermits-only mode — stopping before match/email steps")
        return

    if not permit_ok:
        print("\n⚠ No permits scraped successfully — skipping downstream steps")
        return

    run_match_and_score()
    run_enrich()
    run_email_list()

    print("\nBroward pipeline complete.")


if __name__ == "__main__":
    main()
