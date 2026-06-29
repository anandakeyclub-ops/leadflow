#!/usr/bin/env python3
"""
irs_foia_importer.py
=========================================================
Imports IRS FOIA federal tax lien data (ENTITY_*.txt / PERIOD_*.txt, pipe-
delimited, no header) into Postgres for lead generation.

- ENTITY files  → irs_foia_liens   (one row per lien; state parsed from city_state_zip)
- PERIOD files  → irs_foia_periods (tax periods per lien; 941/940 = payroll = contractors)

Only active liens in the 10 target states are imported (SELF-RELEASED skipped).
After import, liens are matched against google_places_contacts by business name.

Usage:
  python scripts/scrapers/irs_foia_importer.py --all
  python scripts/scrapers/irs_foia_importer.py --state FL
  python scripts/scrapers/irs_foia_importer.py --state FL --payroll-only
  python scripts/scrapers/irs_foia_importer.py --stats
  python scripts/scrapers/irs_foia_importer.py --dry-run --state FL
"""
from __future__ import annotations

import sys
# Emoji/Unicode output must not crash under Task Scheduler's cp1252 console.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
import glob
import hashlib
import re
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]           # leadflow repo root
sys.path.insert(0, str(BASE_DIR))                        # make `app` importable when run directly

from dotenv import load_dotenv
load_dotenv()

from app.core.db import get_connection, release_connection
from psycopg2.extras import execute_values

FOIA_DIR  = BASE_DIR / "data" / "irs_foia"
TARGET_STATES = {"FL", "TX", "AZ", "GA", "NC", "SC", "PA", "OH", "TN", "VA"}
PAYROLL_FORMS = {"941", "940"}
CSZ_RE    = re.compile(r"^(.+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$")
BATCH     = 1000
LOG_EVERY = 100_000

# ENTITY has 16 data fields (0-15) + a trailing empty field from the trailing "|".
ENTITY_MIN_FIELDS = 16
PERIOD_FIELDS     = 6

