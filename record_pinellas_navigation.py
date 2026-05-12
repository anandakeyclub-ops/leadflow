"""
record_pinellas_navigation.py
==============================
Opens the Pinellas Acclaim portal in a visible browser.
You navigate manually while this script records:
  - Every URL you visit
  - Every element you click
  - All form field IDs and values
  - The page HTML at each step

Press ENTER in the terminal after each step to capture a snapshot.
Press Q + ENTER when done.

Run: python record_pinellas_navigation.py
"""
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from pathlib import Path
import time, json

DEBUG_DIR = Path("data/docs/pinellas_debug")
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

opts = Options()
opts.add_argument("--window-size=1440,900")
opts.add_argument("--disable-blink-features=AutomationControlled")
opts.add_experimental_option("excludeSwitches", ["enable-automation"])
opts.add_experimental_option("useAutomationExtension", False)

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=opts
)

# Start at Pinellas home
driver.get("https://officialrecords.mypinellasclerk.gov/AcclaimWeb/")
print("\n" + "="*60)
print("Browser is open. Navigate manually to the lien search.")
print("After EACH step (page load, click, form fill) press ENTER.")
print("Press Q then ENTER to finish and save the navigation log.")
print("="*60 + "\n")

steps = []
step_num = 0

while True:
    cmd = input(f"Step {step_num} — describe what you just did (or Q to quit): ").strip()
    if cmd.upper() == "Q":
        break

    step_num += 1
    snapshot = {
        "step":        step_num,
        "action":      cmd,
        "url":         driver.current_url,
        "title":       driver.title,
        "inputs":      [],
        "selects":     [],
        "buttons":     [],
        "links":       [],
    }

    # Capture all inputs
    for el in driver.find_elements(By.TAG_NAME, "input"):
        try:
            iid   = el.get_attribute("id") or ""
            iname = el.get_attribute("name") or ""
            itype = el.get_attribute("type") or "text"
            ival  = el.get_attribute("value") or ""
            iplaceholder = el.get_attribute("placeholder") or ""
            if iid or iname:
                snapshot["inputs"].append({
                    "id": iid, "name": iname,
                    "type": itype, "value": ival,
                    "placeholder": iplaceholder
                })
        except Exception:
            pass

    # Capture all selects
    for el in driver.find_elements(By.TAG_NAME, "select"):
        try:
            sid = el.get_attribute("id") or "(no id)"
            selected = ""
            try:
                from selenium.webdriver.support.ui import Select
                selected = Select(el).first_selected_option.text
            except Exception:
                pass
            options = []
            for opt in el.find_elements(By.TAG_NAME, "option"):
                options.append(opt.text.strip())
            snapshot["selects"].append({
                "id": sid, "selected": selected,
                "options": options[:20]
            })
        except Exception:
            pass

    # Capture buttons
    for el in driver.find_elements(By.XPATH, "//input[@type='submit' or @type='button'] | //button"):
        try:
            snapshot["buttons"].append({
                "id":    el.get_attribute("id") or "",
                "value": el.get_attribute("value") or el.text or "",
                "class": el.get_attribute("class") or "",
            })
        except Exception:
            pass

    # Capture relevant links
    for el in driver.find_elements(By.TAG_NAME, "a"):
        try:
            text = el.text.strip()
            href = el.get_attribute("href") or ""
            if text and ("search" in text.lower() or "record" in text.lower()
                        or "lien" in text.lower() or "document" in text.lower()
                        or "official" in text.lower() or "export" in text.lower()
                        or "csv" in text.lower()):
                snapshot["links"].append({"text": text, "href": href})
        except Exception:
            pass

    # Save screenshot
    screenshot_path = DEBUG_DIR / f"step_{step_num:02d}_{cmd[:20].replace(' ','_')}.png"
    driver.save_screenshot(str(screenshot_path))

    # Save HTML
    html_path = DEBUG_DIR / f"step_{step_num:02d}.html"
    html_path.write_text(driver.page_source, encoding="utf-8", errors="ignore")

    steps.append(snapshot)

    # Print what we captured
    print(f"\n  URL: {snapshot['url']}")
    print(f"  Inputs ({len(snapshot['inputs'])}):")
    for inp in snapshot["inputs"]:
        print(f"    id={inp['id']!r} name={inp['name']!r} type={inp['type']!r} value={inp['value']!r}")
    print(f"  Selects ({len(snapshot['selects'])}):")
    for sel in snapshot["selects"]:
        print(f"    id={sel['id']!r} selected={sel['selected']!r} options={sel['options'][:5]}")
    print(f"  Buttons ({len(snapshot['buttons'])}):")
    for btn in snapshot["buttons"]:
        print(f"    id={btn['id']!r} value={btn['value']!r}")
    print(f"  Links:")
    for lnk in snapshot["links"][:8]:
        print(f"    '{lnk['text']}' → {lnk['href'][:80]}")
    print(f"  Screenshot: {screenshot_path.name}\n")

# Save full log
log_path = DEBUG_DIR / "navigation_log.json"
log_path.write_text(json.dumps(steps, indent=2), encoding="utf-8")
print(f"\nNavigation log saved: {log_path}")
print(f"Screenshots saved: {DEBUG_DIR}")
print(f"\nTotal steps recorded: {len(steps)}")

driver.quit()
