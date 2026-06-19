#!/usr/bin/env python3
"""
irs_bulk_import.py
==================
Bulk-import IRS / county tax-lien data files into the leadflow lien store.

Accepts a CSV or pipe-delimited file, auto-detects the delimiter, fuzzy-maps the
file's column headers onto our canonical lien fields, and inserts the rows into
`normalized_liens` (the live lien store the rest of the pipeline reads from and
enriches into lien_dbpr_contacts).

Why normalized_liens and not lien_dbpr_contacts: lien_dbpr_contacts is a derived
enrichment table — its lien_id (NOT NULL, UNIQUE, FK) and county_id (NOT NULL)
mean every row must point to a lien that already exists, and it has no columns
for amount / filed_date / lien_type / case_number. normalized_liens has exactly
those fields plus a UNIQUE `normalized_hash` purpose-built for ON CONFLICT
DO NOTHING dedup. (case_number has no dedicated column here — it is folded into
the dedup hash as the stable source key.)

Conflict handling: ON CONFLICT (normalized_hash) DO NOTHING. The hash is
  irs_bulk::{case_number}            when a case/document number is present
  md5(name|address|city|state|date|amount)   otherwise (composite key)

Usage:
  python scripts/data/irs_bulk_import.py --file data/raw/irs_import.csv --state FL --dry-run
  python scripts/data/irs_bulk_import.py --file data/raw/irs_import.psv --state FL
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

import psycopg2

# Repo root on sys.path so we can reuse the shared normalize + pipeline logger.
BASE      = Path(__file__).resolve().parent          # scripts/data
REPO_ROOT = BASE.parent.parent                        # leadflow repo root
sys.path.insert(0, str(REPO_ROOT))

from app.services.normalize import make_hash  # noqa: E402

# Console may be cp1252 on Windows — never let an emoji crash the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass


# ── Canonical schema + fuzzy column aliases ─────────────────────────────────────
# Each canonical field maps to a set of accepted source-header aliases. Headers
# are normalized (lowercased, non-alphanumerics stripped) before matching, so
# "Taxpayer Name", "taxpayer_name" and "TAXPAYER-NAME" all collapse to the same
# key.
COLUMN_ALIASES: dict[str, list[str]] = {
    "business_name": [
        "businessname", "business", "company", "companyname", "entityname",
        "dba", "businessnameowner", "taxpayerbusiness", "corporatename",
    ],
    "owner_name": [
        "ownername", "owner", "taxpayername", "taxpayer", "name", "debtorname",
        "debtor", "responsibleparty", "fullname", "individualname", "lienholder",
    ],
    "first_name": ["firstname", "fname", "first", "givenname"],
    "last_name":  ["lastname", "lname", "last", "surname", "familyname"],
    "address": [
        "address", "street", "streetaddress", "address1", "addressline1",
        "mailingaddress", "addr", "propertyaddress", "situsaddress",
    ],
    "city": ["city", "town", "municipality", "mailingcity"],
    "county_name": ["countyname", "county", "countynm", "cnty"],
    "state": ["state", "st", "stateabbr", "statecode", "province", "mailingstate"],
    "lien_amount": [
        "lienamount", "amount", "balance", "total", "taxdue", "amountdue",
        "lienbalance", "unpaidbalance", "assessedamount", "totaldue", "value",
    ],
    "filed_date": [
        "fileddate", "datefiled", "filingdate", "filedate", "recordeddate",
        "date", "liendate", "recordingdate", "assessmentdate", "noticedate",
    ],
    "lien_type": [
        "lientype", "type", "liencategory", "filingtype", "noticetype",
        "documenttype", "doctype", "instrumenttype",
    ],
    "case_number": [
        "casenumber", "caseno", "case", "serialnumber", "serialno",
        "documentnumber", "docnumber", "instrumentnumber", "instrumentno",
        "recordingnumber", "filenumber", "liennumber", "certificatenumber",
        "sourcerecordid", "recordid",
    ],
}

# Reverse lookup: normalized alias -> canonical field.
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canon, _aliases in COLUMN_ALIASES.items():
    _ALIAS_TO_CANONICAL[_norm_key := re.sub(r"[^a-z0-9]", "", _canon.lower())] = _canon
    for _a in _aliases:
        _ALIAS_TO_CANONICAL[re.sub(r"[^a-z0-9]", "", _a.lower())] = _canon


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


# ── State normalization ──────────────────────────────────────────────────────────
_ABBR_TO_NAME = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut", "DE": "delaware",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia", "PR": "puerto rico",
}
_NAME_TO_ABBR = {v: k for k, v in _ABBR_TO_NAME.items()}


def normalize_state(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    if len(s) == 2:
        return s.upper()
    return _NAME_TO_ABBR.get(s.lower(), s.upper()[:2])


# ── Value parsers ─────────────────────────────────────────────────────────────────

def parse_amount(raw) -> float | None:
    if raw is None:
        return None
    s = re.sub(r"[^\d.\-]", "", str(raw).replace("(", "-").replace(")", ""))
    if not s or s in ("-", ".", "-."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


_DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%b-%Y", "%d-%B-%Y",
    "%m-%d-%Y", "%b %d, %Y", "%B %d, %Y", "%Y%m%d", "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
]


def parse_date(raw) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ── Database ─────────────────────────────────────────────────────────────────────

def get_conn():
    import os
    url = os.getenv("DATABASE_URL")
    if url:
        from urllib.parse import urlparse
        r = urlparse(url)
        return psycopg2.connect(
            dbname=r.path[1:], user=r.username, password=r.password,
            host=r.hostname, port=r.port or 5432, sslmode="require",
        )
    return psycopg2.connect(
        host="localhost", port=5434, dbname="leadflow",
        user="postgres", password="postgres",
    )


class CountyResolver:
    """Resolve (county_name, state) -> county_id, creating missing counties on
    the fly (outside dry-run). Cached so a big file hits the DB once per county."""

    def __init__(self, cur, dry_run: bool):
        self.cur = cur
        self.dry_run = dry_run
        self.cache: dict[tuple[str, str], int | None] = {}
        self.created: list[str] = []

    def resolve(self, county_name: str, state: str) -> int | None:
        county_name = (county_name or "").strip()
        state = (state or "").strip().upper()
        if not county_name or not state:
            return None
        key = (county_name.lower(), state.lower())
        if key in self.cache:
            return self.cache[key]

        self.cur.execute(
            "SELECT id FROM counties WHERE LOWER(county_name) = %s AND UPPER(state) = %s",
            (county_name.lower(), state),
        )
        row = self.cur.fetchone()
        if row:
            self.cache[key] = row[0]
            return row[0]

        if self.dry_run:
            # Dry run can't create rows, but a live run WOULD create this county
            # and the row would insert — so return a sentinel id (not None) so the
            # row is previewed as insertable rather than counted as a failure.
            self.cache[key] = -1
            self.created.append(f"{county_name}, {state}")
            return -1

        self.cur.execute(
            "INSERT INTO counties (county_name, state) VALUES (%s, %s) RETURNING id",
            (county_name, state),
        )
        new_id = self.cur.fetchone()[0]
        self.cache[key] = new_id
        self.created.append(f"{county_name}, {state}")
        return new_id


# ── File reading ──────────────────────────────────────────────────────────────────

def detect_and_open(path: Path):
    """Return (reader, fieldnames, delimiter). Auto-detects the delimiter via
    csv.Sniffer, falling back to a comma."""
    sample = path.read_text(encoding="utf-8-sig", errors="replace")[:8192]
    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",|\t;")
        delimiter = dialect.delimiter
    except csv.Error:
        # Heuristic fallback: pick the most common candidate in the header line.
        header = sample.splitlines()[0] if sample.splitlines() else ""
        delimiter = max(",|\t;", key=lambda d: header.count(d)) or ","

    f = path.open("r", encoding="utf-8-sig", errors="replace", newline="")
    reader = csv.DictReader(f, delimiter=delimiter)
    return f, reader, reader.fieldnames or [], delimiter


def build_field_map(fieldnames: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map canonical field -> source header. Returns (mapping, unmapped_headers)."""
    mapping: dict[str, str] = {}
    unmapped: list[str] = []
    for header in fieldnames:
        canon = _ALIAS_TO_CANONICAL.get(_norm_header(header))
        if canon and canon not in mapping:
            mapping[canon] = header
        elif not canon:
            unmapped.append(header)
    return mapping, unmapped


