import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.services.gmail_client import get_gmail_service

service = get_gmail_service()

print("Connected. Checking Gmail profile...")
profile = service.users().getProfile(userId="me").execute()
print("Email:", profile.get("emailAddress"))

print("Checking inbox count...")
resp = service.users().messages().list(userId="me", maxResults=5).execute()
print("Messages key present:", "messages" in resp)
print("Result:", resp)