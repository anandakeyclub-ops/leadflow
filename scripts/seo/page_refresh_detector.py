"""
page_refresh_detector.py
========================
Detects pages on taxcasereview.org that need content refresh.

Combines:
  - GSC performance data (falling CTR, falling position, stale pages)
  - Local page file analysis (age of content, missing sections)
  - Lien DB data (county pages with outdated counts)

Generates:
  - Refresh priority queue with specific recommendations
  - Saves to data/ops/refresh_queue_YYYY-MM-DD.json

Usage:
  python scripts/seo/page_refresh_detector.py
  python scripts/seo/page_refresh_detector.py --dry-run
  python scripts/seo/page_refresh_detector.py --report

Schedule: Every Monday 7:15 AM (after gsc_monitor.py)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

LEADFLOW_DIR  = Path(__file__).resolve().parents[2]
TAXCASE_REPO  = Path(r"C:\Users\Dana\Desktop\taxcasereview-web")
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

GSC_TOKEN_FILE = LEADFLOW_DIR / os.getenv(
    "GSC_TOKEN", "data/credentials/gsc-token.json")
GSC_SITE_URL   = os.getenv("GSC_SITE_URL", "sc-domain:taxcasereview.org")
DATA_OPS       = LEADFLOW_DIR / "data" / "ops"
DATA_OPS.mkdir(parents=True, exist_ok=True)

SITE_BASE      = "https://taxcasereview.org"

# How many days before a page is considered "stale"
STALE_DAYS     = 60

try:
    from app.core.db import get_connection
    HAS_DB = True
except ImportError:
    HAS_DB = False


# ── GSC service ───────────────────────────────────────────────────────────────

def get_gsc_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
    creds  = Credentials.from_authorized_user_file(
        str(GSC_TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        GSC_TOKEN_FILE.write_text(creds.to_json())
    return build("searchconsole", "v1", credentials=creds)


# ── Page file analysis ────────────────────────────────────────────────────────

def scan_local_pages() -> list[dict]:
    """
    Scan the taxcasereview-web repo for page files.
    Returns list of page metadata including age and content signals.
    """
    pages = []

    if not TAXCASE_REPO.exists():
        print(f"  ⚠  Repo not found at {TAXCASE_REPO}")
        return pages

    # Scan all page.tsx files
    for tsx_file in TAXCASE_REPO.rglob("page.tsx"):
        # Skip root app directory files
        rel = tsx_file.relative_to(TAXCASE_REPO)
        parts = rel.parts

        # Build URL path
        url_path = "/" + "/".join(parts[:-1])  # remove page.tsx

        # Get file age
        stat      = tsx_file.stat()
        file_date = date.fromtimestamp(stat.st_mtime)
        age_days  = (date.today() - file_date).days

        # Read content for basic analysis
        try:
            content = tsx_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            content = ""

        # Detect missing sections
        missing = []
        if "FAQ" not in content and "faq" not in content.lower():
            missing.append("FAQ section")
        if "schema" not in content.lower() and "structured" not in content.lower():
            missing.append("Structured data")
        if len(content) < 2000:
            missing.append("Thin content (< 2000 chars)")

        pages.append({
            "path":      url_path,
            "file":      str(rel),
            "age_days":  age_days,
            "file_date": file_date.isoformat(),
            "is_stale":  age_days > STALE_DAYS,
            "content_length": len(content),
            "missing_sections": missing,
        })

    # Scan markdown files in content/
    for md_file in TAXCASE_REPO.rglob("*.md"):
        rel       = md_file.relative_to(TAXCASE_REPO)
        stat      = md_file.stat()
        file_date = date.fromtimestamp(stat.st_mtime)
        age_days  = (date.today() - file_date).days

        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            content = ""

        url_path = "/" + str(rel).replace("\\", "/").replace(
            "content/blog/", "blog/md/").replace(
            "content/reports/", "reports/").replace(".md", "")

        pages.append({
            "path":      url_path,
            "file":      str(rel),
            "age_days":  age_days,
            "file_date": file_date.isoformat(),
            "is_stale":  age_days > STALE_DAYS,
            "content_length": len(content),
            "missing_sections": [],
        })

    return pages


# ── DB: county lien counts ────────────────────────────────────────────────────

def get_county_lien_counts() -> dict[str, int]:
    """Get current lien counts per county for page freshness check."""
    if not HAS_DB:
        return {}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.county_name, COUNT(*) as cnt
                FROM normalized_liens nl
                JOIN counties c ON c.id = nl.county_id
                GROUP BY c.county_name
            """)
            return {r[0].lower().replace(" ", "-"): r[1]
                    for r in cur.fetchall()}
    finally:
        conn.close()


# ── GSC: performance signals ──────────────────────────────────────────────────

