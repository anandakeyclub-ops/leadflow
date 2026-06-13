# setup_data_engine_task.ps1
# ==========================
# Registers the Windows Task Scheduler entry for the LeadFlow data engine.
# Runs daily at 6:30 AM — BEFORE the email enrichment job at 7:00 AM.
#
# Run once as Administrator:
#   Right-click PowerShell -> Run as Administrator
#   cd C:\Users\Dana\Desktop\leadflow
#   .\scripts\data_engine\setup_data_engine_task.ps1

$python  = "C:\Users\Dana\AppData\Local\Microsoft\WindowsApps\python.exe"
$workdir = "C:\Users\Dana\Desktop\leadflow"

Write-Host "`n[LeadFlow] Registering Data Engine scheduled task..." -ForegroundColor Cyan

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "C:\Users\Dana\Desktop\leadflow\scripts\data_engine\run_daily.py" `
    -WorkingDirectory $workdir

$trigger = New-ScheduledTaskTrigger -Daily -At 6:30AM

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName "LeadFlow - Data Engine" `
    -Action   $action `
    -Trigger  $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "  OK  LeadFlow - Data Engine: Daily at 6:30 AM" -ForegroundColor Green
Write-Host "      Command : $python scripts\data_engine\run_daily.py"
Write-Host "      Start in: $workdir`n"
Write-Host "  Verify with:" -ForegroundColor Cyan
Write-Host "    Get-ScheduledTask -TaskName 'LeadFlow - Data Engine'`n"
