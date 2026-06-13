"""
check_tasks.py
==============
Audits all LeadFlow Task Scheduler jobs.
Run: python check_tasks.py
"""
import subprocess
from datetime import datetime

result = subprocess.run(
    ["schtasks", "/query", "/fo", "LIST", "/v"],
    capture_output=True, text=True
)

# Parse tasks
tasks = {}
current = {}
for line in result.stdout.splitlines():
    line = line.strip()
    if line.startswith("TaskName:"):
        if current.get("name"):
            tasks[current["name"]] = current
        current = {"name": line.split(":", 1)[1].strip().lstrip("\\")}
    elif line.startswith("Task To Run:"):
        current["command"] = line.split(":", 1)[1].strip()
    elif line.startswith("Last Run Time:"):
        current["last_run"] = line.split(":", 1)[1].strip()
    elif line.startswith("Last Result:"):
        current["last_result"] = line.split(":", 1)[1].strip()
    elif line.startswith("Next Run Time:"):
        current["next_run"] = line.split(":", 1)[1].strip()
    elif line.startswith("Days:"):
        current["days"] = line.split(":", 1)[1].strip()
    elif line.startswith("Start Time:"):
        current["start_time"] = line.split(":", 1)[1].strip()
    elif line.startswith("Schedule Type:"):
        current["schedule_type"] = line.split(":", 1)[1].strip()

if current.get("name"):
    tasks[current["name"]] = current

# Filter LeadFlow tasks
lf_tasks = {k: v for k, v in tasks.items()
            if "leadflow" in k.lower()}

print(f"\n{'='*70}")
print(f"  LeadFlow Task Scheduler Audit")
print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
print(f"  Total LeadFlow tasks: {len(lf_tasks)}")
print(f"{'='*70}\n")

# Tasks that are OK to have never run (startup or future-scheduled)
NEVER_RAN_OK = {
    "LeadFlow - API Server",       # startup task
    "LeadFlow - ngrok Tunnel",     # startup task
    "LeadFlow - AZ ROC Scaper",    # monthly — July 1
    "LeadFlow - TDLR Scraper",     # monthly — July 1
    "LeadFlow - Harris Lien Scraper", # monthly — July 1
    "LeadFlow - TX Lien Match",    # monthly — July 1
    "LeadFlow - Multi-State Enrichment", # monthly — July 1
    "LeadFlow - Email Enrichment", # new daily — hasn't run yet
    "LeadFlow - Reel Stats",       # Wednesday — hasn't fired yet
    "LeadFlow - GSC Monitor",
    "LeadFlow - Monthly Reports", 
    "LeadFlow - Page Refresh",
    "LeadFlow - Selenium TX Scraper",
    "LeadFlow - Weekly Intelligenc",
}

# Expected correct arguments for each task
EXPECTED_ARGS = {
    "LeadFlow - API Server":           "-m uvicorn app.api.main:app",
    "LeadFlow - ngrok Tunnel":         "http --domain",
    "LeadFlow - Daily Email":          "send_email_sequence --auto",
    "LeadFlow - Daily Summary":        "-m app.workers.daily_summary",
    "LeadFlow  - Weekly Scrape":       "weekly_scrape.py",
    "LeadFlow - GSC Monitor":          "scripts/seo/gsc_monitor.py",
    "LeadFlow - Page Refresh":         "scripts/seo/page_refresh_detector.py",
    "LeadFlow - Weekly Intelligenc":   "scripts/reports/weekly_intelligence.py",
    "LeadFlow - Blog Draft":           "social_media_poster.py --blog-only",
    "LeadFlow - Social Media":         "social_media_poster.py --auto",
    "LeadFlow - Educational":          "social_media_poster.py --auto",
    "LeadFlow-Sucess":                 "social_media_poster.py --auto",
    "LeadFlow - Reel Educational":     "reel_generator.py --auto",
    "LeadFlow - Reel Stats":           "reel_generator.py --remotion",
    "LeadFlow - Monthly Reports":      "monthly_state_report.py --all",
    "LeadFlow - TDLR Scraper":         "tdlr_scraper.py",
    "LeadFlow - AZ ROC Scaper":        "arizona_roc_scraper.py",
    "LeadFlow - Harris Lien Scraper":  "harris_county_lien_scraper.py",
    "LeadFlow - Email Enrichment":     "multi_state_email_enrichment.py",
    "LeadFlow - TX Lien Match":        "selenium_tx_scraper.py --all --days 35 --match",
    "LeadFlow - Multi-State Enrichment": "multi_state_enrichment.py",
}

issues   = []
ok_tasks = []

for name, task in sorted(lf_tasks.items()):
    cmd         = task.get("command", "")
    last_run    = task.get("last_run", "")
    last_result = task.get("last_result", "")
    next_run    = task.get("next_run", "N/A")
    days        = task.get("days", "")
    start_time  = task.get("start_time", "")
    sched_type  = task.get("schedule_type", "")

    task_issues = []

    # Check double python (real bug)
    if "python.exe python " in cmd or \
       ("python.exe" in cmd and " python " in cmd.split("python.exe")[1]):
        task_issues.append("❌ DOUBLE PYTHON in command")

    # Check result 2 = actual runtime failure (not 267011 which is "never triggered")
    if last_result == "2":
        task_issues.append(f"⚠ LAST RESULT: 2 (runtime error — check script)")

    # Check never ran — only flag if NOT in the OK list and not a startup task
    if "1999" in last_run and name not in NEVER_RAN_OK and sched_type != "At system start up":
        task_issues.append("⚠ NEVER RAN — may need attention")

    # Check expected args
    if name in EXPECTED_ARGS:
        expected = EXPECTED_ARGS[name]
        if expected.lower() not in cmd.lower():
            task_issues.append(f"❌ WRONG ARGS — expected to contain: '{expected}'")

    if task_issues:
        issues.append((name, task_issues, cmd, last_run, next_run, days, start_time))
    else:
        ok_tasks.append((name, cmd, last_run, next_run, days, start_time))

# Print OK tasks
print("✅ PASSING TASKS:")
print(f"{'─'*70}")
for name, cmd, last_run, next_run, days, start_time in ok_tasks:
    short_cmd = cmd.replace(
        "C:\\Users\\Dana\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe", "python"
    )
    last_short = last_run[:16] if last_run else "never"
    next_short = next_run[:16] if next_run else "N/A"
    print(f"  ✅ {name:<42} {days:<5} {start_time}")
    print(f"     cmd : {short_cmd[:65]}")
    print(f"     last: {last_short:<18} next: {next_short}")
    print()

# Print issues
print(f"\n{'='*70}")
if issues:
    print(f"❌ ISSUES FOUND ({len(issues)} tasks need fixing):")
    print(f"{'─'*70}")
    for name, task_issues, cmd, last_run, next_run, days, start_time in issues:
        short_cmd = cmd.replace(
            "C:\\Users\\Dana\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe", "python"
        )
        print(f"\n  Task : {name}")
        print(f"  Day  : {days}  Time: {start_time}")
        for issue in task_issues:
            print(f"    {issue}")
        print(f"    cmd : {short_cmd[:68]}")
        print(f"    last: {last_run[:25]}  next: {next_run[:20]}")
else:
    print("✅ ALL TASKS PASSING — no issues found!")

print(f"\n{'='*70}")
print(f"  Summary: {len(ok_tasks)} passing, {len(issues)} issues")
print(f"{'='*70}\n")