def get_performance_signals(service) -> dict[str, dict]:
    """
    Get 28-day GSC performance per page.
    Returns dict keyed by path.
    """
    end_date   = date.today() - timedelta(days=3)
    start_date = end_date - timedelta(days=28)

    try:
        rows = service.searchanalytics().query(
            siteUrl=GSC_SITE_URL,
            body={
                "startDate":  str(start_date),
                "endDate":    str(end_date),
                "dimensions": ["page"],
                "rowLimit":   500,
            }
        ).execute().get("rows", [])
    except Exception as e:
        print(f"  ⚠  GSC error: {e}")
        return {}

    signals = {}
    for row in rows:
        path = row["keys"][0].replace(SITE_BASE, "")
        signals[path] = {
            "impressions": int(row.get("impressions", 0)),
            "clicks":      int(row.get("clicks", 0)),
            "position":    round(row.get("position", 0), 1),
            "ctr":         round(row.get("ctr", 0) * 100, 2),
        }
    return signals


# ── Refresh scoring ───────────────────────────────────────────────────────────

def score_page(page: dict, gsc: dict, county_counts: dict) -> dict:
    """
    Score a page's refresh urgency (0-100).
    Higher = more urgent.
    """
    score   = 0
    reasons = []
    recs    = []

    path    = page["path"]
    perf    = gsc.get(path, {})

    # Age scoring
    if page["age_days"] > 90:
        score += 30
        reasons.append(f"Content is {page['age_days']} days old")
        recs.append("Update statistics and data references")
    elif page["age_days"] > 60:
        score += 15
        reasons.append(f"Content is {page['age_days']} days old")

    # Performance scoring
    impr = perf.get("impressions", 0)
    pos  = perf.get("position", 0)
    ctr  = perf.get("ctr", 0)

    if impr > 5 and perf.get("clicks", 0) == 0:
        score += 25
        reasons.append(f"{impr} impressions, 0 clicks — CTR problem")
        recs.append("Rewrite title tag and meta description")
        recs.append("Add structured data / FAQ schema")

    if pos > 20 and impr > 3:
        score += 20
        reasons.append(f"Position {pos} — not on page 1 or 2")
        recs.append("Expand content depth and word count")
        recs.append("Add internal links from related pages")

    if pos > 0 and pos <= 10 and ctr < 2.0:
        score += 20
        reasons.append(f"Page 1 position {pos} but only {ctr}% CTR")
        recs.append("Improve title tag with power words")
        recs.append("Add rich snippet schema")

    # Missing sections
    for m in page["missing_sections"]:
        score += 10
        reasons.append(f"Missing: {m}")
        recs.append(f"Add {m}")

    # County page with stale lien count
    if "/irs-tax-lien-help" in path:
        county_slug = path.split("/")[2] if path.count("/") >= 2 else ""
        if county_slug in county_counts:
            score += 10
            reasons.append("County lien counts may need updating")
            recs.append(f"Update with current count: "
                        f"{county_counts[county_slug]:,} liens on record")

    # Thin content
    if page["content_length"] < 2000:
        score += 15
        reasons.append(f"Thin content ({page['content_length']} chars)")
        recs.append("Expand content to at least 800 words")

    return {
        **page,
        "gsc_impressions": impr,
        "gsc_clicks":      perf.get("clicks", 0),
        "gsc_position":    pos,
        "gsc_ctr":         ctr,
        "refresh_score":   min(score, 100),
        "reasons":         reasons,
        "recommendations": list(dict.fromkeys(recs)),  # dedupe
        "priority":        "HIGH" if score >= 50
                           else "MEDIUM" if score >= 25
                           else "LOW",
    }


def generate_title_suggestions(page: dict) -> list[str]:
    """Generate title tag alternatives for CTR improvement."""
    path = page["path"]

    if "/irs-tax-lien-help" in path:
        parts  = path.strip("/").split("/")
        county = parts[1].replace("-", " ").title() if len(parts) > 1 else ""
        state  = parts[0].title() if parts else ""
        return [
            f"IRS Tax Lien Help in {county}, {state} | $399 Case Review",
            f"{county} County IRS Tax Lien? See Your Options | TaxCase Review",
            f"Federal Tax Lien in {county}? Licensed Help Available",
        ]
    elif "/irs-notices/" in path:
        notice = path.split("/")[-1].replace("-notice", "").upper()
        return [
            f"{notice} Notice: What It Means and What to Do | TaxCase Review",
            f"Got an IRS {notice}? Here's What Happens Next",
            f"IRS {notice} Notice — Don't Ignore It | TaxCase Review",
        ]
    elif "/blog/" in path or "/reports/" in path:
        return ["Update title with current year and specific data points"]
    return []


