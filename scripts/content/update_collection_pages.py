#!/usr/bin/env python3
r"""
update_collection_pages.py
==========================
Refresh the *verified* IRS-lien stats on the TaxCase Review collection pages
from the leadflow DB, and flag counties that have grown big enough to deserve
their own page.

What it does (v1 scope: Florida + Texas):
  1. Query normalized_liens + matched_leads/contacts for, per county:
       - tracked liens (verified liens in our DB)
       - email-ready leads (matched, valid enriched email, not opted out)
       - match rate %  (matched liens / tracked liens)
  2. Regenerate lib/seo-data/live-lien-stats.ts in the v0-tax-landing repo and
     publish it via the GitHub Contents API (same pattern as the blog scripts).
     This NEVER touches the population-based SEO estimates in *-locations.ts.
  3. Detect Florida counties with >= --threshold liens that have NO entry in
     floridaCounties[] yet — report them as "new page" drafts (does NOT publish
     a new county unless --publish-new is given).
  4. POST the Vercel deploy hook (VERCEL_DEPLOY_HOOK_URL) so the static pages
     rebuild, if the env var is set.
  5. Log a PipelineLogger("collection_pages") record so the daily summary's
     "Content Automation" section shows pages updated / drafts created.

Usage:
  python scripts/content/update_collection_pages.py                 # FL + TX, publish
  python scripts/content/update_collection_pages.py --dry-run       # query + diff only
  python scripts/content/update_collection_pages.py --states fl
  python scripts/content/update_collection_pages.py --publish-new   # auto-add new FL counties
  python scripts/content/update_collection_pages.py --threshold 50
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

from app.core.db import get_connection, release_connection  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "anandakeyclub-ops/v0-tax-resolution-landing-page")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
VERCEL_DEPLOY_HOOK_URL = os.getenv("VERCEL_DEPLOY_HOOK_URL", "")

# Path of the generated data file inside the frontend repo.
TARGET_PATH = "lib/seo-data/live-lien-stats.ts"
# Authoritative source of which FL counties already have pages.
FL_LOCATIONS_PATH = "lib/seo-data/florida-locations.ts"

# Min lead_score for an "email-ready" lead. Mirror the email job's threshold if
# it's importable, else count every emailable matched_dbpr lead.
try:
    from app.workers.generate_email_list import MIN_LEAD_SCORE as _MIN_SCORE  # type: ignore
    EMAIL_READY_MIN_SCORE = int(_MIN_SCORE)
except Exception:
    EMAIL_READY_MIN_SCORE = int(os.getenv("EMAIL_READY_MIN_SCORE", "0"))

DEFAULT_STATES = ["fl", "tx"]
DEFAULT_THRESHOLD = 50

# Blog publish history written by scripts/maintenance/run_daily_blog.py.
BLOG_HISTORY_FILE = LEADFLOW_DIR / "data" / "blog_publish_history.json"
RECENT_POST_DAYS = 30
RECENT_POST_CAP = 3
STATE_FULL_NAME = {"fl": "florida", "tx": "texas"}
SLUG_ACRONYMS = {"irs": "IRS", "oic": "OIC", "llc": "LLC", "cp14": "CP14",
                 "cp504": "CP504", "lt11": "LT11", "tax": "Tax"}


# ── Helpers ───────────────────────────────────────────────────────────────────
def slugify_county(county_name: str) -> str:
    """'Miami-Dade County' -> 'miami-dade'; matches the frontend county slugs."""
    s = county_name.strip().lower()
    s = re.sub(r"\s+county$", "", s)            # drop a trailing ' county'
    s = re.sub(r"[^a-z0-9]+", "-", s)           # non-alnum runs -> hyphen
    return s.strip("-")


def humanize_slug(slug: str) -> str:
    """'florida-irs-tax-lien-help' -> 'Florida IRS Tax Lien Help'."""
    words = []
    for w in slug.split("-"):
        words.append(SLUG_ACRONYMS.get(w, w.capitalize()))
    return " ".join(words)


def load_blog_history() -> dict:
    """{slug: 'YYYY-MM-DD'} of published posts; empty if missing/unreadable."""
    try:
        return json.loads(BLOG_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  blog history unavailable ({e}) — skipping recent posts")
        return {}


def recent_posts_for(history: dict, county_slug: str, state_code: str,
                     today: date) -> list[dict]:
    """Posts from the last RECENT_POST_DAYS whose slug mentions the county or
    state, newest first, capped at RECENT_POST_CAP."""
    state_name = STATE_FULL_NAME.get(state_code, state_code)
    matches = []
    for slug, pub in history.items():
        try:
            d = date.fromisoformat(pub)
        except Exception:
            continue
        if d > today or (today - d).days > RECENT_POST_DAYS:
            continue
        s = slug.lower()
        if county_slug in s or state_name in s:
            matches.append((d, slug))
    matches.sort(key=lambda x: x[0], reverse=True)
    return [{"title": humanize_slug(slug), "date": d.isoformat(), "slug": slug}
            for d, slug in matches[:RECENT_POST_CAP]]


def query_county_stats(states: list[str]) -> dict[str, dict[str, dict]]:
    """Return {state: {slug: {trackedLiens, emailReadyLeads, matchRatePct}}}."""
    state_codes = [s.upper() for s in states]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH lien_agg AS (
                    SELECT c.id AS county_id, c.county_name, UPPER(c.state) AS state,
                           COUNT(nl.id)                 AS liens,
                           COUNT(DISTINCT ml.lien_id)   AS matched
                    FROM counties c
                    JOIN normalized_liens nl ON nl.county_id = c.id
                    LEFT JOIN matched_leads ml ON ml.lien_id = nl.id
                    WHERE UPPER(c.state) = ANY(%(states)s)
                    GROUP BY c.id, c.county_name, c.state
                ),
                email_agg AS (
                    SELECT ml.county_id, COUNT(*) AS email_ready
                    FROM matched_leads ml
                    JOIN contacts ct ON ml.id = ct.lead_id
                    WHERE COALESCE(ml.lead_score, 0) >= %(min_score)s
                      AND COALESCE(ml.lead_status, 'new')
                          NOT IN ('replied', 'booked', 'closed', 'do_not_contact')
                      AND ct.email IS NOT NULL
                      AND ct.email LIKE '%%@%%'
                      AND ct.email NOT LIKE '%%leadflow.invalid'
                      AND ct.email NOT LIKE '%%noemail%%'
                      AND ct.enrichment_status LIKE 'matched_dbpr%%'
                    GROUP BY ml.county_id
                )
                SELECT la.county_name, la.state, la.liens, la.matched,
                       COALESCE(ea.email_ready, 0) AS email_ready
                FROM lien_agg la
                LEFT JOIN email_agg ea ON ea.county_id = la.county_id
                WHERE la.liens > 0
                ORDER BY la.state, la.liens DESC
                """,
                {"states": state_codes, "min_score": EMAIL_READY_MIN_SCORE},
            )
            rows = cur.fetchall()
        conn.commit()
    finally:
        release_connection(conn)

    out: dict[str, dict[str, dict]] = {s.lower(): {} for s in states}
    for county_name, state, liens, matched, email_ready in rows:
        st = (state or "").lower()
        if st not in out:
            out[st] = {}
        slug = slugify_county(county_name)
        match_pct = round(100.0 * matched / liens, 1) if liens else 0.0
        out[st][slug] = {
            "trackedLiens": int(liens),
            "emailReadyLeads": int(email_ready),
            "matchRatePct": match_pct,
        }
    return out


