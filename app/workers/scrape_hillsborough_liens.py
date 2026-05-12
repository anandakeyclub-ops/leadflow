r"""
scrape_hillsborough_liens.py
=============================
Imports ONLY Hillsborough County IRS federal tax lien records from the Clerk's
public Daily Index files into raw_liens + normalized_liens, and attempts to save
a PDF copy of each lien document page using the public Official Records access
site.

Federal tax liens only (IRS / U.S. Treasury) by default. State/local liens are
skipped unless --include-state is passed.

Sources:
  Daily index files:
    https://publicrec.hillsclerk.com/OfficialRecords/DailyIndexes/

  Public instrument access:
    https://publicaccess.hillsclerk.com/oripublicaccess/?instrument=<instrument_number>

Usage from project root:
  python -m app.workers.scrape_hillsborough_liens --days-back 30
  python -m app.workers.scrape_hillsborough_liens --days-back 30 --visible
  python -m app.workers.scrape_hillsborough_liens --days-back 30 --no-pdf
  python -m app.workers.scrape_hillsborough_liens --local-only
  python -m app.workers.scrape_hillsborough_liens --include-state

Place this file at:
  C:\Users\Dana\Desktop\leadflow\app\workers\scrape_hillsborough_liens.py
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app.core.db import get_connection

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError:
    webdriver = None
    Options = None
    Service = None
    By = None
    EC = None
    WebDriverWait = None

try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    ChromeDriverManager = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = BASE_DIR / "data" / "raw" / "hillsborough" / "liens"
PDF_DIR = RAW_DIR / "pdfs"
DEBUG_DIR = RAW_DIR / "debug"

for d in [RAW_DIR, PDF_DIR, DEBUG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

INDEX_URL = os.getenv(
    "HILLSBOROUGH_OR_INDEX_URL",
    "https://publicrec.hillsclerk.com/OfficialRecords/DailyIndexes/",
)

PUBLIC_ACCESS_URL = os.getenv(
    "HILLSBOROUGH_OR_PUBLIC_ACCESS_URL",
    "https://publicaccess.hillsclerk.com/oripublicaccess/",
)

COUNTY_NAME = "Hillsborough"
SOURCE_NAME = "scrape_hillsborough_tax_liens"

# Keep only lien-ish document codes that can contain tax liens.
# Do NOT include JUD / DRJUD / CCJ / LP / MEDLN here. Those are noise for tax-resolution leads.
TARGET_DOC_CODES = {
    "LN",        # Generic lien; must be filtered by creditor/lienholder.
    "LNCORPTX", # Corporate tax lien; still filtered by creditor/lienholder.
}

FEDERAL_TAX_LIENHOLDER_PATTERNS = (
    "INTERNAL REVENUE SERVICE",
    "INTERNAL REV SERVICE",
    "IRS",
    "UNITED STATES OF AMERICA",
    "UNITED STATES TREASURY",
    "US TREASURY",
    "U S TREASURY",
    "DEPARTMENT OF THE TREASURY",
    "DEPT OF TREASURY",
    "TREASURY",
)

STATE_TAX_LIENHOLDER_PATTERNS = (
    "STATE OF FLORIDA",
    "FLORIDA DEPARTMENT OF REVENUE",
    "FL DEPARTMENT OF REVENUE",
    "FLORIDA DEPT OF REVENUE",
    "DEPARTMENT OF REVENUE",
    "DEPT OF REVENUE",
    "FLORIDA DEPARTMENT",
    "FL DEPARTMENT",
    "FL DEPT",
    "TAX COLLECTOR",
    "HILLSBOROUGH COUNTY TAX COLLECTOR",
    "HILLSBOROUGH COUNTY",
    "CITY OF TAMPA",
    "CITY OF PLANT CITY",
    "CITY OF TEMPLE TERRACE",
)

BUSINESS_KEYWORDS = (
    " LLC",
    " INC",
    " CORP",
    " CORPORATION",
    " LTD",
    " CO",
    " COMPANY",
    " GROUP",
    " ENTERPRISE",
    " PA",
    " P.A.",
    " PLLC",
    " LP",
    " LLP",
    " TRUST",
    " ASSOCIATION",
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@dataclass
class HillsboroughRecord:
    instrument_number: str
    record_date: Optional[date]
    record_time: str
    doc_type_code: str
    doc_type_description: str
    case_number: str
    number_of_pages: Optional[int]
    from_names: list[str]
    to_names: list[str]
    source_file: str
    raw_doc: dict
    tax_lien_type: str
    pdf_path: Optional[str] = None
    pdf_url: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_name(value: str | None) -> str:
    value = (value or "").upper().strip()
    value = re.sub(r"\s+", " ", value)
    return value


def title_name(value: str | None) -> str:
    value = (value or "").strip()
    value = re.sub(r"\s+", " ", value)
    return value.title()


def safe_filename(value: str | None) -> str:
    value = re.sub(r"[^\w\-.]+", "_", str(value or "").strip())
    return value.strip("_")[:180] or f"file_{datetime.now():%Y%m%d_%H%M%S}"


def has_any(value: str, patterns: Iterable[str]) -> bool:
    upper = normalize_name(value)
    return any(pattern in upper for pattern in patterns)


def parse_date(value: str | None) -> Optional[date]:
    if not value:
        return None
    value = str(value).strip()
    for fmt in ("%m/%d/%Y", "%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_int(value: str | None) -> Optional[int]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(str(value).strip())
    except ValueError:
        return None


def classify_tax_lien(from_names: list[str], doc_type_code: str, doc_type_description: str) -> str:
    """
    Returns:
      federal_tax_lien
      state_tax_lien
      ignore

    Hillsborough's LN bucket contains lots of non-tax junk, so creditor/lienholder
    filtering is mandatory.
    """
    joined = " | ".join(from_names)
    doc_text = f"{doc_type_code} {doc_type_description}"

    if has_any(joined, FEDERAL_TAX_LIENHOLDER_PATTERNS):
        return "federal_tax_lien"

    if has_any(joined, STATE_TAX_LIENHOLDER_PATTERNS):
        return "state_tax_lien"

    # Corporate tax lien code is useful, but still not enough by itself.
    # If doc code says corporate tax and the creditor was not captured cleanly,
    # keep it as state tax lien rather than throwing it away.
    if normalize_name(doc_type_code) == "LNCORPTX" or "TAX" in normalize_name(doc_text):
        return "state_tax_lien"

    return "ignore"


def is_business_name(value: str) -> bool:
    upper = f" {normalize_name(value)}"
    return any(k in upper for k in BUSINESS_KEYWORDS)


def normalized_hash(record: HillsboroughRecord, debtor_name: str) -> str:
    key = "|".join(
        [
            "hillsborough_tax_lien",
            record.instrument_number,
            normalize_name(debtor_name),
            record.doc_type_code,
            str(record.record_date or ""),
            record.tax_lien_type,
        ]
    )
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def instrument_url(instrument_number: str) -> str:
    return f"{PUBLIC_ACCESS_URL}?instrument={instrument_number}"


def save_debug(driver, label: str) -> None:
    if driver is None:
        return
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = safe_filename(label)
        (DEBUG_DIR / f"{stamp}_{safe}.html").write_text(
            driver.page_source,
            encoding="utf-8",
            errors="ignore",
        )
        driver.save_screenshot(str(DEBUG_DIR / f"{stamp}_{safe}.png"))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_county_id(cur) -> int:
    cur.execute("SELECT id FROM counties WHERE county_name = %s", (COUNTY_NAME,))
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        "INSERT INTO counties (county_name, state, active) VALUES (%s, %s, TRUE) RETURNING id",
        (COUNTY_NAME, "FL"),
    )
    return cur.fetchone()[0]


def ensure_schema(cur) -> None:
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_liens_county_record
        ON raw_liens (county_id, source_record_id)
        WHERE source_record_id IS NOT NULL
        """
    )

    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_normalized_liens_county_hash
        ON normalized_liens (county_id, normalized_hash)
        WHERE normalized_hash IS NOT NULL
        """
    )

    for col in [
        "lien_type TEXT",
        "lien_source TEXT",
        "pdf_path TEXT",
        "pdf_url TEXT",
    ]:
        try:
            cur.execute(f"ALTER TABLE normalized_liens ADD COLUMN IF NOT EXISTS {col}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# File discovery/download
# ---------------------------------------------------------------------------

def fetch_index_links() -> list[str]:
    resp = requests.get(INDEX_URL, timeout=60)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        name = href.rsplit("/", 1)[-1]
        if re.match(r"^[DPM]\d{8}\d{2}i?d?\.29$", name, re.I):
            links.append(urljoin(INDEX_URL, href))

    return sorted(set(links))


def select_recent_file_urls(days_back: int) -> list[str]:
    links = fetch_index_links()
    min_date = date.today() - timedelta(days=max(1, days_back))
    selected = []

    for url in links:
        name = url.rsplit("/", 1)[-1]
        m = re.match(r"^([DPM])(\d{8})", name, re.I)
        if not m:
            continue

        file_date = datetime.strptime(m.group(2), "%Y%m%d").date()
        if file_date >= min_date:
            selected.append(url)

    return selected


def download_recent_files(days_back: int) -> list[Path]:
    urls = select_recent_file_urls(days_back)

    if not urls:
        print("[warn] No Hillsborough daily index URLs found for requested window.")
        return get_local_files()

    downloaded: list[Path] = []

    for url in urls:
        name = url.rsplit("/", 1)[-1]
        dest = RAW_DIR / name

        if dest.exists() and dest.stat().st_size > 0:
            downloaded.append(dest)
            continue

        print(f"  [download] {name}")
        r = requests.get(url, timeout=90)
        r.raise_for_status()
        dest.write_bytes(r.content)
        downloaded.append(dest)

    return downloaded


def get_local_files() -> list[Path]:
    return sorted(
        [p for p in RAW_DIR.glob("*.29") if p.is_file()],
        key=lambda p: p.name,
    )


def pair_files(files: list[Path]) -> list[tuple[Path, Optional[Path], Optional[Path]]]:
    by_date: dict[str, dict[str, Path]] = defaultdict(dict)

    for p in files:
        m = re.match(r"^([DPM])(\d{8})", p.name, re.I)
        if not m:
            continue

        kind, yyyymmdd = m.group(1).upper(), m.group(2)
        by_date[yyyymmdd][kind] = p

    out = []
    for yyyymmdd, group in sorted(by_date.items()):
        if "D" in group:
            out.append((group["D"], group.get("P"), group.get("M")))

    return out


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_doc_file(path: Path) -> dict[str, dict]:
    docs: dict[str, dict] = {}

    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.reader(f, delimiter="|")

        for parts in reader:
            if len(parts) < 5:
                continue

            instrument = parts[2].strip()
            if not instrument:
                continue

            row = {
                "record_type": parts[0].strip() if len(parts) > 0 else "",
                "county_code": parts[1].strip() if len(parts) > 1 else "",
                "instrument_number": instrument,
                "doc_type_code": parts[3].strip().upper() if len(parts) > 3 else "",
                "doc_type_description": parts[4].strip() if len(parts) > 4 else "",
                "case_number": parts[5].strip() if len(parts) > 5 else "",
                "field_6": parts[6].strip() if len(parts) > 6 else "",
                "field_7": parts[7].strip() if len(parts) > 7 else "",
                "field_8": parts[8].strip() if len(parts) > 8 else "",
                "field_9": parts[9].strip() if len(parts) > 9 else "",
                "number_of_pages": parts[10].strip() if len(parts) > 10 else "",
                "record_date": parts[11].strip() if len(parts) > 11 else "",
                "record_time": parts[12].strip() if len(parts) > 12 else "",
                "source_file": path.name,
                "raw_parts": parts,
            }

            docs[instrument] = row

    return docs


def parse_party_file(path: Path | None) -> dict[str, dict[str, list[str]]]:
    parties: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"FRM": [], "TO": [], "OTHER": []})

    if not path:
        return parties

    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.reader(f, delimiter="|")

        for parts in reader:
            if len(parts) < 6:
                continue

            instrument = parts[2].strip()
            role = parts[4].strip().upper()
            name = parts[5].strip()

            if not instrument or not name:
                continue

            if role not in {"FRM", "TO"}:
                role = "OTHER"

            parties[instrument][role].append(name)

    return parties


def build_records(files: list[Path]) -> list[HillsboroughRecord]:
    records: list[HillsboroughRecord] = []

    ignored_non_tax = 0

    for doc_file, party_file, _map_file in pair_files(files):
        docs = parse_doc_file(doc_file)
        parties = parse_party_file(party_file)

        for instrument, doc in docs.items():
            code = doc["doc_type_code"]

            if code not in TARGET_DOC_CODES:
                continue

            related = parties.get(instrument, {"FRM": [], "TO": [], "OTHER": []})
            from_names = related.get("FRM") or []
            to_names = related.get("TO") or []

            if not to_names:
                continue

            tax_lien_type = classify_tax_lien(
                from_names=from_names,
                doc_type_code=code,
                doc_type_description=doc.get("doc_type_description", ""),
            )

            if tax_lien_type == "ignore":
                ignored_non_tax += 1
                continue

            records.append(
                HillsboroughRecord(
                    instrument_number=instrument,
                    record_date=parse_date(doc.get("record_date")),
                    record_time=doc.get("record_time", ""),
                    doc_type_code=code,
                    doc_type_description=doc.get("doc_type_description", ""),
                    case_number=doc.get("case_number", ""),
                    number_of_pages=parse_int(doc.get("number_of_pages")),
                    from_names=from_names,
                    to_names=to_names,
                    source_file=doc_file.name,
                    raw_doc=doc,
                    tax_lien_type=tax_lien_type,
                )
            )

    # Store as function attribute so main/import stats can report it without broad refactor.
    build_records.ignored_non_tax = ignored_non_tax  # type: ignore[attr-defined]
    return records


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------

def make_pdf_driver(visible: bool = False):
    if webdriver is None:
        raise RuntimeError("selenium is required for PDF download. Run: pip install selenium webdriver-manager")

    options = Options()
    if not visible:
        options.add_argument("--headless=new")

    options.add_argument("--window-size=1440,1600")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-notifications")

    prefs = {
        "download.default_directory": str(PDF_DIR),
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True,
        "savefile.default_directory": str(PDF_DIR),
    }
    options.add_experimental_option("prefs", prefs)

    if ChromeDriverManager:
        return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    return webdriver.Chrome(options=options)


def page_has_document(driver) -> bool:
    text = ""
    try:
        text = (driver.find_element(By.TAG_NAME, "body").text or "").upper()
    except Exception:
        pass

    bad_markers = (
        "NO RECORD",
        "NO RESULTS",
        "NOT FOUND",
        "ERROR",
        "UNABLE TO",
    )
    if any(marker in text for marker in bad_markers):
        # Don't reject merely because the site has an alert area; only reject if very little useful text.
        if len(text) < 800:
            return False

    good_markers = (
        "INSTRUMENT",
        "OFFICIAL RECORDS",
        "DOCUMENT",
        "BOOK",
        "PAGE",
        "GRANTOR",
        "GRANTEE",
        "RECORDING",
    )
    return any(marker in text for marker in good_markers) or len(text) > 1200


def try_download_direct_pdf_links(driver, record: HillsboroughRecord) -> tuple[Optional[str], Optional[str]]:
    """
    Looks for obvious PDF/image/download links on the public instrument page.
    If a direct PDF is exposed, download it with requests using browser cookies.
    """
    try:
        links = driver.execute_script(
            """
            const out = [];
            function add(url) {
                if (!url) return;
                if (url.startsWith('/')) url = location.origin + url;
                if (!url.startsWith('http')) return;
                if (!out.includes(url)) out.push(url);
            }

            document.querySelectorAll('a[href], iframe[src], embed[src], object[data]').forEach(el => {
                add(el.getAttribute('href'));
                add(el.getAttribute('src'));
                add(el.getAttribute('data'));
            });

            document.querySelectorAll('[onclick]').forEach(el => {
                const onclick = el.getAttribute('onclick') || '';
                const matches = onclick.match(/['"]([^'"]*(?:pdf|image|document|download|print|view)[^'"]*)['"]/ig) || [];
                for (const m of matches) add(m.replace(/^['"]|['"]$/g, ''));
            });

            return out;
            """
        ) or []
    except Exception:
        links = []

    if not links:
        return None, None

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    try:
        for c in driver.get_cookies():
            session.cookies.set(c["name"], c["value"])
    except Exception:
        pass

    out_path = PDF_DIR / f"hillsborough_{safe_filename(record.instrument_number)}.pdf"

    for link in links:
        lower = link.lower()
        if not any(token in lower for token in ("pdf", "image", "document", "download", "print", "view")):
            continue
        try:
            r = session.get(link, timeout=45, allow_redirects=True)
            content_type = r.headers.get("content-type", "").lower()
            content = r.content or b""
            if len(content) > 500 and (b"%PDF" in content[:20] or "pdf" in content_type or "octet-stream" in content_type):
                out_path.write_bytes(content)
                if out_path.stat().st_size > 500:
                    return str(out_path), link
        except Exception:
            continue

    return None, None


def print_page_to_pdf(driver, record: HillsboroughRecord, url: str) -> tuple[Optional[str], Optional[str]]:
    """
    Uses Chrome DevTools Page.printToPDF to save the public instrument page as a PDF.
    This is a fallback when the site does not expose a raw PDF endpoint.
    """
    out_path = PDF_DIR / f"hillsborough_{safe_filename(record.instrument_number)}.pdf"

    try:
        result = driver.execute_cdp_cmd(
            "Page.printToPDF",
            {
                "printBackground": True,
                "landscape": False,
                "paperWidth": 8.5,
                "paperHeight": 11,
                "marginTop": 0.25,
                "marginBottom": 0.25,
                "marginLeft": 0.25,
                "marginRight": 0.25,
                "scale": 0.85,
            },
        )
        data = base64.b64decode(result["data"])
        if len(data) > 500:
            out_path.write_bytes(data)
            return str(out_path), url
    except Exception as exc:
        print(f"    [pdf] printToPDF failed for {record.instrument_number}: {exc}")

    return None, None


def download_pdf_for_record(driver, record: HillsboroughRecord, timeout: int = 25) -> tuple[Optional[str], Optional[str]]:
    url = instrument_url(record.instrument_number)
    out_path = PDF_DIR / f"hillsborough_{safe_filename(record.instrument_number)}.pdf"

    if out_path.exists() and out_path.stat().st_size > 500:
        return str(out_path), url

    try:
        driver.get(url)
        try:
            WebDriverWait(driver, timeout).until(lambda d: d.execute_script("return document.readyState") == "complete")
        except Exception:
            pass
        time.sleep(3)

        if not page_has_document(driver):
            save_debug(driver, f"pdf_no_doc_{record.instrument_number}")
            return None, url

        direct_path, direct_url = try_download_direct_pdf_links(driver, record)
        if direct_path:
            return direct_path, direct_url

        return print_page_to_pdf(driver, record, url)

    except Exception as exc:
        print(f"    [pdf] failed for {record.instrument_number}: {exc}")
        try:
            save_debug(driver, f"pdf_error_{record.instrument_number}")
        except Exception:
            pass
        return None, url


def download_pdfs(records: list[HillsboroughRecord], visible: bool = False, limit: Optional[int] = None) -> dict:
    stats = {
        "pdf_attempted": 0,
        "pdf_saved": 0,
        "pdf_failed": 0,
    }

    if not records:
        return stats

    driver = make_pdf_driver(visible=visible)

    try:
        selected_records = records[:limit] if limit else records
        total = len(selected_records)

        for idx, rec in enumerate(selected_records, start=1):
            stats["pdf_attempted"] += 1
            print(f"  [pdf {idx}/{total}] {rec.instrument_number}")

            pdf_path, pdf_url = download_pdf_for_record(driver, rec)
            rec.pdf_path = pdf_path
            rec.pdf_url = pdf_url

            if pdf_path:
                stats["pdf_saved"] += 1
                print(f"    saved: {Path(pdf_path).name}")
            else:
                stats["pdf_failed"] += 1
                print("    failed")

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return stats


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def insert_raw_lien(
    cur,
    county_id: int,
    source_record_id: str,
    payload: dict,
    filed_date: Optional[date],
) -> tuple[int, bool]:
    cur.execute(
        """
        INSERT INTO raw_liens (county_id, source_file, source_record_id, raw_payload, filed_date)
        VALUES (%s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (county_id, source_record_id)
        DO UPDATE SET
            raw_payload = EXCLUDED.raw_payload,
            filed_date = EXCLUDED.filed_date,
            scraped_at = NOW()
        RETURNING id, (xmax = 0) AS is_insert
        """,
        (county_id, SOURCE_NAME, source_record_id, json.dumps(payload, default=str), filed_date),
    )

    row = cur.fetchone()
    return row[0], bool(row[1])


def insert_normalized_lien(
    cur,
    county_id: int,
    raw_lien_id: int,
    record: HillsboroughRecord,
    debtor_name: str,
) -> tuple[Optional[int], bool]:
    debtor = title_name(debtor_name)
    filing_type = f"{record.doc_type_code} - {record.tax_lien_type.replace('_', ' ').title()}"
    n_hash = normalized_hash(record, debtor_name)
    business_name = debtor if is_business_name(debtor) else None

    cur.execute(
        """
        INSERT INTO normalized_liens (
            county_id,
            raw_lien_id,
            debtor_name,
            business_name,
            address_1,
            city,
            state,
            zip,
            filing_type,
            lien_type,
            amount,
            filed_date,
            normalized_hash,
            lien_source,
            pdf_path,
            pdf_url
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (county_id, normalized_hash)
        WHERE normalized_hash IS NOT NULL
        DO UPDATE SET
            raw_lien_id = EXCLUDED.raw_lien_id,
            debtor_name = EXCLUDED.debtor_name,
            business_name = EXCLUDED.business_name,
            filing_type = EXCLUDED.filing_type,
            lien_type = EXCLUDED.lien_type,
            filed_date = COALESCE(EXCLUDED.filed_date, normalized_liens.filed_date),
            lien_source = EXCLUDED.lien_source,
            pdf_path = COALESCE(EXCLUDED.pdf_path, normalized_liens.pdf_path),
            pdf_url = COALESCE(EXCLUDED.pdf_url, normalized_liens.pdf_url)
        RETURNING id, (xmax = 0) AS is_insert
        """,
        (
            county_id,
            raw_lien_id,
            debtor,
            business_name,
            None,
            None,
            "FL",
            None,
            filing_type,
            record.tax_lien_type,
            None,
            record.record_date,
            n_hash,
            "Hillsborough Official Records Daily Index",
            record.pdf_path,
            record.pdf_url,
        ),
    )

    row = cur.fetchone()
    return (row[0], bool(row[1])) if row else (None, False)


def import_records(records: list[HillsboroughRecord]) -> dict:
    stats = {
        "records_seen": len(records),
        "records_imported": 0,
        "raw_inserted": 0,
        "normalized_inserted": 0,
        "normalized_updated": 0,
        "federal_tax_records": 0,
        "state_tax_records": 0,
        "ignored_non_tax_records": getattr(build_records, "ignored_non_tax", 0),
        "debtors_imported": 0,
        "skipped_by_filter": 0,
    }

    conn = get_connection()

    try:
        with conn:
            with conn.cursor() as cur:
                county_id = get_county_id(cur)
                ensure_schema(cur)

                for rec in records:
                    if rec.tax_lien_type == "federal_tax_lien":
                        stats["federal_tax_records"] += 1
                    elif rec.tax_lien_type == "state_tax_lien":
                        stats["state_tax_records"] += 1

                    payload = {
                        "source": "hillsborough_official_records_daily_indexes",
                        "instrument_number": rec.instrument_number,
                        "doc_type_code": rec.doc_type_code,
                        "doc_type_description": rec.doc_type_description,
                        "record_date": str(rec.record_date) if rec.record_date else None,
                        "record_time": rec.record_time,
                        "case_number": rec.case_number,
                        "number_of_pages": rec.number_of_pages,
                        "from_names": rec.from_names,
                        "to_names": rec.to_names,
                        "lien_type": rec.tax_lien_type,
                        "source_file": rec.source_file,
                        "pdf_path": rec.pdf_path,
                        "pdf_url": rec.pdf_url,
                        "raw_doc": rec.raw_doc,
                    }

                    source_record_id = f"HILLSBOROUGH-TAX-{rec.instrument_number}"
                    raw_lien_id, is_new_raw = insert_raw_lien(
                        cur,
                        county_id,
                        source_record_id,
                        payload,
                        rec.record_date,
                    )

                    stats["records_imported"] += 1

                    if is_new_raw:
                        stats["raw_inserted"] += 1

                    for debtor_name in rec.to_names:
                        lien_id, is_insert = insert_normalized_lien(
                            cur,
                            county_id,
                            raw_lien_id,
                            rec,
                            debtor_name,
                        )

                        if lien_id:
                            stats["debtors_imported"] += 1
                            if is_insert:
                                stats["normalized_inserted"] += 1
                            else:
                                stats["normalized_updated"] += 1

    finally:
        conn.close()

    return stats


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def write_snapshot(records: list[HillsboroughRecord]) -> Optional[Path]:
    if not records:
        return None

    snap = RAW_DIR / f"hillsborough_tax_liens_{datetime.now():%Y%m%d_%H%M%S}.json"
    snap.write_text(
        json.dumps(
            [
                {
                    "instrument_number": r.instrument_number,
                    "record_date": str(r.record_date) if r.record_date else None,
                    "doc_type_code": r.doc_type_code,
                    "doc_type_description": r.doc_type_description,
                    "from_names": r.from_names,
                    "to_names": r.to_names,
                    "tax_lien_type": r.tax_lien_type,
                    "pdf_path": r.pdf_path,
                    "pdf_url": r.pdf_url,
                    "source_file": r.source_file,
                    "raw_doc": r.raw_doc,
                }
                for r in records
            ],
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return snap


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Hillsborough IRS federal tax lien importer (federal-only by default)")
    parser.add_argument(
        "--days-back",
        type=int,
        default=30,
        help="Daily index lookback window. Hillsborough keeps at least two months online.",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Parse files already downloaded into data/raw/hillsborough/liens.",
    )
    parser.add_argument(
        "--include-state",
        action="store_true",
        help="Also import state/local tax lien records (federal-only is the default).",
    )
    parser.add_argument(
        "--no-pdf",
        action="store_true",
        help="Do not attempt to save PDFs.",
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Show Chrome while saving PDFs.",
    )
    parser.add_argument(
        "--pdf-limit",
        type=int,
        default=None,
        help="Limit number of PDFs attempted for testing.",
    )
    args = parser.parse_args()

    if args.local_only:
        files = get_local_files()
    else:
        files = download_recent_files(args.days_back)

    if not files:
        print(f"No Hillsborough .29 files found in {RAW_DIR}")
        return

    print(f"Using {len(files)} Hillsborough daily index file(s) from {RAW_DIR}")

    records = build_records(files)

    # Default: federal tax liens only. Pass --include-state to also get state liens.
    filtered_records = []
    for rec in records:
        if rec.tax_lien_type == "federal_tax_lien":
            filtered_records.append(rec)
        elif args.include_state and rec.tax_lien_type == "state_tax_lien":
            filtered_records.append(rec)

    print(f"Candidate federal/state tax lien instruments found: {len(records)}")
    print(f"Records after CLI filter: {len(filtered_records)}")

    pdf_stats = {"pdf_attempted": 0, "pdf_saved": 0, "pdf_failed": 0}
    if not args.no_pdf and filtered_records:
        pdf_stats = download_pdfs(filtered_records, visible=args.visible, limit=args.pdf_limit)

    snap = write_snapshot(filtered_records)
    if snap:
        print(f"Snapshot saved: {snap}")

    stats = import_records(filtered_records)

    print("\n--- Hillsborough tax lien import summary ---")
    for key, value in stats.items():
        print(f"  {key:24}: {value}")

    print("\n--- Hillsborough PDF summary ---")
    for key, value in pdf_stats.items():
        print(f"  {key:24}: {value}")

    if pdf_stats["pdf_failed"]:
        print(f"\n  [tip] Check PDF debug files here: {DEBUG_DIR}")
        print("  [tip] If PDFs fail, run a small visible test:")
        print("        python -m app.workers.scrape_hillsborough_liens --days-back 7 --pdf-limit 3 --visible")


if __name__ == "__main__":
    main()