def print_refresh_report(queue: list):
    """Print formatted refresh queue to console."""
    high   = [p for p in queue if p["priority"] == "HIGH"]
    medium = [p for p in queue if p["priority"] == "MEDIUM"]

    print(f"\n{'='*65}")
    print(f"  Page Refresh Queue — {date.today().isoformat()}")
    print(f"  {len(high)} HIGH priority | {len(medium)} MEDIUM | "
          f"{len(queue)-len(high)-len(medium)} LOW")
    print(f"{'='*65}")

    for priority in ["HIGH", "MEDIUM"]:
        pages = [p for p in queue if p["priority"] == priority]
        if not pages:
            continue
        print(f"\n── {priority} PRIORITY ──")
        for p in pages[:5]:
            print(f"\n  {p['path']}")
            print(f"  Score: {p['refresh_score']}/100 | "
                  f"Age: {p['age_days']}d | "
                  f"GSC: {p['gsc_impressions']} impr / "
                  f"pos {p['gsc_position']}")
            for r in p["reasons"][:3]:
                print(f"  ⚠  {r}")
            for rec in p["recommendations"][:2]:
                print(f"  → {rec}")

    print(f"\n{'='*65}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TaxCase Review Page Refresh Detector")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report",  action="store_true",
                        help="Print full report")
    args = parser.parse_args()

    print(f"\n{'='*55}")
    print(f"  TaxCase Review Page Refresh Detector")
    print(f"  {date.today().isoformat()}")
    print(f"{'='*55}\n")

    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("page_refresh_detector")
        logger.start()
    except ImportError:
        logger = None

    # ── Scan local pages ──────────────────────────────────────────────────────
    if logger: logger.step_start("scan_pages")
    print("Scanning page files...")
    pages = scan_local_pages()
    print(f"  Found {len(pages)} pages/files")
    if logger:
        logger.step_done("scan_pages", ok=True,
                         detail=f"{len(pages)} pages")

    # ── Get GSC signals ───────────────────────────────────────────────────────
    if logger: logger.step_start("get_gsc_signals")
    print("Getting GSC performance signals...")
    try:
        service = get_gsc_service()
        gsc     = get_performance_signals(service)
        print(f"  GSC data for {len(gsc)} pages")
        if logger: logger.step_done("get_gsc_signals", ok=True,
                                    detail=f"{len(gsc)} pages")
    except Exception as e:
        print(f"  ⚠  GSC unavailable: {e}")
        gsc = {}
        if logger: logger.step_done("get_gsc_signals", ok=False,
                                    error=str(e))

    # ── Get county counts ─────────────────────────────────────────────────────
    if logger: logger.step_start("get_county_counts")
    print("Getting county lien counts...")
    county_counts = get_county_lien_counts()
    print(f"  {len(county_counts)} counties")
    if logger:
        logger.step_done("get_county_counts", ok=True,
                         detail=f"{len(county_counts)} counties")

    # ── Score and rank pages ──────────────────────────────────────────────────
    if logger: logger.step_start("score_pages")
    print("Scoring pages for refresh urgency...")
    scored = [score_page(p, gsc, county_counts) for p in pages]

    # Add title suggestions for high priority pages
    for p in scored:
        if p["priority"] == "HIGH":
            p["title_suggestions"] = generate_title_suggestions(p)

    # Sort by score descending
    queue = sorted(scored, key=lambda x: x["refresh_score"], reverse=True)

    high_ct   = len([p for p in queue if p["priority"] == "HIGH"])
    medium_ct = len([p for p in queue if p["priority"] == "MEDIUM"])
    print(f"  HIGH: {high_ct} | MEDIUM: {medium_ct} | "
          f"LOW: {len(queue)-high_ct-medium_ct}")

    if logger:
        logger.step_done("score_pages", ok=True,
                         detail=f"{high_ct} HIGH, {medium_ct} MEDIUM")

    # ── Save outputs ──────────────────────────────────────────────────────────
    if not args.dry_run:
        if logger: logger.step_start("save_outputs")

        queue_file = DATA_OPS / f"refresh_queue_{date.today().isoformat()}.json"
        queue_file.write_text(json.dumps(queue, indent=2))
        print(f"  Saved: {queue_file}")

        # Save latest
        latest = DATA_OPS / "refresh_queue_latest.json"
        latest.write_text(json.dumps(queue, indent=2))

        if logger:
            logger.step_done("save_outputs", ok=True,
                             detail=str(queue_file))

    if args.report or args.dry_run:
        print_refresh_report(queue)

    print(f"\n{'='*55}")
    print(f"  Refresh Detection Complete")
    print(f"  Total pages   : {len(queue)}")
    print(f"  HIGH priority : {high_ct}")
    print(f"  MEDIUM        : {medium_ct}")
    print(f"{'='*55}\n")

    if logger:
        logger.finish({
            "pages_scanned":    len(pages),
            "gsc_pages":        len(gsc),
            "high_priority":    high_ct,
            "medium_priority":  medium_ct,
            "total_in_queue":   len(queue),
        })


if __name__ == "__main__":
    main()
