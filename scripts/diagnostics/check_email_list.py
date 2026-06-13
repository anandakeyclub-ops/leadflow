import csv
import glob
import os

# Find the latest email list
files = sorted(glob.glob("data/exports/email_lists/email_campaign_list_*.csv"))
if not files:
    print("No email list found")
    exit()

latest = files[-1]
print(f"Checking: {latest}\n")

with open(latest, encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

real = [r for r in rows if "leadflow.invalid" not in r.get("email", "")]
fake = [r for r in rows if "leadflow.invalid" in r.get("email", "")]

print(f"Real emails ({len(real)}):")
for r in real:
    print(f"  {r.get('full_name',''):<25} {r.get('email',''):<35} score={r.get('lead_score','')}")

print(f"\nPlaceholder emails ({len(fake)}) — skipped by send_email_campaign:")
for r in fake:
    print(f"  {r.get('full_name',''):<25}")
