cd C:\Users\Dana\Desktop\leadflow

$start = Get-Date "2026-03-25"
$end = Get-Date "2026-04-24"

$current = $start

while ($current -le $end) {
    $day = $current.ToString("yyyy-MM-dd")

    Write-Host ""
    Write-Host "=== Scraping Weston Accela for $day ===" -ForegroundColor Cyan

    python -m app.workers.scrape_broward_permits `
        --source weston_accela `
        --start $day `
        --end $day `
        --limit 0 `
        --pages 10 `
        --debug-pages

    $current = $current.AddDays(1)
}