# ── Row → DB record ────────────────────────────────────────────────────────────────

def extract_record(row: dict, fmap: dict[str, str], default_state: str) -> dict:
    def g(canon: str) -> str:
        src = fmap.get(canon)
        return (row.get(src) or "").strip() if src else ""

    business_name = g("business_name") or None
    owner_name    = g("owner_name")
    first, last   = g("first_name"), g("last_name")
    if not owner_name and (first or last):
        owner_name = f"{first} {last}".strip()
    # debtor_name is the individual/taxpayer; fall back to business when that's
    # all we have so the row is never nameless.
    debtor_name = owner_name or business_name or None

    state = normalize_state(g("state")) or default_state or None

    return {
        "business_name": business_name,
        "debtor_name":   debtor_name,
        "address_1":     g("address") or None,
        "city":          g("city") or None,
        "county_name":   g("county_name") or None,
        "state":         state,
        "amount":        parse_amount(g("lien_amount")),
        "filed_date":    parse_date(g("filed_date")),
        "lien_type":     g("lien_type") or "federal_tax_lien",
        "case_number":   g("case_number") or None,
    }


def compute_hash(rec: dict) -> str:
    if rec["case_number"]:
        return f"irs_bulk::{rec['case_number'].strip().lower()}"
    # Composite key fallback when there's no stable source id.
    return make_hash(
        rec["debtor_name"] or rec["business_name"],
        rec["address_1"], rec["city"], rec["state"],
        rec["filed_date"], rec["amount"],
    )


