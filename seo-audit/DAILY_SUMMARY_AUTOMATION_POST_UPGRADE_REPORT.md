# Daily Summary — Automation Command Center: Post-Upgrade Report

**Date:** 2026-06-28
**Status:** ✅ Implemented and verified (dry-run)

---

## 1. Task coverage — before vs after

| | Before | After |
|---|--------|-------|
| Windows task visibility in summary | none | 58 registered, 55 live-matched |
| Health score | none | P0/P1 health % (live) |
| Failed/missed detection | none | per-task, with manual-fix commands |
| Pipeline-log ↔ Windows-task correlation | partial (content only) | full table (Section C) |
| Naming-issue tracking | none | 4 flagged |
| Calendar gap detection | none | 4 reels missing, 1 unregistered task |
| AI snapshot for automation | none | Section H (copy-paste) |

First live run measured: **expected 29 · succeeded 7 · failed 12 · missed 11 · P0/P1 health 43%.**

---

## 2. Files created

| File | Purpose |
|------|---------|
| `scripts/automation_registry.py` | 58-task source of truth (name, key, category, priority, schedule, script, args, pipeline run_type, severity, manual command) |
| `scripts/maintenance/export_scheduled_tasks.ps1` | Exports LeadFlow* tasks → `logs/task_audit/scheduled_tasks_latest.json` |
| `app/workers/automation_command_center.py` | Builds sections A–H; fully defensive (never raises into `build_html`) |
| `seo-audit/DAILY_SUMMARY_AUTOMATION_AUDIT.md` | Pre-upgrade audit + flags |
| `seo-audit/DAILY_SUMMARY_AUTOMATION_POST_UPGRADE_REPORT.md` | This report |

## 3. Files modified

| File | Change |
|------|--------|
| `app/workers/daily_summary.py` | Added `_automation_sections()` helper + one call in `build_html` **after** the existing AI export block. No existing section, query, or the PipelineLogger format was touched. |
| `generate_topic_blogs.py` | UTF-8 stdout/stderr reconfigure at top (fixes Task Scheduler emoji crash) |
| `social_media_poster.py` | UTF-8 stdout/stderr reconfigure at top |
| `reel_generator.py` | UTF-8 reconfigure + `--remotion/--heygen` accept `auto` (random valid) and fall back gracefully on unknown values instead of an argparse usage error |

---

## 4. Sections added (A–H)

- **A** Command Center header — health score + expected/succeeded/failed/missed/running counts + P0/P1 failure list
- **B** Today at a Glance — timeline of tasks expected today, ordered by time
- **C** Full task status table — Time · Task · Pri · Expected · Win LastRun · Win Result · Pipeline match · Output · Status emoji · Diagnosis
- **D** P0/P1 critical panel — 12 critical tasks with status + manual fix command
- **E** Content engine — blog / social / reel ran today?
- **F** Data engine — new liens today (+ by state), matched contacts, email-ready, Google Places & AZ ROC raw contacts (real tables)
- **G** Infrastructure — live `http://localhost:8000` check, ngrok last run, DB pool probe
- **H** Automation Snapshot — copy-paste plain text block for Claude

Status legend: 🟢 OK · 🔴 Failed · 🟠 No output · 🟡 Running · ⚫ Disabled · ❌ Missed · ⚪ Unknown

---

## 5. Scripts fixed

- **`generate_topic_blogs.py` — "prints usage instead of running":** root cause was
  the emoji output (`📊`, `✅`) crashing under Task Scheduler's cp1252 console
  *after* the API call; the run died and only the trailing help text was visible.
  Fixed with `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`.
  Verified: `--topic --limit 1 --dry-run` now prints `Generating 1 posts` and
  proceeds to `[1/1] How Long Does an IRS Tax Lien Last?…`.
- **`reel_generator.py` — reels "printing usage":** root cause was `--remotion`
  having strict `choices=[…]` that excluded scheduled values like `public-record`
  and `myth-bust`, so argparse exited with a usage error. Now `--remotion`/`--heygen`
  accept any value, treat `auto` as "pick a random valid type", and fall back
  gracefully (with a warning) on unknown values. UTF-8 reconfigure added.
- **`social_media_poster.py`:** UTF-8 reconfigure added.

---

## 6. Test results

| Test | Result |
|------|--------|
| `python scripts/automation_registry.py` | ✅ 58 tasks (P0=6, P1=8, P2=15, P3=29); 4 naming issues |
| `export_scheduled_tasks.ps1` | ✅ 55 LeadFlow tasks → `scheduled_tasks_latest.json` |
| `python -m app.workers.daily_summary --dry-run` | ✅ preview generated; all 8 sections render; no errors |
| `python -X utf8 generate_topic_blogs.py --topic --limit 1 --dry-run` | ✅ runs and generates (no usage/emoji crash) |
| `python -X utf8 reel_generator.py --heygen educational --dry-run` | ✅ runs and generates script |

> The daily summary was verified with `--dry-run` (builds the full email + saves
> `data/daily_summary_preview.html`) to avoid sending a live digest during
> testing. The blog was verified with `--dry-run` to avoid publishing a real post.
> Both run clean; remove `--dry-run` for live execution.

---

## 7. Recommended follow-ups (not done — out of scope)

1. Run `export_scheduled_tasks.ps1` a few minutes before `LeadFlow - Daily Summary`
   (add a scheduled task) so Section A always has fresh Windows data.
2. Add `PipelineLogger` calls to scrapers/enrichment/reels/social so they show
   🟢 instead of 🟠 *No output* (registry already declares the expected run_types).
3. Fix `LeadFlow  - Weekly Scrape` (LastTaskResult=2, file-not-found) and the
   4 documented naming typos.
4. Create Windows triggers for the 4 registered-but-unscheduled weekday reels,
   or switch the reel schedule to `reel_generator.py --auto`.
