"""
debug_selenium_dallas.py
========================
Shows exactly what Selenium sees on Dallas PublicSearch.
Run: python debug_selenium_dallas.py
"""
import time
from datetime import date, timedelta
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

options = Options()
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--window-size=1920,1080")
options.add_argument(
    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
)
options.binary_location = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# Run visible so we can see what happens
service = Service(ChromeDriverManager().install())
driver  = webdriver.Chrome(service=service, options=options)

end_date  = date.today()
start     = end_date - timedelta(days=30)
date_from = start.strftime("%Y%m%d")
date_to   = end_date.strftime("%Y%m%d")

url = (
    f"https://dallas.tx.publicsearch.us/results"
    f"?department=RP"
    f"&keywordSearch=false"
    f"&recordedDateRange={date_from},{date_to}"
    f"&searchOcrText=false"
    f"&searchType=quickSearch"
    f"&searchValue=Internal+Revenue+Service"
)

print(f"Loading: {url}")
driver.get(url)

# Wait longer for JS to render
print("Waiting 8 seconds for JS to render...")
time.sleep(8)

# Get page title and URL
print(f"Page title: {driver.title}")
print(f"Current URL: {driver.current_url}")

# Get full page text
body_text = driver.find_element(By.TAG_NAME, "body").text
print(f"\nPage text length: {len(body_text)} chars")
print(f"\nFirst 2000 chars of body text:")
print(body_text[:2000])
print("\n...")
print(f"Last 500 chars:")
print(body_text[-500:])

# Save full page source
out_html = Path("data/texas/dallas_selenium_debug.html")
out_html.write_text(driver.page_source, encoding="utf-8")
print(f"\nFull HTML saved: {out_html}")

# Save body text
out_txt = Path("data/texas/dallas_selenium_text.txt")
out_txt.write_text(body_text, encoding="utf-8")
print(f"Body text saved: {out_txt}")

# Check for key elements
print("\nElement checks:")
checks = [
    ("table",                     "by TAG_NAME"),
    ("tbody tr",                  "by CSS"),
    ("[class*='result']",         "by CSS"),
    ("[class*='Result']",         "by CSS"),
    ("[class*='row']",            "by CSS"),
    ("[class*='Row']",            "by CSS"),
    ("[class*='instrument']",     "by CSS"),
    ("[class*='grantor']",        "by CSS"),
]
for selector, method in checks:
    try:
        if method == "by TAG_NAME":
            els = driver.find_elements(By.TAG_NAME, selector)
        else:
            els = driver.find_elements(By.CSS_SELECTOR, selector)
        print(f"  {selector:<35} {len(els):>3} elements found")
        if els and len(els) < 5:
            for el in els[:3]:
                print(f"    text: {el.text[:100]}")
    except Exception as e:
        print(f"  {selector:<35} error: {e}")

# Check for key text
print("\nKey text checks:")
for term in ["INTERNAL REVENUE", "FEDERAL TAX LIEN", "Grantor",
             "No results", "Loading", "Sign In", "Register"]:
    found = term.lower() in body_text.lower()
    print(f"  {'✅' if found else '❌'} '{term}'")

input("\nPress Enter to close browser...")
driver.quit()
