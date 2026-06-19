#!/usr/bin/env python3
"""
county_lien_intelligence.py
===========================
Weekly county lien intelligence blog post generator for TaxCase Review.

Queries the leadflow PostgreSQL database for real lien activity in a Florida
county, then writes a data-driven blog post using the Claude API and publishes
it to the v0-tax-resolution-landing-page repo via the GitHub API — the same
publish mechanism, WRITING_RULES, frontmatter style and IndexNow ping used by
scripts/archive/generate_topic_blogs.py.

County rotation is automatic by ISO week number, so each Sunday run lands on a
different county and the cycle repeats every 12 weeks.

Usage:
  python county_lien_intelligence.py --auto                 # rotate by week number
  python county_lien_intelligence.py --county "Miami-Dade" --state FL
  python county_lien_intelligence.py --auto --dry-run       # print, don't publish
  python county_lien_intelligence.py --status               # show history
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import requests
from dotenv import load_dotenv

# Load .env from the repo root explicitly so the script works regardless of the
# current working directory (Task Scheduler may launch it from anywhere).
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

# Console output contains emoji (💾 ✅ ❌ ⏭). On a non-UTF-8 console (Windows
# cp1252) printing those raises UnicodeEncodeError. Force UTF-8 with replacement
# so logging can never crash a run (same guard as pipeline_log.py). No-op on
# already-UTF-8 streams.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE         = Path(__file__).resolve().parent          # scripts/content
REPO_ROOT    = BASE.parent.parent                        # leadflow repo root
HISTORY_FILE = REPO_ROOT / "data" / "county_lien_intel_history.json"

# ── Config (identical publish target to generate_topic_blogs.py) ────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO       = os.getenv("GITHUB_REPO", "anandakeyclub-ops/v0-tax-resolution-landing-page")
GITHUB_BRANCH     = os.getenv("GITHUB_BRANCH", "main")

SITE_URL     = "https://taxcasereview.org"
PHONE        = "(561) 247-0678"
INDEXNOW_KEY = "9e9b2e673445719e87ed5e2213724841"  # same key as run_daily_blog.py
TODAY        = date.today()

# ── County rotation ─────────────────────────────────────────────────────────────
# Ordered list — rotation index is ISO week number % len(FL_COUNTIES), so the
# full cycle repeats every 12 weeks.
FL_COUNTIES = [
    "Miami-Dade", "Martin", "Lake", "Manatee", "Polk", "Pasco",
    "Osceola", "Duval", "Pinellas", "Hillsborough", "Sarasota", "Palm Beach",
]


# ── WRITING_RULES (loaded verbatim from generate_topic_blogs.py) ────────────────
# Resolve the generator wherever it lives so a future move can't silently change
# the rules out from under this script (same approach as run_daily_blog.py).
def _load_writing_rules() -> str:
    candidates = [
        REPO_ROOT / "scripts" / "archive" / "generate_topic_blogs.py",
        REPO_ROOT / "scripts" / "maintenance" / "generate_topic_blogs.py",
        REPO_ROOT / "generate_topic_blogs.py",
    ]
    for path in candidates:
        if not path.exists():
            continue
        spec = importlib.util.spec_from_file_location("generate_topic_blogs", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.WRITING_RULES
    raise FileNotFoundError(
        "generate_topic_blogs.py not found in any known location: "
        + ", ".join(str(c) for c in candidates)
    )


WRITING_RULES = _load_writing_rules()


# ── Database ─────────────────────────────────────────────────────────────────────

def get_conn():
    """Connect using DATABASE_URL if set, else fall back to localhost:5434/leadflow."""
    url = os.getenv("DATABASE_URL")
    if url:
        r = urlparse(url)
        return psycopg2.connect(
            dbname=r.path[1:], user=r.username, password=r.password,
            host=r.hostname, port=r.port or 5432, sslmode="require",
        )
    return psycopg2.connect(
        host="localhost", port=5434, dbname="leadflow",
        user="postgres", password="postgres",
    )


def _table_columns(cur, table: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return {r[0] for r in cur.fetchall()}


def gather_stats(county: str, state: str) -> dict:
    """Pull lien stats for a county. Every stat is guarded by a column-existence
    check so a schema change can never crash the run — a missing column just
    yields a skipped (None) stat.

    Real schema (verified against leadflow):
      lien_dbpr_contacts : county_id, trade, created_at, state  (no county_name,
                           no amount)  -> joined to `counties` for the name
      normalized_liens   : county_id, amount  -> source for avg lien amount
      counties           : id, county_name, state
    """
    stats = {
        "total_liens": 0,
        "new_30d": None,
        "top_trades": [],
        "avg_amount": None,
    }

    conn = get_conn()
    try:
        cur = conn.cursor()

        # Pick the contacts table that actually has trade + county linkage.
        contacts_table = None
        for candidate in ("lien_dbpr_contacts", "normalized_liens"):
            cols = _table_columns(cur, candidate)
            if cols and "county_id" in cols:
                contacts_table = candidate
                contacts_cols = cols
                break
        if not contacts_table:
            return stats

        counties_cols = _table_columns(cur, "counties")
        has_counties = {"id", "county_name", "state"} <= counties_cols

        # Build the WHERE clause + params for the chosen table. Prefer joining
        # `counties` for the human county name; fall back to a county_name column
        # on the contacts table itself if one ever appears.
        if has_counties:
            base_from = f"{contacts_table} d JOIN counties c ON c.id = d.county_id"
            where     = "c.county_name = %s AND c.state = %s"
        elif "county_name" in contacts_cols:
            base_from = f"{contacts_table} d"
            where     = "d.county_name = %s AND d.state = %s"
        else:
            return stats
        params = (county, state)

        # total_liens
        cur.execute(f"SELECT COUNT(*) FROM {base_from} WHERE {where}", params)
        stats["total_liens"] = cur.fetchone()[0]

        # new_30d — only if created_at exists
        if "created_at" in contacts_cols:
            cur.execute(
                f"SELECT COUNT(*) FROM {base_from} "
                f"WHERE {where} AND d.created_at >= NOW() - INTERVAL '30 days'",
                params,
            )
            stats["new_30d"] = cur.fetchone()[0]

        # top_trades — prefer `trade`, fall back to `business_type`
        trade_col = "trade" if "trade" in contacts_cols else (
            "business_type" if "business_type" in contacts_cols else None
        )
        if trade_col:
            cur.execute(
                f"SELECT d.{trade_col}, COUNT(*) AS n FROM {base_from} "
                f"WHERE {where} AND d.{trade_col} IS NOT NULL "
                f"GROUP BY d.{trade_col} ORDER BY n DESC LIMIT 3",
                params,
            )
            stats["top_trades"] = [(r[0], r[1]) for r in cur.fetchall()]

        # avg_amount — try the contacts table first, then normalized_liens.
        amount_col = next(
            (c for c in ("lien_amount", "amount") if c in contacts_cols), None
        )
        if amount_col:
            cur.execute(
                f"SELECT AVG(d.{amount_col}) FROM {base_from} "
                f"WHERE {where} AND d.{amount_col} IS NOT NULL",
                params,
            )
            avg = cur.fetchone()[0]
            stats["avg_amount"] = float(avg) if avg is not None else None
        elif has_counties:
            nl_cols = _table_columns(cur, "normalized_liens")
            nl_amount = next(
                (c for c in ("lien_amount", "amount") if c in nl_cols), None
            )
            if nl_amount and "county_id" in nl_cols:
                cur.execute(
                    f"SELECT AVG(n.{nl_amount}) FROM normalized_liens n "
                    f"JOIN counties c ON c.id = n.county_id "
                    f"WHERE c.county_name = %s AND c.state = %s "
                    f"AND n.{nl_amount} IS NOT NULL",
                    params,
                )
                avg = cur.fetchone()[0]
                stats["avg_amount"] = float(avg) if avg is not None else None

        return stats
    finally:
        conn.close()


# ── Blog generation ──────────────────────────────────────────────────────────────

def _county_slug(county: str) -> str:
    return county.lower().replace(" ", "-")


def build_user_prompt(county: str, stats: dict) -> str:
    today_str   = TODAY.isoformat()
    month_year  = TODAY.strftime("%B %Y")
    county_slug = _county_slug(county)

    # Format stats for the prompt; gracefully describe any skipped stat.
    top_trades = (
        ", ".join(f"{t} ({n})" for t, n in stats["top_trades"])
        if stats["top_trades"] else "Not available in current dataset"
    )
    new_30d = (
        str(stats["new_30d"]) if stats["new_30d"] is not None
        else "Not available in current dataset"
    )
    avg_amount = (
        f"${stats['avg_amount']:,.0f}" if stats["avg_amount"] is not None
        else "Not available in current dataset"
    )

    return f"""Write a 900-1100 word blog post for TaxCase Review about IRS tax lien activity in {county} County, Florida.

