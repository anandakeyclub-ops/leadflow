"""
scrape_broward_liens.py
========================
Imports Broward County Official Records lien/judgment records from Broward's
public FTP/SFTP bulk index files into raw_liens + normalized_liens.

This is intentionally local-first and pipeline-compatible with the existing
Leadflow schema. It does not replace Palm Beach logic.

Primary source:
  Broward County Records, Taxes & Treasury Official Records FTP files
  Host: BCFTP.Broward.org
  Port: 22
  Username/password: crpublic / crpublic
  Remote folder: /Official_Records_Download

Files used:
  MM-DD-YYYYdoc-ver.txt  one record per instrument/document
  MM-DD-YYYYnme-ver.txt  one record per indexed party name

Usage:
  python -m app.workers.scrape_broward_liens
  python -m app.workers.scrape_broward_liens --days-back 10
  python -m app.workers.scrape_broward_liens --federal-only
  python -m app.workers.scrape_broward_liens --county-only
  python -m app.workers.scrape_broward_liens --local-only
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from app.core.db import get_connection

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = BASE_DIR / "data" / "raw" / "broward" / "liens"
DOCS_DIR = BASE_DIR / "data" / "docs"
RAW_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)

SFTP_HOST = os.getenv("BROWARD_FTP_HOST", "BCFTP.Broward.org")
SFTP_PORT = int(os.getenv("BROWARD_FTP_PORT", "22"))
SFTP_USER = os.getenv("BROWARD_FTP_USER", "crpublic")
SFTP_PASSWORD = os.getenv("BROWARD_FTP_PASSWORD", "crpublic")
SFTP_DIR = os.getenv("BROWARD_FTP_DIR", "/Official_Records_Download")

# Broward document type codes from the county's published index layout/type list.
TARGET_DOC_CODES = {
    "LIE",       # Lien
    "LIEN CORP", # Corporate Lien Warrant Exempt
    "FJ",        # Final Judgment
    "CFJ",       # Certified Final Judgment
    "CJF",       # Certified Judgment - Foreign
    "LP",        # Lis Pendens, useful early distress marker
    "TBLIE",     # Transfer Lien to Bond
    "TCLIE",     # Transfer Lien to Cash Deposit
}

FEDERAL_LIENHOLDER_PATTERNS = (
    "INTERNAL REVENUE SERVICE",
    "IRS",
    "UNITED STATES OF AMERICA",
    "UNITED STATES TREASURY",
    "US TREASURY",
    "DEPARTMENT OF THE TREASURY",
)

COUNTY_LIENHOLDER_PATTERNS = (
    "BROWARD COUNTY",
    "BROWARD CO",
    "BROWARD CLERK",
    "RECORDS TAXES TREASURY",
    "TAX COLLECTOR",
    "PROPERTY APPRAISER",
    "CITY OF ",
    "TOWN OF ",
    "VILLAGE OF ",
    "STATE OF FLORIDA",
    "FLORIDA DEPARTMENT",
    "FL DEPARTMENT",
)

DOC_FIELDS = [
    "instrument_number",
    "record_date_yyyymmdd",
    "record_date",
    "record_time",
    "doc_type_code",
    "consideration_amount",
    "book_number",
    "page_number",
    "book_type",
    "legal_description",
    "parcel_id",
    "documentary_tax",
    "intangible_tax",
    "number_of_names",
    "confidential",
    "status",
    "rerecord_flag",
    "source",
    "case_number",
]


@dataclass
class BrowardRecord:
    instrument_number: str
    record_date: Optional[date]
    doc_type_code: str
    consideration_amount: Optional[float]
    legal_description: str
    parcel_id: str
    case_number: str
    direct_names: list[str]
    reverse_names: list[str]
    source_file: str
    raw_doc: dict


def parse_money(value: str | None) -> Optional[float]:
    if value is None:
        return None
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        amount = float(cleaned)
        return amount if amount else None
    except ValueError:
        return None


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


def normalize_name(value: str | None) -> str:
    value = (value or "").upper().strip()
    value = re.sub(r"\s+", " ", value)
    return value


def title_name(value: str | None) -> str:
    value = (value or "").strip()
    return re.sub(r"\s+", " ", value).title()


def has_any(value: str, patterns: Iterable[str]) -> bool:
    upper = normalize_name(value)
    return any(pattern in upper for pattern in patterns)


def classify_record(reverse_names: list[str]) -> str:
    joined = " | ".join(reverse_names)
    if has_any(joined, FEDERAL_LIENHOLDER_PATTERNS):
        return "federal_lien"
    if has_any(joined, COUNTY_LIENHOLDER_PATTERNS):
        return "county_lien"
    return "other_lien_or_judgment"


def get_county_id(cur) -> int:
    cur.execute("SELECT id FROM counties WHERE county_name = %s", ("Broward",))
    row = cur.fetchone()
    if row:
        return row[0]

    # Existing schema uses `state`, not `state_code`.
    cur.execute(
        "INSERT INTO counties (county_name, state, active) VALUES (%s, %s, TRUE) RETURNING id",
        ("Broward", "FL"),
    )
    return cur.fetchone()[0]


def ensure_schema(cur) -> None:
    cur.execute(
        """
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'raw_liens'
          AND constraint_name = 'uq_raw_liens_county_record'
        """
    )
    if not cur.fetchone():
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_liens_county_record
            ON raw_liens (county_id, source_record_id)
            WHERE source_record_id IS NOT NULL
            """
        )
        print("  [schema] Ensured unique index raw_liens(county_id, source_record_id)")

    cur.execute(
        """
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'normalized_liens'
          AND constraint_name = 'uq_normalized_liens_hash'
        """
    )
    # Use a unique index because existing DBs may not have a named constraint.
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_normalized_liens_county_hash
        ON normalized_liens (county_id, normalized_hash)
        WHERE normalized_hash IS NOT NULL
        """
    )

    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'matched_leads'
          AND column_name = 'source_document_path'
        """
    )
    if not cur.fetchone():
        cur.execute("ALTER TABLE matched_leads ADD COLUMN source_document_path TEXT")
        print("  [schema] Added matched_leads.source_document_path")


