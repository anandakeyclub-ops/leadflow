"""
find_miami_dade_endpoint3.py
Probes the confirmed MDC services for building permit data.
Run: python find_miami_dade_endpoint3.py
"""
import requests, json, re

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

def probe_mapserver(url, label):
    print(f"\n=== {label} ===")
    print(f"URL: {url}")
    try:
        resp = session.get(url, params={"f": "json"}, timeout=10)
        data = resp.json()
        layers = data.get("layers", [])
        print(f"Layers ({len(layers)}):")
        for l in layers:
            print(f"  [{l.get('id')}] {l.get('name')} — type={l.get('type')}")
        return layers
    except Exception as e:
        print(f"Error: {e}")
        return []

def probe_layer(url, layer_id, label):
    query_url = f"{url}/{layer_id}/query"
    print(f"\n--- {label} layer {layer_id} ---")
    try:
        resp = session.get(query_url, params={
            "where": "1=1", "resultRecordCount": 2,
            "outFields": "*", "f": "json"
        }, timeout=10)
        data = resp.json()
        if "error" in data:
            print(f"  Error: {data['error'].get('message','')}")
            return
        features = data.get("features", [])
        print(f"  Features returned: {len(features)}")
        if features:
            attrs = features[0].get("attributes", {})
            print(f"  Field names: {list(attrs.keys())}")
            # Check for permit-like fields
            permit_fields = [k for k in attrs if any(kw in k.lower() for kw in
                ["permit", "owner", "address", "issued", "date", "folio", "status"])]
            if permit_fields:
                print(f"  Permit-like fields: {permit_fields}")
                print(f"  Sample values:")
                for k in permit_fields[:6]:
                    print(f"    {k}: {attrs[k]}")
    except Exception as e:
        print(f"  Error: {e}")

# 1. Check WASD Permits service
wasd_url = "https://gisweb.miamidade.gov/arcgis/rest/services/Wasd/Permits_4_v1/MapServer"
layers = probe_mapserver(wasd_url, "WASD/Permits_4_v1")
for l in layers[:5]:
    probe_layer(wasd_url, l["id"], l.get("name",""))

# 2. Check root services that might have permits
print("\n=== Checking root services for permit data ===")
root_permit_candidates = [
    "MD_ComparableSales",  # might have permit info
]
# Also check all root services quickly
try:
    resp = session.get("https://gisweb.miamidade.gov/arcgis/rest/services", params={"f": "json"}, timeout=10)
    data = resp.json()
    all_services = data.get("services", [])
    for svc in all_services:
        name = svc.get("name", "")
        stype = svc.get("type", "")
        print(f"  {name} ({stype})")
except Exception as e:
    print(f"Error: {e}")

# 3. Check EnerGov folder (MDC uses EnerGov for permits)
print("\n=== EnerGov folder ===")
try:
    resp = session.get("https://gisweb.miamidade.gov/arcgis/rest/services/EnerGov", params={"f": "json"}, timeout=10)
    data = resp.json()
    services = data.get("services", [])
    print(f"EnerGov services: {[s['name'] for s in services]}")
    for svc in services[:5]:
        svc_url = f"https://gisweb.miamidade.gov/arcgis/rest/services/{svc['name']}/{svc['type']}"
        probe_mapserver(svc_url, svc['name'])
except Exception as e:
    print(f"EnerGov error: {e}")

# 4. Check RER folder (Department of Regulatory and Economic Resources — issues building permits)
print("\n=== RER folder ===")
try:
    resp = session.get("https://gisweb.miamidade.gov/arcgis/rest/services/RER", params={"f": "json"}, timeout=10)
    data = resp.json()
    services = data.get("services", [])
    print(f"RER services: {[s['name'] for s in services]}")
    for svc in services[:8]:
        name = svc['name'].split('/')[-1]
        if any(kw in name.lower() for kw in ["permit", "build", "construct", "rer"]):
            svc_url = f"https://gisweb.miamidade.gov/arcgis/rest/services/{svc['name']}/{svc['type']}"
            layers = probe_mapserver(svc_url, svc['name'])
            for l in layers[:3]:
                probe_layer(svc_url, l["id"], l.get("name",""))
except Exception as e:
    print(f"RER error: {e}")
