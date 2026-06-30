<#
update_sms_tasks.ps1
====================
Applies the SMS volume changes to Windows Task Scheduler.

  * Bumps LeadFlow SMS Morning/Midday/Afternoon from --limit 50 to --limit 100
    (preserves --delay 8, --batch-id, and --i-understand-tcpa-risk).
  * Registers/updates "LeadFlow - SMS FOIA": daily 2:00 PM, IRS FOIA payroll leads.

Result: 3x100 ROC + 1x100 FOIA = 400 SMS/day max (shared daily cap, enforced in
twilio_sms_campaign.py).

MUST be run from an ELEVATED (Administrator) PowerShell — the SMS tasks run at
RunLevel=Highest and cannot be modified by a non-admin process.

Usage (in an Administrator PowerShell):
    cd C:\Users\Dana\Desktop\leadflow
    .\scripts\maintenance\update_sms_tasks.ps1
  (you will be prompted once for the 'Dana' account password, used only to
   register the new FOIA task; it is never written to disk)
#>
param(
    [string]$Password
)

$ErrorActionPreference = 'Stop'

# Elevation check
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Not elevated. Right-click PowerShell -> Run as administrator, then re-run this script."
    exit 1
}

$PYTHON  = "C:\Users\Dana\Desktop\leadflow\.venv\Scripts\python.exe"
$WORKDIR = "C:\Users\Dana\Desktop\leadflow"

# --- 1. Bump the three ROC batches to --limit 100 (preserve all other flags) ---
$names = @('LeadFlow - SMS Morning','LeadFlow - SMS Midday','LeadFlow - SMS Afternoon')
foreach ($n in $names) {
    $t = Get-ScheduledTask -TaskName $n
    $a = $t.Actions[0]
    $newArgs = $a.Arguments -replace '--limit\s+\d+','--limit 100'
    $wd = if ($a.WorkingDirectory) { $a.WorkingDirectory } else { $WORKDIR }
    $act = New-ScheduledTaskAction -Execute $a.Execute -Argument $newArgs -WorkingDirectory $wd
    # -Action only preserves the existing principal + stored credentials.
    Set-ScheduledTask -TaskName $n -Action $act | Out-Null
    Write-Host "Updated: $n"
    Write-Host ("   " + (Get-ScheduledTask -TaskName $n).Actions[0].Arguments)
}

# --- 2. Register / update the daily FOIA payroll SMS batch (2:00 PM) ---
if (-not $Password) {
    $sec = Read-Host "Password for account 'Dana' (to register FOIA task)" -AsSecureString
    $Password = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}

$foiaName = "LeadFlow - SMS FOIA"
$foiaArgs = "-m scripts.maintenance.twilio_sms_campaign --source irs_foia --limit 100 --delay 8 --batch-id foia --i-understand-tcpa-risk"
$foiaAction  = New-ScheduledTaskAction -Execute $PYTHON -Argument $foiaArgs -WorkingDirectory $WORKDIR
$foiaTrigger = New-ScheduledTaskTrigger -Daily -At 14:00
$foiaSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)

if (Get-ScheduledTask -TaskName $foiaName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $foiaName -Confirm:$false
}
Register-ScheduledTask -TaskName $foiaName -Action $foiaAction -Trigger $foiaTrigger `
    -Settings $foiaSettings -User "Dana" -Password $Password -RunLevel Highest | Out-Null
Write-Host "Registered: $foiaName  (daily 14:00)"
Write-Host ("   " + $foiaArgs)

Write-Host "`nDone. Daily SMS capacity is now 3x100 ROC + 100 FOIA = up to 400/day (shared cap)."
