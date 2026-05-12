"""
find_miami_dade_endpoint2.py
Deeper search through MDC's ArcGIS REST services directory.
Run: python find_miami_dade_endpoint2.py
"""
import requests, json, re

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

# Step 1: List all folders in MDC GISWeb
print("=== MDC GISWeb service folders ===")
try:
    resp = session.get("https://gisweb.miamidade.gov/arcgis/rest/services", params={"f": "json"}, timeout=10)
    data = resp.json()
    folders = data.get("folders", [])
    services = data.get("services", [])
    print(f"Folders: {folders}")
    print(f"Root services: {[s['name'] for s in services[:10]]}")
except Exception as e:
    print(f"Error: {e}")

# Step 2: Check each folder for permit/building services
for folder in folders:
    try:
        resp = session.get(f"https://gisweb.miamidade.gov/arcgis/rest/services/{folder}", params={"f": "json"}, timeout=10)
        data = resp.json()
        svcs = data.get("services", [])
        hits = [s for s in svcs if any(k in s.get("name","").lower() for k in ["permit", "build", "construct"])]
        if hits:
            print(f"\nFolder '{folder}' has permit/building services:")
            for s in hits:
                print(f"  {s['name']} ({s['type']})")
    except Exception:
        continue

# Step 3: Try the open data hub API to find the dataset service URL
print("\n=== Open Data Hub API ===")
hub_urls = [
    "https://gis-mdc.opendata.arcgis.com/api/v2/datasets/MDC::building-permit",
    "https://gis-mdc.opendata.arcgis.com/api/v3/datasets/MDC::building-permit",
    "https://hub.arcgis.com/api/v2/datasets/MDC::building-permit",
    "https://hub.arcgis.com/api/v3/datasets?q=building+permit&orgId=8Pc9XBTAsYuxx9Ny",
]
for url in hub_urls:
    try:
        resp = session.get(url, timeout=10)
        print(f"{resp.status_code} {url[:80]}")
        if resp.status_code == 200:
            data = resp.json()
            # Look for serviceUrl or url fields
            text = json.dumps(data)
            urls = re.findall(r'https?://[^\s"\']+FeatureServer[^\s"\']*', text)
            if urls:
                print(f"  FeatureServer URLs found: {urls[:3]}")
            else:
                print(f"  Keys: {list(data.keys())[:8]}")
    except Exception as e:
        print(f"ERR {url[:60]}: {e}")

# Step 4: Try the Socrata open data endpoint (MDC also uses Socrata)
print("\n=== Socrata Open Data ===")
socrata_urls = [
    "https://opendata.miamidade.gov/api/catalog/v1?q=building+permit&limit=5",
    "https://opendata.miamidade.gov/api/views?q=permit&limit=5",
]
for url in socrata_urls:
    try:
        resp = session.get(url, timeout=10)
        print(f"{resp.status_code} {url[:80]}")
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                results = data.get("results", data.get("views", [data]))
            else:
                results = data
            for item in results[:3]:
                if isinstance(item, dict):
                    name = item.get("name", item.get("title", ""))
                    uid = item.get("id", item.get("uid", ""))
                    print(f"  {name} — id={uid}")
    except Exception as e:
        print(f"ERR: {e}")

# Step 5: Direct Socrata dataset query if we find an ID
print("\n=== Trying known Socrata dataset IDs for MDC permits ===")
known_ids = ["9yue-zu4q", "i6kg-pvr8", "wqhs-6a6n", "8ys5-e5eh"]
for uid in known_ids:
    try:
        url = f"https://opendata.miamidade.gov/resource/{uid}.json?$limit=2"
        resp = session.get(url, timeout=8)
        if resp.status_code == 200 and resp.json():
            print(f"✓ {uid}: {list(resp.json()[0].keys())[:8]}")
        else:
            print(f"✗ {uid}: {resp.status_code}")
    except Exception:
        print(f"✗ {uid}: error")
