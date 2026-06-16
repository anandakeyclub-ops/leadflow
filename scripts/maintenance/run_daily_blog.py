#!/usr/bin/env python3
"""
run_daily_blog.py  (v2)
========================
Daily blog automation for TaxCase Review.
Tracks published posts to prevent repeats.
Every post is unique — never regenerates an already-published slug.

Schedule logic:
  Monday    — National topic post
  Tuesday   — State rotation post
  Wednesday — National topic post
  Thursday  — State rotation post
  Friday    — National topic post
  Saturday  — State rotation post
  Sunday    — Rest

Usage:
  python run_daily_blog.py              # auto-detect day
  python run_daily_blog.py --topic      # force topic post
  python run_daily_blog.py --state florida
  python run_daily_blog.py --dry-run
  python run_daily_blog.py --status     # show what's been published
"""

import argparse
import json
import subprocess
import sys
import random
from datetime import date, datetime
from pathlib import Path

import requests

INDEXNOW_KEY = "9e9b2e673445719e87ed5e2213724841"  # same key as social_media_poster.py / reel_generator.py
SITE_URL     = "https://taxcasereview.org"

BASE         = Path(__file__).resolve().parent          # scripts/maintenance
REPO_ROOT    = BASE.parent.parent                        # leadflow repo root
HISTORY_FILE = REPO_ROOT / "data" / "blog_publish_history.json"

# generate_topic_blogs.py was relocated from the repo root into scripts/archive/
# by the "organize untracked files" commit (bc3803d). It writes to content/ and
# loads .env relative to the repo root, so it must run with cwd=REPO_ROOT. Resolve
# its path by searching known locations so a future move can't silently break the
# daily blog again.
def _find_generator() -> Path:
    candidates = [
        REPO_ROOT / "scripts" / "archive" / "generate_topic_blogs.py",
        REPO_ROOT / "scripts" / "maintenance" / "generate_topic_blogs.py",
        REPO_ROOT / "generate_topic_blogs.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        "generate_topic_blogs.py not found in any known location: "
        + ", ".join(str(c) for c in candidates)
    )

GENERATOR = _find_generator()

# ── All available national topic slugs ───────────────────────────────────────
TOPIC_SLUGS = [
    "how-long-does-irs-tax-lien-last",
    "irs-tax-lien-on-house",
    "irs-fresh-start-program-explained",
    "irs-penalty-abatement-letter",
    "how-to-remove-irs-tax-lien-from-credit-report",
    "irs-froze-bank-account-what-to-do",
    "trust-fund-recovery-penalty",
    "irs-payment-plan-rejected",
    "irs-tax-debt-self-employed",
    "irs-tax-lien-on-llc",
]

# ── State topics (slug suffix per state) ─────────────────────────────────────
STATE_KEYS = [
    "florida", "texas", "georgia", "arizona",
    "california", "new_york", "north_carolina",
    "illinois", "ohio", "pennsylvania",
]

STATE_TOPIC_SUFFIXES = [
    "irs-tax-lien-help-contractors",
    "small-business-irs-debt",
    "irs-levy-wage-garnishment",
]

def all_state_slugs() -> list[str]:
    slugs = []
    for state in STATE_KEYS:
        state_slug = state.replace("_", "-")
        for suffix in STATE_TOPIC_SUFFIXES:
            slugs.append(f"{state_slug}-{suffix}")
    return slugs

ALL_TOPIC_SLUGS = TOPIC_SLUGS
ALL_STATE_SLUGS = all_state_slugs()  # 30 state-specific slugs


# ── Publish history ───────────────────────────────────────────────────────────

def load_history() -> dict:
    """Load publish history: {slug: date_published}"""
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_history(slug: str):
    history = load_history()
    history[slug] = date.today().isoformat()
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


def get_unpublished(slugs: list[str]) -> list[str]:
    """Return slugs not yet published."""
    history = load_history()
    return [s for s in slugs if s not in history]


