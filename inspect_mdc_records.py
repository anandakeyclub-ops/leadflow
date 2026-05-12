"""
inspect_mdc_records.py
Inspects the Miami-Dade official records search form (React SPA).
Run: python inspect_mdc_records.py
"""
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import time

opts = Options()
opts.add_argument("--window-size=1440,900")
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=opts)

BASE = "https://onlineservices.miamidadeclerk.gov/officialrecords"

print(f"Loading: {BASE}/")
driver.get(f"{BASE}/")
time.sleep(8)  # React SPA needs time

print(f"Title: {driver.title}")
print(f"URL: {driver.current_url}")

# Save screenshot 1 — initial load
driver.save_screenshot("data/docs/mdc_01_loaded.png")
print("Screenshot: mdc_01_loaded.png")

# Look for navigation links to Standard Search
print("\n--- All links ---")
for a in driver.find_elements(By.TAG_NAME, "a"):
    try:
        text = a.text.strip()
        href = a.get_attribute("href") or ""
        if text:
            print(f"  '{text}' → {href[:80]}")
    except Exception:
        pass

# Try clicking Standard Search or Search link
for text in ["Standard Search", "Search Records", "Search", "Official Records"]:
    try:
        el = driver.find_element(By.XPATH, f"//*[contains(text(),'{text}')]")
        print(f"\nClicking: '{text}'")
        driver.execute_script("arguments[0].click();", el)
        time.sleep(5)
        driver.save_screenshot(f"data/docs/mdc_02_after_{text.replace(' ','_')}.png")
        print(f"  URL now: {driver.current_url}")
        
        # Check for inputs
        inputs = driver.find_elements(By.TAG_NAME, "input")
        selects = driver.find_elements(By.TAG_NAME, "select")
        print(f"  Inputs: {len(inputs)}  Selects: {len(selects)}")
        if inputs or selects:
            break
    except Exception:
        continue

# Try direct hash routes that React SPAs use
hash_routes = ["#/search", "#/standard-search", "#StandardSearch", "#/DocumentType"]
for route in hash_routes:
    try:
        url = f"{BASE}/{route}"
        print(f"\nTrying: {url}")
        driver.get(url)
        time.sleep(5)
        inputs = driver.find_elements(By.TAG_NAME, "input")
        selects = driver.find_elements(By.TAG_NAME, "select")
        print(f"  Inputs: {len(inputs)}  Selects: {len(selects)}")
        if inputs or selects:
            driver.save_screenshot(f"data/docs/mdc_route_{route.replace('#','').replace('/','_')}.png")
            print(f"  FOUND FORM at {url}")
            break
    except Exception as e:
        print(f"  Error: {e}")

# Final dump
print("\n--- Final page inputs ---")
for el in driver.find_elements(By.TAG_NAME, "input"):
    try:
        print(f"  id={el.get_attribute('id')!r} name={el.get_attribute('name')!r} type={el.get_attribute('type')!r}")
    except Exception:
        pass

print("\n--- Final page selects ---")
for sel in driver.find_elements(By.TAG_NAME, "select"):
    try:
        sid = sel.get_attribute("id") or "(no id)"
        opts_list = [f"{o.get_attribute('value')!r}:{o.text!r}" for o in sel.find_elements(By.TAG_NAME, "option") if o.text.strip()]
        print(f"  id={sid}: {opts_list[:8]}")
    except Exception:
        pass

driver.save_screenshot("data/docs/mdc_final.png")
print(f"\nFinal URL: {driver.current_url}")
driver.quit()