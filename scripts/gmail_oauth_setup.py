import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.services.gmail_client import get_gmail_service


if __name__ == "__main__":
    service = get_gmail_service()
    profile = service.users().getProfile(userId="me").execute()
    print("Gmail OAuth connected.")
    print("Email:", profile.get("emailAddress"))