"""
test_timing.py - Tests if waiting longer fixes the parsing issue
Run: python test_timing.py
"""
import time
import re
from datetime import date, timedelta
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

driver.get(url)

# Check row count at different intervals
for wait_secs in [2, 4, 6, 8, 10]:
    time.sleep(2)
    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
    body = driver.find_element(By.TAG_NAME, "body").text
    has_irs = "INTERNAL REVENUE" in body.upper()
    has_ftl = "FEDERAL TAX LIEN" in body.upper()
    print(f"  After {wait_secs}s: {len(rows)} rows | IRS:{has_irs} | FTL:{has_ftl}")
    if rows:
        # Try to read first row
        try:
            cells = rows[0].find_elements(By.TAG_NAME, "td")
            if cells:
                print(f"    First row cells: {len(cells)}")
                for i, cell in enumerate(cells[:5]):
                    print(f"      [{i}]: {cell.text[:50]}")
        except Exception as e:
            print(f"    Cell read error: {e}")
        break

print("\nDone")
driver.quit()
