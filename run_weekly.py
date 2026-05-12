"""
run_weekly.py
=============
LeadFlow weekly pipeline runner.

Runs all steps in order:
  1. Scrape liens (all active counties)
  2. Scrape permits (all active counties)
  3. match_and_score
  4. match_lien_to_dbpr  (Option 3 — counties with no contractor names)
  5. enrich_dbpr
  6. enrich_contacts
  7. generate_email_list
  8. Email inventory report to you

Usage:
  python run_weekly.py                    # full run
  python run_weekly.py --skip-scrape      # skip scraping, just match/enrich/email
  python run_weekly.py --skip-email       # skip emailing the report
  python run_weekly.py --county Miami-Dade  # one county only

Add to Windows Task Scheduler to run every Monday at 6am.
"""
from __future__ import annotations

import argparse
import importlib
import os
import smtplib
import subprocess
import sys
import traceback
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# Config — set in .env
# ---------------------------------------------------------------------------
REPORT_TO       = os.getenv("REPORT_EMAIL", "")       # your email
SMTP_HOST       = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT       = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER       = os.getenv("SMTP_USER", "")
SMTP_PASS       = os.getenv("SMTP_PASS", "")
SMTP_FROM       = os.getenv("SMTP_FROM", SMTP_USER)

# Counties and their scrapers
LIEN_SCRAPERS = [
    ("Miami-Dade",   "app.workers.scrape_miami_dade_liens",   ["--days-back", "14"]),
    ("Hillsborough", "app.workers.scrape_hillsborough_liens", ["--days-back", "14", "--include-state"]),
    ("Pinellas",     "app.workers.scrape_pinellas_liens",     ["--days-back", "14", "--visible"]),
    ("Polk",         "app.workers.scrape_polk_liens",         ["--days-back", "14", "--visible"]),
    ("Duval",        "app.workers.scrape_duval_liens",        ["--days-back", "14", "--visible"]),
    ("Palm Beach",   "app.workers.scrape_palm_beach_liens",   ["--days-back", "14", "--visible"]),
    ("Lee",          "app.workers.scrape_lee_liens",          ["--days-back", "14", "--visible"]),
]

PERMIT_SCRAPERS = [
    ("Miami-Dade",   "app.workers.scrape_miami_dade_permits",   ["--days-back", "14"]),
    ("Hillsborough", "app.workers.scrape_hillsborough_permits", ["--days-back", "14"]),
    ("Pinellas",     "app.workers.scrape_pinellas_permits",     ["--days-back", "14"]),
    ("Polk",         "app.workers.scrape_polk_permits",         ["--days-back", "14"]),
    ("Duval",        "app.workers.scrape_duval_permits",        ["--days-back", "14"]),
    ("Palm Beach",   "app.workers.scrape_palm_beach_permits",   ["--days-back", "14"]),
    ("Lee",          "app.workers.scrape_lee_permits",          ["--weeks-back", "2"]),
]

