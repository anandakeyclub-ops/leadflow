import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.db import get_connection
from app.services.normalize import normalize_name, normalize_address, make_hash

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = BASE_DIR / "data" / "raw" / "broward" / "permits"

TRADE_KEYWORDS = {
    "roofing": ["roof", "reroof", "re-roof", "shingle", "tile roof", "flat roof"],
    "hvac": ["hvac", "a/c", "air conditioning", "mechanical", "duct"],
    "plumbing": ["plumb", "water heater", "sewer", "drain", "backflow"],
    "electrical": ["electric", "panel", "meter", "service change", "generator"],
    "solar": ["solar", "photovoltaic", "pv system"],
    "pool": ["pool", "spa"],
    "windows_doors": ["window", "door", "impact"],
    "general": ["building", "alteration", "remodel", "addition", "renovation"],
}


def get_county_id(cur, county_name: str = "Broward") -> int:
    cur.execute("SELECT id FROM counties WHERE lower(county_name) = lower(%s)", (county_name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        """
        INSERT INTO counties (county_name, state, active, created_at)
        VALUES (%s, %s, TRUE, NOW())
        RETURNING id
        """,
        (county_name, "FL"),
    )
    return cur.fetchone()[0]


def parse_date(value: Optional[str]):
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    # remove timestamps if present
    value = value.split("T")[0].split(" ")[0]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%b-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def detect_delimiter(path: Path) -> str:
    sample = path.read_text(encoding="utf-8-sig", errors="ignore")[:8000]
    candidates = [("\t", sample.count("\t")), ("|", sample.count("|")), (",", sample.count(",")), (";", sample.count(";"))]
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0] if candidates and candidates[0][1] > 0 else ","


def normalized_keys(row: dict) -> dict:
    return {str(k).strip().lower().replace("_", " ").replace("-", " "): k for k in row.keys()}


def pick(row: dict, *keys: str, default: str = "") -> str:
    lowered = normalized_keys(row)
    wanted = [k.lower().replace("_", " ").replace("-", " ").strip() for k in keys]
    for key in wanted:
        if key in lowered:
            return str(row.get(lowered[key], default) or default).strip()
    for actual_norm, actual in lowered.items():
        for key in wanted:
            if key in actual_norm:
                return str(row.get(actual, default) or default).strip()
    return default


