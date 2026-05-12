"""
find_mdc_search_api.py
Intercepts network requests from the MDC official records SPA
to find the underlying search API endpoint.
Run: python find_mdc_search_api.py
"""
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import time, json

opts = Options()
opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
opts.add_argument("--window-size=1440,900")
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=opts)

BASE = "https://onlineservices.miamidadeclerk.gov/officialrecords"

print("Loading MDC official records...")
driver.get(f"{BASE}/")
time.sleep(10)

driver.save_screenshot("data/docs/mdc_spa_01.png")
print(f"URL: {driver.current_url}")
print(f"Title: {driver.title}")

# Check page source for API URLs
src = driver.page_source
import re
api_urls = re.findall(r'https?://[^\s"\'<>]+api[^\s"\'<>]*', src, re.I)
print(f"\nAPI URLs in page source:")
for u in list(set(api_urls))[:20]:
    print(f"  {u}")

# Look for any search-related text or buttons
print("\nAll visible text elements:")
for el in driver.find_elements(By.XPATH, "//*[text()]"):
    try:
        text = el.text.strip()
        tag = el.tag_name
        if text and len(text) < 100 and tag not in ("script", "style"):
            print(f"  <{tag}>: {text}")
    except Exception:
        pass

# Check performance logs for network requests
print("\nNetwork requests captured:")
logs = driver.get_log("performance")
for log in logs:
    try:
        msg = json.loads(log["message"])
        method = msg.get("message", {}).get("method", "")
        if "Network.requestWillBeSent" in method:
            req = msg["message"]["params"].get("request", {})
            url = req.get("url", "")
            if any(kw in url for kw in ["api", "search", "record", "lien", "document"]):
                print(f"  {req.get('method','GET')} {url[:120]}")
    except Exception:
        pass

# Try clicking any visible element and capture more requests
print("\nLooking for search entry point...")
for xpath in [
    "//button", "//a[contains(@href,'search')]",
    "//*[contains(text(),'Search')]",
    "//*[contains(text(),'Document')]",
    "//*[contains(@class,'search')]",
]:
    els = driver.find_elements(By.XPATH, xpath)
    if els:
        for el in els[:3]:
            try:
                text = el.text.strip() or el.get_attribute("class") or ""
                href = el.get_attribute("href") or ""
                print(f"  Found: <{el.tag_name}> '{text}' href={href[:60]}")
            except Exception:
                pass

driver.save_screenshot("data/docs/mdc_spa_02.png")
driver.quit()
print("\nDone. Check data/docs/mdc_spa_01.png and mdc_spa_02.png")