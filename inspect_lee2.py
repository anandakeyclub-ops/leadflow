"""
inspect_lee2.py
===============
Shows every clickable element on the Lee County home page.
Run: python inspect_lee2.py
Press Enter to snapshot. Type 'q' to quit.
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
print("\nBrowser open. Navigate to home page, then press Enter.\nType 'q' to quit.\n")

while True:
    cmd = input(">>> ").strip().lower()
    if cmd == 'q':
        break

    print(f"\nURL: {driver.current_url}")

    # Get ALL elements that have onclick or are clickable
    result = driver.execute_script("""
        var items = [];
        var all = document.querySelectorAll('*');
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            var rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            var text = el.innerText ? el.innerText.trim().substring(0, 50) : '';
            var onclick = el.getAttribute('onclick') || '';
            var href = el.getAttribute('href') || '';
            var tag = el.tagName.toLowerCase();
            var cls = el.getAttribute('class') || '';
            var id = el.getAttribute('id') || '';

            if (onclick || href || tag === 'button' ||
                (text && text.length < 30 && (tag === 'a' || tag === 'div' || tag === 'li' || tag === 'span'))) {
                items.push({
                    tag: tag,
                    text: text,
                    onclick: onclick.substring(0, 80),
                    href: href.substring(0, 80),
                    cls: cls.substring(0, 50),
                    id: id
                });
            }
        }
        return items;
    """)

    print(f"\nCLICKABLE ELEMENTS ({len(result)}):")
    for item in result:
        if item['text'] or item['onclick'] or item['href']:
            print(f"  <{item['tag']}> "
                  f"text={item['text']!r:25} "
                  f"id={item['id']!r:20} "
                  f"class={item['cls']!r:30} "
                  f"onclick={item['onclick']!r:40} "
                  f"href={item['href']!r}")

    print("\n" + "="*80)

driver.quit()
