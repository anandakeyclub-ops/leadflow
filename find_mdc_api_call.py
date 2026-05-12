"""
find_mdc_api_call.py
Clicks Name/Document search, performs a test search, and captures the API call.
Run: python find_mdc_api_call.py
"""
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import time, json, re

opts = Options()
opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
opts.add_argument("--window-size=1440,900")
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=opts)

BASE = "https://onlineservices.miamidadeclerk.gov/officialrecords"

print("Loading MDC official records...")
driver.get(f"{BASE}/")
time.sleep(8)

# Click Name/Document in left nav
print("Clicking Name/Document...")
try:
    el = driver.find_element(By.XPATH, "//*[contains(text(),'Name/Document')]")
    driver.execute_script("arguments[0].click();", el)
    time.sleep(4)
    driver.save_screenshot("data/docs/mdc_name_doc.png")
    print(f"  URL: {driver.current_url}")
except Exception as e:
    print(f"  Error: {e}")

# Dump all inputs and selects now
print("\n--- Inputs after clicking Name/Document ---")
for el in driver.find_elements(By.TAG_NAME, "input"):
    try:
        iid = el.get_attribute("id") or ""
        iname = el.get_attribute("name") or ""
        itype = el.get_attribute("type") or "text"
        iplaceholder = el.get_attribute("placeholder") or ""
        if iid or iname or iplaceholder:
            print(f"  id={iid!r} name={iname!r} type={itype!r} placeholder={iplaceholder!r}")
    except Exception:
        pass

print("\n--- Selects after clicking Name/Document ---")
for sel in driver.find_elements(By.TAG_NAME, "select"):
    try:
        sid = sel.get_attribute("id") or "(no id)"
        opts_list = [f"{o.get_attribute('value')!r}:{o.text!r}" 
                     for o in sel.find_elements(By.TAG_NAME, "option") if o.text.strip()]
        print(f"  id={sid}: {opts_list[:10]}")
    except Exception:
        pass

# Try filling a search and intercepting the API call
print("\nAttempting test search...")
# Fill name field with "SMITH" (common name)
for placeholder in ["Last Name", "Name", "Party Name", "Grantor"]:
    try:
        el = driver.find_element(By.XPATH, f"//input[@placeholder='{placeholder}']")
        el.send_keys("SMITH")
        print(f"  Filled '{placeholder}' with SMITH")
        break
    except Exception:
        continue

# Fill document type if there's a dropdown
for text in ["Lien", "LIEN", "Tax Lien", "Judgment"]:
    try:
        sel_el = driver.find_element(By.XPATH, "//select[.//option[contains(text(),'Lien') or contains(text(),'lien')]]")
        Select(sel_el).select_by_visible_text(text)
        print(f"  Selected doc type: {text}")
        break
    except Exception:
        continue

# Click search
for xpath in ["//button[contains(text(),'Search')]", "//input[@type='submit']", "//button[@type='submit']"]:
    try:
        btn = driver.find_element(By.XPATH, xpath)
        driver.execute_script("arguments[0].click();", btn)
        print(f"  Clicked search via {xpath}")
        time.sleep(5)
        break
    except Exception:
        continue

driver.save_screenshot("data/docs/mdc_after_search.png")
print(f"  URL after search: {driver.current_url}")

# Capture all API calls made
print("\n--- API calls captured ---")
logs = driver.get_log("performance")
api_calls = set()
for log in logs:
    try:
        msg = json.loads(log["message"])
        method = msg.get("message", {}).get("method", "")
        if "Network.requestWillBeSent" in method:
            req = msg["message"]["params"].get("request", {})
            url = req.get("url", "")
            if "officialrecords/api" in url:
                method_type = req.get("method", "GET")
                post_data = req.get("postData", "")
                key = f"{method_type} {url}"
                if key not in api_calls:
                    api_calls.add(key)
                    print(f"  {method_type} {url}")
                    if post_data:
                        print(f"    Body: {post_data[:200]}")
    except Exception:
        pass

driver.quit()
print("\nDone. Check data/docs/mdc_name_doc.png and mdc_after_search.png")
