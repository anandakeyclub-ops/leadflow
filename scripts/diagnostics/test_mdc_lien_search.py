"""
test_mdc_lien_search.py
Uses Selenium to establish browser session then calls API directly.
Run: python test_mdc_lien_search.py
"""
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
import requests, time, json

BASE = "https://onlineservices.miamidadeclerk.gov/officialrecords"

# Step 1: Use Selenium to load the page and get session cookies
print("Loading MDC in browser to get session cookies...")
opts = Options()
opts.add_argument("--window-size=1440,900")
opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=opts)

driver.get(f"{BASE}/")
time.sleep(6)

# Click Name/Document to trigger the search form API calls
try:
    el = driver.find_element(By.XPATH, "//*[contains(text(),'Name/Document')]")
    driver.execute_script("arguments[0].click();", el)
    time.sleep(3)
    print("  Clicked Name/Document")
except Exception as e:
    print(f"  Nav click error: {e}")

# Extract cookies from Selenium
selenium_cookies = driver.get_cookies()
print(f"  Cookies: {[c['name'] for c in selenium_cookies]}")

# Step 2: Transfer cookies to requests session
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Origin":     BASE,
    "Referer":    f"{BASE}/",
})
for cookie in selenium_cookies:
    session.cookies.set(cookie["name"], cookie["value"])

# Also get the anti-forgery token if present
for cookie in selenium_cookies:
    if "token" in cookie["name"].lower() or "xsrf" in cookie["name"].lower() or "csrf" in cookie["name"].lower():
        session.headers["X-XSRF-TOKEN"] = cookie["value"]
        session.headers["X-CSRF-TOKEN"]  = cookie["value"]
        print(f"  CSRF token found: {cookie['name']}")

# Step 3: Check network logs for the exact POST format the browser used
print("\nCapturing network requests...")
logs = driver.get_log("performance")
for log in logs:
    try:
        msg = json.loads(log["message"])
        method = msg.get("message", {}).get("method", "")
        if "Network.requestWillBeSent" in method:
            req = msg["message"]["params"].get("request", {})
            url = req.get("url", "")
            if "standardsearch" in url or "documentType" in url:
                print(f"  Browser called: {req.get('method')} {url[:120]}")
                if req.get("postData"):
                    print(f"  Body: {req['postData'][:300]}")
                if req.get("headers"):
                    relevant = {k:v for k,v in req["headers"].items() 
                               if k.lower() in ["content-type","x-xsrf-token","x-csrf-token","authorization","cookie"]}
                    if relevant:
                        print(f"  Headers: {relevant}")
    except Exception:
        pass

driver.quit()

# Step 4: Try the search with session cookies
print("\n=== Testing search with browser cookies ===")
start = "01/29/2026"
end   = "04/29/2026"

for code in ["LIEN - LIE", "LIE", "ANY LIEN JUDGMENT - LNJUD", "FEDERAL TAX LIEN  - FTL", ""]:
    params = {
        "partyName":     "",
        "dateRangeFrom": start,
        "dateRangeTo":   end,
        "documentType":  code,
        "searchT":       "",
        "firstQuery":    "y",
        "searchtype":    "Name/Document",
    }
    resp = session.post(f"{BASE}/api/home/standardsearch", params=params, json={}, timeout=30)
    data = resp.json()
    valid = data.get("isValidSearch")
    qs    = data.get("qs")
    print(f"  {code!r:<35} valid={valid} qs={'YES' if qs else 'NO'} status={resp.status_code}")
    if qs:
        resp2 = session.get(f"{BASE}/api/SearchResults/downloadcsv", params={"qs": qs}, timeout=60)
        lines = resp2.text.count('\n')
        print(f"    → CSV: {lines} lines")
        if lines > 1:
            print(f"    Header: {resp2.text.split(chr(10))[0][:120]}")
            print(f"    Row 1:  {resp2.text.split(chr(10))[1][:120]}")
        break