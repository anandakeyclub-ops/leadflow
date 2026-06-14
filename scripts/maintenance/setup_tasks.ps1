# setup_tasks.ps1
# ================
# Creates Windows Task Scheduler tasks for LeadFlow automation.
# Run once as Administrator:
#   Right-click PowerShell → Run as Administrator
#   cd C:\Users\Dana\Desktop\leadflow
#   .\setup_tasks.ps1

$python = "C:\Users\Dana\AppData\Local\Microsoft\WindowsApps\python.exe"
$workdir = "C:\Users\Dana\Desktop\leadflow"

Write-Host "`n[LeadFlow] Setting up scheduled tasks..." -ForegroundColor Cyan

# ── Daily Email — 8:00 AM only, on scheduled days (Mon/Tue/Wed/Thu/Sat) ──────
# Single morning send. The extra 1:00 PM and 5:00 PM runs were redundant — they
# fired after the day's Gmail limit was already spent, so they only logged
# "550 5.4.5 Daily user sending limit exceeded" throttle errors. Removed.
$emailAction = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "-m app.workers.send_email_sequence --auto --limit 50 --delay 12" `
    -WorkingDirectory $workdir

$emailTriggers = @(
    $(New-ScheduledTaskTrigger -Weekly `
        -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Saturday -At 8:00AM)
)

$emailSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 30)

# RunLevel Highest needs an elevated shell; fall back to a per-user task so this
# still registers if run non-elevated.
try {
    Register-ScheduledTask `
        -TaskName "LeadFlow - Daily Email" `
        -Action   $emailAction `
        -Trigger  $emailTriggers `
        -Settings $emailSettings `
        -RunLevel Highest `
        -Force -ErrorAction Stop | Out-Null
} catch {
    Register-ScheduledTask `
        -TaskName "LeadFlow - Daily Email" `
        -Action   $emailAction `
        -Trigger  $emailTriggers `
        -Settings $emailSettings `
        -Force | Out-Null
}

Write-Host "  ✓ Daily Email: 8:00 AM Mon/Tue/Wed/Thu/Sat (1 PM & 5 PM removed)" -ForegroundColor Green

# ── Weekly Scrape — Every Monday at 7:00 AM ──────────────────────────────────
$scrapeAction = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "weekly_scrape.py --days 7" `
    -WorkingDirectory $workdir

$scrapeTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 7:00AM

$scrapeSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 60)

Register-ScheduledTask `
    -TaskName   "LeadFlow - Weekly Scrape" `
    -Action     $scrapeAction `
    -Trigger    $scrapeTrigger `
    -Settings   $scrapeSettings `
    -RunLevel   Highest `
    -Force | Out-Null

Write-Host "  ✓ Weekly Scrape: Every Monday at 7:00 AM" -ForegroundColor Green

# ── DBPR Enrichment — Every Monday at 1:00 PM (after scrape finishes) ────────
$dbprAction = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "-m app.workers.enrich_liens_from_dbpr --force --export" `
    -WorkingDirectory $workdir

$dbprTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 1:00PM

$dbprSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName   "LeadFlow - DBPR Enrichment" `
    -Action     $dbprAction `
    -Trigger    $dbprTrigger `
    -Settings   $dbprSettings `
    -RunLevel   Highest `
    -Force | Out-Null

Write-Host "  ✓ DBPR Enrichment: Every Monday at 1:00 PM" -ForegroundColor Green

# ── Export Contacts — Every Monday at 3:00 PM (after enrichment) ─────────────
$exportAction = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "export_contacts.py" `
    -WorkingDirectory $workdir

$exportTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 3:00PM

Register-ScheduledTask `
    -TaskName   "LeadFlow - Export Contacts" `
    -Action     $exportAction `
    -Trigger    $exportTrigger `
    -Settings   (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1)) `
    -RunLevel   Highest `
    -Force | Out-Null

Write-Host "  ✓ Export Contacts: Every Monday at 3:00 PM" -ForegroundColor Green

# ── Weekly Intelligence Report — Every Sunday at 7:30 AM ─────────────────────
# Generates + publishes the 10-state rotation intelligence report (one state per
# week, Florida weekly). Sunday 7:30 AM matches the run pattern already in the
# pipeline logs. No flags = generate + publish.
$intelAction = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "scripts/reports/weekly_intelligence.py" `
    -WorkingDirectory $workdir

$intelTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 7:30AM

$intelSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName   "LeadFlow - Weekly Intelligence" `
    -Action     $intelAction `
    -Trigger    $intelTrigger `
    -Settings   $intelSettings `
    -RunLevel   Highest `
    -Force | Out-Null

Write-Host "  ✓ Weekly Intelligence: Every Sunday at 7:30 AM" -ForegroundColor Green

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host "`n[LeadFlow] All tasks created successfully!" -ForegroundColor Cyan
Write-Host "`n  Weekly schedule:"
Write-Host "    Sunday  7:30 AM  - Weekly intelligence report (10-state rotation)"
Write-Host "    Monday  7:00 AM  - Scrape all counties (Tue-Thu get fresh leads)"
Write-Host "    Monday  1:00 PM  - DBPR enrichment"
Write-Host "    Monday  3:00 PM  - Export contacts CSV"
Write-Host "    Email sequence 8:00 AM - Mon/Tue/Wed/Thu/Sat (limit 50/day)"
Write-Host "`n  Palm Beach (manual CAPTCHA):"
Write-Host "    Run whenever ready: python palm_beach_manual.py"
Write-Host "`n  To verify tasks:"
Write-Host "    Get-ScheduledTask | Where-Object {`$_.TaskName -like 'LeadFlow*'}"
Write-Host ""
