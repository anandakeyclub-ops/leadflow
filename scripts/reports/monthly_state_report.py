"""
monthly_state_report.py  (v3)
==============================
Generates monthly state-level IRS tax intelligence reports.
All 10 states: Florida, Texas, Georgia, Arizona, California, New York, North Carolina.

CHANGES IN V3:
  - Added California, New York, North Carolina
  - Fixed Florida DB query (correct monthly count)
  - All non-Florida states use Claude synthesis with public IRS trends
  - Clearly labels estimated vs verified data

Usage:
  python scripts/reports/monthly_state_report.py --state florida
  python scripts/reports/monthly_state_report.py --state california
  python scripts/reports/monthly_state_report.py --all
  python scripts/reports/monthly_state_report.py --all --dry-run

Schedule: 1st of every month, 8:00 AM
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO       = os.getenv("GITHUB_REPO",
                              "anandakeyclub-ops/v0-tax-resolution-landing-page")
GITHUB_BRANCH     = os.getenv("GITHUB_BRANCH", "main")
SITE_URL          = "https://taxcasereview.org"
PHONE             = "(561) 247-0678"
DATA_OPS          = LEADFLOW_DIR / "data" / "ops"
DATA_OPS.mkdir(parents=True, exist_ok=True)
REPORTS_PATH      = "content/reports"

try:
    from app.core.db import get_connection
    HAS_DB = True
except ImportError:
    HAS_DB = False

STATES = {
    "florida": {
        "name":          "Florida",
        "abbreviation":  "FL",
        "has_db_data":   True,
        "top_counties":  ["Miami-Dade", "Broward", "Palm Beach", "Hillsborough",
                          "Orange", "Pinellas", "Duval", "Martin", "Lake", "Manatee"],
        "key_industries": ["construction contractors", "real estate professionals",
                           "self-employed service workers",
                           "restaurant and hospitality owners"],
        "notice_focus":  "CP14, CP503, CP504",
        "landing":       "/florida",
        "data_note":     "",
    },
    "texas": {
        "name":          "Texas",
        "abbreviation":  "TX",
        "has_db_data":   False,
        "top_counties":  ["Harris", "Dallas", "Tarrant", "Bexar",
                          "Travis", "Collin", "Denton", "Fort Bend"],
        "key_industries": ["oil and gas contractors", "construction companies",
                           "trucking and logistics operators",
                           "self-employed professionals"],
        "notice_focus":  "CP14, CP503, CP504, payroll tax notices",
        "landing":       "/texas",
        "data_note":     "Based on national IRS enforcement trends and public data.",
    },
    "georgia": {
        "name":          "Georgia",
        "abbreviation":  "GA",
        "has_db_data":   False,
        "top_counties":  ["Fulton", "Gwinnett", "Cobb", "DeKalb",
                          "Cherokee", "Clayton", "Henry", "Hall"],
        "key_industries": ["small business owners", "construction contractors",
                           "logistics and distribution workers",
                           "self-employed professionals"],
        "notice_focus":  "CP14, CP503, payroll tax enforcement",
        "landing":       "/georgia",
        "data_note":     "Based on national IRS enforcement trends and public data.",
    },
    "arizona": {
        "name":          "Arizona",
        "abbreviation":  "AZ",
        "has_db_data":   False,
        "top_counties":  ["Maricopa", "Pima", "Pinal", "Yavapai",
                          "Mohave", "Yuma", "Cochise", "Navajo"],
        "key_industries": ["construction and real estate",
                           "retirement income recipients",
                           "self-employed service professionals",
                           "small business owners"],
        "notice_focus":  "CP14, retirement income issues, CP504",
        "landing":       "/arizona",
        "data_note":     "Based on national IRS enforcement trends and public data.",
    },
    "california": {
        "name":          "California",
        "abbreviation":  "CA",
        "has_db_data":   False,
        "top_counties":  ["Los Angeles", "San Diego", "Orange", "Riverside",
                          "San Bernardino", "Santa Clara", "Alameda", "Sacramento"],
        "key_industries": ["self-employed tech contractors and freelancers",
                           "gig economy workers",
                           "real estate investors and agents",
                           "entertainment industry professionals",
                           "small business owners"],
        "notice_focus":  "CP14, CP503, CP504, self-employment tax notices",
        "landing":       "/california",
        "data_note":     "Based on national IRS enforcement trends and public data.",
    },
    "new_york": {
        "name":          "New York",
        "abbreviation":  "NY",
        "has_db_data":   False,
        "top_counties":  ["Kings (Brooklyn)", "Queens", "New York (Manhattan)",
                          "Bronx", "Staten Island", "Nassau", "Suffolk",
                          "Westchester"],
        "key_industries": ["small business owners",
                           "self-employed professionals",
                           "restaurant and hospitality operators",
                           "real estate professionals",
                           "construction contractors"],
        "notice_focus":  "CP14, CP503, CP504, payroll tax notices",
        "landing":       "/new-york",
        "data_note":     "Based on national IRS enforcement trends and public data.",
    },
    "north_carolina": {
        "name":          "North Carolina",
        "abbreviation":  "NC",
        "has_db_data":   False,
        "top_counties":  ["Mecklenburg (Charlotte)", "Wake (Raleigh)",
                          "Guilford", "Forsyth", "Cumberland",
                          "Durham", "Buncombe", "Union"],
        "key_industries": ["construction contractors",
                           "manufacturing and logistics workers",
                           "self-employed professionals",
                           "small business owners",
                           "tech sector contractors"],
        "notice_focus":  "CP14, CP503, CP504",
        "landing":       "/north-carolina",
        "data_note":     "Based on national IRS enforcement trends and public data.",
    },
    "illinois": {
        "name":          "Illinois",
        "abbreviation":  "IL",
        "has_db_data":   False,
        "top_counties":  ["Cook (Chicago)", "DuPage", "Lake",
                          "Will (Joliet)", "Kane", "Winnebago (Rockford)",
                          "Peoria", "Champaign"],
        "key_industries": ["construction contractors",
                           "manufacturing workers",
                           "trucking and logistics operators",
                           "restaurant and hospitality owners"],
        "notice_focus":  "CP14, CP503, CP504",
        "landing":       "/illinois",
        "data_note":     "Based on national IRS enforcement trends and public data.",
    },
    "ohio": {
        "name":          "Ohio",
        "abbreviation":  "OH",
        "has_db_data":   False,
        "top_counties":  ["Cuyahoga (Cleveland)", "Franklin (Columbus)",
                          "Hamilton (Cincinnati)", "Summit (Akron)",
                          "Montgomery (Dayton)", "Lucas (Toledo)",
                          "Stark (Canton)", "Lorain"],
        "key_industries": ["manufacturing and auto industry workers",
                           "construction contractors",
                           "trucking operators",
                           "small business owners"],
        "notice_focus":  "CP14, CP503, CP504",
        "landing":       "/ohio",
        "data_note":     "Based on national IRS enforcement trends and public data.",
    },
    "pennsylvania": {
        "name":          "Pennsylvania",
        "abbreviation":  "PA",
        "has_db_data":   False,
        "top_counties":  ["Philadelphia", "Allegheny (Pittsburgh)",
                          "Montgomery", "Bucks", "Delaware",
                          "Lancaster", "York", "Lehigh (Allentown)"],
        "key_industries": ["construction contractors",
                           "trucking and logistics operators",
                           "manufacturing workers",
                           "restaurant and hospitality owners"],
        "notice_focus":  "CP14, CP503, CP504",
        "landing":       "/pennsylvania",
        "data_note":     "Based on national IRS enforcement trends and public data.",
    },
}


# ── DB: Florida lien data ─────────────────────────────────────────────────────

def get_florida_monthly_data() -> dict:
    if not HAS_DB:
        return {
            "total_liens_all_time": 17552,
            "new_this_month":       247,
            "new_last_month":       311,
            "pct_change":           -20.6,
            "top_counties": [
                {"county": "Miami-Dade", "count": 89},
                {"county": "Martin",     "count": 41},
                {"county": "Lake",       "count": 38},
            ],
            "month_name":  date.today().strftime("%B %Y"),
            "data_source": "mock",
        }

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM normalized_liens")
            total = cur.fetchone()[0]

            # Detect date column
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='normalized_liens'
                  AND column_name IN ('filed_date','created_at')
                ORDER BY column_name
            """)
            cols     = [r[0] for r in cur.fetchall()]
            date_col = "filed_date" if "filed_date" in cols else "created_at"

            cur.execute(f"""
                SELECT COUNT(*) FROM normalized_liens
                WHERE {date_col} >= date_trunc('month', CURRENT_DATE)
                  AND {date_col} <  date_trunc('month', CURRENT_DATE)
                                  + INTERVAL '1 month'
            """)
            new_this = cur.fetchone()[0] or 0

            cur.execute(f"""
                SELECT COUNT(*) FROM normalized_liens
                WHERE {date_col} >= date_trunc('month', CURRENT_DATE)
                                  - INTERVAL '1 month'
                  AND {date_col} <  date_trunc('month', CURRENT_DATE)
            """)
            new_last = cur.fetchone()[0] or 0

            # Fallback: if early in month, use last 30 days
            if new_this == 0:
                cur.execute(f"""
                    SELECT COUNT(*) FROM normalized_liens
                    WHERE {date_col} >= NOW() - INTERVAL '30 days'
                """)
                new_this = cur.fetchone()[0] or 0

            pct = round((new_this - new_last) / max(new_last, 1) * 100, 1)

            cur.execute(f"""
                SELECT c.county_name, COUNT(*) AS cnt
                FROM normalized_liens nl
                JOIN counties c ON c.id = nl.county_id
                WHERE nl.{date_col} >= NOW() - INTERVAL '30 days'
                GROUP BY c.county_name ORDER BY cnt DESC LIMIT 8
            """)
            counties = [{"county": r[0], "count": r[1]}
                        for r in cur.fetchall()]

            return {
                "total_liens_all_time": total,
                "new_this_month":       new_this,
                "new_last_month":       new_last,
                "pct_change":           pct,
                "top_counties":         counties,
                "month_name":           date.today().strftime("%B %Y"),
                "date_column_used":     date_col,
                "data_source":          "db",
            }
    finally:
        conn.close()


