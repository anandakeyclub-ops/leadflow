<#
.SYNOPSIS
  Export all LeadFlow* Windows scheduled tasks to JSON for the Daily Summary
  Automation Command Center.

.DESCRIPTION
  Writes logs/task_audit/scheduled_tasks_latest.json (and a dated copy) with one
  record per LeadFlow* task:
    TaskName, LastRunTime, LastTaskResult, NextRunTime, State,
    Execute, Arguments, WorkingDirectory

  Run manually:
    powershell -ExecutionPolicy Bypass -File scripts\maintenance\export_scheduled_tasks.ps1
  Or schedule it to run a few minutes before "LeadFlow - Daily Summary".
#>

$ErrorActionPreference = "Stop"

# Repo root = two levels up from this script (scripts\maintenance\..\..)
$RepoRoot  = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $RepoRoot) { $RepoRoot = "C:\Users\Dana\Desktop\leadflow" }
$OutDir    = Join-Path $RepoRoot "logs\task_audit"
$OutLatest = Join-Path $OutDir   "scheduled_tasks_latest.json"
$Stamp     = Get-Date -Format "yyyy-MM-dd"
$OutDated  = Join-Path $OutDir   "scheduled_tasks_$Stamp.json"

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

$records = @()
$tasks = Get-ScheduledTask | Where-Object { $_.TaskName -like "LeadFlow*" }

foreach ($t in $tasks) {
    $info   = $null
    try { $info = $t | Get-ScheduledTaskInfo } catch {}
    $action = $t.Actions | Select-Object -First 1

    $records += [PSCustomObject]@{
        TaskName         = $t.TaskName
        State            = "$($t.State)"
        LastRunTime      = if ($info) { "$($info.LastRunTime)" } else { $null }
        LastTaskResult   = if ($info) { $info.LastTaskResult } else { $null }
        NextRunTime      = if ($info) { "$($info.NextRunTime)" } else { $null }
        Execute          = if ($action) { $action.Execute } else { $null }
        Arguments        = if ($action) { $action.Arguments } else { $null }
        WorkingDirectory = if ($action) { $action.WorkingDirectory } else { $null }
    }
}

$payload = [PSCustomObject]@{
    generated_at = (Get-Date).ToString("o")
    task_count   = $records.Count
    tasks        = $records
}

# Write UTF-8 without BOM so Python json.load reads it cleanly.
$json = $payload | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($OutLatest, $json, (New-Object System.Text.UTF8Encoding($false)))
[System.IO.File]::WriteAllText($OutDated,  $json, (New-Object System.Text.UTF8Encoding($false)))

Write-Output "Exported $($records.Count) LeadFlow tasks to:"
Write-Output "  $OutLatest"
Write-Output "  $OutDated"
