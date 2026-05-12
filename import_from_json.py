"""
import_from_json.py
===================
Re-imports all saved JSON lien files into a fresh DB.
Reads from data/raw/*/liens/*.json

Usage:
  python import_from_json.py
  python import_from_json.py --county sarasota
"""
import argparse, json, re
from datetime import datetime
from pathlib import Path

try:
    from app.core.db import get_connection
except ImportError:
    import sys; sys.exit("Run from leadflow directory")

BASE_DIR = Path(__file__).resolve().parent
RAW_DIR  = BASE_DIR / "data" / "raw"

COUNTY_MAP = {
    "miami":      "Miami-Dade",
    "miami_dade": "Miami-Dade",
    "martin":     "Martin",
    "lake":       "Lake",
    "sarasota":   "Sarasota",
    "manatee":    "Manatee",
    "pasco":      "Pasco",
    "osceola":    "Osceola",
    "hillsborough": "Hillsborough",
    "pinellas":   "Pinellas",
    "polk":       "Polk",
    "duval":      "Duval",
    "palm_beach": "Palm Beach",
    "stjohns":    "St. Johns",
    "lee":        "Lee",
    "volusia":    "Volusia",
}

def get_or_create_county(cur, name):
    cur.execute("SELECT id FROM counties WHERE county_name=%s", (name,))
    r = cur.fetchone()
    if r: return r[0]
    cur.execute(
        "INSERT INTO counties(county_name,state,active,created_at) "
        "VALUES(%s,'FL',true,NOW()) RETURNING id", (name,))
    return cur.fetchone()[0]

def parse_date(s):
    if not s or s == 'None': return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try: return datetime.strptime(str(s).split()[0], fmt).date()
        except: pass
    return None

def import_file(cur, json_path: Path, county_name: str, source_name: str):
    data = json.loads(json_path.read_text(encoding='utf-8'))
    if not isinstance(data, list): return 0, 0

    cid = get_or_create_county(cur, county_name)
    inserted = skipped = 0

    for rec in data:
        # Handle both full records and summary records {i, d, f, t}
        instr  = str(rec.get('instrument_number') or rec.get('i') or
                     rec.get('Instrument') or rec.get('Instr#') or
                     rec.get('instrument') or '').strip()
        # Handle list-based name fields (Hillsborough format)
        to_names   = rec.get('to_names') or []
        from_names = rec.get('from_names') or []
        debtor = str(rec.get('debtor_name') or rec.get('d') or
                     (to_names[0] if to_names else '') or
                     rec.get('Name') or rec.get('Cross-Party Name') or
                     rec.get('Grantor') or rec.get('grantor') or
                     rec.get('DirectName') or rec.get('debtor') or '').strip()
        filed  = parse_date(rec.get('filed_date') or rec.get('f') or
                            rec.get('Date') or rec.get('RecordDate') or
                            rec.get('record_date') or rec.get('date') or rec.get('RecDate'))
        ltype  = str(rec.get('lien_type') or rec.get('tax_lien_type') or
                     rec.get('t') or 'federal_tax_lien').strip()
        pdf    = str(rec.get('pdf_path') or '')[:500] or None

        # Skip header rows and non-data entries
        if not debtor or len(debtor) < 2 or debtor.upper() in ('NAME', 'GRANTOR', 'DEBTOR'):
            skipped += 1; continue

        # Skip if this looks like a permit record not a lien
        if rec.get('PermitNo') or rec.get('permit_number'):
            skipped += 1; continue

        debtor = debtor.title()
        sid    = f"{source_name}::{instr}" if instr else f"{source_name}::{debtor[:40]}"

        # Insert raw_lien
        raw_id = None
        try:
            cur.execute("SAVEPOINT sp1")
            cur.execute("""
                INSERT INTO raw_liens(county_id,source_file,source_record_id,raw_payload,filed_date)
                VALUES(%s,%s,%s,%s::jsonb,%s)
                ON CONFLICT DO NOTHING
                RETURNING id
            """, (cid, source_name, sid, json.dumps(rec, default=str), filed))
            r = cur.fetchone(); raw_id = r[0] if r else None
            cur.execute("RELEASE SAVEPOINT sp1")
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT sp1")
            cur.execute("RELEASE SAVEPOINT sp1")
            skipped += 1; continue

        # Insert normalized_lien
        nhash = f"{source_name}::{instr}::{debtor[:40]}"
        try:
            cur.execute("SAVEPOINT sp2")
            cur.execute("""
                INSERT INTO normalized_liens
                    (county_id,raw_lien_id,debtor_name,lien_type,filed_date,normalized_hash,pdf_path)
                VALUES(%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(normalized_hash) DO UPDATE SET
                    debtor_name=EXCLUDED.debtor_name,
                    filed_date=COALESCE(EXCLUDED.filed_date,normalized_liens.filed_date),
                    pdf_path=COALESCE(EXCLUDED.pdf_path,normalized_liens.pdf_path)
            """, (cid, raw_id, debtor, ltype, filed, nhash, pdf))
            cur.execute("RELEASE SAVEPOINT sp2")
            inserted += 1
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT sp2")
            cur.execute("RELEASE SAVEPOINT sp2")
            skipped += 1

    return inserted, skipped

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--county', default=None)
    args = parser.parse_args()

    conn = get_connection(); conn.autocommit = False
    total_ins = total_skip = 0

    try:
        with conn.cursor() as cur:
            # Find all JSON files in data/raw/*/liens/
            for county_dir in sorted(RAW_DIR.iterdir()):
                if not county_dir.is_dir(): continue
                ckey = county_dir.name.lower()
                if args.county and args.county.lower() not in ckey: continue
                county_name = COUNTY_MAP.get(ckey, county_dir.name.title())

                liens_dir = county_dir / 'liens'
                if not liens_dir.exists(): continue

                json_files = sorted(liens_dir.glob('*.json'))
                if not json_files: continue

                print(f"\n  {county_name}: {len(json_files)} JSON files")
                source = f"{ckey}_liens"
                c_ins = c_skip = 0
                for jf in json_files:
                    ins, skip = import_file(cur, jf, county_name, source)
                    c_ins += ins; c_skip += skip
                conn.commit()
                print(f"    → {c_ins} inserted, {c_skip} skipped")
                total_ins += c_ins; total_skip += c_skip

    except Exception as e:
        conn.rollback(); print(f"ERROR: {e}"); import traceback; traceback.print_exc()
    finally:
        conn.close()

    print(f"\n{'='*50}")
    print(f"  TOTAL: {total_ins} inserted, {total_skip} skipped")

if __name__ == '__main__':
    main()