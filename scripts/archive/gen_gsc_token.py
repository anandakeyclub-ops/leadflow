from google_auth_oauthlib.flow import InstalledAppFlow
from pathlib import Path
import json

CLIENT_FILE = Path("data/credentials/gsc-oauth-client.json")
TOKEN_FILE  = Path("data/credentials/gsc-token.json")
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

# Check client file exists and what type it is
with open(CLIENT_FILE) as f:
    client_data = json.load(f)
print("Client type:", list(client_data.keys()))

flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
creds = flow.run_local_server(port=0)
TOKEN_FILE.write_text(creds.to_json())
print(f"Token saved to {TOKEN_FILE}")