import requests, json
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, */*",
    "Referer": "https://recorder.maricopa.gov/",
    "Origin":  "https://recorder.maricopa.gov",
}
for page in [1, 2, 3]:
    params = {
        "businessNames": "", "firstNames": "", "lastNames": "", "middleNameIs": "",
        "documentCode": "FL",
        "beginDate": "2025-06-05", "endDate": "2026-06-05",
        "pageSize": 20, "pageNumber": page, "maxResults": 500,
    }
    r = requests.get("https://publicapi.recorder.maricopa.gov/documents/search",
                     params=params, headers=HEADERS, timeout=15)
    data = r.json()
    items = data.get("searchResults", [])
    total = data.get("totalResults", "MISSING")
    print(f"Page {page}: {len(items)} items, totalResults={total}")
    if len(items) == 0:
        print("  EMPTY — stopping")
        break
    if items:
        print(f"  First rec: {items[0]['recordingNumber']} date:{items[0]['recordingDate']}")
        print(f"  Last rec:  {items[-1]['recordingNumber']} date:{items[-1]['recordingDate']}")
