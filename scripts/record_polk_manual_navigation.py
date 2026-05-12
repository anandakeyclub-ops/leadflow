r"""
record_polk_manual_navigation.py
================================
Manual recorder for Polk County BrowserView Official Records.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


BASE_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = BASE_DIR / "data" / "raw" / "polk" / "manual_trace"
OUT_DIR.mkdir(parents=True, exist_ok=True)

POLK_URL = "https://apps.polkcountyclerk.net/browserviewor/"


def now_stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def setup_driver():
    options = Options()
    options.add_argument("--start-maximized")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)


RECORDER_JS = """
window.__events = [];

document.addEventListener("click", e => {
    window.__events.push({
        type: "click",
        text: e.target.innerText,
        tag: e.target.tagName
    });
});

document.addEventListener("change", e => {
    window.__events.push({
        type: "change",
        value: e.target.value,
        tag: e.target.tagName
    });
});
"""


def main():
    driver = setup_driver()
    driver.get(POLK_URL)

    time.sleep(5)
    driver.execute_script(RECORDER_JS)

    print("\nDo your manual steps now (select liens, dates, search)")
    input("Press ENTER when done...")

    html = driver.page_source
    write_json(OUT_DIR / "events.json", driver.execute_script("return window.__events"))
    (OUT_DIR / "final.html").write_text(html, encoding="utf-8")
    driver.save_screenshot(str(OUT_DIR / "final.png"))

    print("\nSaved files:")
    print(OUT_DIR)

    driver.quit()


if __name__ == "__main__":
    main()