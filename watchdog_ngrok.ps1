@"
`$proc = Get-Process ngrok -ErrorAction SilentlyContinue
if (-not `$proc) {
    Write-Host "ngrok dead — restarting"
    schtasks /run /tn "LeadFlow - ngrok Tunnel"
    Start-Sleep 5
    Write-Host "ngrok restarted at `$(Get-Date)"
} else {
    Write-Host "ngrok running PID `$(`$proc.Id)"
}
"@ | Set-Content "C:\Users\Dana\Desktop\leadflow\watchdog_ngrok.ps1"