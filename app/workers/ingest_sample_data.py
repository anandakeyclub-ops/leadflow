import csv
import json
from pathlib import Path
from datetime import datetime

from app.core.db import get_connection
from app.services.normalize import normalize_name, normalize_address, make_hash

def get_county_id(cur, county_name="Palm Beach"):
    cur.execute("SELECT id FROM counties WHERE county_name = %s", (county_name,))
    row = cur.fetchone()
    if not row:
        raise Exception(f"County not found: {county_name}")
    return row[0]

def parse_date(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None

def ingest_permits(cur, county_id, filepath):
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cur.execute("""
                INSERT INTO raw_permits (county_id, source_file, source_record_id, raw_payload, issued_date)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                RETURNING id
            """, (
                county_id,
                Path(filepath).name,
                row.get("permit_number") or row.get("source_record_id"),
                json.dumps(row),
                parse_date(row.get("issued_date"))
            ))
            raw_id = cur.fetchone()[0]

            owner_name = row.get("owner_name", "")
            address_1 = row.get("address", "")
            permit_type = row.get("permit_type", "")
            description = row.get("description", "")
            issued_date = parse_date(row.get("issued_date"))

            cur.execute("""
                INSERT INTO normalized_permits (
                    county_id, raw_permit_id, owner_name, address_1,
                    permit_number, permit_type, project_description,
                    issued_date, trade, normalized_hash
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                county_id,
                raw_id,
                normalize_name(owner_name),
                normalize_address(address_1),
                row.get("permit_number"),
                permit_type,
                description,
                issued_date,
                permit_type.lower() if permit_type else None,
                make_hash(owner_name, address_1, permit_type, issued_date)
            ))

def ingest_liens(cur, county_id, filepath):
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cur.execute("""
                INSERT INTO raw_liens (county_id, source_file, source_record_id, raw_payload, filed_date)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                RETURNING id
            """, (
                county_id,
                Path(filepath).name,
                row.get("source_record_id"),
                json.dumps(row),
                parse_date(row.get("filed_date"))
            ))
            raw_id = cur.fetchone()[0]

            debtor_name = row.get("debtor_name", "")
            address_1 = row.get("address", "")
            amount = row.get("amount")
            filed_date = parse_date(row.get("filed_date"))

            cur.execute("""
                INSERT INTO normalized_liens (
                    county_id, raw_lien_id, debtor_name, address_1,
                    amount, filed_date, normalized_hash
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                county_id,
                raw_id,
                normalize_name(debtor_name),
                normalize_address(address_1),
                float(amount) if amount else None,
                filed_date,
                make_hash(debtor_name, address_1, amount, filed_date)
            ))

def main():
    base_dir = Path(__file__).resolve().parents[2]
    permit_file = base_dir / "data" / "raw" / "palm_beach" / "permits" / "sample_permits.csv"
    lien_file = base_dir / "data" / "raw" / "palm_beach" / "liens" / "sample_liens.csv"

    conn = get_connection()
    cur = conn.cursor()

    county_id = get_county_id(cur, "Palm Beach")

    ingest_permits(cur, county_id, permit_file)
    ingest_liens(cur, county_id, lien_file)

    conn.commit()
    cur.close()
    conn.close()

    print("Sample permit and lien data ingested.")

if __name__ == "__main__":
    main()