def sftp_download_recent_files(days_back: int = 10) -> list[Path]:
    try:
        import paramiko  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: paramiko. Run: pip install paramiko\n"
            "Or place Broward doc/nme files in data/raw/broward/liens and rerun with --local-only."
        ) from exc

    days_back = max(1, min(days_back, 10))
    wanted_prefixes = {
        (date.today() - timedelta(days=i)).strftime("%m-%d-%Y")
        for i in range(days_back + 1)
    }

    downloaded: list[Path] = []
    transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
    transport.connect(username=SFTP_USER, password=SFTP_PASSWORD)

    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.chdir(SFTP_DIR)
        remote_names = sftp.listdir()

        targets = []
        for name in remote_names:
            lower = name.lower()
            if not (lower.endswith("doc-ver.txt") or lower.endswith("nme-ver.txt") or lower.endswith("img.zip")):
                continue
            if any(name.startswith(prefix) for prefix in wanted_prefixes):
                targets.append(name)

        if not targets:
            # fallback: take newest available doc/nme files by remote modified time
            stats = []
            for name in remote_names:
                lower = name.lower()
                if lower.endswith("doc-ver.txt") or lower.endswith("nme-ver.txt"):
                    attrs = sftp.stat(name)
                    stats.append((attrs.st_mtime, name))
            targets = [name for _, name in sorted(stats, reverse=True)[:20]]

        for name in sorted(set(targets)):
            dest = RAW_DIR / name
            if dest.exists() and dest.stat().st_size > 0:
                downloaded.append(dest)
                continue
            print(f"  [sftp] Downloading {name}")
            sftp.get(name, str(dest))
            downloaded.append(dest)

    finally:
        transport.close()

    return downloaded


def get_local_files() -> list[Path]:
    return sorted(
        [p for p in RAW_DIR.glob("*") if p.is_file() and p.suffix.lower() in {".txt", ".zip"}],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def parse_doc_file(path: Path) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.reader(f, delimiter="|")
        for parts in reader:
            if not parts or not parts[0].strip():
                continue
            parts = parts + [""] * (len(DOC_FIELDS) - len(parts))
            row = dict(zip(DOC_FIELDS, parts[: len(DOC_FIELDS)]))
            instrument = row["instrument_number"].strip()
            if not instrument:
                continue
            row["_source_file"] = path.name
            docs[instrument] = row
    return docs


def parse_name_file(path: Path) -> dict[str, dict[str, list[str]]]:
    names: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"D": [], "R": []})
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.reader(f, delimiter="|")
        for parts in reader:
            if len(parts) < 3:
                continue
            instrument = parts[0].strip()
            party_name = parts[1].strip()
            party_type = parts[2].strip().upper() or "D"
            if not instrument or not party_name:
                continue
            if party_type not in {"D", "R"}:
                party_type = "D"
            names[instrument][party_type].append(party_name)
    return names