def get_next_topic_slug() -> str | None:
    """Get next unpublished topic slug, cycling through list order."""
    unpublished = get_unpublished(ALL_TOPIC_SLUGS)
    if not unpublished:
        # All published — reset and start fresh with new angles
        print("  All topic slugs published — resetting history for topics")
        history = load_history()
        for slug in ALL_TOPIC_SLUGS:
            history.pop(slug, None)
        HISTORY_FILE.write_text(json.dumps(history, indent=2))
        unpublished = ALL_TOPIC_SLUGS
    # Pick the first unpublished (maintains order)
    return unpublished[0]


def get_next_state_for_blog() -> str | None:
    """Get next state that hasn't had a blog post recently."""
    history = load_history()
    # Find states with no recent posts (last 30 days)
    today = date.today()
    recently_posted = set()
    for slug, pub_date in history.items():
        try:
            days_ago = (today - date.fromisoformat(pub_date)).days
            if days_ago < 30:
                for state in STATE_KEYS:
                    if slug.startswith(state.replace("_", "-")):
                        recently_posted.add(state)
        except Exception:
            pass

    # Pick state with fewest recent posts
    available = [s for s in STATE_KEYS if s not in recently_posted]
    if not available:
        # All states posted recently — pick least recently posted
        available = STATE_KEYS

    # Rotate by week number
    week = date.today().isocalendar()[1]
    return available[week % len(available)]


def get_next_state_topic(state: str) -> str | None:
    """Get next unpublished topic suffix for this state."""
    state_slug = state.replace("_", "-")
    history = load_history()

    for suffix in STATE_TOPIC_SUFFIXES:
        slug = f"{state_slug}-{suffix}"
        if slug not in history:
            return suffix

    # All 3 topics published for this state — pick oldest
    oldest_suffix = STATE_TOPIC_SUFFIXES[0]
    oldest_date = None
    for suffix in STATE_TOPIC_SUFFIXES:
        slug = f"{state_slug}-{suffix}"
        pub_date = history.get(slug)
        if pub_date:
            d = date.fromisoformat(pub_date)
            if oldest_date is None or d < oldest_date:
                oldest_date = d
                oldest_suffix = suffix
    return oldest_suffix


def show_status():
    history = load_history()
    print(f"\n{'='*55}")
    print(f"  Blog Publish History")
    print(f"  Total published: {len(history)}")
    print(f"{'='*55}")

    unpub_topics = get_unpublished(ALL_TOPIC_SLUGS)
    print(f"\n  National topics: {len(ALL_TOPIC_SLUGS) - len(unpub_topics)}/{len(ALL_TOPIC_SLUGS)} published")
    for slug in ALL_TOPIC_SLUGS:
        status = history.get(slug, "NOT PUBLISHED")
        mark = "✅" if slug in history else "⬜"
        print(f"    {mark} {slug} — {status}")

    print(f"\n  State posts published: {len(history) - (len(ALL_TOPIC_SLUGS) - len(unpub_topics))}")
    for state in STATE_KEYS:
        state_slug = state.replace("_", "-")
        posts = [(f"{state_slug}-{s}", history.get(f"{state_slug}-{s}")) for s in STATE_TOPIC_SUFFIXES]
        pub_count = sum(1 for _, d in posts if d)
        print(f"    {state.title():20} {pub_count}/3 published")


# ── Runner ────────────────────────────────────────────────────────────────────

def run_cmd(cmd: list, label: str, dry_run: bool = False) -> bool:
    print(f"\n{'='*55}")
    print(f"  {label}")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    if dry_run:
        print(f"  [DRY RUN] Would run: {' '.join(str(x) for x in cmd[-4:])}")
        return True
    print(f"{'='*55}\n")
    result = subprocess.run(
        cmd, cwd=str(REPO_ROOT),
        text=True,
        timeout=300,
    )
    return result.returncode == 0


def index_url(url: str):
    """Submit a freshly published URL to IndexNow (Bing/Yandex) for fast crawl.
    Same key/host as social_media_poster.py and reel_generator.py. Non-blocking —
    indexing must never fail a publish."""
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


