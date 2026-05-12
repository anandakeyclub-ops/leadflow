r"""
record_miami_dade_manual_navigation.py
======================================

Manual recorder for Miami-Dade Official Records.

Run from project root:
  cd C:\Users\Dana\Desktop\leadflow
  .\.venv\Scripts\Activate.ps1
  pip install selenium webdriver-manager
  python scripts\record_miami_dade_manual_navigation.py

Manual steps after Chrome opens:
  1. Click Name/Document
  2. Select FEDERAL TAX LIEN - FTL or NOTICE OF TAX LIEN - NTL
  3. Enter a known-good date range
  4. Submit search
  5. Wait until results appear
  6. If CSV/download is visible, click it manually
  7. Return to PowerShell and press ENTER

Outputs:
  data/raw/miami_dade/manual_trace/
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.common.exceptions import WebDriverException, JavascriptException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


BASE_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = BASE_DIR / "data" / "raw" / "miami_dade" / "manual_trace"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MIAMI_DADE_URL = os.getenv(
    "MIAMI_DADE_OR_URL",
    "https://onlineservices.miamidadeclerk.gov/officialrecords/",
)


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", errors="ignore")


def setup_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--ignore-certificate-errors")

    download_dir = OUT_DIR / "downloads"
    download_dir.mkdir(parents=True, exist_ok=True)

    options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(download_dir),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        },
    )

    options.set_capability("goog:loggingPrefs", {"browser": "ALL", "performance": "ALL"})

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60)
    return driver


RECORDER_JS = r"""
(function () {
  if (window.__dadeRecorderInstalled) {
    console.log("__DADE_RECORDER_ALREADY_INSTALLED__");
    return;
  }

  window.__dadeRecorderInstalled = true;
  window.__dadeRecorderEvents = window.__dadeRecorderEvents || [];

  function cleanText(value) {
    value = (value || "").toString().replace(/\s+/g, " ").trim();
    return value.length > 700 ? value.slice(0, 700) + "..." : value;
  }

  function cssPath(el) {
    if (!el || el.nodeType !== 1) return "";
    if (el.id) return "#" + el.id;

    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 8) {
      let part = el.nodeName.toLowerCase();

      if (el.className && typeof el.className === "string") {
        const cls = el.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).join(".");
        if (cls) part += "." + cls;
      }

      let sib = el;
      let nth = 1;
      while ((sib = sib.previousElementSibling)) {
        if (sib.nodeName.toLowerCase() === el.nodeName.toLowerCase()) nth++;
      }

      part += ":nth-of-type(" + nth + ")";
      parts.unshift(part);
      el = el.parentElement;
    }

    return parts.join(" > ");
  }

  function describe(el) {
    if (!el) return {};
    const row = el.closest && el.closest("tr");
    const card = el.closest && el.closest(".card, [class*='card'], [role='article']");
    return {
      tag: el.tagName || null,
      id: el.id || null,
      name: el.getAttribute ? el.getAttribute("name") : null,
      type: el.getAttribute ? el.getAttribute("type") : null,
      role: el.getAttribute ? el.getAttribute("role") : null,
      placeholder: el.getAttribute ? el.getAttribute("placeholder") : null,
      ariaLabel: el.getAttribute ? el.getAttribute("aria-label") : null,
      value: el.value !== undefined ? el.value : null,
      checked: el.checked !== undefined ? el.checked : null,
      selectedIndex: el.selectedIndex !== undefined ? el.selectedIndex : null,
      selectedText: el.options && el.selectedIndex >= 0 ? el.options[el.selectedIndex].text : null,
      text: cleanText(el.innerText || el.textContent || ""),
      rowText: row ? cleanText(row.innerText || row.textContent || "") : null,
      cardText: card ? cleanText(card.innerText || card.textContent || "") : null,
      cssPath: cssPath(el)
    };
  }

  function snapshotFormState() {
    const out = {
      url: location.href,
      title: document.title,
      bodyTextHead: cleanText(document.body ? document.body.innerText.slice(0, 3000) : ""),
      selects: [],
      inputs: [],
      buttons: []
    };

    document.querySelectorAll("select").forEach((sel, idx) => {
      out.selects.push({
        idx,
        id: sel.id || null,
        name: sel.name || null,
        ariaLabel: sel.getAttribute("aria-label"),
        value: sel.value,
        selectedIndex: sel.selectedIndex,
        selectedText: sel.options && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex].text : null,
        options: Array.from(sel.options || []).map(o => ({text: cleanText(o.text), value: o.value})).slice(0, 150)
      });
    });

    document.querySelectorAll("input, textarea").forEach((inp, idx) => {
      out.inputs.push({
        idx,
        id: inp.id || null,
        name: inp.name || null,
        type: inp.type || null,
        placeholder: inp.getAttribute("placeholder"),
        ariaLabel: inp.getAttribute("aria-label"),
        value: inp.value,
        checked: inp.checked !== undefined ? inp.checked : null
      });
    });

    document.querySelectorAll("button, a").forEach((btn, idx) => {
      const text = cleanText(btn.innerText || btn.textContent || "");
      if (text) {
        out.buttons.push({
          idx,
          id: btn.id || null,
          href: btn.href || null,
          text,
          ariaLabel: btn.getAttribute("aria-label"),
          className: btn.className && typeof btn.className === "string" ? btn.className : null
        });
      }
    });

    return out;
  }

  function record(type, el, extra) {
    const payload = {
      ts: new Date().toISOString(),
      type,
      url: location.href,
      title: document.title,
      element: describe(el),
      formState: snapshotFormState(),
      extra: extra || null
    };

    window.__dadeRecorderEvents.push(payload);
    try {
      console.log("__DADE_RECORDER_EVENT__" + JSON.stringify(payload));
    } catch (e) {
      console.log("__DADE_RECORDER_EVENT__" + JSON.stringify({type, error:String(e)}));
    }
  }

  document.addEventListener("click", function (e) { record("click", e.target, null); }, true);
  document.addEventListener("input", function (e) { record("input", e.target, null); }, true);
  document.addEventListener("change", function (e) { record("change", e.target, null); }, true);
  document.addEventListener("submit", function (e) { record("submit", e.target, null); }, true);

  const originalFetch = window.fetch;
  if (originalFetch) {
    window.fetch = function () {
      try {
        record("fetch", document.body, {
          url: arguments[0] && arguments[0].url ? arguments[0].url : String(arguments[0])
        });
      } catch (e) {}
      return originalFetch.apply(this, arguments);
    };
  }

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url) {
    this.__dadeMethod = method;
    this.__dadeUrl = url;
    return originalOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function (body) {
    try {
      record("xhr_send", document.body, {
        method: this.__dadeMethod,
        url: this.__dadeUrl,
        body: body ? String(body).slice(0, 5000) : null
      });
    } catch (e) {}
    return originalSend.apply(this, arguments);
  };

  record("recorder_installed", document.body, null);
})();
"""


STATE_JS = r"""
(function () {
  function clean(value) {
    return (value || "").toString().replace(/\s+/g, " ").trim();
  }

  const state = {
    url: location.href,
    title: document.title,
    bodyText: clean(document.body ? document.body.innerText.slice(0, 8000) : ""),
    selects: [],
    inputs: [],
    buttons: [],
    resultCards: [],
    tables: [],
    recorderEvents: window.__dadeRecorderEvents || []
  };

  document.querySelectorAll("select").forEach((sel, idx) => {
    state.selects.push({
      idx,
      id: sel.id || null,
      name: sel.name || null,
      ariaLabel: sel.getAttribute("aria-label"),
      value: sel.value,
      selectedIndex: sel.selectedIndex,
      selectedText: sel.options && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex].text : null,
      options: Array.from(sel.options || []).map(o => ({text: clean(o.text), value: o.value}))
    });
  });

  document.querySelectorAll("input, textarea").forEach((inp, idx) => {
    state.inputs.push({
      idx,
      id: inp.id || null,
      name: inp.name || null,
      type: inp.type || null,
      placeholder: inp.getAttribute("placeholder"),
      ariaLabel: inp.getAttribute("aria-label"),
      value: inp.value,
      checked: inp.checked !== undefined ? inp.checked : null
    });
  });

  document.querySelectorAll("button, a").forEach((el, idx) => {
    const text = clean(el.innerText || el.textContent || "");
    if (text || el.href) {
      state.buttons.push({
        idx,
        id: el.id || null,
        text,
        href: el.href || null,
        ariaLabel: el.getAttribute("aria-label"),
        className: typeof el.className === "string" ? el.className : null
      });
    }
  });

  document.querySelectorAll(".card, [class*='card'], [role='article']").forEach((el, idx) => {
    const text = clean(el.innerText || el.textContent || "");
    if (text) state.resultCards.push({idx, text: text.slice(0, 2500)});
  });

  document.querySelectorAll("table").forEach((table, idx) => {
    const rows = Array.from(table.querySelectorAll("tr")).map(tr =>
      Array.from(tr.querySelectorAll("th,td")).map(td => clean(td.innerText || td.textContent))
    );
    state.tables.push({idx, rows: rows.slice(0, 50)});
  });

  return state;
})();
"""


def inject_recorder(driver: webdriver.Chrome) -> None:
    try:
        driver.execute_script(RECORDER_JS)
    except JavascriptException as exc:
        print(f"[warn] Could not inject recorder JS yet: {exc}")


def capture(driver: webdriver.Chrome, label: str) -> dict[str, Any]:
    ts = stamp()
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)

    html_path = OUT_DIR / f"{ts}_{safe}.html"
    png_path = OUT_DIR / f"{ts}_{safe}.png"
    state_path = OUT_DIR / f"{ts}_{safe}_state.json"

    state = {
        "label": label,
        "timestamp": datetime.now().isoformat(),
        "url": None,
        "title": None,
        "html_path": str(html_path),
        "png_path": str(png_path),
        "state_path": str(state_path),
        "errors": [],
    }

    try:
        state["url"] = driver.current_url
        state["title"] = driver.title
        write_text(html_path, driver.page_source)
        driver.save_screenshot(str(png_path))
    except Exception as exc:
        state["errors"].append(f"HTML/screenshot capture failed: {exc}")

    try:
        page_state = driver.execute_script(STATE_JS)
        write_json(state_path, page_state)
    except Exception as exc:
        state["errors"].append(f"State capture failed: {exc}")

    return state


def read_browser_logs(driver: webdriver.Chrome) -> list[dict[str, Any]]:
    out = []
    try:
        for entry in driver.get_log("browser"):
            out.append(entry)
    except Exception as exc:
        out.append({"error": str(exc)})
    return out


def read_network_logs(driver: webdriver.Chrome) -> list[dict[str, Any]]:
    out = []
    try:
        for entry in driver.get_log("performance"):
            try:
                msg = json.loads(entry.get("message", "{}")).get("message", {})
            except Exception:
                continue

            method = msg.get("method", "")
            params = msg.get("params", {})

            if not method.startswith("Network."):
                continue

            request = params.get("request", {})
            response = params.get("response", {})

            item = {"method": method}

            if request:
                item["request"] = {
                    "url": request.get("url"),
                    "method": request.get("method"),
                    "postData": (request.get("postData") or "")[:8000],
                    "headers": request.get("headers"),
                }

            if response:
                item["response"] = {
                    "url": response.get("url"),
                    "status": response.get("status"),
                    "mimeType": response.get("mimeType"),
                    "headers": response.get("headers"),
                }

            if item.get("request") or item.get("response"):
                out.append(item)

    except Exception as exc:
        out.append({"error": str(exc)})

    return out


def main() -> None:
    print("=" * 80)
    print("MIAMI-DADE MANUAL NAVIGATION RECORDER")
    print("=" * 80)
    print(f"Output folder: {OUT_DIR}")
    print(f"Opening: {MIAMI_DADE_URL}")
    print("=" * 80)

    driver = setup_driver()
    states: list[dict[str, Any]] = []

    try:
        try:
            driver.get(MIAMI_DADE_URL)
        except WebDriverException as exc:
            print(f"[warn] Initial load issue: {exc}")

        time.sleep(6)
        inject_recorder(driver)
        states.append(capture(driver, "initial_loaded"))

        print("\nChrome is open and recorder is running.")
        print("Manually perform the exact search that works:")
        print("  1. Click Name/Document")
        print("  2. Select FTL or NTL")
        print("  3. Enter valid dates")
        print("  4. Click Search")
        print("  5. Wait for results")
        print("  6. Click Download CSV if it appears")
        print("\nWhen finished, return here and press ENTER.")
        input("\nPress ENTER after manual search/results/download... ")

        time.sleep(2)
        inject_recorder(driver)
        states.append(capture(driver, "manual_final"))

        direct_events = []
        try:
            direct_events = driver.execute_script("return window.__dadeRecorderEvents || [];")
        except Exception as exc:
            direct_events = [{"error": str(exc)}]

        write_json(OUT_DIR / "manual_trace_states.json", states)
        write_json(OUT_DIR / "manual_trace_events.json", direct_events)
        write_json(OUT_DIR / "manual_trace_browser_logs.json", read_browser_logs(driver))
        write_json(OUT_DIR / "manual_trace_network.json", read_network_logs(driver))

        write_text(OUT_DIR / "manual_trace_final.html", driver.page_source)
        driver.save_screenshot(str(OUT_DIR / "manual_trace_final.png"))

        try:
            write_json(OUT_DIR / "manual_trace_final_state.json", driver.execute_script(STATE_JS))
        except Exception as exc:
            write_json(OUT_DIR / "manual_trace_final_state_error.json", {"error": str(exc)})

        print("\nSaved files:")
        print(f"  {OUT_DIR / 'manual_trace_states.json'}")
        print(f"  {OUT_DIR / 'manual_trace_events.json'}")
        print(f"  {OUT_DIR / 'manual_trace_browser_logs.json'}")
        print(f"  {OUT_DIR / 'manual_trace_network.json'}")
        print(f"  {OUT_DIR / 'manual_trace_final.html'}")
        print(f"  {OUT_DIR / 'manual_trace_final.png'}")
        print(f"  {OUT_DIR / 'manual_trace_final_state.json'}")
        print(f"  Downloads folder: {OUT_DIR / 'downloads'}")

    finally:
        if os.getenv("KEEP_DADE_RECORDER_OPEN", "0") == "1":
            print("\nKEEP_DADE_RECORDER_OPEN=1 set. Leaving Chrome open.")
        else:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