DDL = [
    """CREATE TABLE IF NOT EXISTS irs_foia_liens (
        id SERIAL PRIMARY KEY,
        lien_id TEXT, district TEXT, status TEXT, refile_note TEXT, tax_id TEXT,
        debtor_name TEXT, address TEXT, city TEXT, state TEXT, zip TEXT,
        amount DECIMAL(12,2), lien_date DATE, filing_city TEXT, recorder_type TEXT,
        county TEXT, recorded_date DATE, instrument_num TEXT,
        matched_contact_id INTEGER,
        source TEXT DEFAULT 'irs_foia', record_hash TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    # matched_contact_id was absent from the original spec DDL — ensure it exists
    # even if the table was created by an older run.
    "ALTER TABLE irs_foia_liens ADD COLUMN IF NOT EXISTS matched_contact_id INTEGER",
    """CREATE TABLE IF NOT EXISTS irs_foia_periods (
        id SERIAL PRIMARY KEY,
        lien_id TEXT, form_type TEXT, period_end DATE, assessment_date DATE,
        lien_date DATE, amount DECIMAL(12,2), is_payroll BOOLEAN DEFAULT FALSE,
        source TEXT DEFAULT 'irs_foia', record_hash TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_foia_state ON irs_foia_liens(state)",
    "CREATE INDEX IF NOT EXISTS idx_foia_lien_id ON irs_foia_liens(lien_id)",
    "CREATE INDEX IF NOT EXISTS idx_foia_period_lien ON irs_foia_periods(lien_id)",
    "CREATE INDEX IF NOT EXISTS idx_foia_payroll ON irs_foia_periods(is_payroll)",
]


# ── parsing helpers ──────────────────────────────────────────────────────────

def parse_city_state_zip(s: str):
    m = CSZ_RE.match((s or "").strip())
    if not m:
        return None, None, None
    city, state, zp = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    return city, state, zp


def parse_date(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%m/%d/%Y").date()
    except Exception:
        return None


def parse_amount(s: str):
    s = (s or "").strip().replace(",", "").replace("$", "")
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _md5(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def _entity_files():
    return sorted(glob.glob(str(FOIA_DIR / "ENTITY_*.txt")))


def _period_files():
    return sorted(glob.glob(str(FOIA_DIR / "PERIOD_*.txt")))


def ensure_schema():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for stmt in DDL:
                cur.execute(stmt)
        conn.commit()
    finally:
        release_connection(conn)


# ── ENTITY import ────────────────────────────────────────────────────────────

ENTITY_INSERT = """
    INSERT INTO irs_foia_liens
        (lien_id, district, status, refile_note, tax_id, debtor_name, address,
         city, state, zip, amount, lien_date, filing_city, recorder_type, county,
         recorded_date, instrument_num, record_hash)
    VALUES %s
    ON CONFLICT (record_hash) DO NOTHING
    RETURNING 1
"""


def import_entities(state_filter, payroll_lien_ids, dry_run, limit=None):
    """Returns (counters, imported_lien_ids_set)."""
    c = {"processed": 0, "imported": 0, "skip_state": 0, "skip_released": 0,
         "skip_dup": 0, "skip_malformed": 0, "by_state": {}}
    imported_ids = set()
    conn = get_connection()
    try:
        cur = conn.cursor()
        batch, seen = [], set()
        stop = False
        for path in _entity_files():
            if stop:
                break
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if limit and c["processed"] >= limit:
                        stop = True
                        break
                    line = line.rstrip("\n").rstrip("\r")
                    if not line.strip():
                        continue
                    c["processed"] += 1
                    if c["processed"] % LOG_EVERY == 0:
                        print(f"  ...{c['processed']:,} ENTITY rows processed "
                              f"({c['imported']:,} imported)")
                    f = line.split("|")
                    if len(f) < ENTITY_MIN_FIELDS:
                        c["skip_malformed"] += 1
                        continue
                    status = (f[2] or "").strip().upper()
                    if status == "SELF-RELEASED":
                        c["skip_released"] += 1
                        continue
                    city, state, zp = parse_city_state_zip(f[7])
                    if not state or state not in TARGET_STATES:
                        c["skip_state"] += 1
                        continue
                    if state_filter and state != state_filter:
                        c["skip_state"] += 1
                        continue
                    lien_id = (f[0] or "").strip()
                    if payroll_lien_ids is not None and lien_id not in payroll_lien_ids:
                        # --payroll-only: keep only liens that have a 941/940 period
                        c["skip_state"] += 1  # filtered out (non-payroll lien)
                        continue
                    # name: field 5; sometimes the name is in field 6 (only use it
                    # when field 6 isn't an address — i.e. doesn't start with a digit).
                    name = (f[5] or "").strip()
                    if not name:
                        f6 = (f[6] or "").strip()
                        if f6 and not f6[:1].isdigit():
                            name = f6
                    amount = parse_amount(f[8])
                    rhash = _md5(lien_id, name, amount)
                    if rhash in seen:
                        c["skip_dup"] += 1
                        continue
                    seen.add(rhash)
                    row = (
                        lien_id, (f[1] or "").strip(), status, (f[3] or "").strip(),
                        (f[4] or "").strip(), name, (f[6] or "").strip(),
                        city, state, zp, amount, parse_date(f[9]),
                        (f[10] or "").strip(), (f[11] or "").strip(), (f[12] or "").strip(),
                        parse_date(f[14]), (f[15] or "").strip(), rhash,
                    )
                    if dry_run:
                        c["imported"] += 1
                        c["by_state"][state] = c["by_state"].get(state, 0) + 1
                        imported_ids.add(lien_id)
                        continue
                    batch.append(row)
                    if len(batch) >= BATCH:
                        ins = execute_values(cur, ENTITY_INSERT, batch, fetch=True)
                        n = len(ins); c["imported"] += n; c["skip_dup"] += len(batch) - n
                        conn.commit()
                        batch.clear()
            # commit per file for durability/reproducibility
            if not dry_run:
                conn.commit()
        if not dry_run and batch:
            ins = execute_values(cur, ENTITY_INSERT, batch, fetch=True)
            n = len(ins); c["imported"] += n; c["skip_dup"] += len(batch) - n
            conn.commit()
        # by-state + imported ids from DB (authoritative for the live path)
        if not dry_run:
            sf = " WHERE state = %s" if state_filter else ""
            with conn.cursor() as cur2:
                cur2.execute(f"SELECT state, COUNT(*) FROM irs_foia_liens{sf} GROUP BY state",
                             (state_filter,) if state_filter else None)
                c["by_state"] = {r[0]: r[1] for r in cur2.fetchall()}
                cur2.execute(f"SELECT lien_id FROM irs_foia_liens{sf}",
                             (state_filter,) if state_filter else None)
                imported_ids = {r[0] for r in cur2.fetchall()}
    except Exception as e:
        conn.rollback()
        print(f"  ENTITY import error: {e}")
        raise
    finally:
        release_connection(conn)
    return c, imported_ids


# ── PERIOD import ────────────────────────────────────────────────────────────

PERIOD_INSERT = """
    INSERT INTO irs_foia_periods
        (lien_id, form_type, period_end, assessment_date, lien_date, amount,
         is_payroll, record_hash)
    VALUES %s
    ON CONFLICT (record_hash) DO NOTHING
    RETURNING 1
"""


def import_periods(imported_lien_ids, payroll_only, dry_run, limit=None):
    c = {"processed": 0, "imported": 0, "payroll": 0, "skip_no_lien": 0,
         "skip_dup": 0, "skip_malformed": 0, "skip_nonpayroll": 0}
    conn = get_connection()
    try:
        cur = conn.cursor()
        batch, seen = [], set()
        stop = False
        for path in _period_files():
            if stop:
                break
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if limit and c["processed"] >= limit:
                        stop = True
                        break
                    line = line.rstrip("\n").rstrip("\r")
                    if not line.strip():
                        continue
                    c["processed"] += 1
                    if c["processed"] % LOG_EVERY == 0:
                        print(f"  ...{c['processed']:,} PERIOD rows processed "
                              f"({c['imported']:,} imported)")
                    f = line.split("|")
                    if len(f) < PERIOD_FIELDS:
                        c["skip_malformed"] += 1
                        continue
                    lien_id   = (f[0] or "").strip()
                    form_type = (f[1] or "").strip()
                    if lien_id not in imported_lien_ids:
                        c["skip_no_lien"] += 1
                        continue
                    is_payroll = form_type in PAYROLL_FORMS
                    if payroll_only and not is_payroll:
                        c["skip_nonpayroll"] += 1
                        continue
                    rhash = _md5(lien_id, form_type, (f[2] or "").strip())
                    if rhash in seen:
                        c["skip_dup"] += 1
                        continue
                    seen.add(rhash)
                    row = (lien_id, form_type, parse_date(f[2]), parse_date(f[3]),
                           parse_date(f[4]), parse_amount(f[5]), is_payroll, rhash)
                    if dry_run:
                        c["imported"] += 1
                        if is_payroll:
                            c["payroll"] += 1
                        continue
                    batch.append(row)
                    if len(batch) >= BATCH:
                        ins = execute_values(cur, PERIOD_INSERT, batch, fetch=True)
                        n = len(ins); c["imported"] += n; c["skip_dup"] += len(batch) - n
                        c["payroll"] += sum(1 for r in batch if r[6])
                        conn.commit()
                        batch.clear()
            if not dry_run:
                conn.commit()
        if not dry_run and batch:
            ins = execute_values(cur, PERIOD_INSERT, batch, fetch=True)
            n = len(ins); c["imported"] += n; c["skip_dup"] += len(batch) - n
            c["payroll"] += sum(1 for r in batch if r[6])
            conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"  PERIOD import error: {e}")
        raise
    finally:
        release_connection(conn)
    return c


# ── match against google_places_contacts ─────────────────────────────────────

MATCH_SQL = """
    UPDATE irs_foia_liens f
    SET matched_contact_id = g.id
    FROM google_places_contacts g
    WHERE f.state = g.state
      AND f.status != 'SELF-RELEASED'
      AND LENGTH(f.debtor_name) >= 6
      AND LENGTH(g.business_name) >= 6
      AND UPPER(LEFT(f.debtor_name, 8)) = UPPER(LEFT(g.business_name, 8))
"""


def run_match():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(MATCH_SQL)
            matched = cur.rowcount
        conn.commit()
        return matched
    except Exception as e:
        conn.rollback()
        print(f"  match error: {e}")
        return 0
    finally:
        release_connection(conn)


# ── stats ────────────────────────────────────────────────────────────────────

def print_stats():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            def one(sql, params=()):
                cur.execute(sql, params); return cur.fetchone()[0]
            print("\n" + "=" * 56)
            print("  IRS FOIA — Database Stats")
            print("=" * 56)
            print(f"  Total liens:        {one('SELECT COUNT(*) FROM irs_foia_liens'):,}")
            print(f"  Total periods:      {one('SELECT COUNT(*) FROM irs_foia_periods'):,}")
            print(f"  Payroll liens(941/940 periods): "
                  f"{one('SELECT COUNT(*) FROM irs_foia_periods WHERE is_payroll'):,}")
            print(f"  Matched to Google Places: "
                  f"{one('SELECT COUNT(*) FROM irs_foia_liens WHERE matched_contact_id IS NOT NULL'):,}")
            print("\n  Liens by state:")
            cur.execute("SELECT state, COUNT(*) FROM irs_foia_liens GROUP BY state ORDER BY 2 DESC")
            for st, n in cur.fetchall():
                print(f"    {st or '?':4} {n:,}")
            print("\n  Top 10 counties by lien count:")
            cur.execute("SELECT COALESCE(county,'?'), COUNT(*) FROM irs_foia_liens "
                        "GROUP BY county ORDER BY 2 DESC LIMIT 10")
            for cty, n in cur.fetchall():
                print(f"    {n:>7,}  {cty}")
            print("=" * 56)
    except Exception as e:
        print(f"  stats error: {e}")
    finally:
        release_connection(conn)


# ── main ─────────────────────────────────────────────────────────────────────

def _scan_payroll_lien_ids():
    """First pass over PERIOD files → set of lien_ids that have a 941/940 period."""
    ids = set()
    for path in _period_files():
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                f = line.rstrip().split("|")
                if len(f) >= PERIOD_FIELDS and (f[1] or "").strip() in PAYROLL_FORMS:
                    ids.add((f[0] or "").strip())
    return ids


def main():
    ap = argparse.ArgumentParser(description="IRS FOIA lien importer")
    ap.add_argument("--all", action="store_true", help="Import all target states")
    ap.add_argument("--state", default=None, help="Import a single state (e.g. FL)")
    ap.add_argument("--payroll-only", action="store_true",
                    help="Only liens that have a 941/940 (payroll) period")
    ap.add_argument("--stats", action="store_true", help="Show DB stats and exit")
    ap.add_argument("--dry-run", action="store_true", help="Parse + count, no DB writes")
    ap.add_argument("--limit", type=int, default=None,
                    help="Max rows to process per phase (for testing a subset)")
    args = ap.parse_args()

    if not FOIA_DIR.exists():
        print(f"FOIA dir not found: {FOIA_DIR}"); return

    if args.stats:
        print_stats(); return

    state_filter = args.state.strip().upper() if args.state else None
    if state_filter and state_filter not in TARGET_STATES:
        print(f"'{state_filter}' is not a target state {sorted(TARGET_STATES)}"); return
    if not args.all and not state_filter:
        print("Specify --all or --state <ST> (or --stats)."); return

    logger = None
    if not args.dry_run:
        try:
            sys.path.insert(0, str(BASE_DIR))
            from pipeline_log import PipelineLogger
            logger = PipelineLogger("irs_foia_import")
            logger.start()
        except Exception:
            logger = None

    print(f"\nIRS FOIA importer — {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"  Target: {state_filter or 'ALL target states'} | "
          f"payroll-only: {args.payroll_only}")
    print(f"  ENTITY files: {len(_entity_files())} | PERIOD files: {len(_period_files())}")

    try:
        if not args.dry_run:
            ensure_schema()

        payroll_ids = None
        if args.payroll_only:
            print("  Pre-scanning PERIOD files for 941/940 lien IDs...")
            payroll_ids = _scan_payroll_lien_ids()
            print(f"  {len(payroll_ids):,} liens have a payroll period")

        if logger: logger.step_start("import_entities")
        ec, imported_ids = import_entities(state_filter, payroll_ids, args.dry_run, args.limit)
        if logger: logger.step_done("import_entities", ok=True,
                                    detail=f"{ec['imported']} imported / {ec['processed']} processed")

        if logger: logger.step_start("import_periods")
        pc = import_periods(imported_ids, args.payroll_only, args.dry_run, args.limit)
        if logger: logger.step_done("import_periods", ok=True,
                                    detail=f"{pc['imported']} imported ({pc['payroll']} payroll)")

        matched = 0
        if not args.dry_run:
            if logger: logger.step_start("match_google_places")
            matched = run_match()
            if logger: logger.step_done("match_google_places", ok=True, detail=f"{matched} matched")

        # ── summary ──
        print("\n" + "=" * 56)
        print("  IMPORT SUMMARY" + ("  [DRY RUN]" if args.dry_run else ""))
        print("=" * 56)
        print(f"  ENTITY processed:   {ec['processed']:,}")
        print(f"  ENTITY imported:    {ec['imported']:,}")
        print(f"    by state: " + ", ".join(f"{k}:{v:,}" for k, v in sorted(ec['by_state'].items())))
        print(f"  ENTITY skipped:     wrong-state/filtered {ec['skip_state']:,} · "
              f"released {ec['skip_released']:,} · dup {ec['skip_dup']:,} · "
              f"malformed {ec['skip_malformed']:,}")
        print(f"  PERIOD processed:   {pc['processed']:,}")
        print(f"  PERIOD imported:    {pc['imported']:,}")
        print(f"  Payroll (941/940):  {pc['payroll']:,}")
        print(f"  PERIOD skipped:     no-lien {pc['skip_no_lien']:,} · "
              f"non-payroll {pc['skip_nonpayroll']:,} · dup {pc['skip_dup']:,} · "
              f"malformed {pc['skip_malformed']:,}")
        print(f"  Matched to Google Places: {matched:,}")
        print("=" * 56)

        if logger:
            logger.finish({
                "entity_processed": ec["processed"], "entity_imported": ec["imported"],
                "period_imported": pc["imported"], "payroll": pc["payroll"],
                "matched": matched, "state": state_filter or "all",
            })
    except Exception as e:
        print(f"\nFATAL: {e}")
        import traceback; traceback.print_exc()
        if logger:
            try: logger.finish({"error": str(e)})
            except Exception: pass
        return

    if not args.dry_run:
        print_stats()


if __name__ == "__main__":
    main()
