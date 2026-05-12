"""
test_mdc_query.py
Tests the confirmed MDC ArcGIS endpoints directly.
Run: python test_mdc_query.py
"""
import requests
from datetime import datetime

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

MDI_URL  = "https://gisweb.miamidade.gov/arcgis/rest/services/MD_LandInformation/MapServer/1/query"
WASD_URL = "https://gisweb.miamidade.gov/arcgis/rest/services/Wasd/Permits_4_v1/MapServer/1/query"

print("=== Test 1: MDI no filter ===")
resp = session.get(MDI_URL, params={
    "where": "1=1", "resultRecordCount": 3, "outFields": "PROCNUM,ADDRESS,TYPE,ISSUDATE,CONTRNAME,BPSTATUS", "f": "json"
}, timeout=15)
data = resp.json()
print(f"Status: {resp.status_code}")
if "error" in data:
    print(f"Error: {data['error']}")
else:
    for f in data.get("features", []):
        a = f["attributes"]
        issued = datetime.fromtimestamp(a["ISSUDATE"]/1000).date() if a.get("ISSUDATE") else None
        print(f"  {a.get('PROCNUM')} | {a.get('ADDRESS')} | {a.get('TYPE')} | issued={issued}")

print("\n=== Test 2: MDI with ISSUDATE filter (epoch ms) ===")
# 90 days ago in epoch ms
import time
start_ms = int((time.time() - 90*86400) * 1000)
resp2 = session.get(MDI_URL, params={
    "where": f"ISSUDATE >= {start_ms}",
    "resultRecordCount": 5,
    "outFields": "PROCNUM,ADDRESS,TYPE,ISSUDATE,CONTRNAME",
    "orderByFields": "ISSUDATE DESC",
    "f": "json"
}, timeout=15)
data2 = resp2.json()
if "error" in data2:
    print(f"Error: {data2['error']}")
else:
    features = data2.get("features", [])
    print(f"Features: {len(features)}")
    for f in features:
        a = f["attributes"]
        issued = datetime.fromtimestamp(a["ISSUDATE"]/1000).date() if a.get("ISSUDATE") else None
        print(f"  {a.get('PROCNUM')} | {a.get('ADDRESS')} | {a.get('TYPE')} | issued={issued}")

print("\n=== Test 3: MDI count last 90 days ===")
resp3 = session.get(MDI_URL, params={
    "where": f"ISSUDATE >= {start_ms}",
    "returnCountOnly": "true",
    "f": "json"
}, timeout=15)
print(f"Count: {resp3.json()}")

print("\n=== Test 4: WASD with epoch ms filter ===")
resp4 = session.get(WASD_URL, params={
    "where": f"BLDPRMIDT >= {start_ms}",
    "resultRecordCount": 5,
    "outFields": "BLDPRMNO,BLDPRMTYP,BLDPRMIDT,PROJDESC,PROJZIP",
    "orderByFields": "BLDPRMIDT DESC",
    "f": "json"
}, timeout=15)
data4 = resp4.json()
if "error" in data4:
    print(f"Error: {data4['error']}")
else:
    features = data4.get("features", [])
    print(f"Features: {len(features)}")
    for f in features:
        a = f["attributes"]
        issued = datetime.fromtimestamp(a["BLDPRMIDT"]/1000).date() if a.get("BLDPRMIDT") else None
        print(f"  {a.get('BLDPRMNO')} | {a.get('BLDPRMTYP')} | issued={issued} | {str(a.get('PROJDESC',''))[:40]}")

print("\n=== Test 5: WASD count ===")
resp5 = session.get(WASD_URL, params={
    "where": f"BLDPRMIDT >= {start_ms}",
    "returnCountOnly": "true",
    "f": "json"
}, timeout=15)
print(f"Count: {resp5.json()}")