# ── Main ─────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="IRS / county lien bulk importer")
    parser.add_argument("--file", required=True, help="Path to CSV or pipe-delimited file")
    parser.add_argument("--state", default=None, help="Default state abbr for rows missing one (e.g. FL)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report, do not insert")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N rows")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    if not path.exists():
        print(f"❌ File not found: {path}")
        sys.exit(1)

    default_state = normalize_state(args.state) if args.state else ""

    print(f"\n{'='*60}")
    print(f"  IRS Bulk Lien Import")
    print(f"  File   : {path}")
    print(f"  State  : {default_state or '(per-row)'}")
    print(f"  {'DRY RUN — no inserts' if args.dry_run else 'LIVE — inserting into normalized_liens'}")
    print(f"{'='*60}\n")

    # Pipeline log (skipped on dry-run — a dry run isn't a real import).
    logger = None
    if not args.dry_run:
        try:
            from pipeline_log import PipelineLogger
            logger = PipelineLogger("irs_bulk_import")
            logger.start()
        except ImportError:
            logger = None

    f, reader, fieldnames, delimiter = detect_and_open(path)
    fmap, unmapped = build_field_map(fieldnames)

    delim_label = {",": "comma", "|": "pipe", "\t": "tab", ";": "semicolon"}.get(delimiter, repr(delimiter))
    print(f"  Delimiter detected : {delim_label}")
    print(f"  Columns mapped     : {len(fmap)}/{len(COLUMN_ALIASES)}")
    for canon in COLUMN_ALIASES:
        src = fmap.get(canon)
        mark = "✅" if src else "⬜"
        print(f"    {mark} {canon:<14} <- {src if src else '(not found)'}")
    if unmapped:
        print(f"  Unmapped source columns (ignored): {', '.join(unmapped)}")
    if "case_number" not in fmap:
        print("  ⚠ No case/document number column found — dedup falls back to a "
              "composite name+address+date+amount hash.")

    stats = {"read": 0, "inserted": 0, "skipped": 0, "failed": 0}
    fail_examples: list[str] = []

    conn = get_conn()
    try:
        cur = conn.cursor()
        resolver = CountyResolver(cur, args.dry_run)
        seen_hashes: set[str] = set()  # within-file dedup for dry-run preview

        if logger:
            logger.step_start("import_rows")

        for row in reader:
            if args.limit is not None and stats["read"] >= args.limit:
                break
            stats["read"] += 1
            try:
                rec = extract_record(row, fmap, default_state)
                county_id = resolver.resolve(rec["county_name"], rec["state"] or default_state)

                if county_id is None:
                    # county_id is NOT NULL — can't insert without it.
                    stats["failed"] += 1
                    if len(fail_examples) < 5:
                        fail_examples.append(
                            f"row {stats['read']}: unresolved county "
                            f"({rec['county_name'] or '?'}, {rec['state'] or '?'})"
                        )
                    continue

                n_hash = compute_hash(rec)

                if args.dry_run:
                    # Estimate insert vs duplicate without writing — check both
                    # the DB and the rows already seen in this file.
                    cur.execute(
                        "SELECT 1 FROM normalized_liens WHERE normalized_hash = %s",
                        (n_hash,),
                    )
                    if cur.fetchone() or n_hash in seen_hashes:
                        stats["skipped"] += 1
                    else:
                        seen_hashes.add(n_hash)
                        stats["inserted"] += 1
                    continue

                # Real insert, isolated by a savepoint so one bad row can't abort
                # the whole batch.
                cur.execute("SAVEPOINT row_sp")
                cur.execute(
                    """
                    INSERT INTO normalized_liens (
                        county_id, raw_lien_id, debtor_name, business_name,
                        address_1, city, state, filing_type, lien_type,
                        filed_date, amount, normalized_hash, lien_source
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (normalized_hash) DO NOTHING
                    RETURNING id
                    """,
                    (
                        county_id, None, rec["debtor_name"], rec["business_name"],
                        rec["address_1"], rec["city"], rec["state"],
                        rec["lien_type"], rec["lien_type"],
                        rec["filed_date"], rec["amount"], n_hash, "IRS",
                    ),
                )
                inserted = cur.fetchone()
                cur.execute("RELEASE SAVEPOINT row_sp")
                if inserted:
                    stats["inserted"] += 1
                else:
                    stats["skipped"] += 1

            except Exception as e:
                if not args.dry_run:
                    try:
                        cur.execute("ROLLBACK TO SAVEPOINT row_sp")
                    except Exception:
                        conn.rollback()
                stats["failed"] += 1
                if len(fail_examples) < 5:
                    fail_examples.append(f"row {stats['read']}: {e}")

        if not args.dry_run:
            conn.commit()

        if logger:
            logger.step_done(
                "import_rows", ok=True,
                detail=f"{stats['inserted']} inserted, {stats['skipped']} dupes, "
                       f"{stats['failed']} failed",
            )
    except Exception as e:
        conn.rollback()
        if logger:
            logger.step_done("import_rows", ok=False, error=str(e))
            logger.finish({"published": False, "error": str(e)})
        raise
    finally:
        f.close()
        conn.close()

    # ── Summary ──
    print(f"\n{'─'*60}")
    print(f"  Rows read     : {stats['read']}")
    print(f"  {'Would insert' if args.dry_run else 'Inserted'}      : {stats['inserted']}")
    print(f"  Skipped (dupes): {stats['skipped']}")
    print(f"  Failed        : {stats['failed']}")
    if resolver.created:
        verb = "Counties referenced (new)" if args.dry_run else "Counties created"
        print(f"  {verb}: {len(set(resolver.created))} — {', '.join(sorted(set(resolver.created))[:10])}")
    if fail_examples:
        print(f"  First failures:")
        for ex in fail_examples:
            print(f"    - {ex}")
    print(f"{'─'*60}\n")

    if logger:
        logger.finish({
            "rows_read":     stats["read"],
            "rows_inserted": stats["inserted"],
            "rows_skipped":  stats["skipped"],
            "rows_failed":   stats["failed"],
            "source_file":   path.name,
        })


if __name__ == "__main__":
    main()
