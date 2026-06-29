# Daily Summary — Automation Audit

**Date:** 2026-06-28
**Author:** Automation Command Center upgrade
**Scope:** `app/workers/daily_summary.py` + LeadFlow Windows Task Scheduler jobs

---

## 1. Existing `daily_summary.py` sections (pre-upgrade)

`build_html()` assembles these sections (all preserved, untouched by this upgrade):

| # | Section | Builder |
|---|---------|---------|
| 1 | Dark header band | inline in `build_html` |
| 2 | Engine health scorecards (A–F per engine) | `_build_engine_scorecards` |
| 3 | KPI row (revenue / sends / open rate / email-ready) | inline |
| 4 | Action items (CRITICAL / WARNING / OPPORTUNITY) | `build_action_section` |
| 5 | Revenue | `build_revenue_section` |
| 6 | Email engine | `build_email_section` |
| 7 | SMS engine | `build_sms_section` |
| 8 | Lead (states / counties) | `build_lead_section` |
| 9 | Booking funnel | `build_booking_section` |
| 10 | Traffic + funnel (GA4 / Clarity / UX) | `build_traffic_section` |
| 11 | Content engine (blog/social/reel) | `build_content_section` |
| 12 | Pipeline calendar | `build_pipeline_calendar_section` |
| 13 | Lead Intelligence dashboard | `build_lead_intelligence_section` |
| 14 | Data collection | `build_data_collection_section` |
| 15 | AI export block (engine snapshot) | `build_ai_export_block` |

Data plumbing reused by the upgrade: `safe_query()`, `_read_pipeline_today()`
(reads `logs/pipeline/<date>.jsonl`), `sec2()/tbl()/h()/badge()` HTML helpers,
brand color constants, and `PipelineLogger` (format unchanged).

**Gap (pre-upgrade):** the summary had **no view of Windows Task Scheduler
state** — it could see pipeline logs but not whether a Windows task ran, failed,
or was missed. The Automation Command Center (sections A–H) closes that gap.

---

## 2. LeadFlow scheduled tasks (58 registered)

Registered in `scripts/automation_registry.py`. Priorities:

- **P0 (6):** API Server, ngrok Tunnel, ngrok Watchdog, Daily Summary, Daily Email, Data Engine
- **P1 (8):** Email Enrichment, Free Email Enrichment, Bridge to Email Pool, Abandoned Booking Retargeting, SMS Morning/Midday/Afternoon, DB Connection Cleanup
- **P2 (15):** Blog Draft, Social Media, Educational, Reel Mon–Sun, Reel Educational, Reel Stats, Guest Post Outreach, GSC Monitor, Page Refresh
- **P3 (29):** Weekly Scrape, Weekly Intelligence, Weekly Report Monday, Enforcement Brief Thursday, Monthly Reports, County Lien Intel, DBPR ×5, CourtListener ×9, Google Places, TDLR, TX Lien Match, Harris, Maricopa, AZ ROC, Selenium TX, Multi-State Enrichment

Live status is exported by `scripts/maintenance/export_scheduled_tasks.ps1` to
`logs/task_audit/scheduled_tasks_latest.json` (55 LeadFlow tasks found on the box).

---

## 3. Flagged: naming issues (documented, NOT renamed)

These are real Windows task names with defects. Renaming risks breaking the
scheduler entry, so they are kept verbatim in the registry and flagged here:

| Windows task name | Issue | Should be |
|-------------------|-------|-----------|
| `LeadFlow - AZ ROC Scaper` | typo | `LeadFlow - AZ ROC Scraper` |
| `LeadFlow - Weekly Intelligenc` | truncated | `LeadFlow - Weekly Intelligence` |
| `LeadFlow-Sucess` | misspelled + missing ` - ` | `LeadFlow - Success` |
| `LeadFlow  - Weekly Scrape` | double space after `LeadFlow` | `LeadFlow - Weekly Scrape` |

> Note: `LeadFlow  - Weekly Scrape` currently reports `LastTaskResult = 2`
> (file-not-found / error) in the Windows export — worth fixing the action path.

---

## 4. Flagged: registry ↔ Windows calendar gaps

Computed from `scheduled_tasks_latest.json` vs the registry:

**Registered but NOT scheduled in Windows (missing from calendar):**
- `LeadFlow - Reel Monday`
- `LeadFlow - Reel Tuesday`
- `LeadFlow - Reel Friday`
- `LeadFlow - Reel Saturday`

→ Four weekday reels are defined in the registry/spec but have no Windows
trigger. Either create the tasks or rely on `LeadFlow - Reel Educational` /
`reel_generator.py --auto` (weekday-aware) instead.

**Scheduled in Windows but NOT in registry:**
- `LeadFlow - Google Places Tonight` (a second Google Places run; registry only
  tracks `LeadFlow - Google Places Scraper`)

---

## 5. Flagged: tasks with no PipelineLogger coverage

These tasks do **not** emit a `logs/pipeline/<date>.jsonl` entry, so the only
signal we have is the Windows `LastTaskResult`. The Command Center shows them as
🟠 *No output* when they "succeed" in Windows but log nothing.

- `LeadFlow - API Server` (long-running service — no per-run log expected)
- `LeadFlow - ngrok Tunnel` (long-running service)
- `LeadFlow - ngrok Watchdog`
- `LeadFlow - DB Connection Cleanup`

**Recommended PipelineLogger additions** (set `expected_pipeline_run_type` in the
registry, then add `PipelineLogger("<run_type>")` to the script):
scrapers (CourtListener, Harris, Maricopa, TDLR, Selenium TX, AZ ROC, Google
Places), enrichment (DBPR, Sunbiz, multi-state), reels, social, blog. The
registry already declares the *expected* run_type for these so the dashboard
will light up 🟢 automatically once logging is added.

---

## 6. What the upgrade adds (sections A–H)

A. Automation Command Center header — health score + expected/succeeded/failed/missed/running.
B. Today at a Glance timeline.
C. Full task status table (Windows result × pipeline match × output).
D. P0/P1 critical infrastructure panel with manual-fix commands.
E. Content engine panel (blog/social/reel today).
F. Data engine panel (new liens / contacts / matched leads today).
G. Infrastructure panel (API :8000 live check, ngrok, DB pool).
H. Automation Snapshot (copy-paste plain text for Claude).

All additive — existing revenue/email/SMS/GA4/Clarity/lead sections and the
PipelineLogger format are unchanged.
