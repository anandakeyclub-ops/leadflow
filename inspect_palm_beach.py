"""
inspect_palm_beach.py
=====================
Watches you navigate erec.mypalmbeachclerk.com and prints the form state
at each step so Claude can see exactly what fields/values/IDs are present.

Run:
  python inspect_palm_beach.py

Then navigate manually in the browser window. Press Enter in the terminal
at each step to print the current page state.
"""
import json
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

try:
    from webdriver_manager.chrome import ChromeDriverManager
    drv = webdriver.Chrome(service=Service(ChromeDriverManager().install()),
                           options=Options())
except Exception:
    drv = webdriver.Chrome(options=Options())

opts = Options()
opts.add_argument("--window-size=1440,900")
opts.add_experimental_option("excludeSwitches", ["enable-automation"])

try:
    from webdriver_manager.chrome import ChromeDriverManager
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
except Exception:
    driver = webdriver.Chrome(options=opts)

driver.get("https://erec.mypalmbeachclerk.com/home/index")
print("\nBrowser open. Navigate manually.\n")
print("Press Enter to snapshot the current page state.")
print("Type 'q' + Enter to quit.\n")

while True:
    cmd = input(">>> ").strip().lower()
    if cmd == 'q':
        break

    url   = driver.current_url
    title = driver.title
    print(f"\nURL  : {url}")
    print(f"Title: {title}")

    # All inputs
    inputs = driver.find_elements(By.TAG_NAME, "input")
    selects = driver.find_elements(By.TAG_NAME, "select")
    textareas = driver.find_elements(By.TAG_NAME, "textarea")

    print(f"\nINPUTS ({len(inputs)}):")
    for el in inputs:
        try:
            eid   = el.get_attribute("id") or ""
            ename = el.get_attribute("name") or ""
            etype = el.get_attribute("type") or "text"
            eval_ = el.get_attribute("value") or ""
            disp  = el.is_displayed()
            if eid or ename:
                print(f"  id={eid:35} name={ename:35} type={etype:10} val={eval_[:30]:30} visible={disp}")
        except Exception:
            pass

    print(f"\nSELECTS ({len(selects)}):")
    for el in selects:
        try:
            eid   = el.get_attribute("id") or ""
            ename = el.get_attribute("name") or ""
            eval_ = el.get_attribute("value") or ""
            disp  = el.is_displayed()
            print(f"  id={eid:35} name={ename:35} val={eval_[:30]:30} visible={disp}")
        except Exception:
            pass

    print(f"\nTEXTAREAS ({len(textareas)}):")
    for el in textareas:
        try:
            eid   = el.get_attribute("id") or ""
            ename = el.get_attribute("name") or ""
            eval_ = el.get_attribute("value") or ""
            disp  = el.is_displayed()
            print(f"  id={eid:35} name={ename:35} val={repr(eval_[:60]):60} visible={disp}")
        except Exception:
            pass

    # Buttons and links
    print("\nBUTTONS/SUBMITS:")
    for sel in ["input[type='submit']", "button[type='submit']",
                "a.submitButton", ".submitButton", "button"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        for el in els:
            try:
                if el.is_displayed():
                    eid  = el.get_attribute("id") or ""
                    text = el.text.strip()[:40]
                    cls  = el.get_attribute("class") or ""
                    fname = el.get_attribute("formname") or ""
                    print(f"  [{sel}] id={eid:20} text={text:30} class={cls[:30]} formname={fname}")
            except Exception:
                pass

    # Checkboxes
    cbs = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
    visible_cbs = [c for c in cbs if c.is_displayed()]
    if visible_cbs:
        print(f"\nCHECKBOXES (visible {len(visible_cbs)}):")
        for el in visible_cbs[:20]:
            try:
                eid   = el.get_attribute("id") or ""
                ename = el.get_attribute("name") or ""
                eval_ = el.get_attribute("value") or ""
                checked = el.is_selected()
                label = ""
                try:
                    label = driver.find_element(
                        By.XPATH, f"//label[@for='{eid}']").text.strip()[:40]
                except Exception:
                    pass
                print(f"  id={eid:40} val={eval_:15} checked={checked} label={label}")
            except Exception:
                pass

    # Page body text snippet
    try:
        body = driver.find_element(By.TAG_NAME, "body").text[:300]
        print(f"\nPAGE TEXT (first 300 chars):\n{body}")
    except Exception:
        pass

    print("\n" + "="*60 + "\n")

driver.quit()
print("Done.")