def pair_doc_name_files(files: list[Path]) -> list[tuple[Path, Optional[Path]]]:
    by_prefix: dict[str, dict[str, Path]] = defaultdict(dict)
    for p in files:
        lower = p.name.lower()
        match = re.match(r"^(\d{2}-\d{2}-\d{4})(doc|nme)-ver\.txt$", lower)
        if match:
            by_prefix[match.group(1)][match.group(2)] = p
    pairs = []
    for prefix, parts in sorted(by_prefix.items()):
        if "doc" in parts:
            pairs.append((parts["doc"], parts.get("nme")))
    return pairs


def build_records(files: list[Path]) -> list[BrowardRecord]:
    out: list[BrowardRecord] = []
    for doc_file, name_file in pair_doc_name_files(files):
        docs = parse_doc_file(doc_file)
        names = parse_name_file(name_file) if name_file else {}

        for instrument, doc in docs.items():
            doc_code = doc.get("doc_type_code", "").strip().upper()
            if doc_code not in TARGET_DOC_CODES:
                continue

            party_names = names.get(instrument, {"D": [], "R": []})
            direct_names = party_names.get("D") or []
            reverse_names = party_names.get("R") or []

            # Direct party is usually the debtor/defendant; reverse party is often lienholder/plaintiff.
            if not direct_names:
                continue

            out.append(
                BrowardRecord(
                    instrument_number=instrument,
                    record_date=parse_date(doc.get("record_date") or doc.get("record_date_yyyymmdd")),
                    doc_type_code=doc_code,
                    consideration_amount=parse_money(doc.get("consideration_amount")),
                    legal_description=doc.get("legal_description", "").strip(),
                    parcel_id=doc.get("parcel_id", "").strip(),
                    case_number=doc.get("case_number", "").strip(),
                    direct_names=direct_names,
                    reverse_names=reverse_names,
                    source_file=doc_file.name,
                    raw_doc=doc,
                )
            )
    return out


def normalized_hash(record: BrowardRecord, debtor_name: str, lien_category: str) -> str:
    key = "|".join(
        [
            "broward",
            record.instrument_number,
            debtor_name,
            record.doc_type_code,
            str(record.record_date or ""),
            lien_category,
        ]
    )
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def insert_raw_lien(cur, county_id: int, source_record_id: str, payload: dict, filed_date: Optional[date]) -> tuple[int, bool]:
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
        (county_id, "scrape_broward_liens", source_record_id, json.dumps(payload), filed_date),
    )
    row = cur.fetchone()
    return row[0], bool(row[1])


def insert_normalized_lien(cur, county_id: int, raw_lien_id: int, record: BrowardRecord, debtor_name: str, lien_category: str) -> tuple[Optional[int], bool]:
    debtor = title_name(debtor_name)
    lienholder = " | ".join(record.reverse_names)
    filing_type = f"{record.doc_type_code} - {lien_category.replace('_', ' ').title()}"
    n_hash = normalized_hash(record, debtor_name, lien_category)

    business_keywords = (" LLC", " INC", " CORP", " LTD", " CO", " GROUP", " ENTERPRISE", " PA", " PLLC")
    business_name = debtor if any(k in f" {normalize_name(debtor)}" for k in business_keywords) else None

    cur.execute(
        """
        INSERT INTO normalized_liens (
            county_id, raw_lien_id, debtor_name, business_name,
            address_1, city, state, zip,
            filing_type, amount, filed_date, normalized_hash
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (county_id, normalized_hash)
        DO UPDATE SET
            raw_lien_id = EXCLUDED.raw_lien_id,
            debtor_name = EXCLUDED.debtor_name,
            business_name = EXCLUDED.business_name,
            filing_type = EXCLUDED.filing_type,
            amount = COALESCE(EXCLUDED.amount, normalized_liens.amount),
            filed_date = COALESCE(EXCLUDED.filed_date, normalized_liens.filed_date)
        RETURNING id, (xmax = 0) AS is_insert
        """,
        (
            county_id,
            raw_lien_id,
            debtor,
            business_name,
            None,  # Broward bulk docs usually provide parcel/legal, not mailing street address.
            None,
            "FL",
            None,
            filing_type,
            record.consideration_amount,
            record.record_date,
            n_hash,
        ),
    )
    row = cur.fetchone()
    return (row[0], bool(row[1])) if row else (None, False)