PIPELINE_STEPS = [
    ("match_and_score",    "app.workers.match_and_score",    []),
    ("match_lien_to_dbpr", "app.workers.match_lien_to_dbpr", []),
    ("enrich_dbpr",        "app.workers.enrich_dbpr",        ["--force"]),
    ("enrich_contacts",    "app.workers.enrich_contacts",    []),
    ("generate_email_list","app.workers.generate_email_list",[]),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
class StepResult:
    def __init__(self, name: str):
        self.name     = name
        self.status   = "pending"
        self.output   = ""
        self.duration = 0.0
        self.error    = ""

    def ok(self)   -> bool: return self.status == "ok"
    def fail(self) -> bool: return self.status == "error"
    def skip(self) -> bool: return self.status == "skipped"


def run_module(module: str, extra_args: list[str]) -> tuple[bool, str]:
    """Run a Python module as subprocess, capture output."""
    cmd = [sys.executable, "-m", module] + extra_args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour max per step
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT after 1 hour"
    except Exception as e:
        return False, str(e)


def get_inventory() -> str:
    """Run inventory and capture output."""
    ok, output = run_module("app.workers.inventory", [])
    return output


# ---------------------------------------------------------------------------
# Email report
# ---------------------------------------------------------------------------
def send_report(results: list[StepResult], inventory: str) -> bool:
    if not REPORT_TO or not SMTP_USER or not SMTP_PASS:
        print("\n  Email not configured — set REPORT_EMAIL, SMTP_USER, SMTP_PASS in .env")
        return False

    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = len(results)
    ok    = sum(1 for r in results if r.ok())
    fail  = sum(1 for r in results if r.fail())
    skip  = sum(1 for r in results if r.skip())

    # Build HTML report
    status_color = "#2ecc71" if fail == 0 else "#e74c3c"
    status_text  = "✅ All steps completed" if fail == 0 else f"⚠️ {fail} step(s) failed"

    rows_html = ""
    for r in results:
        icon  = "✅" if r.ok() else ("❌" if r.fail() else "⏭️")
        color = "#2ecc71" if r.ok() else ("#e74c3c" if r.fail() else "#95a5a6")
        rows_html += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #eee">{icon} {r.name}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;color:{color}">{r.status.upper()}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;color:#666">{r.duration:.0f}s</td>
            <td style="padding:8px;border-bottom:1px solid #eee;font-family:monospace;font-size:11px;color:#333">{r.error[:120] if r.error else ''}</td>
        </tr>"""

    inv_html = f"<pre style='background:#f8f8f8;padding:16px;border-radius:4px;font-size:12px;overflow-x:auto'>{inventory}</pre>"

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto;padding:20px">
    <h2 style="color:#2c3e50">LeadFlow Weekly Report — {now}</h2>
    <div style="background:{status_color};color:white;padding:12px 16px;border-radius:6px;margin-bottom:20px">
        <strong>{status_text}</strong> &nbsp;|&nbsp; {ok}/{total} steps OK
    </div>

    <h3 style="color:#2c3e50">Pipeline Steps</h3>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
        <thead>
            <tr style="background:#f0f0f0">
                <th style="padding:8px;text-align:left">Step</th>
                <th style="padding:8px;text-align:left">Status</th>
                <th style="padding:8px;text-align:left">Time</th>
                <th style="padding:8px;text-align:left">Notes</th>
            </tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table>

    <h3 style="color:#2c3e50">DB Inventory</h3>
    {inv_html}

    <p style="color:#999;font-size:12px;margin-top:24px">
        LeadFlow · {now} · auto-generated
    </p>
    </body></html>
    """

    # Plain text fallback
    plain = f"LeadFlow Weekly Report — {now}\n\n"
    plain += f"{status_text}\n\n"
    for r in results:
        plain += f"  {'OK' if r.ok() else 'FAIL' if r.fail() else 'SKIP':4} {r.name} ({r.duration:.0f}s)\n"
    plain += f"\n{inventory}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"LeadFlow Weekly Report — {now} — {'✅ OK' if fail == 0 else f'⚠️ {fail} failed'}"
    msg["From"]    = SMTP_FROM
    msg["To"]      = REPORT_TO
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html,  "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.sendmail(SMTP_FROM, REPORT_TO, msg.as_string())
        print(f"\n  ✅ Report emailed to {REPORT_TO}")
        return True
    except Exception as e:
        print(f"\n  ❌ Email failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="LeadFlow weekly pipeline runner")
    parser.add_argument("--skip-scrape",  action="store_true", help="Skip all scrapers")
    parser.add_argument("--skip-liens",   action="store_true", help="Skip lien scrapers only")
    parser.add_argument("--skip-permits", action="store_true", help="Skip permit scrapers only")
    parser.add_argument("--skip-email",   action="store_true", help="Skip emailing the report")
    parser.add_argument("--county",       help="Only scrape a specific county")
    parser.add_argument("--dry-run",      action="store_true", help="Print steps without running")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  LeadFlow Weekly Pipeline — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    results: list[StepResult] = []

    def run_step(name: str, module: str, extra: list[str]) -> StepResult:
        r = StepResult(name)
        print(f"  [{name}] Starting...")
        if args.dry_run:
            r.status = "skipped"
            r.output = "(dry-run)"
            print(f"  [{name}] Skipped (dry-run)")
            results.append(r)
            return r

        t0 = datetime.now()
        ok, output = run_module(module, extra)
        r.duration = (datetime.now() - t0).total_seconds()
        r.output   = output
        r.status   = "ok" if ok else "error"
        if not ok:
            # Get last 3 lines as error summary
            lines = [l for l in output.strip().splitlines() if l.strip()]
            r.error = " | ".join(lines[-3:]) if lines else "Unknown error"
            print(f"  [{name}] ❌ FAILED ({r.duration:.0f}s)")
            print(f"    {r.error}")
        else:
            # Get summary lines (last 5)
            lines = [l for l in output.strip().splitlines() if l.strip()]
            summary = " | ".join(lines[-3:]) if lines else ""
            print(f"  [{name}] ✅ OK ({r.duration:.0f}s)")
            if summary:
                print(f"    {summary[:120]}")
        results.append(r)
        return r

    # -----------------------------------------------------------------------
    # Step 1: Lien scrapers
    # -----------------------------------------------------------------------
    if not args.skip_scrape and not args.skip_liens:
        print(f"\n--- LIENS ---")
        for county, module, extra in LIEN_SCRAPERS:
            if args.county and args.county.lower() not in county.lower():
                continue
            run_step(f"liens:{county}", module, extra)
    else:
        print("  Lien scrapers: SKIPPED")

    # -----------------------------------------------------------------------
    # Step 2: Permit scrapers
    # -----------------------------------------------------------------------
    if not args.skip_scrape and not args.skip_permits:
        print(f"\n--- PERMITS ---")
        for county, module, extra in PERMIT_SCRAPERS:
            if args.county and args.county.lower() not in county.lower():
                continue
            run_step(f"permits:{county}", module, extra)
    else:
        print("  Permit scrapers: SKIPPED")

    # -----------------------------------------------------------------------
    # Steps 3-7: Pipeline
    # -----------------------------------------------------------------------
    print(f"\n--- PIPELINE ---")
    for name, module, extra in PIPELINE_STEPS:
        run_step(name, module, extra)

    # -----------------------------------------------------------------------
    # Step 8: Inventory + email report
    # -----------------------------------------------------------------------
    print(f"\n--- REPORT ---")
    print("  Running inventory...")
    inventory = get_inventory()
    print(inventory)

    ok_count   = sum(1 for r in results if r.ok())
    fail_count = sum(1 for r in results if r.fail())
    print(f"\n  Pipeline complete: {ok_count} OK, {fail_count} failed")

    if not args.skip_email and not args.dry_run:
        send_report(results, inventory)
    else:
        print("  Email: SKIPPED")


if __name__ == "__main__":
    main()
