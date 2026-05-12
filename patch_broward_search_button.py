from pathlib import Path
import re

path = Path("app/workers/scrape_broward_permits.py")
text = path.read_text(encoding="utf-8")

backup = path.with_suffix(".py.before_search_button_fix")
backup.write_text(text, encoding="utf-8")
print(f"Backup saved: {backup}")

new_click_search = r'''
def click_search(driver: webdriver.Chrome) -> None:
    """
    Accela has multiple 'search' controls.
    The top global search button may be disabled.
    The permit search form uses btnNewSearch.
    """
    candidates = [
        "ctl00_PlaceHolderMain_btnNewSearch",
        "ctl00_PlaceHolderMain_generalSearchForm_btnSearch",
        "btnNewSearch",
    ]

    for element_id in candidates:
        try:
            el = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.ID, element_id))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.3)

            href = el.get_attribute("href") or ""
            if href.startswith("javascript:"):
                driver.execute_script(href.replace("javascript:", ""))
            else:
                driver.execute_script("arguments[0].click();", el)

            time.sleep(6)
            return
        except Exception:
            continue

    # Fallback: use title='Search', but avoid disabled global keyword search.
    xpaths = [
        "//a[@title='Search' and contains(@id,'btnNewSearch')]",
        "//a[contains(@id,'btnNewSearch')]",
        "//input[@type='submit' and contains(translate(@value,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'search')]",
        "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'search') and not(@disabled)]",
    ]

    for xp in xpaths:
        try:
            el = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.XPATH, xp))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.3)
            driver.execute_script("arguments[0].click();", el)
            time.sleep(6)
            return
        except Exception:
            continue

    raise NoSuchElementException("Could not find Accela permit Search button")
'''

text = re.sub(
    r"def click_search\(driver: webdriver\.Chrome\) -> None:\n.*?\n\n\ndef extract_result_links",
    new_click_search + "\n\n\ndef extract_result_links",
    text,
    flags=re.S
)

path.write_text(text, encoding="utf-8")
print("Patched click_search successfully.")
