# Broward Permit Scraper Patch

This adds a real Broward permit scraper for public Accela Citizen Access portals. It does **not** rely on manually downloaded CSV files.

## 1) Install dependencies

Make sure requirements.txt has one package per line, then add:

```txt
selenium
webdriver-manager
beautifulsoup4
```

Then run:

```powershell
pip install selenium webdriver-manager beautifulsoup4
```

## 2) Run DB migration once

Open pgAdmin and run:

```text
app/db/migrations/20260424_broward_permit_indexes.sql
```

## 3) Smoke test scraper visibly

```powershell
cd C:\Users\Dana\Desktop\leadflow
python -m app.workers.scrape_broward_permits --days-back 7 --visible --limit 25
```

## 4) Validate permits landed

```sql
SELECT COUNT(*) FROM normalized_permits WHERE county_id = 4;
SELECT id, permit_number, owner_name, address_1, permit_type, issued_date
FROM normalized_permits
WHERE county_id = 4
ORDER BY id DESC
LIMIT 25;
```

## 5) Then run matching only after Broward permits exist

```powershell
python -m app.workers.match_and_score
```

## Notes

- The scraper targets public Accela portals in Broward municipalities because Broward County's central BCS search is lookup-oriented, not a clean recent-issued bulk feed.
- Fort Lauderdale and Weston are included first because they expose Accela public search pages with date filters.
- Add more Broward city Accela sources in `config/counties/broward_permit_sources.json` using the same structure.
- Debug HTML/screenshots are saved in `data/debug/broward_permits/` when a source fails.