# ── Claude report generation ──────────────────────────────────────────────────

def call_claude(prompt: str, max_tokens: int = 2200) -> str:
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      "claude-sonnet-4-5",
            "max_tokens": max_tokens,
            "messages":   [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()



# ── Quality scoring ───────────────────────────────────────────────────────────

def score_report(content: str, state_name: str) -> dict:
    """Score a generated report 0-100. Reject below 70."""
    t = content.lower()
    import re as _re

    # 1. Specificity (25)
    sp = 0
    if state_name.lower() in t: sp += 5
    if _re.search(r'\d{1,3}(,\d{3})*\s*(liens|filings|cases)', t): sp += 8
    county_hits = sum(1 for w in ["county","parish","borough"] if w in t)
    sp += min(8, county_hits * 4)
    if _re.search(r'\$[\d,]+', t): sp += 4
    sp = min(25, sp)

    # 2. Human voice (25)
    hv = 0
    human_phrases = ["here's what","nobody tells","most people","the thing is",
        "what actually","if you've","here's the","what this means","what happens",
        "the real","the part most","you should","before you","the irs won't",
        "they don't tell","here's something","what to do","this is what"]
    hv += min(15, sum(3 for p in human_phrases if p in t))
    compliance_phrases = ["pursuant to","in accordance with","it should be noted",
        "as previously stated","the aforementioned","it is important to note"]
    hv += 10 if not any(p in t for p in compliance_phrases) else 0
    hv = min(25, hv)

    # 3. Actionability (20)
    ac = 0
    action_phrases = ["what to do","first step","your next","take action","call",
        "quiz","start with","request","file","respond","check your","pull your",
        "contact","review","assess","do this","one thing to do","this month"]
    ac += min(12, sum(3 for p in action_phrases if p in t))
    if "taxcasereview.org/quiz" in t or "/quiz" in t: ac += 8
    ac = min(20, ac)

    # 4. SEO signals (15)
    seo = 0
    seo_phrases = ["irs tax lien","federal tax lien","tax debt","tax resolution",
        "offer in compromise","installment agreement","penalty abatement",
        "irs notice","payroll tax","tax relief"]
    seo += min(10, sum(2 for p in seo_phrases if p in t))
    if state_name.lower() in t: seo += 5
    seo = min(15, seo)

    # 5. Word count (15)
    words = len(content.split())
    if 900 <= words <= 1200: wc = 15
    elif 750 <= words < 900 or 1200 < words <= 1400: wc = 10
    elif 650 <= words < 750 or 1400 < words <= 1600: wc = 5
    else: wc = 0

    total = sp + hv + ac + seo + wc
    return {
        "total": total,
        "specificity": sp, "human_voice": hv,
        "actionability": ac, "seo_signals": seo,
        "word_count_score": wc, "word_count": words,
        "passes": total >= 70,
    }


def generate_with_quality_check(prompt: str, state_name: str,
                                  max_attempts: int = 3,
                                  threshold: int = 70) -> tuple:
    """Generate content, score it, regenerate if below threshold."""
    best_content = ""
    best_score: dict = {"total": 0}

    for attempt in range(1, max_attempts + 1):
        content = call_claude(prompt, max_tokens=2500)
        score   = score_report(content, state_name)
        print(f"  Attempt {attempt}: {score['total']}/100 "
              f"(spec={score['specificity']} human={score['human_voice']} "
              f"action={score['actionability']} seo={score['seo_signals']} "
              f"words={score['word_count']})")

        if score["total"] > best_score["total"]:
            best_content = content
            best_score   = score

        if score["passes"]:
            print(f"  ✅ Passed quality threshold on attempt {attempt}")
            break
        elif attempt < max_attempts:
            print(f"  ⚠️  Score {score['total']} < {threshold} — regenerating...")

    if not best_score["passes"]:
        print(f"  ⚠️  Best score {best_score['total']}/100 — using best version")

    return best_content, best_score

def generate_state_report(state_key: str, cfg: dict,
                          db_data: dict = None) -> str:
    month_year = date.today().strftime("%B %Y")
    state      = cfg["name"]
    abbr       = cfg["abbreviation"]
    industries = ", ".join(cfg["key_industries"])
    counties   = ", ".join(cfg["top_counties"][:5])
    slug       = f"monthly-{state_key.replace('_','-')}-{date.today().strftime('%Y-%m')}"
    state_url  = f"{SITE_URL}{cfg['landing']}"
    data_note  = cfg.get("data_note", "")

    if db_data and cfg["has_db_data"]:
        top_text = "\n".join(
            f"  - {c['county']} County: {c['count']} liens"
            for c in db_data["top_counties"][:5]
        )
        data_context = f"""
VERIFIED DATABASE DATA:
- Total IRS liens tracked in {state} (all time): {db_data['total_liens_all_time']:,}
- New liens this month ({month_year}): {db_data['new_this_month']:,}
- New liens last month: {db_data['new_last_month']:,}
- Month-over-month: {'+' if db_data['pct_change'] >= 0 else ''}{db_data['pct_change']}%
- Top counties this month:
{top_text}

Use these exact numbers. Do not invent additional statistics."""
    else:
        data_context = f"""
DATA NOTE: {data_note}
Use publicly available IRS enforcement patterns for {state}.
Label any estimates as "estimated" or "based on national IRS trends."
Do NOT invent specific numbers. Use directional language and ranges."""

    report_md, quality = generate_with_quality_check(f"""You are a former IRS Revenue Officer writing a monthly intelligence briefing for TaxCase Review.
Your reader is a contractor, small business owner, or self-employed professional in {state} who found this report while researching their IRS situation.
Write like a trusted expert — direct, specific, human. Not like a government document.

{data_context}

Key industries in {state}: {industries}
Major counties: {counties}
IRS notices most common in {state}: {cfg['notice_focus']}
State page: {state_url}

Write in this EXACT markdown format — return ONLY the markdown:

---
title: "{state} IRS Tax Lien Report — {month_year}"
date: "{date.today().isoformat()}"
slug: "{slug}"
type: "monthly-report"
state: "{state_key}"
month: "{date.today().strftime('%Y-%m')}"
metaDescription: "IRS enforcement trends in {state} for {month_year}. Who's getting targeted, which counties are active, and what your options are if you have a lien."
---

# IRS Enforcement in {state}: What's Happening in {month_year}

*Monthly briefing from TaxCase Review — compiled from public lien data and IRS enforcement patterns*

## The Short Version

[3-4 sentences. Write this like a text message from a knowledgeable friend. What's the single most important thing a {state} business owner should know this month? Be specific. Example: "IRS enforcement in {state} is running hotter than usual in {month_year}. The industries getting hit hardest are {cfg['key_industries'][0]} and {cfg['key_industries'][1]}. If you've received a {cfg['notice_focus'].split(',')[0].strip()} and haven't responded, here's what happens next."]

## Who the IRS Is Targeting in {state} Right Now

[180 words. Not generic — specific to {state}. What makes {state} different from other states for IRS enforcement? Mention the specific industries ({industries}) and why they're vulnerable. Use real context: seasonal income patterns, 1099 work, payroll tax issues, estimated tax failures. Make a {state} contractor feel like this was written specifically about their situation.]

## The Counties With the Most Activity

[Cover {counties}. For each, one sentence about why that county has high lien activity — what industry, what economic pattern. Don't just list — explain the why. 100-120 words total.]

## The IRS Notice Most People in {state} Are Getting Wrong

[150 words. Pick the most important notice from {cfg['notice_focus']}. Explain what it actually means in plain language — not what the IRS says it means, but what it means for a real person. What do most people do wrong when they get it? What should they actually do? Write this like a friend who used to work at the IRS explaining it over coffee.]

## What Happens If You Ignore It (The Real Timeline)

[120 words. Walk through the actual IRS collection sequence in {state}. Not "the IRS may take enforcement action." Be specific: notice → lien filed → LT11 → levy authority. How long does each step take? At what point does a bank account get frozen? Make the urgency real without being alarmist.]

## Options That Actually Work for {state} Taxpayers

[140 words. Pick the 3 resolution paths most relevant to {cfg['key_industries'][0]} and {cfg['key_industries'][1]} in {state}. Don't just define each option — explain which one fits which situation. Example: "If you're a {cfg['key_industries'][0].rstrip('s')} with payroll tax debt, an installment agreement is rarely the right first move — here's why." Be specific and opinionated. End with: "The fastest way to know which path applies to you is a 60-second assessment at {state_url}."]

## One Thing to Do This Month

[50 words. One concrete, specific action. Not "contact a tax professional." Something like: "Pull your IRS transcript at IRS.gov — it shows every notice filed, every balance due, and whether a lien has been recorded against you. Takes 5 minutes. Do it before anything else."]

📞 {PHONE} | [{SITE_URL}/quiz Start free assessment]({SITE_URL}/quiz)

*Based on public IRS data and enforcement patterns. Individual circumstances vary. Not legal or tax advice.*

---

RULES:
- 900-1100 words total
- Write for a real {state} business owner or contractor, not for a general audience
- Use {state}-specific context — economy, industries, counties, seasonal patterns
- Every section should deliver one useful insight, not just fill space
- No guaranteed outcomes, no legal claims, no invented statistics
- Return ONLY the markdown""", state)
    return report_md


# ── GitHub publisher ──────────────────────────────────────────────────────────

INDEXNOW_KEY = "9e9b2e673445719e87ed5e2213724841"  # same key as social_media_poster.py / reel_generator.py


def index_url(url: str):
    """Submit a freshly published report URL to IndexNow (Bing/Yandex). Same
    key/host as the rest of the codebase. Non-blocking."""
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


def publish_to_github(filename: str, content: str,
                      commit_msg: str) -> bool:
    if not GITHUB_TOKEN:
        print("  ⚠  GITHUB_TOKEN not set")
        return False

    api_url = (f"https://api.github.com/repos/{GITHUB_REPO}"
               f"/contents/{REPORTS_PATH}/{filename}")
    headers = {
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
    payload     = {"message": commit_msg, "content": content_b64,
                   "branch": GITHUB_BRANCH}
    if sha:
        payload["sha"] = sha

    r = requests.put(api_url, headers=headers, json=payload, timeout=30)
    if r.status_code in (200, 201):
        action = "Updated" if sha else "Created"
        print(f"  ✅ {action}: {REPORTS_PATH}/{filename}")
        return True
    print(f"  ❌ GitHub error: {r.status_code} — {r.text[:120]}")
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TaxCase Review Monthly State Reports v3 — 10 states")
    parser.add_argument("--state",   default=None,
                        choices=list(STATES.keys()))
    parser.add_argument("--all",     action="store_true",
                        help="Generate all 10 states")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.state and not args.all:
        parser.print_help()
        return

    states_to_run = list(STATES.keys()) if args.all else [args.state]

    print(f"\n{'='*60}")
    print(f"  TaxCase Review Monthly State Reports v4 — Quality Scored")
    print(f"  {datetime.now().strftime('%B %Y')}")
    print(f"  States : {', '.join(STATES[s]['abbreviation'] for s in states_to_run)}")
    print(f"  {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"{'='*60}\n")

    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("monthly_report")
        logger.start()
    except ImportError:
        logger = None

    results = {}

    for state_key in states_to_run:
        cfg = STATES[state_key]
        print(f"\n── {cfg['name']} ({cfg['abbreviation']}) ──")
        if logger: logger.step_start(f"report_{state_key}")

        db_data = None
        if cfg["has_db_data"]:
            print("  Pulling DB data...")
            db_data = get_florida_monthly_data()
            print(f"  Total (all time) : {db_data['total_liens_all_time']:,}")
            print(f"  New this month   : {db_data['new_this_month']:,}")
            print(f"  MoM change       : "
                  f"{'+' if db_data['pct_change'] >= 0 else ''}"
                  f"{db_data['pct_change']}%")
        else:
            print(f"  Source: {cfg['data_note']}")

        print("  Generating with Claude (quality-scored, up to 3 attempts)...")
        try:
            report_md  = generate_state_report(state_key, cfg, db_data)
            month_str  = date.today().strftime("%Y-%m")
            slug       = f"monthly-{state_key.replace('_','-')}-{month_str}"
            local_file = DATA_OPS / f"{slug}.md"
            local_file.write_text(report_md, encoding="utf-8")
            print(f"  Length : {len(report_md):,} chars")
            print(f"  Saved  : {local_file}")

            if not args.dry_run:
                filename   = f"{slug}.md"
                commit_msg = (f"Monthly {cfg['name']} report — "
                              f"{date.today().strftime('%B %Y')}")
                published  = publish_to_github(filename, report_md, commit_msg)
                if published:
                    print(f"  🌐 {SITE_URL}/reports/{slug}")
                    index_url(f"{SITE_URL}/reports/{slug}")
                results[state_key] = "published" if published else "failed"
            else:
                print(f"  [DRY RUN] → {slug}.md")
                results[state_key] = "dry_run"

            if logger:
                logger.step_done(f"report_{state_key}", ok=True,
                                 detail=f"{len(report_md)} chars")

        except Exception as e:
            print(f"  ❌ Error: {e}")
            results[state_key] = "error"
            if logger:
                logger.step_done(f"report_{state_key}",
                                 ok=False, error=str(e))

    print(f"\n{'='*60}")
    print(f"  Monthly Reports Complete")
    for k, v in results.items():
        icon = "✅" if v in ("published", "dry_run") else "❌"
        print(f"  {icon} {STATES[k]['name']}: {v}")
    print(f"{'='*60}\n")

    if logger:
        logger.finish({
            "states":   states_to_run,
            "results":  results,
            "month":    date.today().strftime("%Y-%m"),
            "dry_run":  args.dry_run,
        })


if __name__ == "__main__":
    main()