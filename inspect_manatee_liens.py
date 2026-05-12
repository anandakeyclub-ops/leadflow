"""
inspect_manatee_liens.py
=======================
Interactive debugger for Martin County official records portal.
Opens the browser, you navigate manually, press Enter to snapshot DOM.

Run: python inspect_manatee_liens.py
"""
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

try:
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_WDM = True
except ImportError:
    HAS_WDM = False

HOME_URL = "https://records.manateeclerk.com/OfficialRecords/Search"

def make_driver():
    opts = Options()
    opts.add_argument("--window-size=1440,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    if HAS_WDM:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    else:
        driver = webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    })
    return driver

def snapshot(driver):
    print(f"\nURL: {driver.current_url}")
    print(f"Title: {driver.title!r}")

    # All visible buttons and links
    els = driver.execute_script("""
        var r = [];
        document.querySelectorAll('a, button, input[type=submit], input[type=button]').forEach(function(el) {
            var rect = el.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                r.push({
                    tag:   el.tagName,
                    id:    el.id || '',
                    text:  (el.innerText || el.value || '').trim().substring(0, 60),
                    href:  el.href || '',
                    cls:   (el.className || '').substring(0, 50),
                    onclick: (el.getAttribute('onclick') || '').substring(0, 80)
                });
            }
        });
        return r;
    """)
    print(f"\nBUTTONS/LINKS ({len(els)}):")
    for e in els:
        print(f"  <{e['tag']}> id={e['id']!r} text={e['text']!r} onclick={e['onclick']!r}")

    # All visible inputs
    inputs = driver.execute_script("""
        var r = [];
        document.querySelectorAll('input, select, textarea').forEach(function(el) {
            var rect = el.getBoundingClientRect();
            if (rect.width > 0) {
                var opts = [];
                if (el.tagName === 'SELECT') {
                    Array.from(el.options).forEach(function(o) {
                        if (o.text.trim()) opts.push(o.text.trim());
                    });
                }
                r.push({
                    tag:  el.tagName,
                    id:   el.id || '',
                    name: el.name || '',
                    type: el.type || '',
                    val:  (el.value || '').substring(0, 30),
                    ph:   el.placeholder || '',
                    opts: opts.slice(0, 5)
                });
            }
        });
        return r;
    """)
    print(f"\nINPUTS ({len(inputs)}):")
    for i in inputs:
        opts_str = f" OPTIONS={i['opts']}" if i['opts'] else ""
        print(f"  <{i['tag']}> id={i['id']!r} name={i['name']!r} "
              f"type={i['type']!r} val={i['val']!r} ph={i['ph']!r}{opts_str}")

    # Page text snippet
    body = driver.find_element(By.TAG_NAME, "body").text
    print(f"\nPAGE TEXT (first 400): {body[:400]!r}")
    print("=" * 70)

