"""
test_mdc_query2.py
Tests different date filter approaches for MDC ArcGIS endpoints.
Run: python test_mdc_query2.py
"""
import requests
from datetime import datetime, date, timedelta

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

MDI_URL  = "https://gisweb.miamidade.gov/arcgis/rest/services/MD_LandInformation/MapServer/1/query"
WASD_URL = "https://gisweb.miamidade.gov/arcgis/rest/services/Wasd/Permits_4_v1/MapServer/1/query"

start = date.today() - timedelta(days=90)
start_str = start.strftime("%Y-%m-%d")

# ArcGIS date filters — try different syntax options
filters = [
    f"ISSUDATE >= timestamp '{start_str} 00:00:00'",
    f"ISSUDATE >= '{start_str}'",
    f"LSTINSDT >= '{start.strftime('%Y%m%d')}'",   # LSTINSDT is stored as YYYYMMDD integer
    f"LSTINSDT >= {int(start.strftime('%Y%m%d'))}",
    "LSTINSDT >= 20260101",
    "LSTINSDT >= 20250101",
    f"BPSTATUS = 'A'",  # Active permits as proxy
]

print("=== Testing MDI date filters ===")
for where in filters:
    try:
        resp = session.get(MDI_URL, params={
            "where": where,
            "resultRecordCount": 3,
            "outFields": "PROCNUM,ADDRESS,TYPE,ISSUDATE,LSTINSDT,BPSTATUS",
            "f": "json"
        }, timeout=15)
        data = resp.json()
        if "error" in data:
            print(f"  FAIL: {where!r}")
            print(f"        {data['error'].get('message','')}")
        else:
            features = data.get("features", [])
            print(f"  OK ({len(features)} features): {where!r}")
            for f in features[:2]:
                a = f["attributes"]
                issued = datetime.fromtimestamp(a["ISSUDATE"]/1000).date() if a.get("ISSUDATE") else None
                lstins = a.get("LSTINSDT", "")
                print(f"    {a.get('PROCNUM')} | issued={issued} | LSTINSDT={lstins} | {a.get('ADDRESS','')[:30]}")
    except Exception as e:
        print(f"  ERR: {where!r}: {e}")

print("\n=== Testing WASD date filters ===")
wasd_filters = [
    f"BLDPRMIDT >= timestamp '{start_str} 00:00:00'",
    f"BLDPRMIDT >= '{start_str}'",
    "BLDPRMSTAT = 'A'",
    "BLDPRMFLG = 'Y'",
    "1=1",
]
for where in wasd_filters:
    try:
        resp = session.get(WASD_URL, params={
            "where": where,
            "resultRecordCount": 3,
            "outFields": "BLDPRMNO,BLDPRMTYP,BLDPRMIDT,PROJDESC,BLDPRMSTAT",
            "orderByFields": "BLDPRMIDT DESC",
            "f": "json"
        }, timeout=15)
        data = resp.json()
        if "error" in data:
            print(f"  FAIL: {where!r} — {data['error'].get('message','')}")
        else:
            features = data.get("features", [])
            print(f"  OK ({len(features)} features): {where!r}")
            for f in features[:2]:
                a = f["attributes"]
                issued = datetime.fromtimestamp(a["BLDPRMIDT"]/1000).date() if a.get("BLDPRMIDT") else None
                print(f"    {a.get('BLDPRMNO')} | {a.get('BLDPRMTYP')} | issued={issued}")
    except Exception as e:
        print(f"  ERR: {where!r}: {e}")

# Also check MDI total count and max LSTINSDT value
print("\n=== MDI stats ===")
try:
    resp = session.get(MDI_URL, params={
        "where": "1=1",
        "returnCountOnly": "true",
        "f": "json"
    }, timeout=15)
    print(f"Total MDI permits: {resp.json().get('count', 'unknown')}")
except Exception as e:
    print(f"Count error: {e}")

# Get most recent LSTINSDT value to understand the date range
try:
    resp = session.get(MDI_URL, params={
        "where": "1=1",
        "resultRecordCount": 5,
        "outFields": "PROCNUM,ISSUDATE,LSTINSDT,BPSTATUS",
        "orderByFields": "ISSUDATE DESC",
        "f": "json"
    }, timeout=15)
    data = resp.json()
    print("Most recent by ISSUDATE:")
    for f in data.get("features", []):
        a = f["attributes"]
        issued = datetime.fromtimestamp(a["ISSUDATE"]/1000).date() if a.get("ISSUDATE") else None
        print(f"  {a.get('PROCNUM')} | issued={issued} | LSTINSDT={a.get('LSTINSDT')} | status={a.get('BPSTATUS')}")
except Exception as e:
    print(f"Recent query error: {e}")