def main():
    parser = argparse.ArgumentParser(description="TaxCase Review Daily Blog Runner v2")
    parser.add_argument("--topic",   action="store_true", help="Force national topic post")
    parser.add_argument("--state",   default=None, help="Force specific state")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status",  action="store_true", help="Show publish history")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    # Pipeline log so the blog automation shows up in logs/pipeline/ like every
    # other worker (the daily summary's Content Automation section reads this).
    # Skipped for --dry-run (a dry run isn't a real publish).
    logger = None
    if not args.dry_run:
        try:
            sys.path.insert(0, str(REPO_ROOT))  # repo root for pipeline_log
            from pipeline_log import PipelineLogger
            logger = PipelineLogger("blog")
            logger.start()
        except ImportError:
            logger = None

    python  = sys.executable
    weekday = date.today().weekday()  # 0=Mon 6=Sun
    topic_days = {0, 2, 4}           # Mon Wed Fri
    state_days  = {1, 3, 5}          # Tue Thu Sat

    # Sunday — skip
    if weekday == 6 and not args.topic and not args.state:
        print("Sunday — no blog scheduled.")
        if logger:
            logger.step_skip("publish_blog", "Sunday — no blog scheduled")
            logger.finish({"published": False, "reason": "sunday"})
        return

    ok = False
    published_slug = None
    post_kind = None

    try:
        if args.topic or (not args.state and weekday in topic_days):
            # ── National topic post ───────────────────────────────────────
            slug = get_next_topic_slug()
            if not slug:
                print("No unpublished topic slugs available")
                if logger:
                    logger.step_skip("publish_blog", "no unpublished topic slugs")
                    logger.finish({"published": False, "reason": "no_slugs"})
                return

            post_kind = "national_topic"
            print(f"\n  Next topic slug: {slug}")
            cmd = [python, str(GENERATOR), "--slug", slug]
            if args.dry_run:
                cmd.append("--dry-run")

            if logger: logger.step_start("publish_blog")
            ok = run_cmd(cmd, f"National Topic Blog: {slug}", dry_run=args.dry_run)
            if ok and not args.dry_run:
                save_history(slug)
                published_slug = slug
                print(f"  Recorded: {slug} published {date.today().isoformat()}")
            if logger:
                logger.step_done("publish_blog", ok=ok, detail=f"topic:{slug}")

        else:
            # ── State post ────────────────────────────────────────────────
            state = args.state or get_next_state_for_blog()
            topic_suffix = get_next_state_topic(state)
            state_slug   = state.replace("_", "-")
            full_slug    = f"{state_slug}-{topic_suffix}"

            post_kind = "state"
            print(f"\n  Next state: {state.title()} | topic: {topic_suffix}")
            cmd = [python, str(GENERATOR), "--slug", full_slug]
            if args.dry_run:
                cmd.append("--dry-run")

            if logger: logger.step_start("publish_blog")
            ok = run_cmd(cmd, f"State Blog: {state.title()} — {topic_suffix}", dry_run=args.dry_run)
            if ok and not args.dry_run:
                save_history(full_slug)
                published_slug = full_slug
                print(f"  Recorded: {full_slug} published {date.today().isoformat()}")
            if logger:
                logger.step_done("publish_blog", ok=ok, detail=f"state:{full_slug}")

        # Submit the new blog URL to IndexNow so Bing/Yandex crawl it fast.
        # Canonical public blog path is /blog/md/<slug> (_blog_public_url).
        if published_slug and not args.dry_run:
            index_url(f"{SITE_URL}/blog/md/{published_slug}")

        print(f"\n  Blog run complete: {'OK' if ok else 'FAILED'}")
        if logger:
            logger.finish({
                "published": bool(published_slug),
                "slug":      published_slug or "",
                "post_kind": post_kind,
            })
    except Exception as e:
        if logger:
            logger.step_done("publish_blog", ok=False, error=str(e))
            logger.finish({"published": False, "error": str(e)})
        raise


if __name__ == "__main__":
    main()