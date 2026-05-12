"""
inspect_lee.py
==============
Run: python inspect_lee.py
Navigate manually. Press Enter to snapshot. Type 'q' to quit.
"""
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

try:
    from webdriver_manager.chrome import ChromeDriverManager
    opts = Options()
    opts.add_argument("--window-size=1440,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); window.chrome = {runtime: {}};"
    })
except Exception:
    driver = webdriver.Chrome()

driver.get("https://or.leeclerk.org/LandMarkWeb/Home/Index")
print("\nBrowser open at Lee County portal.")
print("Navigate manually. Press Enter to snapshot. Type 'q' + Enter to quit.\n")

while True:
    cmd = input(">>> ").strip().lower()
    if cmd == 'q':
        break

    print(f"\nURL: {driver.current_url}")
    print(f"Title: {driver.title}")

    # All visible links
    links = driver.find_elements(By.TAG_NAME, "a")
    print(f"\nVISIBLE LINKS ({len([l for l in links if l.is_displayed()])}):")
    for l in links:
        try:
            if l.is_displayed():
                print(f"  text={l.text.strip()!r:30} href={str(l.get_attribute('href') or '')[:60]}")
        except Exception:
            pass

    # All visible inputs
    inputs = driver.find_elements(By.TAG_NAME, "input")
    print(f"\nVISIBLE INPUTS:")
    for el in inputs:
        try:
            if el.is_displayed():
                print(f"  id={str(el.get_attribute('id') or ''):30} "
                      f"name={str(el.get_attribute('name') or ''):30} "
                      f"type={str(el.get_attribute('type') or ''):10} "
                      f"value={str(el.get_attribute('value') or '')[:30]} "
                      f"aria-label={str(el.get_attribute('aria-label') or '')[:20]}")
        except Exception:
            pass

    # All visible buttons
    buttons = driver.find_elements(By.TAG_NAME, "button")
    print(f"\nVISIBLE BUTTONS:")
    for el in buttons:
        try:
            if el.is_displayed():
                print(f"  id={str(el.get_attribute('id') or ''):25} "
                      f"text={el.text.strip()!r:25} "
                      f"class={str(el.get_attribute('class') or '')[:40]} "
                      f"aria-label={str(el.get_attribute('aria-label') or '')!r}")
        except Exception:
            pass

    # Textareas
    tas = driver.find_elements(By.TAG_NAME, "textarea")
    print(f"\nTEXTAREAS:")
    for el in tas:
        try:
            print(f"  id={str(el.get_attribute('id') or ''):30} "
                  f"name={str(el.get_attribute('name') or ''):30} "
                  f"value={repr(el.get_attribute('value') or el.text or '')[:50]}")
        except Exception:
            pass

    # Checkboxes
    cbs = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
    vcbs = [c for c in cbs if c.is_displayed()]
    if vcbs:
        print(f"\nVISIBLE CHECKBOXES ({len(vcbs)}):")
        for el in vcbs[:15]:
            try:
                label = ""
                try:
                    label = driver.find_element(By.XPATH, f"//label[@for='{el.get_attribute('id')}']").text.strip()[:40]
                except Exception:
                    pass
                print(f"  id={str(el.get_attribute('id') or ''):35} "
                      f"name={str(el.get_attribute('name') or ''):25} "
                      f"value={str(el.get_attribute('value') or ''):10} "
                      f"checked={el.is_selected()} "
                      f"title={str(el.get_attribute('title') or '')[:30]} "
                      f"label={label}")
            except Exception:
                pass

    print("\n" + "="*70 + "\n")

driver.quit()
print("Done.")