def choose_latest_file() -> Optional[Path]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(
        [p for p in RAW_DIR.iterdir() if p.is_file() and p.suffix.lower() in {".csv", ".txt", ".tsv"}],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def iter_rows(path: Path):
    delimiter = "\t" if path.suffix.lower() == ".tsv" else detect_delimiter(path)
    with path.open(newline="", encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        for row in reader:
            if any(str(v or "").strip() for v in row.values()):
                yield row


def infer_trade(*parts: str) -> Optional[str]:
    text = " ".join([p or "" for p in parts]).lower()
    for trade, words in TRADE_KEYWORDS.items():
        if any(w in text for w in words):
            return trade
    return None


def clean_permit_number(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"\s+", "", value)
    return value[:100]


def import_file(path: Path) -> dict:
    conn = get_connection()
    stats = {
        "rows_seen": 0,
        "raw_inserted": 0,
        "raw_updated": 0,
        "normalized_inserted": 0,
        "normalized_updated": 0,
        "normalized_skipped": 0,
        "no_permit_number": 0,
    }
    try:
        with conn:
            with conn.cursor() as cur:
                county_id = get_county_id(cur, "Broward")
                for row in iter_rows(path):
                    stats["rows_seen"] += 1
                    permit_number = clean_permit_number(pick(
                        row,
                        "permit_number", "permit no", "permitno", "permit num", "permit #", "permit", "application no", "record number", "record id", "recordid"
                    ))
                    owner_name = pick(row, "owner_name", "owner", "property owner", "owner name", "applicant", "applicant name")
                    business_name = pick(row, "business_name", "company", "contractor", "contractor name", "licensee", "business")
                    address_1 = pick(row, "address", "full_address", "full address", "site address", "job address", "property address", "location", "address_1")
                    city = pick(row, "city", "site city", "property city")
                    state = pick(row, "state", "site state", "property state", default="FL") or "FL"
                    zip_code = pick(row, "zip", "zipcode", "postal code", "site zip", "property zip")
                    permit_type = pick(row, "permit_type", "permit type", "type", "work class", "record type", "module", "category")
                    description = pick(row, "description", "project_description", "project description", "work description", "job description", "scope", "permit description")
                    issued_date = parse_date(pick(row, "issued_date", "issued", "issue date", "date issued", "final date", "status date", "opened date", "created date"))

                    if not permit_number:
                        stats["no_permit_number"] += 1
                        continue

                    raw_payload = json.dumps(row, default=str)
                    cur.execute(
                        """
                        INSERT INTO raw_permits (county_id, source_file, source_record_id, raw_payload, issued_date)
                        VALUES (%s, %s, %s, %s::jsonb, %s)
                        ON CONFLICT (county_id, source_record_id)
                        DO UPDATE SET
                            source_file = EXCLUDED.source_file,
                            raw_payload = EXCLUDED.raw_payload,
                            issued_date = EXCLUDED.issued_date
                        RETURNING id, (xmax = 0) AS is_insert
                        """,
                        (county_id, path.name, permit_number, raw_payload, issued_date),
                    )
                    raw_permit_id, is_insert = cur.fetchone()
                    stats["raw_inserted" if is_insert else "raw_updated"] += 1

                    normalized_owner = normalize_name(owner_name or business_name)
                    normalized_addr = normalize_address(address_1)
                    trade = infer_trade(permit_type, description)
                    normalized_hash = make_hash(owner_name or business_name, address_1, permit_type, description, issued_date)

                    cur.execute(
                        """
                        INSERT INTO normalized_permits (
                            county_id, raw_permit_id, owner_name, business_name, address_1,
                            city, state, zip, permit_number, permit_type, project_description,
                            issued_date, trade, normalized_hash
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (county_id, permit_number)
                        DO UPDATE SET
                            raw_permit_id = EXCLUDED.raw_permit_id,
                            owner_name = EXCLUDED.owner_name,
                            business_name = EXCLUDED.business_name,
                            address_1 = EXCLUDED.address_1,
                            city = EXCLUDED.city,
                            state = EXCLUDED.state,
                            zip = EXCLUDED.zip,
                            permit_type = EXCLUDED.permit_type,
                            project_description = EXCLUDED.project_description,
                            issued_date = EXCLUDED.issued_date,
                            trade = EXCLUDED.trade,
                            normalized_hash = EXCLUDED.normalized_hash
                        WHERE normalized_permits.normalized_hash IS DISTINCT FROM EXCLUDED.normalized_hash
                           OR normalized_permits.address_1 IS DISTINCT FROM EXCLUDED.address_1
                           OR normalized_permits.owner_name IS DISTINCT FROM EXCLUDED.owner_name
                        RETURNING id, (xmax = 0) AS is_insert
                        """,
                        (
                            county_id, raw_permit_id, normalized_owner or None, business_name or None,
                            normalized_addr or None, city or None, state or "FL", zip_code or None,
                            permit_number, permit_type or None, description or permit_type or None,
                            issued_date, trade, normalized_hash,
                        ),
                    )
                    result = cur.fetchone()
                    if result is None:
                        stats["normalized_skipped"] += 1
                    elif result[1]:
                        stats["normalized_inserted"] += 1
                    else:
                        stats["normalized_updated"] += 1
    finally:
        conn.close()
    return stats


def ensure_constraints():
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_permits_county_record ON raw_permits (county_id, source_record_id)")
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_normalized_permits_county_permit_number ON normalized_permits (county_id, permit_number)")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Import Broward permit CSV/TXT files into leadflow.")
    parser.add_argument("--file", help="Optional exact path to Broward permit CSV/TXT.")
    args = parser.parse_args()

    ensure_constraints()
    path = Path(args.file) if args.file else choose_latest_file()
    if not path or not path.exists():
        print(f"No Broward permit file found in {RAW_DIR}")
        print("Place a Broward permit CSV/TXT export there, then rerun this importer.")
        return

    print(f"Using Broward permit raw file: {path}")
    stats = import_file(path)
    print("\n--- Broward permit import summary ---")
    for key, value in stats.items():
        print(f"  {key:22}: {value}")


if __name__ == "__main__":
    main()
