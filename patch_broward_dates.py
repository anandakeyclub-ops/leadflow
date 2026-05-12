from pathlib import Path
import re

path = Path("app/workers/scrape_broward_permits.py")
text = path.read_text(encoding="utf-8")

backup = path.with_suffix(".py.bak")
backup.write_text(text, encoding="utf-8")
print(f"Backup saved: {backup}")

if "from selenium.webdriver.common.keys import Keys" not in text:
    text = text.replace(
        "from selenium.webdriver.common.by import By",
        "from selenium.webdriver.common.by import By\nfrom selenium.webdriver.common.keys import Keys"
    )

new_set_input = r'''
def set_input_value(driver, element, value: str) -> None:
    """
    Accela date fields are masked. Normal .clear() often fails silently.
    This uses click + CTRL+A + BACKSPACE + TAB, then verifies the final value.
    """
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
    time.sleep(0.25)

    try:
        driver.execute_script("""
            arguments[0].removeAttribute('readonly');
            arguments[0].removeAttribute('disabled');
        """, element)
    except Exception:
        pass

    for attempt in range(3):
        try:
            element.click()
            time.sleep(0.15)
            element.send_keys(Keys.CONTROL, "a")
            time.sleep(0.05)
            element.send_keys(Keys.BACKSPACE)
            time.sleep(0.05)
            element.send_keys(value)
            time.sleep(0.15)
            element.send_keys(Keys.TAB)
            time.sleep(0.35)

            current = (element.get_attribute("value") or "").strip()
            if current == value:
                return
        except Exception:
            pass

    # Final hard fallback for masked fields.
    driver.execute_script("""
        const el = arguments[0];
        const val = arguments[1];
        el.focus();
        el.value = val;
        el.setAttribute('value', val);
        ['keydown','keypress','keyup','input','change','blur'].forEach(name => {
            el.dispatchEvent(new Event(name, { bubbles: true }));
        });
    """, element, value)
    time.sleep(0.5)

    current = (element.get_attribute("value") or "").strip()
    if current != value:
        raise RuntimeError(f"Could not set input value. Wanted {value}, got {current}")
'''

text = re.sub(
    r"def set_input_value\(driver, element, value: str\) -> None:\n.*?\n\n\ndef try_accept_disclaimer",
    new_set_input + "\n\n\ndef try_accept_disclaimer",
    text,
    flags=re.S
)

new_set_dates = r'''
def set_search_dates(driver, start: date, end: date, source_name: str = "source") -> None:
    start_text = start.strftime("%m/%d/%Y")
    end_text = end.strftime("%m/%d/%Y")

    try:
        start_el = smart_find_date_input(driver, "start")
        end_el = smart_find_date_input(driver, "end")

        set_input_value(driver, start_el, start_text)
        set_input_value(driver, end_el, end_text)

        actual_start = (start_el.get_attribute("value") or "").strip()
        actual_end = (end_el.get_attribute("value") or "").strip()

        print(f"  [dates] requested {start_text} to {end_text}")
        print(f"  [dates] actual    {actual_start} to {actual_end}")

        if actual_start != start_text or actual_end != end_text:
            save_debug(driver, source_name, "date_values_wrong_after_fill")
            dump_page_inventory(driver, source_name, "date_values_wrong_after_fill")
            raise RuntimeError(
                f"Date fields did not stick. Expected {start_text}-{end_text}, got {actual_start}-{actual_end}"
            )

    except Exception:
        save_debug(driver, source_name, "date_fields_not_found")
        dump_page_inventory(driver, source_name, "date_fields_not_found")
        raise
'''

text = re.sub(
    r"def set_search_dates\(driver, start: date, end: date, source_name: str = \"source\"\) -> None:\n.*?\n\n\ndef maybe_select_record_type",
    new_set_dates + "\n\n\ndef maybe_select_record_type",
    text,
    flags=re.S
)

path.write_text(text, encoding="utf-8")
print("Patched scrape_broward_permits.py successfully.")