# ── GitHub Contents API (same pattern as scripts/utils/blog_stats_refresh.py) ──
def _gh_headers() -> dict:
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def github_get_file(path: str) -> tuple[str | None, str | None]:
    """Return (decoded_text, sha) for a repo file, or (None, None) if missing."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}?ref={GITHUB_BRANCH}"
    try:
        r = requests.get(url, headers=_gh_headers(), timeout=20)
        if r.status_code == 200:
            data = r.json()
            text = base64.b64decode(data["content"]).decode("utf-8")
            return text, data.get("sha")
    except Exception as e:
        print(f"  github_get_file({path}) failed: {e}")
    return None, None


def github_put_file(path: str, content: str, message: str, sha: str | None) -> bool:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode(),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=_gh_headers(), json=payload, timeout=30)
    ok = r.status_code in (200, 201)
    print(f"  {'OK ' if ok else 'FAIL'} {r.status_code} PUT {path}")
    return ok


def trigger_deploy() -> bool:
    if not VERCEL_DEPLOY_HOOK_URL:
        print("  VERCEL_DEPLOY_HOOK_URL not set — skipping deploy trigger "
              "(content pushed; Vercel must rebuild via Git integration).")
        return False
    try:
        r = requests.post(VERCEL_DEPLOY_HOOK_URL, timeout=20)
        ok = r.status_code in (200, 201)
        print(f"  Vercel deploy hook: {r.status_code}")
        return ok
    except Exception as e:
        print(f"  Vercel deploy hook failed (non-blocking): {e}")
        return False


# ── Code generation ───────────────────────────────────────────────────────────
def render_ts(stats: dict[str, dict[str, dict]], generated_at: str) -> str:
    """Render the live-lien-stats.ts file deterministically."""
    lines = [
        "// AUTO-GENERATED by leadflow/scripts/content/update_collection_pages.py",
        "// Do NOT edit by hand — the entire file is overwritten on each daily run.",
        "//",
        "// These are VERIFIED IRS liens TaxCase Review is actively tracking in its own",
        "// database (scraped + matched). They are deliberately SEPARATE from the",
        "// population-based SEO estimates in the *-locations.ts files (estimatedTaxLiens,",
        "// taxLienFilings*) — those are never overwritten.",
        "",
        "export interface LiveLienStat {",
        "  trackedLiens: number",
        "  emailReadyLeads: number",
        "  matchRatePct: number",
        "  lastUpdated: string",
        "}",
        "",
        "export const liveLienStats: Record<string, Record<string, LiveLienStat>> = {",
    ]
    for state in sorted(stats.keys()):
        counties = stats[state]
        lines.append(f"  {state}: {{")
        for slug in sorted(counties.keys()):
            d = counties[slug]
            posts = ""
            if d.get("recentPosts"):
                items = ", ".join(
                    "{ title: %s, date: %s, slug: %s }"
                    % (json.dumps(p["title"]), json.dumps(p["date"]), json.dumps(p["slug"]))
                    for p in d["recentPosts"]
                )
                posts = f", recentPosts: [{items}]"
            lines.append(
                f'    "{slug}": {{ trackedLiens: {d["trackedLiens"]}, '
                f'emailReadyLeads: {d["emailReadyLeads"]}, '
                f'matchRatePct: {d["matchRatePct"]}, '
                f'lastUpdated: "{d["lastUpdated"]}"{posts} }},'
            )
        lines.append("  },")
    lines += [
        "}",
        "",
        f'export const liveLienStatsGeneratedAt = "{generated_at}"',
        "",
        "export function getLiveLienStat(state: string, countySlug: string): LiveLienStat | undefined {",
        "  return liveLienStats[state.toLowerCase()]?.[countySlug]",
        "}",
        "",
    ]
    return "\n".join(lines)


def parse_existing_tracked(ts_text: str) -> dict[str, int]:
    """Pull existing '<slug>': { trackedLiens: N ... } pairs for change detection."""
    out: dict[str, int] = {}
    for m in re.finditer(r'"([a-z0-9-]+)":\s*\{\s*trackedLiens:\s*(\d+)', ts_text):
        out[m.group(1)] = int(m.group(2))
    return out


def existing_fl_slugs(fl_text: str) -> set[str]:
    return set(re.findall(r'slug:\s*"([^"]+)"', fl_text))


def strip_timestamp(ts_text: str) -> str:
    """Remove the generatedAt line so unchanged data doesn't look 'changed'."""
    return re.sub(r'export const liveLienStatsGeneratedAt = "[^"]*"', "", ts_text or "")


