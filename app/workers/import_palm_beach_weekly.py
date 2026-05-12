import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.db import get_connection

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR  = BASE_DIR / "data" / "raw" / "palm_beach" / "permits"

# ---------------------------------------------------------------------------
# Palm Beach county permit .txt column definitions (pipe-delimited)
# Confirmed from raw file inspection:
# PERMITNO|PERMIT_DESC_CODE|LAST_ISSUED_DATE|GROSS_SQ_FT|STREET_NO|STREET_DIR|
# STREET_NAME|STREET_TYPE|STREET_POST|CITY|ZIP|ZIP_CODE_EXT|PCN|
# OWN_LAST_NAME|OWN_FIRST_NAME|OWN_MI_NAME|OWNER_STREET_NO|OWNER_STREET_DIR|
# OWNER_STREET_NAME|OWNER_STREET_TYPE|OWNER_STREET_POST|OWNER_CITY|
# OWNER_STATE|OWNER_ZIP|[phone]|[blank]|[subdivision]|[contractor]|
# [contractor_addr]|[contractor_city_state]|[contractor_zip]|[value]|[desc]
# ---------------------------------------------------------------------------

PERMIT_TYPE_CODES = {
    "0045": "Roofing",
    "0046": "Roofing - Commercial",
    "0047": "Reroofing",
    "0048": "Reroofing - Commercial",
    "0050": "Pool - Residential",
    "0051": "Pool - Commercial",
    "0060": "Electrical",
    "0061": "Electrical - Commercial",
    "0062": "Agricultural / Site Improvement",
    "0070": "Mechanical / HVAC",
    "0071": "Mechanical - Commercial",
    "0080": "Plumbing",
    "0090": "New Construction - Residential",
    "0091": "New Construction - Commercial",
    "0100": "Addition / Renovation - Residential",
    "0101": "Addition / Renovation - Commercial",
    "0110": "Demolition",
    "0120": "Fence",
    "0130": "Sign",
    "0140": "Generator",
    "0150": "Solar",
}


