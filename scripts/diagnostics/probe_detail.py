"""Probe publicapi detail endpoints to find debtor name."""
import requests, json

REC_NUM = 20250036083  # Known recording number from search results
BASE    = "https://publicapi.recorder.maricopa.gov"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Referer": "https://recorder.maricopa.gov/",
    "Origin":  "https://recorder.maricopa.gov",
}

endpoints = [
    f"/documents/{REC_NUM}",
    f"/documents/{REC_NUM}/names",
    f"/documents/search?recordingNumber={REC_NUM}",
    f"/document/{REC_NUM}",
    f"/recording/{REC_NUM}",
    f"/recording/{REC_NUM}/names",
    f"/documents/{REC_NUM}/details",
    f"/documents/details?recordingNumber={REC_NUM}",
    f"/documents/names?recordingNumber={REC_NUM}",
    f"/names?recordingNumber={REC_NUM}",
    f"/documents/search?recordingNumbers={REC_NUM}",
]

s = requests.Session()
for path in endpoints:
    url = BASE + path
    try:
        r = s.get(url, headers=HEADERS, timeout=8)
        body = r.text[:300].replace("\n", " ")
        print(f"  {r.status_code}  {url}")
        if r.status_code == 200 and len(r.text) > 10:
            print(f"         BODY: {body}")
    except Exception as e:
        print(f"  ERR  {url}  {e}")
