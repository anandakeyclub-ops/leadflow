"""
automation_registry.py
=========================================================
Single source of truth for every LeadFlow Windows Task Scheduler job.

Used by app/workers/daily_summary.py (Automation Command Center) to:
  - know what SHOULD run (and roughly when)
  - match Windows task results + pipeline logs to each task
  - compute an automation health score and surface P0/P1 failures

Task names are spelled EXACTLY as they appear in Windows Task Scheduler,
including known typos (documented in seo-audit/DAILY_SUMMARY_AUTOMATION_AUDIT.md):
  - "LeadFlow - AZ ROC Scaper"   (should be "Scraper")
  - "LeadFlow - Weekly Intelligenc" (truncated "Intelligence")
  - "LeadFlow-Sucess"            (misspelled "Success", also missing " - ")
  - "LeadFlow  - Weekly Scrape"  (double space after "LeadFlow")

Fields per task:
  task_name                  exact Windows Task Scheduler name
  task_key                   stable snake_case key
  category                   infra | email | sms | content | data | seo | outreach | reports | lead
  priority                   P0 (critical infra) .. P3 (background data jobs)
  expected_schedule          human-readable intended cadence (HH:MM / weekday / continuous)
  script_or_module           the .py path or "-m module" invoked
  args                       full argument string passed to python
  expected_pipeline_run_type PipelineLogger run_type to match in logs/pipeline/<date>.jsonl, or None
  failure_severity           critical | high | medium | low
  manual_run_command         copy-paste command to run it by hand
"""

PYTHON = r".venv\Scripts\python.exe"
REPO = r"C:\Users\Dana\Desktop\leadflow"


def _manual(args: str) -> str:
    """Full copy-paste command run from the repo root."""
    return f'cmd /c "cd /d {REPO} && {PYTHON} {args}"'


def _task(task_name, task_key, category, priority, expected_schedule,
          script_or_module, args, expected_pipeline_run_type, failure_severity):
    return {
        "task_name": task_name,
        "task_key": task_key,
        "category": category,
        "priority": priority,
        "expected_schedule": expected_schedule,
        "script_or_module": script_or_module,
        "args": args,
        "expected_pipeline_run_type": expected_pipeline_run_type,
        "failure_severity": failure_severity,
        "manual_run_command": _manual(args),
    }


