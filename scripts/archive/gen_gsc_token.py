"""
gen_gsc_token.py — re-authorize Google Search Console access.

Run this when gsc_monitor / weekly_intelligence / page_refresh_detector fail with
`invalid_grant: Token has been expired or revoked`. It opens a browser, you sign in
with the Google account that owns the taxcasereview.org GSC property, grant access,
and a fresh token (with a new refresh token) is written to data/credentials/gsc-token.json.

  python scripts/archive/gen_gsc_token.py

NOTE: if the OAuth consent screen is in "Testing" status, Google expires the refresh
token after 7 days and this breaks weekly. Publish the app ("In production") in the
Google Cloud console to stop the recurrence.
"""
from pathlib import Path
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

# Run from anywhere — anchor to the repo root (this file is scripts/archive/).
REPO_ROOT   = Path(__file__).resolve().parents[2]
CLIENT_FILE = REPO_ROOT / "data" / "credentials" / "gsc-oauth-client.json"
TOKEN_FILE  = REPO_ROOT / "data" / "credentials" / "gsc-token.json"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
GSC_SITE_URL = "sc-domain:taxcasereview.org"

with open(CLIENT_FILE) as f:
    client_data = json.load(f)
print("Client type:", list(client_data.keys()))

flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
# access_type=offline + prompt=consent forces Google to return a NEW refresh token,
# even when re-authorizing the same account (otherwise it may omit refresh_token).
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

if not creds.refresh_token:
    sys.exit("ERROR: no refresh_token returned — re-run and make sure you click "
             "through the consent screen (not 'this account is already connected').")

TOKEN_FILE.write_text(creds.to_json())
print(f"Token saved to {TOKEN_FILE}")

# Self-verify: hit the GSC API once so you know it works before relying on the cron jobs.
try:
    from googleapiclient.discovery import build
    service = build("searchconsole", "v1", credentials=creds)
    sites = service.sites().list().execute().get("siteEntry", [])
    urls = [s.get("siteUrl") for s in sites]
    print(f"\n✅ GSC API reachable. Properties on this account: {urls}")
    if GSC_SITE_URL in urls:
        print(f"✅ {GSC_SITE_URL} is accessible — gsc_monitor/weekly_intelligence will work.")
    else:
        print(f"⚠  {GSC_SITE_URL} not in the list above — make sure you signed in with "
              f"the account that has access to that property.")
except Exception as e:
    print(f"⚠  Token saved but verification call failed: {e}")
