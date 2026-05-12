"""
debug_broward_source.py

Standalone diagnostic harness for Broward / Accela permit sources.

Usage from project root:
    python -m app.workers.debug_broward_source --source fort_lauderdale_accela --visible
    python -m app.workers.debug_broward_source --source weston_accela --visible
    python -m app.workers.debug_broward_source --url "https://aca-prod.accela.com/FTL/Cap/CapHome.aspx?module=Permits&TabName=Permits" --visible

This script does NOT write to Postgres.
It saves a full debug session under:
    data/debug/broward_permits/session_YYYYMMDD_HHMMSS_<source>/

Outputs:
    01_loaded.html / .png
    controls.csv
    selects.csv
    buttons.csv
    links.csv
    form_state_before_submit.json
    02_after_submit.html / .png
    after_submit_controls.csv
    result_candidates.csv
    summary.txt
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import Select, WebDriverWait
    from webdriver_manager.chrome import ChromeDriverManager
except Exception as exc:  # pragma: no cover
    print("Missing Selenium dependencies.")
    print("Run: pip install selenium webdriver-manager")
    raise


PROJECT_ROOT = Path.cwd()
DEBUG_ROOT = PROJECT_ROOT / "data" / "debug" / "broward_permits"

SOURCES: Dict[str, Dict[str, str]] = {
    "fort_lauderdale_accela": {
        "url": "https://aca-prod.accela.com/FTL/Cap/CapHome.aspx?module=Permits&TabName=Permits",
        "agency": "FTL",
    },
    "weston_accela": {
        "url": "https://aca-prod.accela.com/WESTON/Cap/CapHome.aspx?module=Building&TabName=Building",
        "agency": "WESTON",
    },
    "hollywood_accela": {
        "url": "https://aca-prod.accela.com/HOLLYWOOD/Cap/CapHome.aspx?module=Building&TabName=Building",
        "agency": "HOLLYWOOD",
    },
    "cooper_city_accela": {
        "url": "https://aca-prod.accela.com/COOPERCITY/Cap/CapHome.aspx?module=Building&TabName=Building",
        "agency": "COOPERCITY",
    },
}


@dataclass
class RunConfig:
    source: str
    url: str
    visible: bool
    wait_seconds: int
    start: Optional[str]
    end: Optional[str]
    permit_type: Optional[str]
    submit: bool
    try_common_actions: bool


def safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value[:120] or "source"


def make_session_dir(source: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = DEBUG_ROOT / f"session_{stamp}_{safe_filename(source)}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_driver(visible: bool) -> webdriver.Chrome:
    opts = Options()
    if not visible:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1440,1200")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--lang=en-US")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(90)
    return driver


def save_page(driver: webdriver.Chrome, out_dir: Path, label: str) -> None:
    html_path = out_dir / f"{label}.html"
    png_path = out_dir / f"{label}.png"
    url_path = out_dir / f"{label}.url.txt"

    html_path.write_text(driver.page_source or "", encoding="utf-8", errors="ignore")
    url_path.write_text(driver.current_url or "", encoding="utf-8", errors="ignore")
    try:
        driver.save_screenshot(str(png_path))
    except Exception as exc:
        (out_dir / f"{label}.screenshot_error.txt").write_text(str(exc), encoding="utf-8")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def attr(el: Any, name: str) -> str:
    try:
        return clean_text(el.get_attribute(name))
    except Exception:
        return ""


def text_of(el: Any) -> str:
    try:
        return clean_text(el.text)
    except Exception:
        return ""


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: Optional[List[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not fieldnames:
        keys = []
        seen = set()
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    keys.append(key)
                    seen.add(key)
        fieldnames = keys or ["empty"]

    with path.open("w", newline="", encoding="utf-8", errors="ignore") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def collect_controls(driver: webdriver.Chrome) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    elements = driver.find_elements(By.XPATH, "//input|//textarea|//select|//button|//a")
    for idx, el in enumerate(elements, start=1):
        tag = (el.tag_name or "").lower()
        row = {
            "idx": idx,
            "tag": tag,
            "id": attr(el, "id"),
            "name": attr(el, "name"),
            "type": attr(el, "type"),
            "role": attr(el, "role"),
            "class": attr(el, "class"),
            "title": attr(el, "title"),
            "aria_label": attr(el, "aria-label"),
            "placeholder": attr(el, "placeholder"),
            "value": attr(el, "value"),
            "text": text_of(el)[:500],
            "href": attr(el, "href"),
            "displayed": safe_bool(lambda: el.is_displayed()),
            "enabled": safe_bool(lambda: el.is_enabled()),
        }
        rows.append(row)
    return rows


def collect_selects(driver: webdriver.Chrome) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    selects = driver.find_elements(By.TAG_NAME, "select")
    for s_idx, select_el in enumerate(selects, start=1):
        try:
            sel = Select(select_el)
            selected_texts = [clean_text(o.text) for o in sel.all_selected_options]
            options = sel.options
        except Exception:
            selected_texts = []
            options = select_el.find_elements(By.TAG_NAME, "option")

        for o_idx, option in enumerate(options, start=1):
            rows.append({
                "select_idx": s_idx,
                "select_id": attr(select_el, "id"),
                "select_name": attr(select_el, "name"),
                "select_class": attr(select_el, "class"),
                "select_displayed": safe_bool(lambda el=select_el: el.is_displayed()),
                "option_idx": o_idx,
                "option_text": text_of(option),
                "option_value": attr(option, "value"),
                "selected": text_of(option) in selected_texts or attr(option, "selected") in {"true", "selected"},
            })
    return rows


def collect_links(driver: webdriver.Chrome) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    anchors = driver.find_elements(By.TAG_NAME, "a")
    for idx, a in enumerate(anchors, start=1):
        href = attr(a, "href")
        text = text_of(a)
        onclick = attr(a, "onclick")
        rows.append({
            "idx": idx,
            "id": attr(a, "id"),
            "class": attr(a, "class"),
            "title": attr(a, "title"),
            "text": text[:500],
            "href": href,
            "onclick": onclick[:500],
            "displayed": safe_bool(lambda el=a: el.is_displayed()),
            "enabled": safe_bool(lambda el=a: el.is_enabled()),
        })
    return rows


def collect_buttons(driver: webdriver.Chrome) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    elements = driver.find_elements(By.XPATH, "//button|//input[@type='button' or @type='submit' or @type='image']|//a")
    for idx, el in enumerate(elements, start=1):
        text = text_of(el)
        value = attr(el, "value")
        title = attr(el, "title")
        eid = attr(el, "id")
        href = attr(el, "href")
        onclick = attr(el, "onclick")
        blob = " ".join([eid, text, value, title, href, onclick]).lower()
        if any(word in blob for word in ["search", "submit", "find", "query", "newsearch", "btnsearch", "postback"]):
            is_candidate = "yes"
        else:
            is_candidate = ""
        rows.append({
            "idx": idx,
            "tag": el.tag_name,
            "id": eid,
            "name": attr(el, "name"),
            "type": attr(el, "type"),
            "text": text[:500],
            "value": value,
            "title": title,
            "href": href,
            "onclick": onclick[:500],
            "displayed": safe_bool(lambda e=el: e.is_displayed()),
            "enabled": safe_bool(lambda e=el: e.is_enabled()),
            "candidate": is_candidate,
        })
    return rows


def safe_bool(fn) -> str:
    try:
        return "true" if fn() else "false"
    except Exception:
        return "error"


def snapshot(driver: webdriver.Chrome, out_dir: Path, label: str) -> None:
    save_page(driver, out_dir, label)
    write_csv(out_dir / f"{label}_controls.csv", collect_controls(driver))
    write_csv(out_dir / f"{label}_selects.csv", collect_selects(driver))
    write_csv(out_dir / f"{label}_buttons.csv", collect_buttons(driver))
    write_csv(out_dir / f"{label}_links.csv", collect_links(driver))


def extract_result_candidates(driver: webdriver.Chrome) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    anchors = driver.find_elements(By.TAG_NAME, "a")
    permit_like = re.compile(r"\b[A-Z]{1,4}\d{2}[- ]?\d{3,8}(?:\.\d+)?\b", re.I)
    cap_detail = re.compile(r"CapDetail|capID1|capID2|capID3|RecordDetail|Permit", re.I)

    for idx, a in enumerate(anchors, start=1):
        text = text_of(a)
        href = attr(a, "href")
        onclick = attr(a, "onclick")
        blob = " ".join([text, href, onclick])
        permit_match = permit_like.search(blob)
        if permit_match or cap_detail.search(blob):
            rows.append({
                "idx": idx,
                "permit_guess": permit_match.group(0) if permit_match else "",
                "text": text[:800],
                "href": href,
                "onclick": onclick[:800],
                "id": attr(a, "id"),
                "class": attr(a, "class"),
            })

    # Also scan table rows for permit-looking text.
    trs = driver.find_elements(By.XPATH, "//tr")
    for idx, tr in enumerate(trs, start=1):
        text = text_of(tr)
        permit_match = permit_like.search(text)
        if permit_match:
            rows.append({
                "idx": f"tr-{idx}",
                "permit_guess": permit_match.group(0),
                "text": text[:1200],
                "href": "",
                "onclick": "",
                "id": attr(tr, "id"),
                "class": attr(tr, "class"),
            })
    return rows


def detect_date_inputs(driver: webdriver.Chrome) -> List[Dict[str, Any]]:
    rows = []
    inputs = driver.find_elements(By.XPATH, "//input[not(@type='hidden')] | //textarea")
    keywords = ["date", "start", "end", "from", "to", "opened", "filed", "issued", "record"]
    for idx, el in enumerate(inputs, start=1):
        blob = " ".join([
            attr(el, "id"), attr(el, "name"), attr(el, "placeholder"), attr(el, "title"), attr(el, "value"), text_of(el)
        ]).lower()
        if any(k in blob for k in keywords):
            rows.append({
                "idx": idx,
                "id": attr(el, "id"),
                "name": attr(el, "name"),
                "type": attr(el, "type"),
                "placeholder": attr(el, "placeholder"),
                "title": attr(el, "title"),
                "value": attr(el, "value"),
                "displayed": safe_bool(lambda e=el: e.is_displayed()),
                "enabled": safe_bool(lambda e=el: e.is_enabled()),
            })
    return rows


def set_input_value(driver: webdriver.Chrome, el: Any, value: str) -> bool:
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        time.sleep(0.25)
        try:
            driver.execute_script("arguments[0].removeAttribute('readonly'); arguments[0].removeAttribute('disabled');", el)
        except Exception:
            pass
        el.click()
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.BACKSPACE)
        el.send_keys(value)
        el.send_keys(Keys.TAB)
        time.sleep(0.4)
        current = attr(el, "value")
        if current == value:
            return True
        driver.execute_script("""
            const el = arguments[0];
            const val = arguments[1];
            el.value = val;
            el.setAttribute('value', val);
            ['input','change','blur','keyup'].forEach(name => el.dispatchEvent(new Event(name, {bubbles:true})));
        """, el, value)
        time.sleep(0.3)
        return attr(el, "value") == value
    except Exception:
        return False


def try_set_dates(driver: webdriver.Chrome, start: Optional[str], end: Optional[str], out_dir: Path) -> Dict[str, Any]:
    report: Dict[str, Any] = {"requested_start": start, "requested_end": end, "attempts": []}
    if not start and not end:
        return report

    candidates = detect_date_inputs(driver)
    report["date_candidates"] = candidates
    write_csv(out_dir / "date_candidates.csv", candidates)

    # Avoid Fort Lauderdale header global search box. It looks like a date candidate after bad scripts pollute it.
    avoid_ids = {"searchInputHeader", "txtSearchCondition"}
    usable = [c for c in candidates if c.get("id") not in avoid_ids]

    elements_by_id = {attr(el, "id"): el for el in driver.find_elements(By.XPATH, "//input|//textarea") if attr(el, "id")}

    if start:
        # Prefer IDs that look like start/from/date but not hidden header.
        for c in usable:
            cid = c.get("id", "")
            cname = c.get("name", "")
            blob = f"{cid} {cname}".lower()
            if any(k in blob for k in ["start", "from", "opened", "filed", "date"]):
                el = elements_by_id.get(cid)
                if el:
                    ok = set_input_value(driver, el, start)
                    report["attempts"].append({"field": cid, "value": start, "ok": ok, "actual": attr(el, "value")})
                    if ok:
                        break

    if end:
        for c in usable:
            cid = c.get("id", "")
            cname = c.get("name", "")
            blob = f"{cid} {cname}".lower()
            if any(k in blob for k in ["end", "to", "opened", "filed", "date"]):
                # Don't reuse the exact field already used for start unless only one field exists.
                el = elements_by_id.get(cid)
                if el:
                    ok = set_input_value(driver, el, end)
                    report["attempts"].append({"field": cid, "value": end, "ok": ok, "actual": attr(el, "value")})
                    if ok:
                        break

    return report


def try_select_permit_type(driver: webdriver.Chrome, permit_type: Optional[str], out_dir: Path) -> Dict[str, Any]:
    report: Dict[str, Any] = {"requested": permit_type, "selected": None, "attempts": []}
    if not permit_type:
        return report

    selects = driver.find_elements(By.TAG_NAME, "select")
    for s in selects:
        sid = attr(s, "id")
        sname = attr(s, "name")
        text = text_of(s).lower()
        blob = f"{sid} {sname} {text}".lower()
        if not any(k in blob for k in ["permit", "record", "type", "cap"]):
            continue
        try:
            sel = Select(s)
            options = sel.options
            for opt in options:
                opt_text = clean_text(opt.text)
                opt_val = attr(opt, "value")
                if permit_type.lower() in opt_text.lower() or permit_type.lower() in opt_val.lower():
                    sel.select_by_visible_text(opt_text)
                    time.sleep(1.0)
                    report["selected"] = opt_text
                    report["attempts"].append({"select_id": sid, "matched": opt_text, "value": opt_val, "ok": True})
                    return report
            report["attempts"].append({"select_id": sid, "ok": False, "reason": "no option matched"})
        except Exception as exc:
            report["attempts"].append({"select_id": sid, "ok": False, "error": str(exc)})
    return report


def try_click_search(driver: webdriver.Chrome, out_dir: Path) -> Dict[str, Any]:
    report: Dict[str, Any] = {"clicked": None, "attempts": []}

    candidates = [
        (By.ID, "ctl00_PlaceHolderMain_btnNewSearch"),
        (By.ID, "btnSearch"),
        (By.ID, "searchButtonHeader"),
        (By.XPATH, "//a[contains(@id,'btnNewSearch')]"),
        (By.XPATH, "//a[contains(@href,'btnNewSearch')]"),
        (By.XPATH, "//input[@type='submit' and contains(translate(@value,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'search')]"),
        (By.XPATH, "//button[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'search')]"),
    ]

    wait = WebDriverWait(driver, 8)
    for by, selector in candidates:
        try:
            el = wait.until(EC.presence_of_element_located((by, selector)))
            details = {
                "by": str(by),
                "selector": selector,
                "id": attr(el, "id"),
                "text": text_of(el),
                "href": attr(el, "href"),
                "onclick": attr(el, "onclick"),
                "displayed": safe_bool(lambda e=el: e.is_displayed()),
                "enabled": safe_bool(lambda e=el: e.is_enabled()),
            }
            report["attempts"].append(details)
            if details["id"] == "searchButtonHeader":
                # Header search is almost never correct. Only use as last resort.
                continue
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.25)
            href = attr(el, "href")
            if href.startswith("javascript:"):
                js = href.replace("javascript:", "", 1)
                driver.execute_script(js)
            else:
                driver.execute_script("arguments[0].click();", el)
            report["clicked"] = details
            time.sleep(8)
            return report
        except Exception as exc:
            report["attempts"].append({"by": str(by), "selector": selector, "error": str(exc)[:300]})

    # As absolute fallback, try header button after we have logged better choices.
    try:
        el = driver.find_element(By.ID, "searchButtonHeader")
        driver.execute_script("arguments[0].click();", el)
        report["clicked"] = {"id": "searchButtonHeader", "warning": "header fallback used"}
        time.sleep(8)
        return report
    except Exception as exc:
        report["header_fallback_error"] = str(exc)

    return report


def dump_form_state(driver: webdriver.Chrome, out_dir: Path, label: str, extra: Optional[Dict[str, Any]] = None) -> None:
    state = {
        "url": driver.current_url,
        "timestamp": datetime.now().isoformat(),
        "inputs": [],
        "selects": [],
    }
    for el in driver.find_elements(By.XPATH, "//input|//textarea"):
        state["inputs"].append({
            "id": attr(el, "id"),
            "name": attr(el, "name"),
            "type": attr(el, "type"),
            "value": attr(el, "value"),
            "displayed": safe_bool(lambda e=el: e.is_displayed()),
            "enabled": safe_bool(lambda e=el: e.is_enabled()),
        })
    for el in driver.find_elements(By.TAG_NAME, "select"):
        try:
            selected = [clean_text(o.text) for o in Select(el).all_selected_options]
        except Exception:
            selected = []
        state["selects"].append({
            "id": attr(el, "id"),
            "name": attr(el, "name"),
            "selected": selected,
            "displayed": safe_bool(lambda e=el: e.is_displayed()),
            "enabled": safe_bool(lambda e=el: e.is_enabled()),
        })
    if extra:
        state["extra"] = extra
    (out_dir / f"{label}.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def run(config: RunConfig) -> Path:
    out_dir = make_session_dir(config.source)
    driver = make_driver(config.visible)
    summary: List[str] = []
    summary.append(f"source={config.source}")
    summary.append(f"url={config.url}")
    summary.append(f"visible={config.visible}")
    summary.append(f"start={config.start}")
    summary.append(f"end={config.end}")
    summary.append(f"permit_type={config.permit_type}")
    summary.append(f"submit={config.submit}")

    try:
        driver.get(config.url)
        time.sleep(config.wait_seconds)
        snapshot(driver, out_dir, "01_loaded")

        form_report: Dict[str, Any] = {}
        if config.try_common_actions:
            form_report["date_report"] = try_set_dates(driver, config.start, config.end, out_dir)
            form_report["permit_type_report"] = try_select_permit_type(driver, config.permit_type, out_dir)
            dump_form_state(driver, out_dir, "form_state_before_submit", form_report)
            snapshot(driver, out_dir, "02_after_fill")

        if config.submit:
            search_report = try_click_search(driver, out_dir)
            dump_form_state(driver, out_dir, "submit_report", {"search_report": search_report})
            snapshot(driver, out_dir, "03_after_submit")
            candidates = extract_result_candidates(driver)
            write_csv(out_dir / "result_candidates.csv", candidates)
            summary.append(f"result_candidates={len(candidates)}")

        # Always save final extraction candidates too.
        final_candidates = extract_result_candidates(driver)
        write_csv(out_dir / "final_result_candidates.csv", final_candidates)
        summary.append(f"final_result_candidates={len(final_candidates)}")

    except Exception as exc:
        summary.append(f"ERROR={type(exc).__name__}: {exc}")
        try:
            snapshot(driver, out_dir, "ERROR_state")
        except Exception:
            pass
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    (out_dir / "summary.txt").write_text("\n".join(summary), encoding="utf-8")
    return out_dir


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug Broward permit source pages without DB writes.")
    parser.add_argument("--source", default="fort_lauderdale_accela", help=f"Known source: {', '.join(SOURCES.keys())}")
    parser.add_argument("--url", default=None, help="Override URL directly.")
    parser.add_argument("--visible", action="store_true", help="Show Chrome browser.")
    parser.add_argument("--wait", type=int, default=8, help="Seconds to wait after page load.")
    parser.add_argument("--start", default="04/24/2026", help="Date string to try, e.g. 04/24/2026. Use empty string to skip.")
    parser.add_argument("--end", default="04/24/2026", help="Date string to try, e.g. 04/24/2026. Use empty string to skip.")
    parser.add_argument("--permit-type", default="Building Permit", help="Permit type text to try selecting. Empty string to skip.")
    parser.add_argument("--no-submit", action="store_true", help="Do not click search; just dump page/form.")
    parser.add_argument("--no-actions", action="store_true", help="Do not fill dates or permit type; just dump page.")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    source_key = args.source
    source = SOURCES.get(source_key, {})
    url = args.url or source.get("url")
    if not url:
        raise SystemExit(f"Unknown source '{source_key}' and no --url provided.")

    config = RunConfig(
        source=source_key,
        url=url,
        visible=args.visible,
        wait_seconds=args.wait,
        start=args.start or None,
        end=args.end or None,
        permit_type=args.permit_type or None,
        submit=not args.no_submit,
        try_common_actions=not args.no_actions,
    )
    out_dir = run(config)
    print(f"\nDebug session saved: {out_dir}")
    print("Key files:")
    print(f"  {out_dir / 'summary.txt'}")
    print(f"  {out_dir / '01_loaded_controls.csv'}")
    print(f"  {out_dir / '01_loaded_selects.csv'}")
    print(f"  {out_dir / '02_after_fill_controls.csv'}")
    print(f"  {out_dir / '03_after_submit_controls.csv'}")
    print(f"  {out_dir / 'result_candidates.csv'}")


if __name__ == "__main__":
    main()