# ── Explicit, individually-tuned tasks ───────────────────────────────────────
_EXPLICIT = [
    # ── P0 — critical infrastructure (must always be up/run) ──
    _task("LeadFlow - API Server", "api_server", "infra", "P0", "continuous (boot)",
          "-m uvicorn", "-m uvicorn app.api.main:app --host 0.0.0.0 --port 8000", None, "critical"),
    _task("LeadFlow - ngrok Tunnel", "ngrok_tunnel", "infra", "P0", "continuous (boot)",
          "ngrok.exe", "http --domain=deflator-rover-outtakes.ngrok-free.dev 8000", None, "critical"),
    _task("LeadFlow - ngrok Watchdog", "ngrok_watchdog", "infra", "P0", "every 15 min",
          r"scripts\maintenance\ngrok_watchdog.py", r"scripts\maintenance\ngrok_watchdog.py", None, "critical"),
    _task("LeadFlow - Daily Summary", "daily_summary", "infra", "P0", "daily 07:00",
          "-m app.workers.daily_summary", "-m app.workers.daily_summary", "daily_summary", "critical"),
    _task("LeadFlow - Daily Email", "daily_email", "email", "P0", "daily 08:00",
          "-m app.workers.send_email_sequence",
          "-m app.workers.send_email_sequence --auto --limit 200 --delay 12 --provider auto", "email_sends", "critical"),
    _task("LeadFlow - Data Engine", "data_engine", "data", "P0", "daily 06:30",
          r"scripts\data_engine\run_daily.py", r"scripts\data_engine\run_daily.py", "data_engine", "critical"),

    # ── P1 — revenue pipeline (enrichment, outreach, SMS) ──
    _task("LeadFlow - Email Enrichment", "email_enrichment", "email", "P1", "daily 05:00",
          r"scripts\enrichment\multi_state_email_enrichment.py",
          r"scripts\enrichment\multi_state_email_enrichment.py --all --limit 100 --resume", "email_enrichment", "high"),
    _task("LeadFlow - Free Email Enrichment", "free_email_enrichment", "email", "P1", "daily 05:30",
          "-m app.workers.enrich_liens_from_sunbiz", "-m app.workers.enrich_liens_from_sunbiz --limit 50",
          "free_email_enrichment", "high"),
    _task("LeadFlow - Bridge to Email Pool", "bridge_to_email_pool", "email", "P1", "daily 05:45",
          r"scripts\maintenance\bridge_to_email_pool.py", r"scripts\maintenance\bridge_to_email_pool.py --source all",
          "bridge_to_email_pool", "high"),
    _task("LeadFlow - Abandoned Booking Retargeting", "abandoned_booking_retargeting", "email", "P1", "daily 10:00",
          r"scripts\retarget_abandoned_bookings.py", r"scripts\retarget_abandoned_bookings.py",
          "abandoned_booking_retargeting", "high"),
    _task("LeadFlow - DB Connection Cleanup", "db_connection_cleanup", "infra", "P1", "hourly",
          "fix_connections.py", "fix_connections.py", None, "high"),

    # ── P2 — content + SEO ──
    _task("LeadFlow - Blog Draft", "blog_draft", "content", "P2", "Mon/Wed/Fri 06:00",
          "generate_topic_blogs.py", "-X utf8 generate_topic_blogs.py --topic --limit 1", "blog_post", "medium"),
    _task("LeadFlow - Social Media", "social_media", "content", "P2", "daily 11:00",
          "social_media_poster.py", "-X utf8 social_media_poster.py --auto", "social_post", "medium"),
    _task("LeadFlow - Educational", "educational", "content", "P2", "daily 14:00",
          "social_media_poster.py", "-X utf8 social_media_poster.py --post viral-hook", "social_post", "medium"),
    _task("LeadFlow - Reel Educational", "reel_educational", "content", "P2", "as scheduled",
          "reel_generator.py", "-X utf8 reel_generator.py --heygen educational", "reel_heygen", "medium"),
    _task("LeadFlow - Reel Stats", "reel_stats", "content", "P2", "as scheduled",
          "reel_generator.py", "-X utf8 reel_generator.py --remotion weekly-stats", "reel_remotion", "medium"),
    _task("LeadFlow - Guest Post Outreach", "guest_post_outreach", "outreach", "P2", "weekly",
          "-m scripts.outreach.guest_post_outreach", "-m scripts.outreach.guest_post_outreach --send --limit 5",
          "guest_post_outreach", "medium"),
    _task("LeadFlow - GSC Monitor", "gsc_monitor", "seo", "P2", "daily 04:00",
          r"scripts\seo\gsc_monitor.py", r"scripts\seo\gsc_monitor.py", "gsc_monitor", "medium"),
    _task("LeadFlow - Page Refresh", "page_refresh", "seo", "P2", "weekly",
          r"scripts\seo\page_refresh_detector.py", r"scripts\seo\page_refresh_detector.py", "page_refresh", "medium"),

    # ── P3 — background data collection + reports ──
    _task("LeadFlow  - Weekly Scrape", "weekly_scrape", "data", "P3", "weekly Sun",
          "weekly_scrape.py", "weekly_scrape.py --days 7", "weekly_scrape", "low"),
    _task("LeadFlow - AZ ROC Scaper", "az_roc_scraper", "data", "P3", "weekly",
          r"scripts\scrapers\arizona_roc_scraper.py", r"scripts\scrapers\arizona_roc_scraper.py", "az_roc_scrape", "low"),
    _task("LeadFlow - County Lien Intel", "county_lien_intel", "data", "P3", "weekly",
          r"scripts\scrapers\county_lien_intel.py", r"scripts\scrapers\county_lien_intel.py --all", "county_lien_intel", "low"),
    _task("LeadFlow - Google Places Scraper", "google_places_scraper", "data", "P3", "nightly",
          r"scripts\scrapers\google_places_scraper.py",
          r"scripts\scrapers\google_places_scraper.py --all-states --all-trades --delay 3", "google_places", "low"),
    _task("LeadFlow - Harris Lien Scraper", "harris_lien_scraper", "data", "P3", "weekly",
          r"scripts\scrapers\harris_county_lien_scraper.py", r"scripts\scrapers\harris_county_lien_scraper.py",
          "harris_lien_scrape", "low"),
    _task("LeadFlow - Maricopa Lien Scraper", "maricopa_lien_scraper", "data", "P3", "weekly",
          "scrape_maricopa_results.py", "scrape_maricopa_results.py", "maricopa_lien_scrape", "low"),
    _task("LeadFlow - Selenium TX Scraper", "selenium_tx_scraper", "data", "P3", "weekly",
          r"scripts\scrapers\selenium_tx_scraper.py", r"scripts\scrapers\selenium_tx_scraper.py --all --days 35 --match",
          "tx_lien_scrape", "low"),
    _task("LeadFlow - TX Lien Match", "tx_lien_match", "data", "P3", "weekly",
          r"scripts\scrapers\selenium_tx_scraper.py", r"scripts\scrapers\selenium_tx_scraper.py --all --days 35 --match",
          "tx_lien_match", "low"),
    _task("LeadFlow - TDLR Scraper", "tdlr_scraper", "data", "P3", "weekly",
          r"scripts\scrapers\tdlr_scraper.py", r"scripts\scrapers\tdlr_scraper.py", "tdlr_scrape", "low"),
    _task("LeadFlow - Multi-State Enrichment", "multi_state_enrichment", "email", "P3", "weekly",
          r"scripts\enrichment\multi_state_email_enrichment.py",
          r"scripts\enrichment\multi_state_email_enrichment.py --all --limit 100 --resume", "email_enrichment", "low"),
    _task("LeadFlow - Weekly Intelligenc", "weekly_intelligence", "reports", "P3", "weekly Thu",
          r"scripts\reports\weekly_intelligence.py", r"scripts\reports\weekly_intelligence.py", "weekly_intelligence", "low"),
    _task("LeadFlow - Weekly Report Monday", "weekly_report_monday", "reports", "P3", "weekly Mon 07:30",
          r"scripts\reports\weekly_intelligence.py", r"scripts\reports\weekly_intelligence.py --monday", "weekly_intelligence", "low"),
    _task("LeadFlow - Enforcement Brief Thursday", "enforcement_brief_thursday", "reports", "P3", "weekly Thu",
          r"scripts\reports\weekly_intelligence.py", r"scripts\reports\weekly_intelligence.py --thursday", "weekly_intelligence", "low"),
    _task("LeadFlow - Monthly Reports", "monthly_reports", "reports", "P3", "monthly 1st",
          r"scripts\reports\monthly_state_report.py", r"scripts\reports\monthly_state_report.py --all", "monthly_report", "low"),
    _task("LeadFlow-Sucess", "leadflow_success", "content", "P3", "daily",
          "social_media_poster.py", "-X utf8 social_media_poster.py --auto", "social_post", "low"),
]

