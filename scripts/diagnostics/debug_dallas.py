"""
debug_dallas.py
===============
Saves raw HTML from Dallas PublicSearch to inspect actual structure.
Run: python debug_dallas.py
"""
import requests
from datetime import date, timedelta
from pathlib import Path

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,*/*",
    "Accept-Language": "en-US,en;q=0.9",
})

end_date   = date.today()
start_date = end_date - timedelta(days=180)
date_from  = start_date.strftime("%Y%m%d")
date_to    = end_date.strftime("%Y%m%d")

url = (
    f"https://dallas.tx.publicsearch.us/results"
    f"?department=RP"
    f"&keywordSearch=false"
    f"&recordedDateRange={date_from},{date_to}"
    f"&searchOcrText=false"
    f"&searchType=quickSearch"
    f"&searchValue=Internal+Revenue+Service"
)

print(f"Fetching: {url}")
r = session.get(url, timeout=30)
print(f"Status: {r.status_code}")
print(f"Content length: {len(r.text):,} chars")

# Save full HTML
out = Path("data/texas/dallas_debug.html")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(r.text, encoding="utf-8")
print(f"Saved: {out}")

# Show first 3000 chars of cleaned text
import re
clean = re.sub(r'<script[^>]*>.*?</script>', ' ', r.text, flags=re.DOTALL)
clean = re.sub(r'<style[^>]*>.*?</style>', ' ', clean, flags=re.DOTALL)
clean = re.sub(r'<[^>]+>', ' ', clean)
clean = re.sub(r'\s+', ' ', clean).strip()

print(f"\nFirst 3000 chars of cleaned response:")
print(clean[:3000])
print("\n...")
print(f"Last 1000 chars:")
print(clean[-1000:])

# Check for key terms
checks = [
    "INTERNAL REVENUE",
    "FEDERAL TAX LIEN",
    "menu icon",
    "Grantor",
    "Grantee",
    "results",
    "No results",
    "sign in",
    "login",
    "register",
]
print("\nKey term checks:")
for term in checks:
    found = term.lower() in r.text.lower()
    print(f"  {'✅' if found else '❌'} '{term}': {found}")
