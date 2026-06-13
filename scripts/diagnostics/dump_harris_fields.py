import requests
import re
import os
import sys
from dotenv import load_dotenv
load_dotenv()

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
})

LOGIN_URL  = "https://www.cclerk.hctx.net/applications/websearch/Login.aspx"
SEARCH_URL = "https://www.cclerk.hctx.net/applications/websearch/RP.aspx"

# Load login page
print("Loading login page...")
r = session.get(LOGIN_URL, timeout=20)
print(f"Login page: {r.status_code} ({len(r.text)} chars)")

# Extract ViewState
def extract(html, field):
    patterns = [
        f'name="{field}"\\s+value="([^"]*)"',
        f'id="{field}"[^>]*value="([^"]*)"',
        f'value="([^"]*)"[^>]*name="{field}"',
    ]
    for p in patterns:
        m = re.search(p, html, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""

vs  = extract(r.text, "__VIEWSTATE")
vsg = extract(r.text, "__VIEWSTATEGENERATOR")
ev  = extract(r.text, "__EVENTVALIDATION")
print(f"ViewState found: {len(vs)} chars")
print(f"EventValidation found: {len(ev)} chars")

# Find login field names
all_fields = re.findall(r'name="([^"]+)"', r.text)
print(f"\nLogin page fields: {all_fields[:20]}")

# Submit login
email    = os.getenv("HARRIS_CLERK_EMAIL", "")
password = os.getenv("HARRIS_CLERK_PASSWORD", "")
print(f"\nLogging in as: {email}")

# Try to find actual username/password field names
user_field = next((f for f in all_fields if "user" in f.lower() or "email" in f.lower()), "ctl00$ContentPlaceHolder1$txtUserName")
pass_field = next((f for f in all_fields if "pass" in f.lower() or "pwd" in f.lower()), "ctl00$ContentPlaceHolder1$txtPassword")
btn_field  = next((f for f in all_fields if "login" in f.lower() or "submit" in f.lower() or "btn" in f.lower()), "ctl00$ContentPlaceHolder1$btnLogin")

print(f"Username field: {user_field}")
print(f"Password field: {pass_field}")
print(f"Button field  : {btn_field}")

login_data = {
    "__VIEWSTATE":          vs,
    "__VIEWSTATEGENERATOR": vsg,
    "__EVENTVALIDATION":    ev,
    "__EVENTTARGET":        "",
    "__EVENTARGUMENT":      "",
    user_field: email,
    pass_field: password,
    btn_field:  "Login",
}

r = session.post(LOGIN_URL, data=login_data, timeout=20, allow_redirects=True)
print(f"\nLogin result: {r.status_code}")
print(f"Contains 'logout': {'logout' in r.text.lower()}")
print(f"Contains 'log out': {'log out' in r.text.lower()}")
print(f"Contains 'invalid': {'invalid' in r.text.lower()}")

# Load search page
print("\nLoading search page...")
r = session.get(SEARCH_URL, timeout=20)
print(f"Search page: {r.status_code} ({len(r.text)} chars)")

# Check if we got redirected to login
if "txtUserName" in r.text or "txtPassword" in r.text or "btnLogin" in r.text:
    print("WARNING: Got redirected back to login page — login may have failed")
else:
    print("OK: Search page loaded (not login page)")

# Save HTML
with open("harris_search_page.html", "w", encoding="utf-8") as f:
    f.write(r.text)
print("Saved: harris_search_page.html")

# Print all field names
all_fields = re.findall(r'name="([^"]+)"', r.text)
print(f"\nAll form fields on search page:")
for f in all_fields:
    print(f"  {f}")

# Print select options
selects = re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', r.text, re.DOTALL | re.IGNORECASE)
for sel_name, sel_content in selects:
    options = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)', sel_content, re.IGNORECASE)
    print(f"\nDropdown '{sel_name}' options:")
    for val, label in options[:20]:
        print(f"  value='{val}' label='{label.strip()}'")
