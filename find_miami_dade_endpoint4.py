"""
find_miami_dade_endpoint4.py
Probes MD_LandInformation and tests WASD permit fields with date filter.
Run: python find_miami_dade_endpoint4.py
"""
import requests
from datetime import date, timedelta

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})
BASE = "https://gisweb.miamidade.gov/arcgis/rest/services"

def probe_mapserver(url, label, check_layers=True):
    print(f"\n=== {label} ===")
    try:
        resp = session.get(url, params={"f": "json"}, timeout=10)
        data = resp.json()
        if "error" in data:
            print(f"  Error: {data['error']}")
            return []
        layers = data.get("layers", [])
        tables = data.get("tables", [])
        print(f"  Layers: {[(l['id'], l['name']) for l in layers[:10]]}")
        if tables:
            print(f"  Tables: {[(t['id'], t['name']) for t in tables[:5]]}")
        return layers
    except Exception as e:
        print(f"  Error: {e}")
        return []

def probe_layer(base_url, layer_id, label, where="1=1"):
    url = f"{base_url}/{layer_id}/query"
    print(f"\n  --- Layer {layer_id}: {label} ---")
    try:
        resp = session.get(url, params={
            "where": where,
            "resultRecordCount": 3,
            "outFields": "*",
            "f": "json"
        }, timeout=15)
        data = resp.json()
        if "error" in data:
            print(f"    Error: {data['error'].get('message','')}")
            return
        features = data.get("features", [])
        print(f"    Features: {len(features)}")
        if features:
            attrs = features[0].get("attributes", {})
            print(f"    All fields: {list(attrs.keys())}")
            # Print all non-null values
            for k, v in attrs.items():
                if v is not None:
                    print(f"      {k}: {v}")
    except Exception as e:
        print(f"    Error: {e}")

# 1. MD_LandInformation — confirmed in search results as having building permits
print("=" * 60)
print("MD_LandInformation MapServer")
print("=" * 60)
url = f"{BASE}/MD_LandInformation/MapServer"
layers = probe_mapserver(url, "MD_LandInformation")
for l in layers[:5]:
    probe_layer(url, l["id"], l.get("name", ""))

# 2. WASD Permits — probe with recent date filter to see real permit data
print("\n" + "=" * 60)
print("WASD/Permits_4_v1 with date filter")
print("=" * 60)
start = (date.today() - timedelta(days=90)).strftime("%Y-%m-%d")
wasd_url = f"{BASE}/Wasd/Permits_4_v1/MapServer"

for layer_id, label in [(0, "Doral"), (1, "Unincorporated")]:
    # Try date filter on BLDPRMIDT (permit issued date — epoch ms)
    url = f"{wasd_url}/{layer_id}/query"
    try:
        resp = session.get(url, params={
            "where":             f"BLDPRMIDT >= DATE '{start}'",
            "outFields":         "BLDPRMNO,BLDPRMTYP,BLDPRMIDT,BLDPRCSTAT,PROJDESC,PROJZIP,PROJNAME,BLDPRMSTAT",
            "resultRecordCount": 5,
            "f":                 "json",
            "orderByFields":     "BLDPRMIDT DESC",
        }, timeout=15)
        data = resp.json()
        features = data.get("features", [])
        print(f"\n  {label} ({len(features)} recent permits):")
        for feat in features:
            a = feat.get("attributes", {})
            from datetime import datetime
            issued = datetime.fromtimestamp(a.get("BLDPRMIDT", 0) / 1000).date() if a.get("BLDPRMIDT") else None
            print(f"    {a.get('BLDPRMNO')} | {a.get('BLDPRMTYP')} | issued={issued} | {a.get('PROJDESC','')[:40]}")
    except Exception as e:
        print(f"  Error: {e}")

# 3. Check MD_EAMSCodeEnforcement for permit-like data
print("\n" + "=" * 60)
print("MD_EAMSCodeEnforcement")
print("=" * 60)
probe_mapserver(f"{BASE}/MD_EAMSCodeEnforcement/MapServer", "MD_EAMSCodeEnforcement")

# 4. Also check count of WASD permits to understand volume
print("\n" + "=" * 60)
print("WASD permit volume check")
print("=" * 60)
for layer_id, label in [(0, "Doral"), (1, "Unincorporated")]:
    try:
        resp = session.get(f"{wasd_url}/{layer_id}/query", params={
            "where":           f"BLDPRMIDT >= DATE '{start}'",
            "returnCountOnly": "true",
            "f":               "json",
        }, timeout=10)
        data = resp.json()
        print(f"  {label} permits last 90 days: {data.get('count', 'unknown')}")
    except Exception as e:
        print(f"  Error: {e}")
