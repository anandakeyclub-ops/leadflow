import os
import pickle

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = ['https://www.googleapis.com/auth/analytics.readonly']

TOKEN_PATH = 'data/credentials/ga4-token.pickle'
CLIENT_SECRET = 'data/credentials/ga4-oauth.json'

creds = None

if os.path.exists(TOKEN_PATH):
    with open(TOKEN_PATH, 'rb') as token:
        creds = pickle.load(token)

if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(
            CLIENT_SECRET,
            SCOPES
        )
        creds = flow.run_local_server(port=0)

    with open(TOKEN_PATH, 'wb') as token:
        pickle.dump(creds, token)

print("GA4 OAuth ready!")