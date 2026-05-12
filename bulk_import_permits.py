from pathlib import Path
from app.workers.import_palm_beach_weekly import import_weekly_file

permits_dir = Path("data/raw/palm_beach/permits")
files = sorted(permits_dir.glob("Permits-Wkly_*.txt"), key=lambda f: f.name)

total = {}
for f in files:
    print(f"Importing: {f.name}")
    stats = import_weekly_file(f)
    print(f"  inserted={stats['normalized_inserted']} updated={stats['normalized_updated']} skipped={stats['normalized_skipped']}")
    for k, v in stats.items():
        total[k] = total.get(k, 0) + v

print(f"\nTOTAL normalized inserted : {total['normalized_inserted']}")
print(f"TOTAL normalized updated  : {total['normalized_updated']}")
print(f"TOTAL raw inserted        : {total['raw_inserted']}")
