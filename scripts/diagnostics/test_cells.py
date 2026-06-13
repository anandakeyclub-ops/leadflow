"""
test_cells.py - Maps all 11 cell positions in Dallas PublicSearch table
Run: python test_cells.py
"""
import time
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
url = (
    f"https://dallas.tx.publicsearch.us/results"
    f"?department=RP&keywordSearch=false"
    f"&recordedDateRange={start.strftime('%Y%m%d')},{end_date.strftime('%Y%m%d')}"
    f"&searchOcrText=false&searchType=quickSearch"
    f"&searchValue=Internal+Revenue+Service"
)

driver.get(url)
time.sleep(8)

rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
print(f"Total rows: {len(rows)}")
print()

# Map first 5 rows fully
for r_idx, row in enumerate(rows[:5]):
    cells = row.find_elements(By.TAG_NAME, "td")
    print(f"Row {r_idx+1} ({len(cells)} cells):")
    for i, cell in enumerate(cells):
        text = cell.text.strip()
        if text:
            print(f"  [{i}]: '{text}'")
    print()

driver.quit()
