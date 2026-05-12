"""
run_weekly_pipeline.py
======================
LeadFlow weekly pipeline orchestrator.

Runs in order:
  1. Scrape liens      (all 5 counties, configurable window)
  2. Scrape permits    (all active county permit scrapers)
  3. Match + Score     (new records only)
  4. Enrich DBPR       (license/contact lookup)
  5. Enrich Property   (mailing address from property appraisers)
  6. Enrich Contacts   (placeholder fallback for unresolved)
  7. Generate email list (CSV export of emailable leads)
  8. Send emails        (SMTP, skipped if --no-email)

Designed for Windows Task Scheduler.

INITIAL PULL (run once manually):
  python run_weekly_pipeline.py --days-back 180 --no-email

WEEKLY CADENCE (Task Scheduler, every Sunday 2am):
  python run_weekly_pipeline.py --days-back 14

SEND EMAILS SEPARATELY (Task Scheduler, every Monday 8am):
  python run_weekly_pipeline.py --liens-only=false --permits-only=false
      --skip-scrape --no-match --no-enrich --send-email

Task Scheduler XML files are in: scripts/task_scheduler/

Usage:
  python run_weekly_pipeline.py --days-back 14
  python run_weekly_pipeline.py --days-back 180 --no-email
  python run_weekly_pipeline.py --skip-scrape          # match+enrich+email only
  python run_weekly_pipeline.py --county miami_dade    # one county only
  python run_weekly_pipeline.py --dry-run              # print plan, no execution
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Config — edit these to match your environment
# ---------------------------------------------------------------------------

# Lien scrapers: (module_path, extra_args)
LIEN_SCRAPERS = [
    ("app.workers.scrape_miami_dade_liens",   ["--no-headless"]),
    ("app.workers.scrape_hillsborough_liens", []),
    ("app.workers.scrape_pinellas_liens",     []),
    ("app.workers.scrape_polk_liens",         []),
    ("app.workers.scrape_palm_beach_liens",   []),
]

# Permit scrapers: (module_path, extra_args)
PERMIT_SCRAPERS = [
    ("app.workers.scrape_miami_dade_permits",   []),
    ("app.workers.scrape_hillsborough_permits", []),
    ("app.workers.scrape_pinellas_permits",     []),
    ("app.workers.scrape_polk_permits",         []),
    ("app.workers.scrape_palm_beach_permits",   []),
]

# Pipeline steps after scraping
POST_SCRAPE_STEPS = [
    ("Match + Score",        "app.workers.match_and_score",           []),
    ("Enrich DBPR",          "app.workers.enrich_dbpr",               []),
    ("Enrich Property",      "app.workers.enrich_property_appraiser", []),
    ("Enrich Contacts",      "app.workers.enrich_contacts",           []),
    ("Generate Email List",  "app.workers.generate_email_list",       []),
]

# County name → scraper module suffix mapping (for --county filter)
COUNTY_MAP = {
    "miami_dade":    "miami_dade",
    "hillsborough":  "hillsborough",
    "pinellas":      "pinellas",
    "polk":          "polk",
    "palm_beach":    "palm_beach",
}

BASE_DIR   = Path(__file__).resolve().parent
LOG_DIR    = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class Logger:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(log_path, "w", encoding="utf-8")
        self.start = datetime.now()

    def log(self, msg: str = ""):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line)
        self._file.write(line + "\n")
        self._file.flush()

    def close(self):
        elapsed = (datetime.now() - self.start).seconds
        self.log(f"\nTotal elapsed: {elapsed // 60}m {elapsed % 60}s")
        self._file.close()


# ---------------------------------------------------------------------------
# Step runner
# ---------------------------------------------------------------------------

def run_step(
    logger: Logger,
    label: str,
    module: str,
    extra_args: list[str],
    dry_run: bool = False,
) -> bool:
    """Run a pipeline step as a subprocess. Returns True on success."""
    cmd = [sys.executable, "-m", module] + extra_args
    logger.log(f"\n{'─'*50}")
    logger.log(f"STEP: {label}")
    logger.log(f"CMD : {' '.join(cmd)}")

    if dry_run:
        logger.log("  [dry-run] skipped")
        return True

    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=False,  # stream to console
            text=True,
            timeout=3600,          # 1 hour max per step
        )
        elapsed = int(time.time() - t0)
        if result.returncode == 0:
            logger.log(f"  ✓ {label} completed in {elapsed}s")
            return True
        else:
            logger.log(f"  ✗ {label} FAILED (exit {result.returncode}) after {elapsed}s")
            return False
    except subprocess.TimeoutExpired:
        logger.log(f"  ✗ {label} TIMED OUT after 3600s")
        return False
    except Exception as e:
        logger.log(f"  ✗ {label} ERROR: {e}")
        return False


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_emails(logger: Logger, csv_path: Path, dry_run: bool = False) -> bool:
    """
    Send emails from the generated CSV via SMTP.
    Reads SMTP config from .env. Logs each send to outreach_events table.
    """
    import csv
    import os
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")

    smtp_host   = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port   = int(os.getenv("SMTP_PORT", "587"))
    smtp_user   = os.getenv("SMTP_USER", "")
    smtp_pass   = os.getenv("SMTP_PASS", "")
    from_name   = os.getenv("EMAIL_FROM_NAME", "LeadFlow")
    from_addr   = os.getenv("EMAIL_FROM_ADDR", smtp_user)
    min_score   = int(os.getenv("MIN_LEAD_SCORE", "60"))

    if not smtp_user or not smtp_pass:
        logger.log("  ✗ SMTP_USER / SMTP_PASS not set in .env — skipping email send")
        return False

    if not csv_path.exists():
        logger.log(f"  ✗ Email list not found: {csv_path}")
        return False

    try:
        from app.core.db import get_connection
        conn = get_connection()
    except Exception as e:
        logger.log(f"  ✗ DB connection failed: {e}")
        return False

    sent = 0
    skipped = 0
    failed = 0

    try:
        with open(csv_path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        logger.log(f"  Email list: {len(rows)} rows in {csv_path.name}")

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)

            for row in rows:
                email     = (row.get("email") or "").strip()
                lead_id   = row.get("lead_id", "")
                full_name = (row.get("full_name") or "there").strip()
                score     = float(row.get("lead_score") or 0)
                county    = row.get("county_name", "")
                prop_addr = row.get("property_address", "")
                lien_date = row.get("lien_date", "")
                biz_name  = row.get("business_name", "")

                if not email or "@" not in email:
                    skipped += 1
                    continue
                if score < min_score:
                    skipped += 1
                    continue
                # Skip placeholder/invalid emails
                if any(d in email for d in ["@example.com", "@leadflow.invalid", ".invalid"]):
                    skipped += 1
                    continue

                first_name = full_name.split()[0].title() if full_name else "there"
                subject, body = build_email(
                    first_name=first_name,
                    full_name=full_name,
                    biz_name=biz_name,
                    county=county,
                    prop_addr=prop_addr,
                    lien_date=lien_date,
                    from_name=from_name,
                )

                if dry_run:
                    logger.log(f"  [dry-run] Would send to {email} | {subject}")
                    sent += 1
                    continue

                try:
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = subject
                    msg["From"]    = f"{from_name} <{from_addr}>"
                    msg["To"]      = email
                    msg.attach(MIMEText(body, "plain"))
                    server.sendmail(from_addr, [email], msg.as_string())

                    # Log to DB
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO outreach_events
                                (lead_id, channel, event_type, recipient_email,
                                 subject, created_at)
                            VALUES (%s, 'email', 'email_sent', %s, %s, NOW())
                            ON CONFLICT DO NOTHING
                        """, (lead_id, email, subject))
                        cur.execute("""
                            UPDATE matched_leads
                            SET lead_status = 'outreach_sent', updated_at = NOW()
                            WHERE id = %s AND lead_status = 'new'
                        """, (lead_id,))
                    conn.commit()

                    sent += 1
                    logger.log(f"  ✉ Sent → {email}")
                    time.sleep(1.5)  # Gmail rate limit: ~40/min

                except Exception as e:
                    failed += 1
                    logger.log(f"  ✗ Failed → {email}: {e}")

    finally:
        conn.close()

    logger.log(f"  Email summary: {sent} sent, {skipped} skipped, {failed} failed")
    return failed == 0