REAL DATA FROM OUR DATABASE (use these exact numbers):
- Total active liens tracked: {stats['total_liens']}
- New liens in last 30 days: {new_30d}
- Top contractor trades affected: {top_trades}
- Average lien amount: {avg_amount}
- Data as of: {today_str}

STRUCTURE:
# {county} County IRS Tax Lien Update — {month_year}

Intro: What we found in public records this week. Use the real numbers above.

## What the Data Shows
Real lien counts, trends, which trades are most affected.

## What This Means for {county} Contractors
Practical implications. What happens if ignored. TFRP risk for trades.

## Resolution Options Still Available
OIC (14.1% acceptance rate per IRS Data Book FY2025), installment agreement, CNC status, lien withdrawal. Be specific about what each means.

## How TaxCase Review Tracks {county} Lien Activity
Our data pipeline pulls from {county} County public records weekly. We tracked {stats['total_liens']} active liens. Former IRS Revenue Officer Romy Cruz reviews cases from this county regularly.

End with CTA linking to {SITE_URL}/florida/{county_slug}

Target keyword naturally: '{county} county IRS tax lien help'
Include disclaimer: 'Data sourced from public county records. Individual situations vary.'
Voice: Romy Cruz, former IRS Revenue Officer, EA. Direct, warm, authoritative."""


def generate_post_body(county: str, stats: dict) -> str:
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      "claude-sonnet-4-6",
            "max_tokens": 2000,
            "system":     WRITING_RULES,
            "messages":   [{"role": "user", "content": build_user_prompt(county, stats)}],
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()


def build_markdown(county: str, stats: dict, body: str) -> tuple[str, str]:
    """Returns (slug, full markdown with frontmatter prepended)."""
    county_slug = _county_slug(county)
    month_year  = TODAY.strftime("%B %Y")
    slug = f"{county_slug}-county-irs-tax-lien-update-{TODAY.year}-{TODAY.month:02d}"

    title       = f"{county} County IRS Tax Lien Update — {month_year}"
    description = (
        f"We tracked {stats['total_liens']} active IRS liens in {county} County. "
        f"Former IRS Revenue Officer Romy Cruz breaks down what the data shows "
        f"and what options remain."
    )
    tags = ["florida", county_slug, "irs-tax-lien", "county-data"]
    tags_yaml = ", ".join(f'"{t}"' for t in tags)

    frontmatter = (
        "---\n"
        f'title: "{title}"\n'
        f'date: "{TODAY.isoformat()}"\n'
        f'slug: "{slug}"\n'
        f'description: "{description}"\n'
        f"tags: [{tags_yaml}]\n"
        "---\n\n"
    )
    return slug, frontmatter + body


# ── Publish (identical mechanism to generate_topic_blogs.py) ────────────────────

def publish_to_github(slug: str, content: str) -> bool:
    if not GITHUB_TOKEN:
        print("  ⚠ GITHUB_TOKEN not set")
        return False
    file_path = f"content/blog/{slug}.md"
    api_url   = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    headers   = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github.v3+json",
    }
    sha = None
    try:
        check = requests.get(api_url, headers=headers, timeout=10)
        if check.status_code == 200:
            sha = check.json().get("sha")
    except Exception:
        pass
    content_b64 = base64.b64encode(content.encode("utf-8")).decode()
    payload = {
        "message": f"Blog: {slug} [{TODAY.isoformat()}]",
        "content": content_b64,
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(api_url, headers=headers, json=payload, timeout=30)
    return r.status_code in (200, 201)


def index_url(url: str):
    """Submit a freshly published URL to IndexNow (Bing/Yandex). Non-blocking —
    indexing must never fail a publish. Identical to run_daily_blog.index_url."""
    try:
        payload = {
            "host":        "taxcasereview.org",
            "key":         INDEXNOW_KEY,
            "keyLocation": f"https://taxcasereview.org/{INDEXNOW_KEY}.txt",
            "urlList":     [url],
        }
        r = requests.post("https://api.indexnow.org/indexnow",
                          json=payload,
                          headers={"Content-Type": "application/json"},
                          timeout=10)
        print(f"  IndexNow ping: {r.status_code} — {url}")
    except Exception as e:
        print(f"  IndexNow ping failed (non-blocking): {e}")


# ── History ──────────────────────────────────────────────────────────────────────

def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_history(slug: str):
    history = load_history()
    history[slug] = TODAY.isoformat()
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


def show_status():
    history = load_history()
    print(f"\n{'='*55}")
    print(f"  County Lien Intelligence — Publish History")
    print(f"  Total published: {len(history)}")
    print(f"{'='*55}")
    if not history:
        print("  (nothing published yet)")
        return
    for slug, pub_date in sorted(history.items(), key=lambda kv: kv[1], reverse=True):
        print(f"  ✅ {slug} — {pub_date}")


# ── Main ─────────────────────────────────────────────────────────────────────────

def select_county() -> str:
    """Auto-select county by ISO week number, cycling every len(FL_COUNTIES)."""
    week = TODAY.isocalendar()[1]
    return FL_COUNTIES[week % len(FL_COUNTIES)]


def main():
    parser = argparse.ArgumentParser(description="County Lien Intelligence Blog Generator")
    parser.add_argument("--auto",    action="store_true",
                        help="Auto-select county by ISO week number")
    parser.add_argument("--county",  default=None,
                        help="Force a specific county (e.g. \"Miami-Dade\")")
    parser.add_argument("--state",   default="FL",
                        help="State for the forced county (default FL)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the post, do not publish")
    parser.add_argument("--status",  action="store_true",
                        help="Show publish history")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if not args.auto and not args.county:
        parser.print_help()
        return

    county = args.county or select_county()
    state  = args.state.upper()

    print(f"\n{'='*55}")
    print(f"  County Lien Intelligence — {county} County, {state}")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    print(f"  {'DRY RUN' if args.dry_run else 'LIVE → publishing to GitHub'}")
    print(f"{'='*55}\n")

    # Pipeline log so this worker shows up in logs/pipeline/ like the others.
    # Skipped for --dry-run (a dry run isn't a real publish).
    logger = None
    if not args.dry_run:
        try:
            sys.path.insert(0, str(REPO_ROOT))
            from pipeline_log import PipelineLogger
            logger = PipelineLogger("county_lien_intel")
            logger.start()
        except ImportError:
            logger = None

    try:
        # Pull real lien stats.
        if logger:
            logger.step_start("gather_stats")
        stats = gather_stats(county, state)
        print(f"  Stats: total={stats['total_liens']} new_30d={stats['new_30d']} "
              f"trades={len(stats['top_trades'])} avg={stats['avg_amount']}")
        if logger:
            logger.step_done("gather_stats", ok=True,
                             detail=f"{county}: {stats['total_liens']} liens")

        # Build slug up front to check the republish guard before spending an API call.
        county_slug = _county_slug(county)
        slug = f"{county_slug}-county-irs-tax-lien-update-{TODAY.year}-{TODAY.month:02d}"

        if not args.dry_run and slug in load_history():
            print(f"  ⏭  {slug} already published this month — skipping.")
            if logger:
                logger.step_skip("publish_blog", "already published this month")
                logger.finish({"published": False, "reason": "already_published",
                               "county": county})
            return

        # Generate post body via Claude.
        if logger:
            logger.step_start("generate_post")
        body = generate_post_body(county, stats)
        slug, content = build_markdown(county, stats, body)
        if logger:
            logger.step_done("generate_post", ok=True, detail=f"{len(content)} chars")

        # Save a local draft regardless of publish outcome.
        out_dir = REPO_ROOT / "blog_drafts" / "county_intel"
        out_dir.mkdir(parents=True, exist_ok=True)
        local = out_dir / f"{slug}.md"
        local.write_text(content, encoding="utf-8")
        print(f"  💾 Saved: {local}")

        if args.dry_run:
            print(f"\n{'─'*55}\n{content}\n{'─'*55}")
            print(f"\n  [DRY RUN] Would publish: {slug}")
            return

        # Publish to GitHub.
        if logger:
            logger.step_start("publish_blog")
        ok = publish_to_github(slug, content)
        if ok:
            url = f"{SITE_URL}/blog/md/{slug}"
            print(f"  ✅ Live: {url}")
            save_history(slug)
            index_url(url)
        else:
            print(f"  ❌ GitHub publish failed — draft saved locally: {local}")
        if logger:
            logger.step_done("publish_blog", ok=ok, detail=slug)
            logger.finish({
                "published":  ok,
                "county":     county,
                "lien_count": stats["total_liens"],
                "blog_slug":  slug,
            })
        print(f"\n  Run complete: {'OK' if ok else 'FAILED'}")
    except Exception as e:
        print(f"  ❌ Error: {e}")
        if logger:
            logger.step_done("publish_blog", ok=False, error=str(e))
            logger.finish({"published": False, "county": county, "error": str(e)})
        raise


if __name__ == "__main__":
    main()