# ── Main run (callable from run_daily.py) ─────────────────────────────────────
def run(states: list[str] | None = None, dry_run: bool = False,
        publish_new: bool = False, threshold: int = DEFAULT_THRESHOLD) -> dict:
    states = states or DEFAULT_STATES
    today = date.today().isoformat()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("collection_pages")
        logger.start()
        logger.step_start("query_db")
    except Exception:
        logger = None

    stats = query_county_stats(states)
    for st in stats.values():
        for d in st.values():
            d["lastUpdated"] = today
    counties_with_data = sum(len(v) for v in stats.values())
    print(f"  Counties with tracked liens: {counties_with_data} "
          f"({', '.join(f'{k}:{len(v)}' for k, v in stats.items())})")
    if logger:
        logger.step_done("query_db", ok=True, detail=f"{counties_with_data} counties")

    # New-county drafts: FL counties >= threshold with no floridaCounties[] entry.
    fl_text, _ = github_get_file(FL_LOCATIONS_PATH)
    have_fl = existing_fl_slugs(fl_text or "")
    new_drafts = [
        {"slug": slug, "liens": d["trackedLiens"]}
        for slug, d in stats.get("fl", {}).items()
        if d["trackedLiens"] >= threshold and slug not in have_fl
    ]

    # Recent blog posts → "Recent insights" per county (keeps pages fresh on
    # every blog publish, without touching the SEO estimates). Attach to every
    # FL county that already has a page, and to TX counties with tracked liens.
    history = load_blog_history()
    today_d = date.today()
    posts_attached = 0
    for slug in sorted(have_fl):
        rp = recent_posts_for(history, slug, "fl", today_d)
        if rp:
            entry = stats.setdefault("fl", {}).setdefault(
                slug, {"trackedLiens": 0, "emailReadyLeads": 0,
                       "matchRatePct": 0.0, "lastUpdated": today})
            entry["recentPosts"] = rp
            posts_attached += 1
    for slug, entry in stats.get("tx", {}).items():
        rp = recent_posts_for(history, slug, "tx", today_d)
        if rp:
            entry["recentPosts"] = rp
            posts_attached += 1
    if posts_attached:
        print(f"  Recent blog posts attached to {posts_attached} counties")
    if new_drafts:
        print(f"  NEW FL county page candidates (>= {threshold} liens, no page yet):")
        for d in new_drafts:
            print(f"    - {d['slug']} ({d['liens']} liens)")

    # Diff against the published file (ignoring the timestamp line).
    new_ts = render_ts(stats, generated_at)
    old_ts, old_sha = github_get_file(TARGET_PATH)
    prev = parse_existing_tracked(old_ts or "")
    now = {slug: d["trackedLiens"] for st in stats.values() for slug, d in st.items()}
    updated = sum(1 for slug, n in now.items() if prev.get(slug) != n)
    content_changed = strip_timestamp(new_ts) != strip_timestamp(old_ts or "")

    published = deployed = False
    if dry_run:
        print(f"  [DRY RUN] would publish {counties_with_data} counties "
              f"({updated} changed); content_changed={content_changed}")
    elif counties_with_data == 0:
        print("  No county data — skipping publish (DB empty or unreachable).")
    elif not content_changed:
        print("  No change since last publish — skipping GitHub PUT + deploy.")
    else:
        msg = (f"content: refresh verified lien stats — {counties_with_data} counties, "
               f"{updated} changed [{today}]")
        published = github_put_file(TARGET_PATH, new_ts, msg, old_sha)
        if published:
            deployed = trigger_deploy()

    # Optional auto-publish of new FL counties (off by default per "draft" policy).
    if publish_new and new_drafts and not dry_run:
        print("  --publish-new is set but auto-add to floridaCounties[] is not "
              "implemented in v1 (kept as drafts to avoid shipping low-confidence "
              "SEO pages). Reported above for manual review.")

    result = {
        "counties": counties_with_data,
        "updated": updated,
        "created": 0,                 # nothing auto-published in v1
        "new_drafts": len(new_drafts),
        "draft_slugs": [d["slug"] for d in new_drafts],
        "published": published,
        "deployed": deployed,
    }
    if logger:
        logger.step_done(
            "publish", ok=(published or dry_run or not content_changed),
            detail=f"updated:{updated} drafts:{len(new_drafts)} "
                   f"published:{published} deployed:{deployed}")
        logger.finish(result)
    print(f"  Collection pages: updated={updated}, new drafts={len(new_drafts)}, "
          f"published={published}, deployed={deployed}")
    return result


def main():
    ap = argparse.ArgumentParser(description="Update TaxCase Review collection pages from DB")
    ap.add_argument("--states", help="Comma list (default fl,tx)")
    ap.add_argument("--dry-run", action="store_true", help="Query + diff, no GitHub/deploy")
    ap.add_argument("--publish-new", action="store_true", help="Auto-add new counties (v1: drafts only)")
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                    help="Min liens for a new-county page candidate (default 50)")
    args = ap.parse_args()
    states = ([s.strip().lower() for s in args.states.split(",") if s.strip()]
              if args.states else DEFAULT_STATES)
    run(states=states, dry_run=args.dry_run,
        publish_new=args.publish_new, threshold=args.threshold)


if __name__ == "__main__":
    main()
