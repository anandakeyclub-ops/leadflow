"""
palm_beach_manual.py
====================
Runs Palm Beach lien scraper manually (requires CAPTCHA).

Usage:
  python palm_beach_manual.py           # last 7 days
  python palm_beach_manual.py --days 365  # full backfill
"""
import argparse, subprocess, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    print(f"\n[Palm Beach Manual Scrape] Days back: {args.days}")
    print("  Note: CAPTCHA required — keep browser window visible\n")

    subprocess.run([
        sys.executable, "-m", "app.workers.scrape_palm_beach_liens",
        f"--days-back={args.days}", "--visible"
    ], cwd=BASE)

    subprocess.run([
        sys.executable, "-m", "app.workers.enrich_liens_from_dbpr",
        "--county", "palm beach", "--export", "--min-score", "0.35"
    ], cwd=BASE)

    subprocess.run([sys.executable, "export_contacts.py"], cwd=BASE)

if __name__ == "__main__":
    main()
