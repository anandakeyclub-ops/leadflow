"""
inspect_sarasota_permits.py
===========================
Opens Sarasota permit portal. You navigate manually.
Press Enter to snapshot DOM state at any point.
Type 'q' to quit.

Run: python inspect_sarasota_permits.py
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
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    })
except Exception:
    driver = webdriver.Chrome()

driver.get("https://building.scgov.net/")
print("\nBrowser open at Sarasota permits portal.")
print("1. Select a Permit Type from the dropdown")
print("2. Set Start/End dates")  
print("3. Press Enter to snapshot")
print("4. Click SEARCH PERMITS")
print("5. Press Enter again to see results")
print("Type 'q' to quit.\n")

while True:
    cmd = input(">>> ").strip().lower()
    if cmd == 'q':
        break

    print(f"\nURL: {driver.current_url}")
    print(f"Page length: {len(driver.find_element(By.TAG_NAME, 'body').text)}")

    # Dump ALL inputs including their current values
    result = driver.execute_script("""
        var items = [];
        document.querySelectorAll('input, select').forEach(function(el) {
            var rect = el.getBoundingClientRect();
            if (rect.width > 0) {
                items.push({
                    tag: el.tagName,
                    id: el.id,
                    name: el.name || '',
                    type: el.type || '',
                    value: el.value,
                    placeholder: el.placeholder || '',
                    class: (el.className || '').substring(0, 60)
                });
            }
        });
        return items;
    """)
    print(f"\nINPUTS ({len(result)}):")
    for item in result:
        print(f"  <{item['tag']}> id={item['id']!r} val={item['value']!r} "
              f"placeholder={item['placeholder']!r} class={item['class']!r}")

    # Dump all buttons
    btns = driver.execute_script("""
        var items = [];
        document.querySelectorAll('button').forEach(function(el) {
            var rect = el.getBoundingClientRect();
            if (rect.width > 0) {
                items.push({
                    text: el.innerText.trim(),
                    class: (el.className || '').substring(0, 80),
                    disabled: el.disabled
                });
            }
        });
        return items;
    """)
    print(f"\nBUTTONS ({len(btns)}):")
    for btn in btns:
        print(f"  {btn['text']!r:30} disabled={btn['disabled']} class={btn['class']!r}")

    # Full page text
    body = driver.find_element(By.TAG_NAME, "body").text
    print(f"\nPAGE TEXT (bottom 500 chars):\n{body[-500:]}")
    print("\n" + "="*70)

driver.quit()
print("Done.")
