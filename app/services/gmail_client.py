import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

CLIENT_SECRETS_FILE = os.getenv(
    "GOOGLE_CLIENT_SECRETS_FILE",
    str(BASE_DIR / "credentials.json")
)
TOKEN_FILE = os.getenv(
    "GOOGLE_GMAIL_TOKEN_FILE",
    str(BASE_DIR / "token_gmail.json")
)


def get_gmail_service():
    creds: Optional[Credentials] = None

    if Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)

    Path(TOKEN_FILE).write_text(creds.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=creds)


def ensure_label(service, label_name: str) -> str:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label.get("name") == label_name:
            return label["id"]

    created = service.users().labels().create(
        userId="me",
        body={
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()

    return created["id"]