def main():
    driver = make_driver()
    print(f"\nOpening: {HOME_URL}")
    driver.get(HOME_URL)
    time.sleep(4)

    print("\nBrowser open. Commands:")
    print("  Enter       = snapshot current page")
    print("  g <url>     = go to URL")
    print("  c           = click Accept/disclaimer")
    print("  m           = open modal, dump checkboxes with labels")
    print("  check <val> = check checkbox with value (e.g. check 71)")
    print("  done        = close modal (try all methods)")
    print("  s           = try to click Search/document search link")
    print("  r           = dump result containers")
    print("  d           = dump all iframes")
    print("  q           = quit")
    print()

    while True:
        try:
            cmd = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if cmd == 'q':
            break
        elif cmd == '' or cmd == 'snap':
            snapshot(driver)
        elif cmd.startswith('g '):
            url = cmd[2:].strip()
            driver.get(url)
            time.sleep(3)
            snapshot(driver)
        elif cmd == 'c':
            # Try to click Accept disclaimer
            clicked = False
            for xpath in [
                "//a[contains(text(),'Accept')]",
                "//button[contains(text(),'Accept')]",
                "//input[@value='Accept']",
                "//a[contains(text(),'accept')]",
            ]:
                try:
                    btn = driver.find_element(By.XPATH, xpath)
                    driver.execute_script("arguments[0].click();", btn)
                    print(f"  Clicked: {btn.text!r}")
                    clicked = True
                    time.sleep(2)
                    break
                except Exception:
                    continue
            if not clicked:
                print("  No Accept button found")
        elif cmd == 's':
            # Try to navigate to document search
            clicked = False
            for xpath in [
                "//a[contains(text(),'Document')]",
                "//a[contains(text(),'Search')]",
                "//a[contains(text(),'Official Records')]",
                "//span[contains(text(),'Search')]",
            ]:
                try:
                    btn = driver.find_element(By.XPATH, xpath)
                    if btn.is_displayed():
                        print(f"  Clicking: {btn.text!r}")
                        driver.execute_script("arguments[0].click();", btn)
                        clicked = True
                        time.sleep(2)
                        break
                except Exception:
                    continue
            if clicked:
                snapshot(driver)
            else:
                print("  No search link found")
        elif cmd == 'm':
            # Open modal, wait, dump checkboxes with full label text
            try:
                btn = driver.find_element(By.ID, "documentTypeSelection-DocumentType")
                driver.execute_script("arguments[0].click();", btn)
                print("  Clicked select button")
                time.sleep(4)
            except Exception as e:
                print(f"  Could not click select: {e}")
            
            # Dump modal contents
            modal_info = driver.execute_script("""
                var result = {checkboxes: [], done_buttons: [], modal: null};
                // Find the modal
                var modals = document.querySelectorAll('[id*="Modal"], [class*="modal"], .modal');
                result.modal = modals.length + ' modals found';
                modals.forEach(function(m) {
                    if (m.style.display !== 'none' && m.offsetHeight > 0) {
                        result.modal = 'VISIBLE: id=' + m.id + ' class=' + m.className.substring(0,40);
                    }
                });
                // Find checkboxes with labels
                document.querySelectorAll('input[type=checkbox]').forEach(function(cb) {
                    var label = document.querySelector('label[for="' + cb.id + '"]');
                    var text = label ? label.innerText.trim() : '';
                    if (!text && cb.parentElement) text = cb.parentElement.innerText.replace(/\\s+/g,' ').trim().substring(0,60);
                    result.checkboxes.push({id: cb.id, val: cb.value, text: text});
                });
                // Find Done buttons
                document.querySelectorAll('input[value="Done"], button, a').forEach(function(el) {
                    if ((el.value||el.innerText||'').trim() === 'Done' && el.offsetHeight > 0) {
                        result.done_buttons.push({tag: el.tagName, id: el.id, text: (el.value||el.innerText).trim(), onclick: (el.getAttribute('onclick')||'').substring(0,80)});
                    }
                });
                return result;
            """)
            print(f"  Modal: {modal_info['modal']}")
            print(f"  Checkboxes ({len(modal_info['checkboxes'])}):")
            for cb in modal_info['checkboxes'][:15]:
                print(f"    id={cb['id']!r} val={cb['val']!r} text={cb['text']!r}")
            print(f"  Done buttons: {modal_info['done_buttons']}")

        elif cmd.startswith('check '):
            # Check a checkbox by value: check 71
            val = cmd.split(' ', 1)[1].strip()
            result = driver.execute_script("""
                var val = arguments[0];
                var cbs = document.querySelectorAll('input[type=checkbox][value="' + val + '"]');
                var checked = [];
                cbs.forEach(function(cb) {
                    cb.checked = true;
                    cb.dispatchEvent(new Event('change', {bubbles:true}));
                    checked.push(cb.id);
                });
                return checked;
            """, val)
            print(f"  Checked val={val!r}: {result}")

        elif cmd == 'done':
            # Try every possible way to close the modal
            result = driver.execute_script("""
                // Try GetDocTypeString
                try { GetDocTypeString(); return 'GetDocTypeString()'; } catch(e) {}
                // Try clicking Done input
                var done = document.querySelector('input[value="Done"]');
                if (done) { done.click(); return 'clicked input[value=Done]'; }
                // Try clicking Done button/link
                var els = document.querySelectorAll('button, a, input');
                for (var i=0; i<els.length; i++) {
                    var t = (els[i].value||els[i].innerText||'').trim();
                    if (t === 'Done' && els[i].offsetHeight > 0) {
                        els[i].click();
                        return 'clicked ' + els[i].tagName + ' text=' + t;
                    }
                }
                return 'nothing worked';
            """)
            print(f"  Done result: {result}")
            time.sleep(1)
            ta = driver.execute_script("var el=document.getElementById('documentType-DocumentType'); return el?el.value:'NOT FOUND';")
            print(f"  textarea value: {ta!r}")

        elif cmd == 'r':
            # Dump what appeared after search results loaded
            time.sleep(3)
            result = driver.execute_script("""
                var r = {};
                // Check common result containers
                ['#searchResults','#searchResults-DocumentType',
                 '.search-results','.results-container',
                 '#resultsDiv','#resultTable',
                 '.k-grid','[id*="result"]','[id*="Result"]',
                 'table tbody tr'
                ].forEach(function(sel) {
                    try {
                        var els = document.querySelectorAll(sel);
                        if (els.length > 0) {
                            r[sel] = {
                                count: els.length,
                                text: els[0].innerText.substring(0, 100)
                            };
                        }
                    } catch(e) {}
                });
                // Also check all tables
                var tables = document.querySelectorAll('table');
                r['_tables'] = tables.length;
                r['_page_length'] = document.body.innerText.length;
                r['_page_sample'] = document.body.innerText.substring(500, 900);
                return r;
            """)
            print(f"\nRESULT CONTAINERS:")
            for k, v in result.items():
                print(f"  {k}: {v}")
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            print(f"  Iframes: {len(iframes)}")
            for i, iframe in enumerate(iframes):
                print(f"    [{i}] id={iframe.get_attribute('id')!r} "
                      f"src={iframe.get_attribute('src')!r}")
        else:
            print(f"  Unknown command: {cmd!r}")

    driver.quit()
    print("Done.")

if __name__ == "__main__":
    main()