def build_email(
    first_name: str,
    full_name: str,
    biz_name: str,
    county: str,
    prop_addr: str,
    lien_date: str,
    from_name: str,
) -> tuple[str, str]:
    """Build subject + plain-text email body."""
    name_display = biz_name or full_name or first_name

    subject = f"We can help resolve your IRS tax lien — {name_display}"

    body = f"""Hi {first_name},

I'm reaching out because public records show an IRS federal tax lien recently filed against {name_display} in {county} County{f" related to {prop_addr}" if prop_addr else ""}{f" (filed {lien_date})" if lien_date else ""}.

I work with contractors and property owners across Florida who are navigating exactly this situation. Resolving an IRS lien before it affects your ability to sell, refinance, or permit new work is critical — and there are more options than most people realize.

If you'd like a free 15-minute call to talk through your situation, I'd be happy to help.

Best,
{from_name}

---
To unsubscribe, reply with "unsubscribe" in the subject line.
"""
    return subject, body


# ---------------------------------------------------------------------------
# Find most recent email list CSV
# ---------------------------------------------------------------------------

def find_latest_email_list() -> Path | None:
    export_dir = BASE_DIR / "data" / "exports" / "email_lists"
    if not export_dir.exists():
        return None
    csvs = sorted(export_dir.glob("email_campaign_list_*.csv"),
                  key=lambda f: f.stat().st_mtime, reverse=True)
    return csvs[0] if csvs else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="LeadFlow weekly pipeline orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--days-back",    type=int, default=14,
                        help="Days of data to pull for liens + permits (default: 14 for weekly, 180 for initial)")
    parser.add_argument("--county",       default="all",
                        choices=["all"] + list(COUNTY_MAP.keys()),
                        help="Run only a specific county's scrapers")
    parser.add_argument("--skip-scrape",  action="store_true",
                        help="Skip all scraping, run match+enrich+email only")
    parser.add_argument("--skip-liens",   action="store_true",
                        help="Skip lien scraping only")
    parser.add_argument("--skip-permits", action="store_true",
                        help="Skip permit scraping only")
    parser.add_argument("--skip-match",   action="store_true",
                        help="Skip match+score step")
    parser.add_argument("--skip-enrich",  action="store_true",
                        help="Skip all enrichment steps")
    parser.add_argument("--no-email",     action="store_true",
                        help="Generate email list but do not send")
    parser.add_argument("--send-only",    action="store_true",
                        help="Only send from most recent email list — skip everything else")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Print plan without executing any steps")
    parser.add_argument("--pdf-limit",    type=int, default=None,
                        help="Max PDFs to download per county (None = all)")
    args = parser.parse_args()

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"pipeline_{ts}.log"
    logger   = Logger(log_path)

    logger.log("=" * 60)
    logger.log(f"LeadFlow Pipeline  —  {datetime.now().strftime('%A %b %d %Y %H:%M')}")
    logger.log(f"Days back    : {args.days_back}")
    logger.log(f"County filter: {args.county}")
    logger.log(f"Log file     : {log_path}")
    logger.log(f"Dry run      : {args.dry_run}")
    logger.log("=" * 60)

    results: dict[str, bool] = {}

    # ── SEND ONLY ────────────────────────────────────────────────────────────
    if args.send_only:
        csv_path = find_latest_email_list()
        if csv_path:
            logger.log(f"\nSend-only mode: {csv_path.name}")
            results["Send Emails"] = send_emails(logger, csv_path, dry_run=args.dry_run)
        else:
            logger.log("No email list found — run without --send-only first")
        logger.close()
        return

    # ── SCRAPE LIENS ─────────────────────────────────────────────────────────
    if not args.skip_scrape and not args.skip_liens:
        logger.log("\n\n── LIEN SCRAPING ──────────────────────────────────────")
        for module, extra in LIEN_SCRAPERS:
            # Filter by county if requested
            if args.county != "all":
                if COUNTY_MAP[args.county] not in module:
                    continue

            step_args = [f"--days-back={args.days_back}"] + extra
            if args.pdf_limit is not None:
                step_args.append(f"--pdf-limit={args.pdf_limit}")

            label = module.split(".")[-1].replace("scrape_", "").replace("_liens", "").title()
            ok = run_step(logger, f"Liens: {label}", module, step_args, dry_run=args.dry_run)
            results[f"liens_{label}"] = ok

            if not ok:
                logger.log(f"  ⚠ {label} lien scrape failed — continuing with other counties")

    # ── SCRAPE PERMITS ───────────────────────────────────────────────────────
    if not args.skip_scrape and not args.skip_permits:
        logger.log("\n\n── PERMIT SCRAPING ────────────────────────────────────")
        for module, extra in PERMIT_SCRAPERS:
            if args.county != "all":
                if COUNTY_MAP[args.county] not in module:
                    continue

            # Check if permit scraper exists before trying
            module_path = BASE_DIR / module.replace(".", "/").replace(
                "app/workers", "app/workers") .replace("/", "\\") + ".py"

            step_args = [f"--days-back={args.days_back}"] + extra
            label = module.split(".")[-1].replace("scrape_", "").replace("_permits", "").title()
            ok = run_step(logger, f"Permits: {label}", module, step_args, dry_run=args.dry_run)
            results[f"permits_{label}"] = ok

            if not ok:
                logger.log(f"  ⚠ {label} permit scrape failed — continuing")

    # ── MATCH + SCORE ────────────────────────────────────────────────────────
    if not args.skip_match:
        logger.log("\n\n── MATCH + SCORE ──────────────────────────────────────")
        ok = run_step(logger, "Match + Score", "app.workers.match_and_score",
                      [], dry_run=args.dry_run)
        results["match_score"] = ok

    # ── ENRICH ───────────────────────────────────────────────────────────────
    if not args.skip_enrich:
        logger.log("\n\n── ENRICHMENT ─────────────────────────────────────────")

        # DBPR first (highest quality — has phone + license)
        ok = run_step(logger, "Enrich DBPR", "app.workers.enrich_dbpr",
                      [], dry_run=args.dry_run)
        results["enrich_dbpr"] = ok

        # Property appraiser (mailing address)
        ok = run_step(logger, "Enrich Property Appraiser",
                      "app.workers.enrich_property_appraiser",
                      [], dry_run=args.dry_run)
        results["enrich_property"] = ok

        # Placeholder fallback — fills any remaining gaps
        ok = run_step(logger, "Enrich Contacts (fallback)",
                      "app.workers.enrich_contacts",
                      [], dry_run=args.dry_run)
        results["enrich_contacts"] = ok

    # ── GENERATE EMAIL LIST ──────────────────────────────────────────────────
    logger.log("\n\n── EMAIL LIST GENERATION ──────────────────────────────")
    ok = run_step(logger, "Generate Email List", "app.workers.generate_email_list",
                  [], dry_run=args.dry_run)
    results["email_list"] = ok

    # ── SEND EMAILS ──────────────────────────────────────────────────────────
    if not args.no_email and ok:
        logger.log("\n\n── SEND EMAILS ────────────────────────────────────────")
        csv_path = find_latest_email_list()
        if csv_path:
            results["send_email"] = send_emails(logger, csv_path, dry_run=args.dry_run)
        else:
            logger.log("  ✗ No email list CSV found to send")

    # ── SUMMARY ──────────────────────────────────────────────────────────────
    logger.log("\n\n" + "=" * 60)
    logger.log("PIPELINE SUMMARY")
    logger.log("=" * 60)
    passed = sum(1 for v in results.values() if v)
    failed_steps = [k for k, v in results.items() if not v]

    for step, ok in results.items():
        icon = "✓" if ok else "✗"
        logger.log(f"  {icon}  {step}")

    logger.log(f"\n  {passed}/{len(results)} steps passed")
    if failed_steps:
        logger.log(f"  Failed: {', '.join(failed_steps)}")

    logger.close()
    sys.exit(0 if not failed_steps else 1)


if __name__ == "__main__":
    main()