# ── Repetitive task families (generated) ─────────────────────────────────────

# SMS — 3 daily sends (P1)
_SMS = [
    ("Morning", "sms_morning", "daily 09:00", "30"),
    ("Midday", "sms_midday", "daily 12:00", "30"),
    ("Afternoon", "sms_afternoon", "daily 15:00", "30"),
]
_SMS_TASKS = [
    _task(f"LeadFlow - SMS {label}", key, "sms", "P1", sched,
          "-m scripts.maintenance.twilio_sms_campaign",
          f"-m scripts.maintenance.twilio_sms_campaign --state AZ --source roc --limit {limit}",
          "sms_campaign", "high")
    for label, key, sched, limit in _SMS
]

# CourtListener — one task per state (P3). "Scraper" is the NY one (legacy name).
_CL = [
    ("GA", "courtlistener_ga", "GA"), ("IL", "courtlistener_il", "IL"),
    ("NC", "courtlistener_nc", "NC"), ("OH", "courtlistener_oh", "OH"),
    ("PA", "courtlistener_pa", "PA"), ("SC", "courtlistener_sc", "SC"),
    ("TN", "courtlistener_tn", "TN"), ("VA", "courtlistener_va", "VA"),
]
_CL_TASKS = [
    _task(f"LeadFlow - CourtListener {label}", key, "data", "P3", "daily 03:00",
          r"scripts\scrapers\courtlistener_scraper.py",
          rf"scripts\scrapers\courtlistener_scraper.py --state {st} --limit 20 --filed-after 35",
          "courtlistener_scrape", "low")
    for label, key, st in _CL
]
# Legacy-named NY CourtListener task
_CL_TASKS.append(
    _task("LeadFlow - CourtListener Scraper", "courtlistener_ny", "data", "P3", "daily 03:00",
          r"scripts\scrapers\courtlistener_scraper.py",
          r"scripts\scrapers\courtlistener_scraper.py --state NY --limit 20 --filed-after 35",
          "courtlistener_scrape", "low")
)