def get_county_id(cur, county_name: str = "Palm Beach") -> int:
    cur.execute("SELECT id FROM counties WHERE county_name = %s", (county_name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO counties (county_name, state_code, created_at, updated_at) "
        "VALUES (%s, %s, NOW(), NOW()) RETURNING id",
        (county_name, "FL"),
    )
    return cur.fetchone()[0]


def parse_date(value: Optional[str]) -> Optional[datetime.date]:
    if not value:
        return None
    value = str(value).strip()
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def normalize_name(name: str) -> str:
    if not name:
        return ""
    return " ".join(name.strip().split()).title()


def normalize_address(parts: list[str]) -> str:
    return " ".join(p.strip() for p in parts if p and p.strip()).upper()


def make_hash(*parts) -> str:
    joined = "|".join(str(p or "") for p in parts)
    return hashlib.md5(joined.encode()).hexdigest()


def parse_contractor(raw: str) -> tuple[str, str]:
    """
    Parse contractor field like: "Baker, Mark(BAKER LANDSCAPE COMPANY)"
    Returns (contractor_name, contractor_business)
    """
    if not raw:
        return "", ""
    raw = raw.strip().strip('"')
    biz_match = re.search(r'\(([^)]+)\)', raw)
    biz = biz_match.group(1).strip() if biz_match else ""
    name_part = raw[:biz_match.start()].strip().rstrip(',').strip() if biz_match else raw
    return name_part, biz


def choose_latest_file() -> Optional[Path]:
    if not RAW_DIR.exists():
        return None
    files = sorted(
        [p for p in RAW_DIR.iterdir()
         if p.is_file() and p.suffix.lower() in {".txt", ".csv", ".tsv"}],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def iter_rows(path: Path):
    """
    Parse Palm Beach pipe-delimited permit file.
    Handles quoted fields with embedded pipes/commas.
    Returns dicts with normalized field names.
    """
    with path.open(newline="", encoding="utf-8-sig", errors="ignore") as f:
        # Detect if first line is a header
        first_line = f.readline().strip()
        f.seek(0)

        has_header = "PERMITNO" in first_line or "permit" in first_line.lower()

        reader = csv.reader(f, delimiter="|", quotechar='"')

        if has_header:
            headers = [h.strip().upper() for h in next(reader)]
        else:
            # Use positional column names based on confirmed file structure
            headers = [
                "PERMITNO", "PERMIT_DESC_CODE", "LAST_ISSUED_DATE", "GROSS_SQ_FT",
                "STREET_NO", "STREET_DIR", "STREET_NAME", "STREET_TYPE", "STREET_POST",
                "CITY", "ZIP", "ZIP_CODE_EXT", "PCN",
                "OWN_LAST_NAME", "OWN_FIRST_NAME", "OWN_MI_NAME",
                "OWNER_STREET_NO", "OWNER_STREET_DIR", "OWNER_STREET_NAME",
                "OWNER_STREET_TYPE", "OWNER_STREET_POST", "OWNER_CITY",
                "OWNER_STATE", "OWNER_ZIP",
                "PHONE", "BLANK", "SUBDIVISION",
                "CONTRACTOR", "CONTRACTOR_ADDR", "CONTRACTOR_CITY_STATE",
                "CONTRACTOR_ZIP", "VALUE", "DESCRIPTION",
            ]

        for row in reader:
            if not row or not any(row):
                continue
            # Pad short rows, truncate long rows to header length
            while len(row) < len(headers):
                row.append("")
            d = {headers[i]: row[i].strip() for i in range(len(headers))}
            yield d


def import_weekly_file(path: Path) -> dict:
    conn = get_connection()
    stats = {
        "raw_inserted": 0, "raw_skipped": 0,
        "normalized_inserted": 0, "normalized_updated": 0,
        "normalized_skipped": 0, "no_permit_number": 0,
    }

    try:
        with conn:
            with conn.cursor() as cur:
                county_id = get_county_id(cur, "Palm Beach")

                for row in iter_rows(path):
                    permit_number = row.get("PERMITNO", "").strip()
                    if not permit_number:
                        stats["no_permit_number"] += 1
                        continue

                    # Owner name — combine last + first, skip if both are numeric
                    own_last  = row.get("OWN_LAST_NAME", "").strip()
                    own_first = row.get("OWN_FIRST_NAME", "").strip()
                    own_mi    = row.get("OWN_MI_NAME", "").strip()

                    # Skip records where owner name is just a number (contractor IDs)
                    if own_last.isdigit() and own_first.isdigit():
                        own_last, own_first = "", ""

                    if own_last and own_first:
                        owner_name = normalize_name(f"{own_first} {own_mi} {own_last}".strip())
                    elif own_last:
                        owner_name = normalize_name(own_last)
                    else:
                        owner_name = ""

                    # Permit site address
                    address_1 = normalize_address([
                        row.get("STREET_NO", ""),
                        row.get("STREET_DIR", ""),
                        row.get("STREET_NAME", ""),
                        row.get("STREET_TYPE", ""),
                        row.get("STREET_POST", ""),
                    ])
                    city  = row.get("CITY", "").strip().title()
                    state = "FL"
                    zip_  = row.get("ZIP", "").strip()

                    # Permit type — decode code or use description
                    type_code   = row.get("PERMIT_DESC_CODE", "").strip()
                    permit_type = PERMIT_TYPE_CODES.get(type_code, type_code)
                    description = row.get("DESCRIPTION", "").strip()

                    # Contractor
                    contractor_raw  = row.get("CONTRACTOR", "").strip()
                    contractor_name, business_name = parse_contractor(contractor_raw)

                    # Dates and value
                    issued_date = parse_date(row.get("LAST_ISSUED_DATE", ""))
                    value_str   = row.get("VALUE", "").strip().replace(",", "").replace("$", "")
                    try:
                        project_value = float(value_str) if value_str else None
                    except ValueError:
                        project_value = None

                    # PCN (parcel control number) — useful for deduplication
                    pcn = row.get("PCN", "").strip()

                    raw_payload = json.dumps({k: v for k, v in row.items() if v})

                    normalized_hash = make_hash(
                        county_id, permit_number,
                        owner_name, address_1, issued_date
                    )

                    # ---- raw_permits upsert ----
                    cur.execute(
                        """
                        INSERT INTO raw_permits (
                            county_id, source_file, source_record_id,
                            raw_payload, issued_date
                        )
                        VALUES (%s, %s, %s, %s::jsonb, %s)
                        ON CONFLICT (county_id, source_record_id) DO UPDATE SET
                            source_file = EXCLUDED.source_file,
                            raw_payload = EXCLUDED.raw_payload,
                            issued_date = EXCLUDED.issued_date
                        RETURNING id, (xmax = 0) AS is_insert
                        """,
                        (county_id, path.name, permit_number, raw_payload, issued_date),
                    )
                    res = cur.fetchone()
                    raw_permit_id = res[0]
                    if res[1]:
                        stats["raw_inserted"] += 1
                    else:
                        stats["raw_skipped"] += 1

                    # ---- normalized_permits upsert ----
                    cur.execute(
                        """
                        INSERT INTO normalized_permits (
                            county_id, raw_permit_id, owner_name, business_name,
                            address_1, city, state, zip,
                            permit_number, permit_type, project_description,
                            issued_date, trade, normalized_hash
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (county_id, permit_number) DO UPDATE SET
                            raw_permit_id       = EXCLUDED.raw_permit_id,
                            owner_name          = EXCLUDED.owner_name,
                            business_name       = EXCLUDED.business_name,
                            address_1           = EXCLUDED.address_1,
                            city                = EXCLUDED.city,
                            zip                 = EXCLUDED.zip,
                            permit_type         = EXCLUDED.permit_type,
                            project_description = EXCLUDED.project_description,
                            issued_date         = EXCLUDED.issued_date,
                            trade               = EXCLUDED.trade,
                            normalized_hash     = EXCLUDED.normalized_hash
                        WHERE
                            normalized_permits.normalized_hash
                                IS DISTINCT FROM EXCLUDED.normalized_hash
                        RETURNING id, (xmax = 0) AS is_insert
                        """,
                        (
                            county_id, raw_permit_id,
                            owner_name, business_name,
                            address_1, city, state, zip_,
                            permit_number, permit_type, description,
                            issued_date,
                            permit_type.lower()[:100] if permit_type else None,
                            normalized_hash,
                        ),
                    )
                    res = cur.fetchone()
                    if res is None:
                        stats["normalized_skipped"] += 1
                    elif res[1]:
                        stats["normalized_inserted"] += 1
                    else:
                        stats["normalized_updated"] += 1

    finally:
        conn.close()

    return stats


def main():
    latest = choose_latest_file()
    if not latest:
        print(f"No Palm Beach weekly permit file found in {RAW_DIR}")
        return

    print(f"Using file: {latest}")
    stats = import_weekly_file(latest)

    print(f"\n--- Import summary ---")
    print(f"  raw_permits    inserted : {stats['raw_inserted']}")
    print(f"  raw_permits    skipped  : {stats['raw_skipped']}  (already seen)")
    print(f"  normalized     inserted : {stats['normalized_inserted']}")
    print(f"  normalized     updated  : {stats['normalized_updated']}  (data changed)")
    print(f"  normalized     skipped  : {stats['normalized_skipped']}  (unchanged)")
    print(f"  rows skipped (no permit#): {stats['no_permit_number']}")


if __name__ == "__main__":
    main()