"""
test_papa_detail.py
Tests parsing the PAPA property detail page for mailing address.
Run: python test_papa_detail.py
"""
import re
import requests

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
}

# The name search redirects directly to detail page
# Test with Alicia Brosen
print("Fetching PAPA detail for Alicia Brosen via name search...")
url = "https://pbcpao.gov/MasterSearch/SearchResults"
params = {"propertyType": "RE", "searchvalue": "BROSEN ALICIA"}
resp = requests.get(url, params=params, timeout=15, headers=headers, allow_redirects=True)
print(f"Status: {resp.status_code}")
print(f"Final URL: {resp.url}")

html = resp.text

# Save for inspection
with open("data/docs/papa_detail_test.html", "w", encoding="utf-8") as f:
    f.write(html)
print("Saved to data/docs/papa_detail_test.html")

# Try to find mailing address in the page
print("\n--- Looking for address/owner data ---")
# Strip HTML tags for text search
text = re.sub(r"<[^>]+>", " ", html)
text = re.sub(r"\s+", " ", text)

# Find key patterns
patterns = {
    "Mailing":   r"Mailing.{0,200}",
    "Owner":     r"Owner.{0,200}",
    "Address":   r"\d+ [A-Z].{5,50}(?:Ave|Blvd|St|Dr|Ln|Rd|Way|Ct|Pl|Ter|Cir|Loop)\b.{0,80}",
    "BROSEN":    r"BROSEN.{0,100}",
    "19666":     r"19666.{0,100}",
    "Mail addr": r"[Mm]ail.{0,50}\d+.{0,80}",
}

for label, pattern in patterns.items():
    matches = re.findall(pattern, text, re.I)
    if matches:
        print(f"\n{label}:")
        for m in matches[:3]:
            print(f"  {m.strip()[:200]}")

# Also check if parcelId is in the URL
parcel_m = re.search(r"parcelId=(\d+)", resp.url)
if parcel_m:
    print(f"\nParcel ID from URL: {parcel_m.group(1)}")
    # Format as PCN: 00-41-47-12-15-010-0260 (18 digits with leading zeros)
    raw = parcel_m.group(1)
    print(f"Raw parcel: {raw} ({len(raw)} digits)")
