"""
cleanup_orphan_pdfs.py
======================
Removes PDF files that have no matching record in normalized_liens.
Keeps only PDFs where the instrument number appears in the DB.

Usage:
  python cleanup_orphan_pdfs.py --dry-run   # preview only
  python cleanup_orphan_pdfs.py             # actually delete
  python cleanup_orphan_pdfs.py --county manatee
"""
import argparse, re
from pathlib import Path

try:
    from app.core.db import get_connection
except ImportError:
    import sys; sys.exit("Run from leadflow directory")

BASE_DIR = Path(__file__).resolve().parent
RAW_DIR  = BASE_DIR / "data" / "raw"

COUNTY_DIRS = {
    "manatee":      RAW_DIR / "manatee"    / "liens" / "pdfs",
    "martin":       RAW_DIR / "martin"     / "liens" / "pdfs",
    "lake":         RAW_DIR / "lake"       / "liens" / "pdfs",
    "miami_dade":   RAW_DIR / "miami_dade" / "liens" / "pdfs",
    "pasco":        RAW_DIR / "pasco"      / "liens" / "pdfs",
    "osceola":      RAW_DIR / "osceola"    / "liens" / "pdfs",
    "hillsborough": RAW_DIR / "hillsborough"/ "liens"/ "pdfs",
    "polk":         RAW_DIR / "polk"       / "liens" / "pdfs",
    "pinellas":     RAW_DIR / "pinellas"   / "liens" / "pdfs",
    "sarasota":     RAW_DIR / "sarasota"   / "liens" / "pdfs",
    "duval":        RAW_DIR / "duval"      / "liens" / "pdfs",
    "palm_beach":   RAW_DIR / "palm_beach" / "liens" / "pdfs",
}

def get_db_instrument_numbers(cur, county_name: str) -> set:
    """Get all instrument numbers stored in DB for a county."""
    cur.execute("""
        SELECT rl.raw_payload
        FROM raw_liens rl
        JOIN counties c ON c.id = rl.county_id
        WHERE c.county_name ILIKE %s
    """, (f"%{county_name.replace('_',' ')}%",))
    rows = cur.fetchall()

    instruments = set()
    import json
    for (payload,) in rows:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                continue
        if isinstance(payload, dict):
            for key in ["instrument_number", "instrument", "i",
                        "Instrument", "Instr#", "doc_id"]:
                val = str(payload.get(key, "") or "").strip()
                if val and len(val) > 3:
                    instruments.add(val)
                    # Also add without leading zeros
                    instruments.add(val.lstrip("0"))
    return instruments

def extract_instrument_from_filename(filename: str) -> str:
    """Extract instrument number from PDF filename."""
    name = Path(filename).stem
    # Common patterns: county_INSTRUMENT.pdf, county_INSTRUMENT_name.pdf
    parts = name.split("_")
    for part in parts:
        # Instrument numbers are typically 10+ digits
        if re.match(r"^\d{6,}$", part):
            return part
        # Or alphanumeric like "2026123456"
        if re.match(r"^[A-Z0-9]{6,}$", part.upper()) and any(c.isdigit() for c in part):
            return part
    # Fall back to full stem
    return name

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--county",  default=None)
    args = parser.parse_args()

    conn = get_connection()
    total_deleted = total_kept = total_missing = 0

    counties = ([args.county] if args.county
                else list(COUNTY_DIRS.keys()))

    print(f"\n[Cleanup Orphan PDFs] {'DRY RUN' if args.dry_run else 'LIVE'}")

    try:
        with conn.cursor() as cur:
            for county in counties:
                pdf_dir = COUNTY_DIRS.get(county)
                if not pdf_dir or not pdf_dir.exists():
                    continue

                pdfs = list(pdf_dir.glob("*.pdf"))
                if not pdfs:
                    continue

                print(f"\n  {county.title()}: {len(pdfs)} PDFs on disk")

                # Get instrument numbers from DB
                db_instruments = get_db_instrument_numbers(cur, county)
                print(f"    DB instruments: {len(db_instruments)}")

                # Also get pdf_path values stored in normalized_liens
                cur.execute("""
                    SELECT pdf_path FROM normalized_liens nl
                    JOIN counties c ON c.id = nl.county_id
                    WHERE c.county_name ILIKE %s
                    AND pdf_path IS NOT NULL
                """, (f"%{county.replace('_',' ')}%",))
                db_paths = {Path(r[0]).name for r in cur.fetchall() if r[0]}

                deleted = kept = 0
                for pdf in pdfs:
                    instr = extract_instrument_from_filename(pdf.name)

                    # Check if in DB by instrument number or filename
                    in_db = (instr in db_instruments or
                             pdf.name in db_paths or
                             any(instr in inst for inst in db_instruments))

                    if in_db:
                        kept += 1
                    else:
                        deleted += 1
                        if args.dry_run:
                            if deleted <= 5:
                                print(f"    Would delete: {pdf.name}")
                        else:
                            pdf.unlink()

                if args.dry_run and deleted > 5:
                    print(f"    ... and {deleted-5} more would be deleted")

                print(f"    Kept: {kept} | {'Would delete' if args.dry_run else 'Deleted'}: {deleted}")
                total_kept    += kept
                total_deleted += deleted

    finally:
        conn.close()

    print(f"\n{'='*50}")
    print(f"  Total kept    : {total_kept}")
    print(f"  Total {'would delete' if args.dry_run else 'deleted'}  : {total_deleted}")

if __name__ == "__main__":
    main()
