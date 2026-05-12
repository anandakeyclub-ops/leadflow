import requests
import re

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Test 1: ArcGIS parcel layer
print("=" * 60)
print("TEST 1: ArcGIS parcel layer")
print("=" * 60)
url = "https://maps.co.palm-beach.fl.us/arcgis/rest/services/Parcels/Parcels/MapServer/0/query"
params = {
    "where":             "SITEADDR LIKE '19666%'",
    "outFields":         "*",
    "f":                 "json",
    "resultRecordCount": 3,
}
try:
    resp = requests.get(url, params=params, timeout=15, headers=headers)
    print(f"Status: {resp.status_code}")
    print(resp.text[:3000])
except Exception as e:
    print(f"Error: {e}")

# Test 2: PAPA HTML name search
print("\n" + "=" * 60)
print("TEST 2: PAPA HTML name search for BROSEN ALICIA")
print("=" * 60)
url2 = "https://pbcpao.gov/MasterSearch/SearchResults"
params2 = {"propertyType": "RE", "searchvalue": "BROSEN ALICIA"}
try:
    resp2 = requests.get(url2, params=params2, timeout=15, headers=headers)
    print(f"Status: {resp2.status_code}")
    print(f"Final URL: {resp2.url}")
    # Print first 3000 chars
    print(resp2.text[:3000])
except Exception as e:
    print(f"Error: {e}")

# Test 3: PAPA property detail by PCN
print("\n" + "=" * 60)
print("TEST 3: PAPA detail page for PCN 41-47-12-15-010-0260")
print("=" * 60)
pcn = "41471215010 0260"
url3 = f"https://pbcpao.gov/property/{pcn.replace('-','').replace(' ','')}"
try:
    resp3 = requests.get(url3, timeout=15, headers=headers)
    print(f"Status: {resp3.status_code}")
    print(f"Final URL: {resp3.url}")
    # Look for address/owner info
    text = resp3.text[:5000]
    for pattern in [r"mail", r"address", r"owner", r"mailing", r"BROSEN", r"19666"]:
        matches = re.findall(r".{0,80}" + pattern + r".{0,80}", text, re.I)
        if matches:
            print(f"\n  Pattern '{pattern}':")
            for m in matches[:3]:
                print(f"    {m.strip()}")
except Exception as e:
    print(f"Error: {e}")