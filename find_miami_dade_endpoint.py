"""
find_miami_dade_endpoint.py
Discovers the working Miami-Dade building permit ArcGIS endpoint.
Run: python find_miami_dade_endpoint.py
"""
import requests

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

# Try to get the FeatureServer URL from the Open Data Hub API
# The dataset ID is in the URL: MDC::building-permit
# ArcGIS Online item ID can be found via the Hub API

candidates = [
    # From the geoservice page hint — try common MDC FeatureServer IDs
    "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/BuildingPermit/FeatureServer/0/query",
    "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/Building_Permit/FeatureServer/0/query",
    "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/MDC_BuildingPermit/FeatureServer/0/query",
    # Try the MDC GIS server directly
    "https://gisweb.miamidade.gov/arcgis/rest/services/Building/BuildingPermits/FeatureServer/0/query",
    "https://gisweb.miamidade.gov/arcgis/rest/services/Building/BuildingPermit/FeatureServer/0/query",
    "https://gisweb.miamidade.gov/arcgis/rest/services/BuildingPermit/FeatureServer/0/query",
    "https://gisweb.miamidade.gov/arcgis/rest/services/BuildingPermit/MapServer/0/query",
    # Try the open data services endpoint
    "https://gis-mdc.opendata.arcgis.com/api/v2/datasets/MDC::building-permit/layers/0/query",
    # Hub API to get service URL
    "https://opendata.arcgis.com/api/v2/datasets?q=building+permit+miami-dade&f=json",
]

print("Testing Miami-Dade ArcGIS endpoints...\n")
for url in candidates:
    try:
        # For query endpoints, try a simple 1=1 query
        if "query" in url:
            resp = session.get(url, params={"where": "1=1", "resultRecordCount": 1, "outFields": "*", "f": "json"}, timeout=10)
        else:
            resp = session.get(url, params={"f": "json"}, timeout=10)
        
        print(f"{'✓' if resp.status_code == 200 else '✗'} {resp.status_code} {url[:80]}")
        
        if resp.status_code == 200:
            data = resp.json()
            if "features" in data:
                features = data["features"]
                print(f"  → {len(features)} features returned")
                if features:
                    attrs = features[0].get("attributes", {})
                    print(f"  → Field names: {list(attrs.keys())[:10]}")
            elif "error" in data:
                print(f"  → Error: {data['error'].get('message','')}")
            elif "services" in data or "layers" in data or "datasets" in data:
                print(f"  → Found services/layers/datasets")
                print(f"  → Keys: {list(data.keys())[:5]}")
    except Exception as e:
        print(f"✗ ERR  {url[:80]}")
        print(f"       {e}")

# Also try the Hub API to find the service URL
print("\n--- Trying Hub API ---")
try:
    resp = session.get(
        "https://opendata.arcgis.com/api/v2/datasets",
        params={"q": "building permit miami-dade county", "f": "json", "page[size]": 5},
        timeout=10
    )
    if resp.status_code == 200:
        data = resp.json()
        for item in data.get("data", [])[:5]:
            attrs = item.get("attributes", {})
            name = attrs.get("name", "")
            url = attrs.get("url", "") or attrs.get("downloadLink", "")
            layer_id = attrs.get("layerId", "")
            print(f"  {name}: {url[:80]}")
except Exception as e:
    print(f"Hub API error: {e}")

# Try Miami-Dade's own REST services directory
print("\n--- MDC REST Services directory ---")
try:
    resp = session.get("https://gisweb.miamidade.gov/arcgis/rest/services", params={"f": "json"}, timeout=10)
    print(f"GISWeb status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        services = data.get("services", [])
        building = [s for s in services if "build" in s.get("name","").lower() or "permit" in s.get("name","").lower()]
        print(f"Building/permit services found: {building[:5]}")
except Exception as e:
    print(f"GISWeb error: {e}")
