"""
outreach_db.py
=============
Shared persistence for all outreach systems (HARO, guest post, press release,
broken link, directory). Replaces the per-script CSV trackers with one DB table.

  python scripts/outreach/outreach_db.py --create     # create the table
  python scripts/outreach/outreach_db.py --migrate     # import legacy CSV trackers
  python scripts/outreach/outreach_db.py --counts      # print cumulative counts

record_outreach() upserts on UNIQUE(domain, outreach_type); each script calls it
with a domain key unique enough not to clobber distinct opportunities (see the
per-type notes in migrate_csvs / each script).
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
OUT_DIR  = BASE_DIR / "data" / "outreach"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from app.core.db import get_connection, release_connection
except Exception:  # pragma: no cover
    get_connection = None
    release_connection = None

DDL = """
CREATE TABLE IF NOT EXISTS backlink_outreach (
  id SERIAL PRIMARY KEY,
  domain VARCHAR(255) NOT NULL,
  outreach_type VARCHAR(50) NOT NULL,
  contact_email VARCHAR(255),
  contact_name VARCHAR(255),
  subject TEXT,
  pitched_at TIMESTAMP,
  status VARCHAR(50) DEFAULT 'pending',
  article_title TEXT,
  published_url TEXT,
  backlink_url TEXT,
  backlink_confirmed BOOLEAN DEFAULT FALSE,
  notes TEXT,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(domain, outreach_type)
);
"""

_FIELDS = ("contact_email", "contact_name", "subject", "pitched_at", "status",
           "article_title", "published_url", "backlink_url",
           "backlink_confirmed", "notes")


def _conn():
    if get_connection is None:
        raise RuntimeError("DB unavailable (app.core.db not importable)")
    return get_connection()


def ensure_table(cur) -> None:
    cur.execute(DDL)


def record_outreach(domain: str, outreach_type: str, **fields) -> bool:
    """Upsert one outreach row. Non-fatal: returns False on any DB error so an
    outreach run never crashes on logging."""
    if not domain or get_connection is None:
        return False
    cols = ["domain", "outreach_type"]
    vals = [domain[:255], outreach_type[:50]]
    for k in _FIELDS:
        if k in fields and fields[k] not in (None, ""):
            cols.append(k); vals.append(fields[k])
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in ("domain", "outreach_type"))
    updates = (updates + ", updated_at=NOW()") if updates else "updated_at=NOW()"
    sql = (f"INSERT INTO backlink_outreach ({', '.join(cols)}) "
           f"VALUES ({', '.join(['%s'] * len(vals))}) "
           f"ON CONFLICT (domain, outreach_type) DO UPDATE SET {updates}")
    conn = _conn()
    try:
        with conn.cursor() as cur:
            ensure_table(cur)
            cur.execute(sql, vals)
        conn.commit()
        return True
    except Exception as e:
        try: conn.rollback()
        except Exception: pass
        print(f"  [outreach_db] record failed ({outreach_type}/{domain}): {e}")
        return False
    finally:
        if release_connection: release_connection(conn)
        else: conn.close()


def _domainify(s: str) -> str:
    s = re.sub(r"^https?://", "", (s or "").strip().lower())
    return s.split("/")[0] or s[:60] or "unknown"


def _read_csv(name: str) -> list[dict]:
    p = OUT_DIR / name
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def migrate_csvs() -> dict:
    """Import the legacy CSV trackers into backlink_outreach. Idempotent (upsert).
    Domain keys are chosen to keep distinct opportunities distinct."""
    counts = {}
    # HARO — domain = outlet (one tracked opportunity per outlet)
    n = 0
    for r in _read_csv("haro_log.csv"):
        outlet = r.get("outlet", "");
        if not outlet: continue
        status = "sent" if r.get("response_sent") == "Yes" else (
            "drafted" if r.get("response_drafted") == "Yes" else "reviewed")
        if record_outreach(_domainify(outlet) + f"#{(r.get('query_summary') or '')[:20]}",
                           "haro", subject=r.get("query_summary", ""), status=status,
                           backlink_url=r.get("backlink_url", ""),
                           backlink_confirmed=bool(r.get("backlink_url"))):
            n += 1
    counts["haro"] = n
    # Guest post — domain = target
    n = 0
    for r in _read_csv("guest_post_tracker.csv"):
        tgt = r.get("target", "")
        if not tgt: continue
        if record_outreach(_domainify(tgt), "guest_post",
                           contact_email=r.get("contact_email", ""),
                           pitched_at=r.get("pitched_date") or None,
                           status=r.get("response_status", "pending"),
                           article_title=r.get("article_assigned", ""),
                           published_url=r.get("published_date", ""),
                           backlink_url=r.get("backlink_url", ""),
                           backlink_confirmed=bool(r.get("backlink_url"))):
            n += 1
    counts["guest_post"] = n
    # Press release — domain = pr-<date>-<headline slug> (keep each PR distinct)
    n = 0
    for r in _read_csv("press_release_log.csv"):
        head = r.get("headline", "")
        if not head or head.startswith("(not"): continue
        slug = re.sub(r"[^a-z0-9]+", "-", head.lower())[:40]
        if record_outreach(f"pr-{r.get('date','')}-{slug}", "press_release",
                           subject=head, pitched_at=r.get("date") or None,
                           status="submitted" if r.get("submitted") == "Yes" else "drafted",
                           backlink_url=r.get("backlink_url", ""),
                           backlink_confirmed=bool(r.get("backlink_url"))):
            n += 1
    counts["press_release"] = n
    # Broken link — domain = source_page host
    n = 0
    for r in _read_csv("broken_link_outreach.csv"):
        src = r.get("source_page", "")
        if not src: continue
        if record_outreach(_domainify(src), "broken_link",
                           subject=r.get("subject", ""),
                           status="drafted" if r.get("drafted") == "Yes" else "pending",
                           published_url=r.get("replacement_url", ""),
                           backlink_url=r.get("backlink_url", ""),
                           backlink_confirmed=bool(r.get("backlink_url"))):
            n += 1
    counts["broken_link"] = n
    # Directory — domain = directory site
    n = 0
    for r in _read_csv("directory_list.csv"):
        url = r.get("url", "")
        if not url: continue
        if record_outreach(_domainify(url), "directory",
                           subject=r.get("directory_name", ""),
                           status="submitted" if (r.get("submitted") or "").lower() == "yes" else "pending",
                           backlink_url=r.get("backlink_url", ""),
                           backlink_confirmed=bool(r.get("backlink_url"))):
            n += 1
    counts["directory"] = n
    return counts


def rows_for(outreach_type: str) -> list[dict]:
    """All rows for one outreach type (used by scripts for dedup/state). Empty on
    DB/table absence."""
    if get_connection is None:
        return []
    conn = _conn()
    try:
        with conn.cursor() as cur:
            ensure_table(cur)
            cur.execute("""SELECT domain, contact_email, pitched_at, status,
                                  article_title, published_url, backlink_url
                           FROM backlink_outreach WHERE outreach_type=%s""", (outreach_type,))
            cols = ["domain", "contact_email", "pitched_at", "status",
                    "article_title", "published_url", "backlink_url"]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        if release_connection: release_connection(conn)
        else: conn.close()


def get_counts() -> dict:
    """Cumulative counts for the daily summary. Returns zeros if DB/table absent."""
    if get_connection is None:
        return {}
    conn = _conn()
    try:
        with conn.cursor() as cur:
            ensure_table(cur)
            def one(sql):
                cur.execute(sql); return cur.fetchone()[0] or 0
            return {
                "guest_post_total": one("SELECT COUNT(*) FROM backlink_outreach WHERE outreach_type='guest_post' AND pitched_at IS NOT NULL"),
                "guest_post_responses": one("SELECT COUNT(*) FROM backlink_outreach WHERE outreach_type='guest_post' AND status IN ('responded','accepted','published')"),
                "press_release_month": one("SELECT COUNT(*) FROM backlink_outreach WHERE outreach_type='press_release' AND date_trunc('month', COALESCE(pitched_at, created_at)) = date_trunc('month', NOW())"),
                "broken_link_total": one("SELECT COUNT(*) FROM backlink_outreach WHERE outreach_type='broken_link'"),
                "directories_submitted": one("SELECT COUNT(*) FROM backlink_outreach WHERE outreach_type='directory' AND status='submitted'"),
                "backlinks_confirmed": one("SELECT COUNT(*) FROM backlink_outreach WHERE backlink_confirmed=TRUE"),
            }
    finally:
        if release_connection: release_connection(conn)
        else: conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="backlink_outreach table admin")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--migrate", action="store_true")
    ap.add_argument("--counts", action="store_true")
    args = ap.parse_args()
    if args.create:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                ensure_table(cur)
            conn.commit(); print("  backlink_outreach table ready")
        finally:
            (release_connection or (lambda c: c.close()))(conn)
    if args.migrate:
        print("  migrated:", migrate_csvs())
    if args.counts:
        print("  counts:", get_counts())
    if not any((args.create, args.migrate, args.counts)):
        ap.print_help()


if __name__ == "__main__":
    main()
