"""
Debug script — tests Harris County search with correct field names
and saves the raw HTML response to inspect.
"""
import requests
import re
import os
from datetime import date, timedelta
from dotenv import load_dotenv
load_dotenv()

EMAIL    = os.getenv("HARRIS_CLERK_EMAIL", "")
PASSWORD = os.getenv("HARRIS_CLERK_PASSWORD", "")

LOGIN_URL  = "https://www.cclerk.hctx.net/applications/websearch/Login.aspx"
SEARCH_URL = "https://www.cclerk.hctx.net/applications/websearch/RP.aspx"

session = requests.Session()
session.headers.update({
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
})

def extract(html, field):
    for p in [
        f'name="{field}"\\s+value="([^"]*)"',
        f'id="{field}"[^>]*value="([^"]*)"',
        f'value="([^"]*)"[^>]*name="{field}"',
    ]:
        m = re.search(p, html, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""

# ── Step 1: Login ─────────────────────────────────────────────────────────────
print("=== Step 1: Login ===")
r = session.get(LOGIN_URL, timeout=20)
vs  = extract(r.text, "__VIEWSTATE")
vsg = extract(r.text, "__VIEWSTATEGENERATOR")
ev  = extract(r.text, "__EVENTVALIDATION")
print(f"ViewState: {len(vs)} chars")

r = session.post(LOGIN_URL, data={
    "__VIEWSTATE":          vs,
    "__VIEWSTATEGENERATOR": vsg,
    "__EVENTVALIDATION":    ev,
    "__VIEWSTATEENCRYPTED": "",
    "__EVENTTARGET":        "",
    "__EVENTARGUMENT":      "",
    "ctl00$ContentPlaceHolder1$txtUserName": EMAIL,
    "ctl00$ContentPlaceHolder1$txtPassword": PASSWORD,
    "ctl00$ContentPlaceHolder1$btnLogin":    "Login",
}, timeout=20, allow_redirects=True)
print(f"Login: {r.status_code} | URL after: {r.url}")
print(f"Login form still present: {'txtUserName' in r.text}")

# ── Step 2: Load search page ──────────────────────────────────────────────────
print("\n=== Step 2: Load Search Page ===")
r = session.get(SEARCH_URL, timeout=20)
print(f"Search page: {r.status_code} | {len(r.text)} chars | URL: {r.url}")

vs  = extract(r.text, "__VIEWSTATE")
vsg = extract(r.text, "__VIEWSTATEGENERATOR")
ev  = extract(r.text, "__EVENTVALIDATION")
print(f"New ViewState: {len(vs)} chars")

# ── Step 3: Submit search ─────────────────────────────────────────────────────
print("\n=== Step 3: Submit Search ===")
date_from = "04/01/2026"
date_to   = "05/26/2026"
grantee   = "INTERNAL REVENUE SERVICE"

print(f"Searching: grantee='{grantee}' from={date_from} to={date_to}")

form_data = {
    "__VIEWSTATE":          vs,
    "__VIEWSTATEGENERATOR": vsg,
    "__EVENTVALIDATION":    ev,
    "__VIEWSTATEENCRYPTED": "",
    "__EVENTTARGET":        "",
    "__EVENTARGUMENT":      "",
    "ctl00$ContentPlaceHolder1$txtFileNo":     "",
    "ctl00$ContentPlaceHolder1$txtFilmCd":     "",
    "ctl00$ContentPlaceHolder1$txtFrom":       date_from,
    "ctl00$ContentPlaceHolder1$txtTo":         date_to,
    "ctl00$ContentPlaceHolder1$txtOR":         "",
    "ctl00$ContentPlaceHolder1$txtEE":         grantee,
    "ctl00$ContentPlaceHolder1$txtNameTee":    "",
    "ctl00$ContentPlaceHolder1$txtDesc":       "",
    "ctl00$ContentPlaceHolder1$txtInstrument": "",
    "ctl00$ContentPlaceHolder1$txtVolNo":      "",
    "ctl00$ContentPlaceHolder1$txtPageNo":     "",
    "ctl00$ContentPlaceHolder1$txtSection":    "",
    "ctl00$ContentPlaceHolder1$txtLot":        "",
    "ctl00$ContentPlaceHolder1$txtBlock":      "",
    "ctl00$ContentPlaceHolder1$txtUnit":       "",
    "ctl00$ContentPlaceHolder1$txtAbstract":   "",
    "ctl00$ContentPlaceHolder1$txtOutLot":     "",
    "ctl00$ContentPlaceHolder1$txtTract":      "",
    "ctl00$ContentPlaceHolder1$txtReserve":    "",
    "ctl00$ContentPlaceHolder1$btnSearch":     "Search",
}

r = session.post(SEARCH_URL, data=form_data,
                 timeout=45, allow_redirects=True)
print(f"Search result: {r.status_code} | {len(r.text)} chars | URL: {r.url}")

# Save response
with open("harris_search_result.html", "w", encoding="utf-8") as f:
    f.write(r.text)
print("Saved: harris_search_result.html")

# ── Step 4: Inspect response ──────────────────────────────────────────────────
print("\n=== Step 4: Inspect Response ===")

# Check for common result indicators
checks = [
    ("RP-2026",          "File numbers present"),
    ("INTERNAL REVENUE", "IRS grantee present"),
    ("Grantor",          "Grantor label present"),
    ("T/L",              "Tax Lien type present"),
    ("no records",       "No records message"),
    ("0 records",        "Zero records message"),
    ("No results",       "No results message"),
    ("error",            "Error message"),
    ("login",            "Login redirect"),
]

for term, label in checks:
    found = term.lower() in r.text.lower()
    print(f"  {'✅' if found else '❌'} {label}: {found}")

# Print first 3000 chars of response body (stripped of most HTML)
clean = re.sub(r'<script[^>]*>.*?</script>', '', r.text, flags=re.DOTALL)
clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL)
clean = re.sub(r'<[^>]+>', ' ', clean)
clean = re.sub(r'\s+', ' ', clean).strip()

print(f"\nFirst 2000 chars of cleaned response:")
print(clean[:2000])
print("\n...")
print(f"Last 500 chars:")
print(clean[-500:])
