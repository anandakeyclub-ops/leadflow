from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
import time

opts = Options()
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=opts)

# Accept disclaimer using confirmed id=idAcceptYes
driver.get("https://erec.mypalmbeachclerk.com/Account/LogOn")
time.sleep(4)
print(f"LogOn URL: {driver.current_url}")

try:
    el = driver.find_element(By.ID, "idAcceptYes")
    driver.execute_script("arguments[0].click();", el)
    print("Accepted via idAcceptYes")
    time.sleep(4)
except Exception as e:
    print(f"idAcceptYes failed: {e}")

print(f"After accept URL: {driver.current_url}")

# Navigate to Document Type Search tab directly
driver.get("https://erec.mypalmbeachclerk.com/Search/Index")
time.sleep(4)
print(f"Search URL: {driver.current_url}")

# Click Document Type Search tab to load those fields
try:
    tab = driver.find_element(By.XPATH,
        "//li[@data-section='DocumentTypeSection'] | "
        "//a[@id='searchCriteriaDocuments-tab']"
    )
    driver.execute_script("arguments[0].click();", tab)
    time.sleep(2)
    print("Clicked Document Type Search tab")
except Exception as e:
    print(f"Tab click: {e}")

# Click the document type select button to ensure section is active
try:
    btn = driver.find_element(By.ID, "documentTypeSelection-DocumentType")
    driver.execute_script("arguments[0].click();", btn)
    time.sleep(1)
    # Close modal
    driver.find_element(By.XPATH, "//button[contains(@class,'close') or contains(@data-dismiss,'modal')]").click()
    time.sleep(1)
except Exception:
    pass

print("\n--- ALL SELECT dropdowns ---")
for sel in driver.find_elements(By.TAG_NAME, "select"):
    sid   = sel.get_attribute("id") or "(no id)"
    sname = sel.get_attribute("name") or "(no name)"
    opts_list = [
        f"  value='{o.get_attribute('value')}' text='{o.text}'"
        for o in sel.find_elements(By.TAG_NAME, "option")
        if o.text.strip()
    ]
    if opts_list:
        print(f"\n  SELECT id={sid} name={sname}")
        for o in opts_list[:20]:
            print(f"  {o}")

print("\n--- ALL INPUT[type=text] fields ---")
for inp in driver.find_elements(By.XPATH, "//input[@type='text' or not(@type)]"):
    iid   = inp.get_attribute("id") or ""
    iname = inp.get_attribute("name") or ""
    ival  = inp.get_attribute("value") or ""
    if iid:
        print(f"  id={iid} name={iname} value='{ival}'")

driver.quit()
print("\nDone.")