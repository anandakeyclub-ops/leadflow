import os
import requests
from dotenv import load_dotenv

load_dotenv()

project_id = os.getenv("CLARITY_PROJECT_ID")
token = os.getenv("CLARITY_API_TOKEN")

if not project_id or not token:
    raise SystemExit("Missing CLARITY_PROJECT_ID or CLARITY_API_TOKEN in .env")

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}

url = f"https://www.clarity.ms/export-data/api/v1/project-live-insights?projectId={project_id}"

r = requests.get(url, headers=headers)

print("Status:", r.status_code)
print("Body:")
print(r.text[:2000])