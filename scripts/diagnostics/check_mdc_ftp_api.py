"""
check_mdc_ftp_api.py
Checks what bulk data folders are available via the MDC FTP API.
Add MDC_AUTH_KEY to your .env file first.
Run: python check_mdc_ftp_api.py
"""
import os
import sys
sys.path.insert(0, ".")

import requests
from dotenv import load_dotenv
load_dotenv()

AUTH_KEY = os.getenv("MDC_AUTH_KEY", "")
if not AUTH_KEY:
    print("ERROR: MDC_AUTH_KEY not set in .env file")
    sys.exit(1)

BASE = "https://www2.miamidadeclerk.gov/Developers"
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

print(f"Using AuthKey: {AUTH_KEY[:4]}...")

# Test 1: List available folders
print("\n=== Available FTP folders ===")
known_folders = [
    "OfficialRecords",
    "Official_Records", 
    "OR",
    "Liens",
    "Judgments",
    "Daily",
    "Weekly",
    "LienJudgment",
]
for folder in known_folders:
    try:
        resp = session.get(
            f"{BASE}/api/FTPapi",
            params={"folderListName": folder, "AuthKey": AUTH_KEY},
            timeout=15
        )
        data = resp.json()
        status = data.get("Status", "")
        desc = data.get("StatusDesc", "")
        balance = data.get("UnitsBalance", "?")
        files = data.get("FileList", data.get("Files", []))
        if status == "Success" or files:
            print(f"  ✓ {folder}: status={status} files={len(files) if isinstance(files, list) else files}")
            if isinstance(files, list):
                for f in files[:5]:
                    print(f"    {f}")
        else:
            print(f"  ✗ {folder}: {status} — {desc}")
        print(f"    Balance: {balance} units")
    except Exception as e:
        print(f"  ERR {folder}: {e}")

# Test 2: Try the OfficialRecords API with a known CFN
# CFN year 2026, sequence R100000 (test)
print("\n=== OfficialRecords API test ===")
try:
    resp = session.get(
        f"{BASE}/api/OfficialRecords",
        params={"parameter1": "2026", "parameter2": "R100000", "authKey": AUTH_KEY},
        timeout=15
    )
    data = resp.json()
    print(f"Status: {data.get('Status')}")
    print(f"StatusDesc: {data.get('StatusDesc')}")
    print(f"UnitsBalance: {data.get('UnitsBalance')}")
    records = data.get("OfficialRecordList", [])
    print(f"Records: {len(records)}")
    if records:
        r = records[0]
        print(f"Sample: DOC_TYPE={r.get('DOC_TYPE')} FIRST_PARTY={r.get('FIRST_PARTY')} REC_DATE={r.get('REC_DATE')}")
except Exception as e:
    print(f"Error: {e}")

# Test 3: Check account info / what subscriptions are active
print("\n=== Account info ===")
try:
    resp = session.get(
        f"{BASE}/api/FTPapi",
        params={"folderName": "OfficialRecords", "AuthKey": AUTH_KEY},
        timeout=15
    )
    print(f"Status: {resp.status_code}")
    data = resp.json()
    print(json.dumps(data, indent=2, default=str)[:1000])
except Exception as e:
    print(f"Error: {e}")
