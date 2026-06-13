"""Quick probe to show raw API response structure."""
import json
import requests

API_URL = "https://publicapi.recorder.maricopa.gov/documents/search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Referer": "https://recorder.maricopa.gov/",
    "Origin":  "https://recorder.maricopa.gov",
}

params = {
    "businessNames": "",
    "firstNames":    "",
    "lastNames":     "",
    "middleNameIs":  "",
    "documentCode":  "FL",
    "beginDate":     "2025-01-01",
    "endDate":       "2026-06-05",
    "pageSize":      20,
    "pageNumber":    1,
    "maxResults":    500,
}

print("Fetching:", API_URL)
print("Params:", params)
print()

r = requests.get(API_URL, params=params, headers=HEADERS, timeout=20)
print(f"Status: {r.status_code}")
print(f"Content-Type: {r.headers.get('content-type','?')}")
print(f"Response length: {len(r.text)} chars")
print()
print("=== RAW RESPONSE (first 3000 chars) ===")
print(r.text[:3000])
print()

try:
    data = r.json()
    print("=== JSON TYPE:", type(data).__name__, "===")
    if isinstance(data, dict):
        print("Top-level keys:", list(data.keys()))
        for k, v in data.items():
            if isinstance(v, list):
                print(f"  {k}: list of {len(v)} items")
                if v:
                    print(f"    First item keys: {list(v[0].keys()) if isinstance(v[0], dict) else type(v[0])}")
                    print(f"    First item: {json.dumps(v[0], default=str)[:500]}")
            else:
                print(f"  {k}: {repr(v)[:100]}")
    elif isinstance(data, list):
        print(f"Top-level list: {len(data)} items")
        if data and isinstance(data[0], dict):
            print(f"First item keys: {list(data[0].keys())}")
            print(f"First item: {json.dumps(data[0], default=str)[:500]}")
except Exception as e:
    print(f"JSON parse error: {e}")
    print("Raw text:", r.text[:500])
