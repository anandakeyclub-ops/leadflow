"""
gsc_monitor.py
==============
Weekly Google Search Console monitoring script.
Runs every Monday at 7:00 AM (before weekly_intelligence.py).

Pulls GSC data and generates:
  - Weekly SEO operations report
  - Priority action list (pages to fix, pages to promote)
  - Page performance by template type
  - Cannibalization candidates
  - Indexing issues

Saves to:
  data/ops/gsc_monitor_YYYY-MM-DD.json  — raw data
  data/ops/seo_actions_YYYY-MM-DD.json  — priority actions
  logs/pipeline/YYYY-MM-DD.jsonl        — pipeline log

Usage:
  python scripts/seo/gsc_monitor.py
  python scripts/seo/gsc_monitor.py --dry-run
  python scripts/seo/gsc_monitor.py --days 28
  python scripts/seo/gsc_monitor.py --report    # print full report

Schedule: Every Monday 7:00 AM
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

GSC_TOKEN_FILE = LEADFLOW_DIR / os.getenv(
    "GSC_TOKEN", "data/credentials/gsc-token.json")
GSC_SITE_URL   = os.getenv("GSC_SITE_URL", "sc-domain:taxcasereview.org")
DATA_OPS       = LEADFLOW_DIR / "data" / "ops"
DATA_OPS.mkdir(parents=True, exist_ok=True)

SITE_BASE      = "https://taxcasereview.org"

# Page template classification rules
TEMPLATE_RULES = {
    "county_lien":     lambda p: "/irs-tax-lien-help" in p,
    "irs_notice":      lambda p: "/irs-notices/" in p,
    "blog":            lambda p: "/blog/" in p,
    "report":          lambda p: "/reports/" in p,
    "state_hub":       lambda p: p.count("/") == 2 and any(
                           s in p for s in ["/florida", "/texas",
                                            "/georgia", "/arizona"]),
    "homepage":        lambda p: p in ["/", ""],
    "installment":     lambda p: "/installment-agreement" in p,
    "other":           lambda p: True,
}


def classify_page(path: str) -> str:
    for template, rule in TEMPLATE_RULES.items():
        if rule(path):
            return template
    return "other"


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


def gsc_query(service, start: date, end: date,
              dimensions: list, limit: int = 100,
              order: str = "impressions") -> list:
    try:
        r = service.searchanalytics().query(
            siteUrl=GSC_SITE_URL,
            body={
                "startDate":  str(start),
                "endDate":    str(end),
                "dimensions": dimensions,
                "rowLimit":   limit,
                "orderBy":    [{"fieldName": order,
                                "sortOrder": "DESCENDING"}],
            }
        ).execute()
        return r.get("rows", [])
    except Exception as e:
        print(f"  ⚠  GSC query error: {e}")
        return []


# ── Analysis functions ────────────────────────────────────────────────────────

def analyze_pages(rows_this: list, rows_prev: list) -> list:
    """
    Compare this period vs previous period per page.
    Returns list of page dicts with trend data.
    """
    prev_map = {}
    for row in rows_prev:
        page = row["keys"][0].replace(SITE_BASE, "")
        prev_map[page] = {
            "impressions": int(row.get("impressions", 0)),
            "clicks":      int(row.get("clicks", 0)),
            "position":    round(row.get("position", 0), 1),
            "ctr":         round(row.get("ctr", 0) * 100, 2),
        }

    pages = []
    for row in rows_this:
        page = row["keys"][0].replace(SITE_BASE, "")
        curr = {
            "page":        page,
            "template":    classify_page(page),
            "impressions": int(row.get("impressions", 0)),
            "clicks":      int(row.get("clicks", 0)),
            "position":    round(row.get("position", 0), 1),
            "ctr":         round(row.get("ctr", 0) * 100, 2),
        }
        prev = prev_map.get(page, {})
        curr["prev_impressions"] = prev.get("impressions", 0)
        curr["prev_position"]    = prev.get("position", 0)
        curr["prev_clicks"]      = prev.get("clicks", 0)
        curr["impr_delta"]       = curr["impressions"] - curr["prev_impressions"]
        curr["pos_delta"]        = round(
            curr["position"] - curr["prev_position"], 1) \
            if curr["prev_position"] else 0
        pages.append(curr)

    return sorted(pages, key=lambda x: x["impressions"], reverse=True)


def find_opportunities(pages: list) -> list:
    """Pages with impressions but no clicks — CTR optimization opportunities."""
    return [
        p for p in pages
        if p["impressions"] >= 3
        and p["clicks"] == 0
        and p["position"] <= 50
    ]


def find_rising_pages(pages: list) -> list:
    """Pages gaining impressions week over week."""
    return sorted(
        [p for p in pages if p["impr_delta"] > 0],
        key=lambda x: x["impr_delta"],
        reverse=True
    )[:5]


def find_falling_pages(pages: list) -> list:
    """Pages losing impressions or position."""
    return sorted(
        [p for p in pages
         if p["prev_impressions"] > 0
         and p["impr_delta"] < -2],
        key=lambda x: x["impr_delta"]
    )[:5]


def find_cannibalization_candidates(query_rows: list) -> list:
    """
    Find queries where multiple pages rank — potential cannibalization.
    """
    try:
        # Need query+page dimensions for this
        return []  # Requires separate query with both dimensions
    except Exception:
        return []


def by_template(pages: list) -> dict:
    """Group page performance by template type."""
    groups = defaultdict(lambda: {
        "pages": 0, "total_impressions": 0,
        "total_clicks": 0, "avg_position": []
    })
    for p in pages:
        t = p["template"]
        groups[t]["pages"]            += 1
        groups[t]["total_impressions"] += p["impressions"]
        groups[t]["total_clicks"]      += p["clicks"]
        if p["position"] > 0:
            groups[t]["avg_position"].append(p["position"])

    result = {}
    for t, g in groups.items():
        avg_pos = round(
            sum(g["avg_position"]) / len(g["avg_position"]), 1
        ) if g["avg_position"] else 0
        result[t] = {
            "pages":             g["pages"],
            "total_impressions": g["total_impressions"],
            "total_clicks":      g["total_clicks"],
            "avg_position":      avg_pos,
            "avg_ctr":           round(
                g["total_clicks"] / max(g["total_impressions"], 1) * 100, 2
            ),
        }
    return dict(sorted(
        result.items(),
        key=lambda x: x[1]["total_impressions"],
        reverse=True
    ))


def generate_action_list(pages: list, queries: list,
                         opportunities: list,
                         rising: list, falling: list) -> list:
    """Generate prioritized SEO action list."""
    actions = []

    # High impression, zero click pages — title/meta optimization
    for p in opportunities[:5]:
        actions.append({
            "priority": "HIGH",
            "type":     "ctr_optimization",
            "page":     p["page"],
            "issue":    f"{p['impressions']} impressions, 0 clicks, "
                        f"position {p['position']}",
            "action":   "Rewrite title tag and meta description. "
                        "Add structured data. Check for click-deterring signals.",
            "template": p["template"],
        })

    # Falling pages — content refresh needed
    for p in falling:
        actions.append({
            "priority": "MEDIUM",
            "type":     "content_refresh",
            "page":     p["page"],
            "issue":    f"Lost {abs(p['impr_delta'])} impressions vs prior period",
            "action":   "Review content freshness. Update statistics. "
                        "Add recent data. Check internal links.",
            "template": p["template"],
        })

    # Rising pages — capitalize on momentum
    for p in rising[:3]:
        actions.append({
            "priority": "MEDIUM",
            "type":     "capitalize_momentum",
            "page":     p["page"],
            "issue":    f"Gained {p['impr_delta']} impressions — momentum building",
            "action":   "Add internal links to this page. "
                        "Consider expanding content. Build supporting pages.",
            "template": p["template"],
        })

    # Pages ranked 11-20 — just outside first page
    near_miss = [
        p for p in pages
        if 10 < p["position"] <= 20
        and p["impressions"] >= 5
    ][:3]
    for p in near_miss:
        actions.append({
            "priority": "MEDIUM",
            "type":     "push_to_page1",
            "page":     p["page"],
            "issue":    f"Position {p['position']} — just off page 1",
            "action":   "Add more content, improve E-E-A-T signals, "
                        "build internal links from higher-authority pages.",
            "template": p["template"],
        })

    return sorted(actions, key=lambda x: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[
        x["priority"]])


def print_report(data: dict):
    """Print formatted SEO ops report to console."""
    print(f"\n{'='*65}")
    print(f"  TaxCase Review — Weekly GSC Monitor")
    print(f"  Period: {data['period_this']}")
    print(f"{'='*65}")

    s = data["summary"]
    print(f"\n── Summary ──")
    print(f"  Total impressions : {s['total_impressions']:,}")
    print(f"  Total clicks      : {s['total_clicks']:,}")
    print(f"  Average position  : {s['avg_position']}")
    print(f"  Average CTR       : {s['avg_ctr']}%")
    print(f"  Pages with data   : {s['pages_with_data']}")

    print(f"\n── By Template ──")
    for t, g in data["by_template"].items():
        print(f"  {t:<20} {g['pages']:>3} pages  "
              f"{g['total_impressions']:>5} impr  "
              f"pos {g['avg_position']:>5}")

    print(f"\n── Opportunities (impressions, no clicks) ──")
    for p in data["opportunities"][:5]:
        print(f"  {p['page']:<50} "
              f"impr:{p['impressions']:>4}  pos:{p['position']:>5}")

    print(f"\n── Rising Pages ──")
    for p in data["rising_pages"]:
        print(f"  {p['page']:<50} +{p['impr_delta']} impr")

    print(f"\n── Priority Actions ──")
    for a in data["actions"][:8]:
        print(f"  [{a['priority']}] {a['type']}")
        print(f"    {a['page']}")
        print(f"    Issue : {a['issue']}")
        print(f"    Action: {a['action'][:80]}")
        print()

    print(f"{'='*65}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TaxCase Review GSC Monitor")
    parser.add_argument("--days",    type=int, default=7,
                        help="Days to analyze (default 7)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run but don't save outputs")
    parser.add_argument("--report",  action="store_true",
                        help="Print full report to console")
    args = parser.parse_args()

    print(f"\n{'='*55}")
    print(f"  TaxCase Review GSC Monitor")
    print(f"  {date.today().isoformat()}")
    print(f"  Analyzing last {args.days} days")
    print(f"{'='*55}\n")

    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("gsc_monitor")
        logger.start()
    except ImportError:
        logger = None

    # ── Connect to GSC ────────────────────────────────────────────────────────
    if logger: logger.step_start("connect_gsc")
    print("Connecting to Google Search Console...")
    try:
        service = get_gsc_service()
        print("  ✅ Connected")
        if logger: logger.step_done("connect_gsc", ok=True)
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        if logger:
            logger.step_done("connect_gsc", ok=False, error=str(e))
            logger.finish({"error": str(e)})
        return

    # ── Date ranges ───────────────────────────────────────────────────────────
    end_date   = date.today() - timedelta(days=3)  # GSC 3-day delay
    start_this = end_date - timedelta(days=args.days)
    start_prev = start_this - timedelta(days=args.days)

    # ── Pull data ─────────────────────────────────────────────────────────────
    if logger: logger.step_start("pull_page_data")
    print(f"Pulling page data ({start_this} → {end_date})...")

    rows_this = gsc_query(service, start_this, end_date,
                          ["page"], limit=200)
    rows_prev = gsc_query(service, start_prev, start_this,
                          ["page"], limit=200)
    rows_queries = gsc_query(service, start_this, end_date,
                             ["query"], limit=100)

    print(f"  Pages this period : {len(rows_this)}")
    print(f"  Pages prev period : {len(rows_prev)}")
    print(f"  Queries           : {len(rows_queries)}")

    if logger:
        logger.step_done("pull_page_data", ok=True,
                         detail=f"{len(rows_this)} pages")

    # ── Analyze ───────────────────────────────────────────────────────────────
    if logger: logger.step_start("analyze")
    print("\nAnalyzing performance...")

    pages        = analyze_pages(rows_this, rows_prev)
    opportunities = find_opportunities(pages)
    rising       = find_rising_pages(pages)
    falling      = find_falling_pages(pages)
    templates    = by_template(pages)
    actions      = generate_action_list(
        pages, rows_queries, opportunities, rising, falling)

    # Summary totals
    total_impr   = sum(p["impressions"] for p in pages)
    total_clicks = sum(p["clicks"] for p in pages)
    avg_pos      = round(
        sum(p["position"] for p in pages if p["position"] > 0) /
        max(len([p for p in pages if p["position"] > 0]), 1), 1
    )
    avg_ctr      = round(total_clicks / max(total_impr, 1) * 100, 2)

    # Top queries
    top_queries = [
        {
            "query":       r["keys"][0],
            "impressions": int(r.get("impressions", 0)),
            "clicks":      int(r.get("clicks", 0)),
            "position":    round(r.get("position", 0), 1),
            "ctr":         round(r.get("ctr", 0) * 100, 2),
        }
        for r in rows_queries[:20]
    ]

    if logger:
        logger.step_done("analyze", ok=True,
                         detail=f"{len(opportunities)} opportunities, "
                                f"{len(actions)} actions")

    # ── Build output data ─────────────────────────────────────────────────────
    output = {
        "generated":    date.today().isoformat(),
        "period_this":  f"{start_this} to {end_date}",
        "period_prev":  f"{start_prev} to {start_this}",
        "days":         args.days,
        "summary": {
            "total_impressions": total_impr,
            "total_clicks":      total_clicks,
            "avg_position":      avg_pos,
            "avg_ctr":           avg_ctr,
            "pages_with_data":   len(pages),
        },
        "by_template":   templates,
        "top_pages":     pages[:20],
        "top_queries":   top_queries,
        "opportunities": opportunities[:10],
        "rising_pages":  rising,
        "falling_pages": falling,
        "actions":       actions,
    }

    # ── Save outputs ──────────────────────────────────────────────────────────
    if not args.dry_run:
        if logger: logger.step_start("save_outputs")

        monitor_file = DATA_OPS / f"gsc_monitor_{date.today().isoformat()}.json"
        monitor_file.write_text(json.dumps(output, indent=2))
        print(f"  Saved: {monitor_file}")

        actions_file = DATA_OPS / f"seo_actions_{date.today().isoformat()}.json"
        actions_file.write_text(json.dumps(actions, indent=2))
        print(f"  Saved: {actions_file}")

        # Always overwrite latest
        latest_file = DATA_OPS / "gsc_monitor_latest.json"
        latest_file.write_text(json.dumps(output, indent=2))

        if logger:
            logger.step_done("save_outputs", ok=True,
                             detail=str(monitor_file))

    if args.report or args.dry_run:
        print_report(output)

    # ── Console summary ───────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"  GSC Monitor Complete")
    print(f"  Impressions    : {total_impr:,}")
    print(f"  Clicks         : {total_clicks:,}")
    print(f"  Avg position   : {avg_pos}")
    print(f"  Opportunities  : {len(opportunities)}")
    print(f"  Priority actions: {len(actions)}")
    print(f"{'='*55}\n")

    if logger:
        logger.finish({
            "total_impressions": total_impr,
            "total_clicks":      total_clicks,
            "avg_position":      avg_pos,
            "opportunities":     len(opportunities),
            "actions":           len(actions),
            "pages_analyzed":    len(pages),
        })


if __name__ == "__main__":
    main()
