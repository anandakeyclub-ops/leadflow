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

# Prefer -RunLevel Highest (matches the other LeadFlow tasks) but that requires
# an elevated session. Fall back to a normal per-user task so this also works
# without admin. Report honestly on failure.
$registered = $false
try {
    Register-ScheduledTask `
        -TaskName "LeadFlow - Data Engine" `
        -Action   $action `
        -Trigger  $trigger `
        -Settings $settings `
        -RunLevel Highest `
        -Force -ErrorAction Stop | Out-Null
    $registered = $true
    Write-Host "  OK  LeadFlow - Data Engine: Daily 6:30 AM (RunLevel Highest)" -ForegroundColor Green
} catch {
    Write-Host "  ! Elevated registration denied; retrying as a normal per-user task..." -ForegroundColor Yellow
    try {
        Register-ScheduledTask `
            -TaskName "LeadFlow - Data Engine" `
            -Action   $action `
            -Trigger  $trigger `
            -Settings $settings `
            -Force -ErrorAction Stop | Out-Null
        $registered = $true
        Write-Host "  OK  LeadFlow - Data Engine: Daily 6:30 AM (per-user)" -ForegroundColor Green
    } catch {
        Write-Host "  X  Could not register task: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "     Re-run this script from an elevated PowerShell." -ForegroundColor Red
    }
}
if (-not $registered) { exit 1 }
Write-Host "      Command : $python scripts\data_engine\run_daily.py"
Write-Host "      Start in: $workdir`n"
Write-Host "  Verify with:" -ForegroundColor Cyan
Write-Host "    Get-ScheduledTask -TaskName 'LeadFlow - Data Engine'`n"