# DBPR enrichment — one task per Florida county group (P3)
_DBPR = [
    ("Lake", "dbpr_lake", "Lake"), ("Manatee", "dbpr_manatee", "Manatee"),
    ("Martin", "dbpr_martin", "Martin"), ("Miami-Dade", "dbpr_miami_dade", "Miami-Dade"),
    ("Polk-Pasco", "dbpr_polk_pasco", "Polk"),
]
_DBPR_TASKS = [
    _task(f"LeadFlow - DBPR {label}", key, "data", "P3", "weekly",
          "-m app.workers.enrich_liens_from_dbpr",
          f"-m app.workers.enrich_liens_from_dbpr --county {county} --export", "dbpr_enrich", "low")
    for label, key, county in _DBPR
]

# Reels — one per weekday (P2). engine/type mirror the Windows task args.
_REELS = [
    ("Monday", "reel_monday", "remotion", "public-record", "Mon"),
    ("Tuesday", "reel_tuesday", "remotion", "myth-bust", "Tue"),
    ("Wednesday", "reel_wednesday", "remotion", "weekly-stats", "Wed"),
    ("Thursday", "reel_thursday", "heygen", "contractor", "Thu"),
    ("Friday", "reel_friday", "remotion", "notice", "Fri"),
    ("Saturday", "reel_saturday", "remotion", "county-breakdown", "Sat"),
    ("Sunday", "reel_sunday", "heygen", "contractor-disaster", "Sun"),
]
_REEL_TASKS = [
    _task(f"LeadFlow - Reel {label}", key, "content", "P2", f"weekly {day} 13:00",
          "reel_generator.py", f"-X utf8 reel_generator.py --{engine} {rtype}",
          f"reel_{engine}", "medium")
    for label, key, engine, rtype, day in _REELS
]

# ── Master registry ──────────────────────────────────────────────────────────
TASKS = _EXPLICIT + _SMS_TASKS + _CL_TASKS + _DBPR_TASKS + _REEL_TASKS

# Lookup helpers ─────────────────────────────────────────────────────────────
BY_NAME = {t["task_name"]: t for t in TASKS}
BY_KEY = {t["task_key"]: t for t in TASKS}


def by_priority(p: str):
    return [t for t in TASKS if t["priority"] == p]


def p0_p1():
    return [t for t in TASKS if t["priority"] in ("P0", "P1")]


# Known Windows Task Scheduler naming issues (documented, not yet renamed).
NAMING_ISSUES = {
    "LeadFlow - AZ ROC Scaper": "Typo: 'Scaper' should be 'Scraper'",
    "LeadFlow - Weekly Intelligenc": "Truncated: should be 'Weekly Intelligence'",
    "LeadFlow-Sucess": "Misspelled 'Success' and missing ' - ' separator",
    "LeadFlow  - Weekly Scrape": "Double space after 'LeadFlow'",
}


if __name__ == "__main__":
    from collections import Counter
    print(f"Registered tasks: {len(TASKS)}")
    print("By priority:", dict(Counter(t["priority"] for t in TASKS)))
    print("By category:", dict(Counter(t["category"] for t in TASKS)))
    print("Naming issues flagged:", len(NAMING_ISSUES))
    missing_run_type = [t["task_name"] for t in TASKS if not t["expected_pipeline_run_type"]]
    print(f"No pipeline run_type ({len(missing_run_type)}):", ", ".join(missing_run_type))