def import_records(records: list[BrowardRecord], federal_only: bool = False, county_only: bool = False, include_other: bool = False) -> dict:
    stats = {
        "records_seen": len(records),
        "records_imported": 0,
        "raw_inserted": 0,
        "normalized_inserted": 0,
        "normalized_updated": 0,
        "federal_records": 0,
        "county_records": 0,
        "other_records": 0,
        "debtors_imported": 0,
    }

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                county_id = get_county_id(cur)
                ensure_schema(cur)

                for rec in records:
                    category = classify_record(rec.reverse_names)
                    if category == "federal_lien":
                        stats["federal_records"] += 1
                    elif category == "county_lien":
                        stats["county_records"] += 1
                    else:
                        stats["other_records"] += 1

                    if federal_only and category != "federal_lien":
                        continue
                    if county_only and category != "county_lien":
                        continue
                    if not include_other and category == "other_lien_or_judgment":
                        continue

                    payload = {
                        "source": "broward_official_records_bulk",
                        "instrument_number": rec.instrument_number,
                        "doc_type_code": rec.doc_type_code,
                        "record_date": str(rec.record_date) if rec.record_date else None,
                        "consideration_amount": rec.consideration_amount,
                        "legal_description": rec.legal_description,
                        "parcel_id": rec.parcel_id,
                        "case_number": rec.case_number,
                        "direct_names": rec.direct_names,
                        "reverse_names": rec.reverse_names,
                        "lien_category": category,
                        "source_file": rec.source_file,
                        "raw_doc": rec.raw_doc,
                    }
                    source_record_id = f"BROWARD-{rec.instrument_number}"
                    raw_lien_id, is_new_raw = insert_raw_lien(cur, county_id, source_record_id, payload, rec.record_date)
                    stats["records_imported"] += 1
                    if is_new_raw:
                        stats["raw_inserted"] += 1

                    for debtor_name in rec.direct_names:
                        lien_id, is_insert = insert_normalized_lien(cur, county_id, raw_lien_id, rec, debtor_name, category)
                        if lien_id:
                            stats["debtors_imported"] += 1
                            if is_insert:
                                stats["normalized_inserted"] += 1
                            else:
                                stats["normalized_updated"] += 1
    finally:
        conn.close()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-back", type=int, default=10, help="Broward public feed exposes 10 continuous days; default 10.")
    parser.add_argument("--local-only", action="store_true", help="Do not connect to SFTP; parse files already in data/raw/broward/liens.")
    parser.add_argument("--federal-only", action="store_true", help="Import only federal IRS / U.S. Treasury lienholder records.")
    parser.add_argument("--county-only", action="store_true", help="Import only county/city/state lienholder records.")
    parser.add_argument("--include-other", action="store_true", help="Also import other lien/judgment records not classified as county/federal.")
    args = parser.parse_args()

    if not args.local_only:
        try:
            files = sftp_download_recent_files(args.days_back)
        except Exception as exc:
            print(f"[warn] Broward SFTP download failed: {exc}")
            print("[warn] Falling back to local files in data/raw/broward/liens")
            files = get_local_files()
    else:
        files = get_local_files()

    txt_files = [p for p in files if p.suffix.lower() == ".txt"]
    if not txt_files:
        print(f"No Broward doc/nme .txt files found in {RAW_DIR}")
        return

    print(f"Using {len(txt_files)} Broward index file(s) from {RAW_DIR}")
    records = build_records(txt_files)
    print(f"Candidate lien/judgment instruments found: {len(records)}")

    stats = import_records(
        records,
        federal_only=args.federal_only,
        county_only=args.county_only,
        include_other=args.include_other,
    )

    print("\n--- Broward lien import summary ---")
    for key, value in stats.items():
        print(f"  {key:22}: {value}")


if __name__ == "__main__":
    main()
