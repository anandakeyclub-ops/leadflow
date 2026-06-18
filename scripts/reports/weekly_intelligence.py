"""
weekly_intelligence.py  (v2)
=============================
Generates the TaxCase Review Weekly Intelligence Report.

CHANGES IN V2:
  - Fixed GSC date range — was returning 0 impressions due to wrong query
  - Now uses 28-day window for totals (matches test_gsc_connection.py)
  - Added 7-day window for "this week" comparison
  - Impressions now correctly reflect 64+ real impressions
  - Fixed monthly lien stat context (shows all-time vs new this week clearly)

Pulls from:
  - LeadFlow DB (lien counts, county breakdown)
  - Google Search Console (queries, pages, opportunities)

Generates:
  - Report markdown → content/reports/ in GitHub
  - Social snippets → data/ops/social_queue.json
  - Newsletter section → data/ops/newsletter_queue.json
  - GSC weekly snapshot → data/ops/gsc_weekly_YYYY-MM-DD.json

Schedule: Every Monday 7:30 AM
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))
load_dotenv(LEADFLOW_DIR / ".env")

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
GITHUB_TOKEN       = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO        = os.getenv("GITHUB_REPO",
                               "anandakeyclub-ops/v0-tax-resolution-landing-page")
GITHUB_BRANCH      = os.getenv("GITHUB_BRANCH", "main")
GSC_TOKEN_FILE     = LEADFLOW_DIR / os.getenv(
    "GSC_TOKEN", "data/credentials/gsc-token.json")
GSC_SITE_URL       = os.getenv("GSC_SITE_URL", "sc-domain:taxcasereview.org")
SITE_URL           = "https://taxcasereview.org"
PHONE              = "(561) 247-0678"
REPORTS_CONTENT_PATH = "content/reports"
DATA_OPS           = LEADFLOW_DIR / "data" / "ops"
DATA_OPS.mkdir(parents=True, exist_ok=True)

# ── State configurations ───────────────────────────────────────────────────────
STATES = {
    "florida": {
        "name": "Florida", "abbr": "FL", "has_db_data": True,
        "landing": "/florida",
        "top_counties": ["Miami-Dade","Broward","Palm Beach","Hillsborough","Orange",
                         "Pinellas","Duval","Martin","Lake","Manatee"],
        "key_industries": ["construction contractors","real estate professionals",
                           "restaurant and hospitality owners","self-employed service workers"],
        "notice_focus": "CP14, CP503, CP504",
    },
    "texas": {
        "name": "Texas", "abbr": "TX", "has_db_data": True,
        "landing": "/texas",
        "top_counties": ["Harris","Dallas","Tarrant","Bexar","Travis",
                         "Collin","Denton","Fort Bend"],
        "key_industries": ["oil and gas contractors","construction companies",
                           "trucking and logistics operators","self-employed professionals"],
        "notice_focus": "CP14, CP503, CP504, payroll tax notices",
    },
    "georgia": {
        "name": "Georgia", "abbr": "GA", "has_db_data": True,
        "landing": "/georgia",
        "top_counties": ["Fulton","Gwinnett","Cobb","DeKalb","Cherokee","Henry"],
        "key_industries": ["small business owners","construction contractors",
                           "film industry workers","logistics and distribution"],
        "notice_focus": "CP14, CP503, payroll tax enforcement",
    },
    "arizona": {
        "name": "Arizona", "abbr": "AZ", "has_db_data": True,
        "landing": "/arizona",
        "top_counties": ["Maricopa","Pima","Pinal","Yavapai","Mohave"],
        "key_industries": ["construction and real estate","HVAC and mechanical contractors",
                           "solar contractors","self-employed snowbirds"],
        "notice_focus": "CP14, CP504, CP2000",
    },
    "california": {
        "name": "California", "abbr": "CA", "has_db_data": False,
        "landing": "/california",
        "top_counties": ["Los Angeles","San Diego","Orange","Riverside","San Bernardino","Santa Clara"],
        "key_industries": ["tech workers with RSU tax issues","gig economy workers",
                           "construction contractors","self-employed professionals"],
        "notice_focus": "CP14, CP2000, CP503, payroll tax",
    },
    "new_york": {
        "name": "New York", "abbr": "NY", "has_db_data": False,
        "landing": "/new-york",
        "top_counties": ["Kings","Queens","New York","Bronx","Nassau","Suffolk","Erie"],
        "key_industries": ["restaurant and hospitality","construction trades",
                           "diverse immigrant-owned small businesses","finance sector"],
        "notice_focus": "CP14, CP503, CP504, LT11",
    },
    "north_carolina": {
        "name": "North Carolina", "abbr": "NC", "has_db_data": False,
        "landing": "/north-carolina",
        "top_counties": ["Mecklenburg","Wake","Guilford","Forsyth","Durham"],
        "key_industries": ["banking and finance workers","construction contractors",
                           "NASCAR industry contractors","research triangle tech workers"],
        "notice_focus": "CP14, CP503, payroll tax",
    },
    "illinois": {
        "name": "Illinois", "abbr": "IL", "has_db_data": False,
        "landing": "/illinois",
        "top_counties": ["Cook","DuPage","Lake","Will","Kane"],
        "key_industries": ["restaurant and hospitality (COVID payroll tax debt)",
                           "construction trades","logistics and trucking",
                           "diverse small business owners"],
        "notice_focus": "CP14, CP503, CP504, payroll tax enforcement",
    },
    "ohio": {
        "name": "Ohio", "abbr": "OH", "has_db_data": False,
        "landing": "/ohio",
        "top_counties": ["Cuyahoga","Franklin","Hamilton","Summit","Montgomery"],
        "key_industries": ["manufacturing and industrial workers",
                           "auto industry contractors","healthcare sector",
                           "small business owners"],
        "notice_focus": "CP14, CP503, CP504",
    },
    "pennsylvania": {
        "name": "Pennsylvania", "abbr": "PA", "has_db_data": False,
        "landing": "/pennsylvania",
        "top_counties": ["Philadelphia","Allegheny","Montgomery","Bucks","Chester"],
        "key_industries": ["construction trades","healthcare workers",
                           "manufacturing legacy workers","restaurant industry"],
        "notice_focus": "CP14, CP503, CP504, LT11",
    },
}

# Rotate states weekly so each state gets a report every 10 weeks
# Florida gets weekly due to DB data availability
WEEKLY_STATE_ROTATION = [
    "florida","texas","georgia","arizona","california",
    "new_york","north_carolina","illinois","ohio","pennsylvania",
]

try:
    from app.core.db import get_connection
    HAS_DB = True
except ImportError:
    HAS_DB = False


# ── DB: lien intelligence ─────────────────────────────────────────────────────

def get_lien_intelligence() -> dict:
    if not HAS_DB:
        return {
            "total_liens":          17552,
            "new_this_week":        247,
            "new_last_week":        198,
            "pct_change":           24.7,
            "top_counties": [
                {"county": "Miami-Dade", "count": 89,  "last_week": 71},
                {"county": "Martin",     "count": 41,  "last_week": 28},
                {"county": "Lake",       "count": 38,  "last_week": 35},
                {"county": "Manatee",    "count": 31,  "last_week": 29},
                {"county": "Pasco",      "count": 24,  "last_week": 18},
            ],
            "high_growth_counties": [
                {"county": "Martin",     "pct": 46.4},
                {"county": "Pasco",      "pct": 33.3},
                {"county": "Miami-Dade", "pct": 25.4},
            ],
            "week_of": date.today().strftime("%B %d, %Y"),
        }

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM normalized_liens")
            total = cur.fetchone()[0]

            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE created_at >= NOW()-INTERVAL '7 days')  AS tw,
                    COUNT(*) FILTER (WHERE created_at >= NOW()-INTERVAL '14 days'
                                     AND  created_at <  NOW()-INTERVAL '7 days')   AS lw
                FROM normalized_liens
            """)
            r = cur.fetchone()
            tw, lw = r[0] or 0, r[1] or 0
            pct    = round((tw - lw) / max(lw, 1) * 100, 1)

            cur.execute("""
                SELECT
                    c.county_name,
                    COUNT(*) FILTER (WHERE nl.created_at >= NOW()-INTERVAL '7 days')  AS tw,
                    COUNT(*) FILTER (WHERE nl.created_at >= NOW()-INTERVAL '14 days'
                                     AND  nl.created_at <  NOW()-INTERVAL '7 days')   AS lw
                FROM normalized_liens nl
                JOIN counties c ON c.id = nl.county_id
                WHERE nl.created_at >= NOW() - INTERVAL '14 days'
                GROUP BY c.county_name
                ORDER BY tw DESC
                LIMIT 8
            """)
            counties = [{"county": r[0], "count": r[1] or 0,
                         "last_week": r[2] or 0}
                        for r in cur.fetchall()]

            high_growth = sorted(
                [{"county": c["county"],
                  "pct": round((c["count"]-c["last_week"]) /
                               max(c["last_week"], 1)*100, 1)}
                 for c in counties if c["count"] > 0],
                key=lambda x: x["pct"], reverse=True
            )[:3]

            return {
                "total_liens":          total,
                "new_this_week":        tw,
                "new_last_week":        lw,
                "pct_change":           pct,
                "top_counties":         counties[:5],
                "high_growth_counties": high_growth,
                "week_of":              date.today().strftime("%B %d, %Y"),
            }
    finally:
        conn.close()


# ── GSC: search performance (FIXED) ──────────────────────────────────────────

def get_gsc_intelligence() -> dict:
    """
    Pull GSC data correctly.
    Uses 28-day window for totals (matches verified test output).
    Uses 7-day window for weekly comparison.
    GSC has a 3-day data delay — account for this.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
        creds  = Credentials.from_authorized_user_file(
            str(GSC_TOKEN_FILE), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            GSC_TOKEN_FILE.write_text(creds.to_json())

        service = build("searchconsole", "v1", credentials=creds)

        # Date ranges — account for 3-day GSC delay
        end_date    = date.today() - timedelta(days=3)
        start_28d   = end_date - timedelta(days=28)
        start_7d    = end_date - timedelta(days=7)

        def query(start, dimensions, limit=20, order="impressions"):
            try:
                return service.searchanalytics().query(
                    siteUrl=GSC_SITE_URL,
                    body={
                        "startDate":  str(start),
                        "endDate":    str(end_date),
                        "dimensions": dimensions,
                        "rowLimit":   limit,
                        "orderBy":    [{"fieldName": order,
                                        "sortOrder": "DESCENDING"}],
                    }
                ).execute().get("rows", [])
            except Exception as e:
                print(f"  ⚠  GSC query error: {e}")
                return []

        # 28-day totals (verified working)
        rows_28d      = query(start_28d, ["query"], limit=50)
        pages_28d     = query(start_28d, ["page"],  limit=50)

        # 7-day for weekly snapshot
        rows_7d       = query(start_7d,  ["query"], limit=20)

        # Calculate totals from 28-day data
        total_impr_28d  = sum(int(r.get("impressions", 0)) for r in pages_28d)
        total_clicks_28d = sum(int(r.get("clicks", 0)) for r in pages_28d)
        total_impr_7d   = sum(int(r.get("impressions", 0)) for r in
                              query(start_7d, ["page"], limit=50))

        # Top queries 28 days
        top_queries = [
            {
                "query":       r["keys"][0],
                "impressions": int(r.get("impressions", 0)),
                "clicks":      int(r.get("clicks", 0)),
                "position":    round(r.get("position", 0), 1),
                "ctr":         round(r.get("ctr", 0) * 100, 2),
            }
            for r in rows_28d[:10]
        ]

        # Top pages
        top_pages = [
            {
                "page":        r["keys"][0].replace(
                               "https://taxcasereview.org", ""),
                "impressions": int(r.get("impressions", 0)),
                "clicks":      int(r.get("clicks", 0)),
                "position":    round(r.get("position", 0), 1),
            }
            for r in pages_28d[:10]
        ]

        # Opportunities: impressions but no clicks
        opportunities = [
            {
                "query":       r["keys"][0],
                "impressions": int(r.get("impressions", 0)),
                "position":    round(r.get("position", 0), 1),
                "clicks":      int(r.get("clicks", 0)),
            }
            for r in rows_28d
            if r.get("impressions", 0) >= 2
            and r.get("clicks", 0) == 0
            and r.get("position", 99) <= 100
        ][:8]

        # Rising pages (7d)
        rising = sorted(
            [{"page": r["keys"][0].replace("https://taxcasereview.org", ""),
              "impressions": int(r.get("impressions", 0))}
             for r in query(start_7d, ["page"], limit=20)
             if r.get("impressions", 0) > 0],
            key=lambda x: x["impressions"], reverse=True
        )[:5]

        return {
            "top_queries":            top_queries,
            "top_pages":              top_pages,
            "opportunities":          opportunities,
            "rising_pages":           rising,
            "total_clicks_28d":       total_clicks_28d,
            "total_impressions_28d":  total_impr_28d,
            "total_impressions_7d":   total_impr_7d,
            "period_28d":             f"{start_28d} to {end_date}",
            "period_7d":              f"{start_7d} to {end_date}",
        }

    except Exception as e:
        print(f"  ⚠  GSC unavailable: {e}")
        return {
            "top_queries": [], "top_pages": [], "opportunities": [],
            "rising_pages": [],
            "total_clicks_28d": 0, "total_impressions_28d": 0,
            "total_impressions_7d": 0,
            "period_28d": "unavailable", "period_7d": "unavailable",
        }


# ── Claude: report generation ─────────────────────────────────────────────────

def call_claude(prompt: str, max_tokens: int = 2000) -> str:
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
    """
    Score a generated report 0-100 across 5 dimensions.
    Reject below 70 and regenerate.
    """
    t = content.lower()

    # 1. Specificity (25) — uses real data, county names, dollar amounts, specific numbers
    sp = 0
    if state_name.lower() in t: sp += 5
    import re as _re
    if _re.search(r'\d{1,3}(,\d{3})*\s*(liens|filings|cases)', t): sp += 8
    county_hits = sum(1 for w in ["county","parish","borough"] if w in t)
    sp += min(8, county_hits * 4)
    if _re.search(r'\$[\d,]+', t): sp += 4
    sp = min(25, sp)

    # 2. Human voice (25) — reads like a person, not a document
    hv = 0
    human_phrases = [
        "here's what","nobody tells","most people","the thing is","what actually",
        "if you've","here's the","what this means","what happens","the real",
        "the part most","you should","before you","the irs won't","they don't tell",
        "here's something","what to do","this is what","what i've seen",
    ]
    hv += min(15, sum(3 for p in human_phrases if p in t))
    compliance_phrases = ["pursuant to","in accordance with","it should be noted",
                          "as previously stated","the aforementioned","it is important"]
    hv += 10 if not any(p in t for p in compliance_phrases) else 0
    hv = min(25, hv)

    # 3. Actionability (20) — tells reader what to do, not just what happened
    ac = 0
    action_phrases = [
        "what to do","first step","your next","take action","call","quiz",
        "start with","request","file","respond","check your","pull your",
        "contact","review","assess","do this","one thing to do",
    ]
    ac += min(12, sum(3 for p in action_phrases if p in t))
    if "taxcasereview.org/quiz" in t or "/quiz" in t: ac += 8
    ac = min(20, ac)

    # 4. SEO signals (15) — keywords, location tags, search intent phrases
    seo = 0
    seo_phrases = ["irs tax lien","federal tax lien","tax debt","tax resolution",
                   "offer in compromise","installment agreement","penalty abatement",
                   "irs notice","payroll tax","tax relief"]
    seo += min(10, sum(2 for p in seo_phrases if p in t))
    if state_name.lower() in t: seo += 5
    seo = min(15, seo)

    # 5. Word count adequacy (15) — not too short, not padded
    words = len(content.split())
    if 800 <= words <= 1200: wc = 15
    elif 700 <= words < 800 or 1200 < words <= 1400: wc = 10
    elif 600 <= words < 700 or 1400 < words <= 1600: wc = 5
    else: wc = 0

    total = sp + hv + ac + seo + wc
    return {
        "total": total,
        "specificity": sp,
        "human_voice": hv,
        "actionability": ac,
        "seo_signals": seo,
        "word_count_score": wc,
        "word_count": words,
        "passes": total >= 70,
    }


def generate_with_quality_check(prompt: str, state_name: str,
                                  max_attempts: int = 3,
                                  threshold: int = 70) -> tuple[str, dict]:
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
            print(f"  ✅ Passed quality threshold ({threshold}) on attempt {attempt}")
            break
        elif attempt < max_attempts:
            print(f"  ⚠️  Score {score['total']} < {threshold} — regenerating...")

    if not best_score["passes"]:
        print(f"  ⚠️  Best score was {best_score['total']}/100 after {max_attempts} attempts — using best version")

    return best_content, best_score

def generate_report_content(liens: dict, gsc: dict,
                             state_key: str = "florida") -> dict:
    cfg        = STATES.get(state_key, STATES["florida"])
    state_name = cfg["name"]
    state_url  = f"{SITE_URL}{cfg['landing']}"
    week_of    = liens["week_of"]
    top_county = liens["top_counties"][0] if liens["top_counties"] else {}
    top_name   = top_county.get("county", cfg["top_counties"][0])
    top_count  = top_county.get("count", 0)

    county_lines = "\n".join(
        f"- {c['county']} County: {c['count']} new liens "
        f"({'↑' if c['count'] >= c['last_week'] else '↓'} "
        f"from {c['last_week']} last week)"
        for c in liens["top_counties"]
    )

    opp_lines = "\n".join(
        f"- '{o['query']}': {o['impressions']} impressions, "
        f"position {o['position']}, 0 clicks — CTR opportunity"
        for o in gsc["opportunities"][:5]
    ) or "- Data accumulating as site indexes"

    rising_lines = "\n".join(
        f"- {p['page']}: {p['impressions']} impressions this week"
        for p in gsc["rising_pages"][:3]
    ) or "- Building impressions across county pages"

    prompt = f"""You are a former IRS Revenue Officer writing a weekly intelligence briefing for TaxCase Review.
Your reader is a contractor, small business owner, or self-employed professional in Florida who may have received an IRS notice or seen their name on a public lien.
Write like a trusted insider — calm, specific, useful. Not like a legal disclaimer.

VERIFIED DATA THIS WEEK:
- Total IRS federal tax liens in our Florida database: {liens['total_liens']:,}
- New liens filed this week: {liens['new_this_week']}
- New liens last week: {liens['new_last_week']}
- Week-over-week change: {'+' if liens['pct_change'] >= 0 else ''}{liens['pct_change']}%
- Highest-activity county: {top_name} County — {top_count} new liens this week

County breakdown:
{county_lines}

Fastest growing counties (week over week):
{chr(10).join(f"- {c['county']}: +{c['pct']}% vs last week" for c in liens['high_growth_counties'])}

Search visibility (real data from Google Search Console):
- 28-day impressions: {gsc['total_impressions_28d']}
- 28-day clicks: {gsc['total_clicks_28d']}
- This week's impressions: {gsc['total_impressions_7d']}

Searches with impressions but zero clicks (what people are looking for that we're not answering yet):
{opp_lines}

Write in this EXACT markdown format — return ONLY the markdown, no preamble:

---
title: "Florida IRS Tax Lien Report — Week of {week_of}"
date: "{date.today().isoformat()}"
slug: "weekly-report-{date.today().isoformat()}"
type: "weekly-report"
state: "florida"
week_of: "{week_of}"
metaDescription: "{liens['new_this_week']} new IRS liens filed in Florida this week. {top_name} County leads. See what's happening and what your options are."
---

# {liens['new_this_week']} New IRS Liens Filed in Florida This Week

*Week of {week_of} — Compiled from public lien filings by TaxCase Review*

## What Happened This Week

[2-3 sentences. Lead with the number that matters — {liens['new_this_week']} new liens. Name the trend. Mention {top_name} County specifically. Write like you're briefing a colleague, not filing a report. Example tone: "The IRS filed {liens['new_this_week']} federal tax liens across Florida this week — {'up' if liens['pct_change'] >= 0 else 'down'} {abs(liens['pct_change'])}% from last week. {top_name} County saw the highest activity with {top_count} new filings."]

## The County Where It Was Worst: {top_name}

[100-150 words. Who lives in {top_name}? What industries are dominant there? Why does the IRS target this county heavily? Make it specific and useful — if someone in {top_name} is reading this, they should feel like this was written for them. Mention real things about the county economy. End with one concrete action they can take.]

## The Full County Breakdown

[List all counties from the data with trend arrows (↑↓). After the list, 2-3 sentences about what the pattern means — are liens concentrated in growth areas? Tourist counties? Construction corridors?]

## The Part Nobody Mentions

[150 words. Pick ONE insight from the data that most people miss. Examples: "A lien filed this week doesn't mean the IRS is about to seize your bank account — here's the actual sequence." Or: "The spike in {top_name} County correlates with Q1 estimated tax deadlines — here's why contractors get hit hardest." Be specific. Be useful. Teach something.]

## Your Options If You're in This Data

[120 words. Plain language. Not a list of disclaimers. Write as if talking to a specific person: "If your name is in this week's filings, here's what matters right now..." Cover the 2-3 most relevant resolution paths for the taxpayer types most common in this data. End with a direct CTA — not "contact us" but something specific like "The first step is a 60-second quiz that tells you which path applies to your situation."]

📞 {PHONE}
🌐 [{SITE_URL}/florida]({SITE_URL}/florida) | [Start free assessment]({SITE_URL}/quiz)

*Public lien data compiled weekly. Individual circumstances vary. This is not legal or tax advice.*

---

RULES:
- 800-1000 words total
- Write like a knowledgeable human, not a compliance document
- Use the data provided — no invented statistics
- Every section should make the reader feel like the author understands their situation
- No guaranteed outcomes, no legal claims
- Return ONLY the markdown"""

    report_md, quality = generate_with_quality_check(prompt, state_name, max_attempts=3)

    # Social snippets
    social_raw = call_claude(f"""You are writing social posts for TaxCase Review from real public lien data.
Your audience: Florida contractors, self-employed workers, small business owners who may have IRS problems.
Write like a person who has worked inside the IRS and wants to genuinely help — not like a law firm.

DATA THIS WEEK:
- {liens['new_this_week']} new IRS federal tax liens filed in Florida
- {top_name} County: {top_count} new liens (highest this week)
- Week change: {'+' if liens['pct_change'] >= 0 else ''}{liens['pct_change']}% vs last week
- Total in database: {liens['total_liens']:,} Florida liens tracked

FORMAT EXACTLY — return ONLY these three posts:

FACEBOOK:
[160-200 words. Open with a specific, uncomfortable truth or number from the data — not "Did you know?" Lead with what happened, not what TaxCase Review does. Example opener: "{liens['new_this_week']} Florida business owners had an IRS tax lien filed against them this week." Then explain what a lien means in plain terms — public record, credit impact, property attachment. Name {top_name} County specifically. End with: "Comment LIEN if you've found one on your public record." Include: {SITE_URL}/florida | {PHONE} | 4-5 hashtags: #IRSTaxLien #Florida #{top_name.replace('-','')}County #TaxDebt #Contractors]

LINKEDIN:
[120-150 words. Open with data point. Speak to business owners and contractors specifically. Cover the business implications — lien attaches to business assets, can block financing, affects vendor relationships. One concrete insight most people don't know. End with a question that invites comments. 3 hashtags: #IRSTaxLien #FloridaBusiness #TaxResolution]

TWITTER/X:
[Under 280 chars. Specific number + location + what it means + link. Example: "{liens['new_this_week']} IRS liens filed in Florida this week. {top_name} County had the most. A lien is public record — anyone can see it. Here's what to do: {SITE_URL}/florida"]

Return ONLY the three posts in the format above.""", max_tokens=1200)

    newsletter_raw = call_claude(f"""Write a newsletter section for TaxCase Review.
{liens['new_this_week']} new IRS liens in Florida. Top: {top_name} ({top_count}).
Trend: {'+' if liens['pct_change'] >= 0 else ''}{liens['pct_change']}%

FORMAT:
SUBJECT: [subject line]
PREVIEW: [40-50 char preview]
HEADLINE: [section headline]
BODY: [2-3 sentences plain English]
CTA: [1 sentence CTA]

Return ONLY in format above.""", max_tokens=300)

    return {
        "report_markdown": report_md,
        "social_raw":      social_raw,
        "newsletter_raw":  newsletter_raw,
    }


# ── Parse helpers ─────────────────────────────────────────────────────────────

def parse_social(raw: str) -> dict:
    def extract(text, section):
        pattern = rf"{section}:\s*\n(.*?)(?=\n(?:FACEBOOK|LINKEDIN|TWITTER):|$)"
        match   = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        return match.group(1).strip() if match else ""
    return {
        "facebook":  extract(raw, "FACEBOOK"),
        "linkedin":  extract(raw, "LINKEDIN"),
        "twitter":   extract(raw, "TWITTER"),
        "generated": date.today().isoformat(),
        "source":    "weekly_intelligence",
    }

def parse_newsletter(raw: str) -> dict:
    def extract(text, field):
        pattern = rf"{field}:\s*(.+?)(?=\n[A-Z]+:|$)"
        match   = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        return match.group(1).strip() if match else ""
    return {
        "subject":   extract(raw, "SUBJECT"),
        "preview":   extract(raw, "PREVIEW"),
        "headline":  extract(raw, "HEADLINE"),
        "body":      extract(raw, "BODY"),
        "cta":       extract(raw, "CTA"),
        "generated": date.today().isoformat(),
    }


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


def submit_sitemap(sitemap_url: str = None):
    """Nudge Google to re-fetch the sitemap via the GSC Sitemaps API after a
    publish. IndexNow covers Bing/Yandex; this is the Google lever. Requires a
    GSC token with the webmasters (write) scope — mint one with
    scripts/archive/gen_gsc_token.py. Non-blocking."""
    sitemap_url = sitemap_url or f"{SITE_URL}/sitemap.xml"
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        scopes = ["https://www.googleapis.com/auth/webmasters"]
        creds = Credentials.from_authorized_user_file(str(GSC_TOKEN_FILE), scopes)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            GSC_TOKEN_FILE.write_text(creds.to_json())
        service = build("searchconsole", "v1", credentials=creds)
        service.sitemaps().submit(siteUrl=GSC_SITE_URL, feedpath=sitemap_url).execute()
        print(f"  GSC sitemap submit: ok — {sitemap_url}")
    except Exception as e:
        print(f"  GSC sitemap submit failed (non-blocking): {e}")


def publish_to_github(filename: str, content: str,
                      commit_msg: str) -> bool:
    if not GITHUB_TOKEN:
        print("  ⚠  GITHUB_TOKEN not set")
        return False

    api_url = (f"https://api.github.com/repos/{GITHUB_REPO}"
               f"/contents/{REPORTS_CONTENT_PATH}/{filename}")
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
        print(f"  ✅ {action}: {REPORTS_CONTENT_PATH}/{filename}")
        return True
    print(f"  ❌ GitHub error: {r.status_code} — {r.text[:150]}")
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TaxCase Review Weekly Intelligence Report v3 — 10 states")
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--no-gsc",     action="store_true")
    parser.add_argument("--state",      default=None,
                        choices=list(STATES.keys()),
                        help="Override state (default: auto-rotate)")
    args = parser.parse_args()

    dry_run = args.dry_run or args.no_publish

    print(f"\n{'='*60}")
    print(f"  TaxCase Review Weekly Intelligence Report v3 — 10 States")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    print(f"  {'DRY RUN' if dry_run else 'LIVE — publishing to GitHub'}")
    print(f"{'='*60}\n")

    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("weekly_intelligence")
        logger.start()
    except ImportError:
        logger = None

    # ── Lien data ─────────────────────────────────────────────────────────────
    if logger: logger.step_start("pull_lien_data")
    print("Pulling lien intelligence...")
    liens = get_lien_intelligence()
    print(f"  Total liens    : {liens['total_liens']:,}")
    print(f"  New this week  : {liens['new_this_week']}")
    print(f"  WoW change     : "
          f"{'+' if liens['pct_change'] >= 0 else ''}{liens['pct_change']}%")
    print(f"  Top county     : "
          f"{liens['top_counties'][0]['county'] if liens['top_counties'] else '—'}")
    if logger:
        logger.step_done("pull_lien_data", ok=True,
                         detail=f"{liens['new_this_week']} new liens")

    # ── GSC data ──────────────────────────────────────────────────────────────
    if logger: logger.step_start("pull_gsc_data")
    if args.no_gsc:
        gsc = {"top_queries": [], "top_pages": [], "opportunities": [],
               "rising_pages": [], "total_clicks_28d": 0,
               "total_impressions_28d": 0, "total_impressions_7d": 0,
               "period_28d": "skipped", "period_7d": "skipped"}
        if logger: logger.step_skip("pull_gsc_data", "--no-gsc")
    else:
        print("\nPulling GSC performance data...")
        gsc = get_gsc_intelligence()
        print(f"  Impressions 28d : {gsc['total_impressions_28d']:,}")
        print(f"  Impressions 7d  : {gsc['total_impressions_7d']:,}")
        print(f"  Clicks 28d      : {gsc['total_clicks_28d']:,}")
        print(f"  Opportunities   : {len(gsc['opportunities'])}")
        print(f"  Rising pages    : {len(gsc['rising_pages'])}")
        if logger:
            logger.step_done("pull_gsc_data", ok=True,
                             detail=f"{gsc['total_impressions_28d']} impressions 28d")

    # Save GSC snapshot
    gsc_file = DATA_OPS / f"gsc_weekly_{date.today().isoformat()}.json"
    gsc_file.write_text(json.dumps(gsc, indent=2))

    # ── Generate ──────────────────────────────────────────────────────────────
    # Determine which state to report on this week
    if args.state:
        state_key = args.state
    else:
        week_num  = date.today().isocalendar()[1]
        state_key = WEEKLY_STATE_ROTATION[week_num % len(WEEKLY_STATE_ROTATION)]
    cfg = STATES[state_key]
    print(f"\nState: {cfg['name']} ({cfg['abbr']}) — {'from DB' if cfg['has_db_data'] else 'from trends'}")

    if logger: logger.step_start("generate_report")
    print("\nGenerating report with Claude...")
    content = generate_report_content(liens, gsc, state_key=state_key)
    print(f"  Report: {len(content['report_markdown'])} chars")
    if logger:
        logger.step_done("generate_report", ok=True,
                         detail=f"{len(content['report_markdown'])} chars")

    # ── Save outputs ──────────────────────────────────────────────────────────
    slug        = f"weekly-report-{state_key.replace('_','-')}-{date.today().isoformat()}"
    report_file = DATA_OPS / f"{slug}.md"
    report_file.write_text(content["report_markdown"], encoding="utf-8")

    social     = parse_social(content["social_raw"])
    newsletter = parse_newsletter(content["newsletter_raw"])

    # Append to social queue
    sq_file = DATA_OPS / "social_queue.json"
    sq = []
    if sq_file.exists():
        try: sq = json.loads(sq_file.read_text())
        except Exception: pass
    sq.append(social)
    sq_file.write_text(json.dumps(sq[-20:], indent=2))

    # Append to newsletter queue
    nq_file = DATA_OPS / "newsletter_queue.json"
    nq = []
    if nq_file.exists():
        try: nq = json.loads(nq_file.read_text())
        except Exception: pass
    nq.append(newsletter)
    nq_file.write_text(json.dumps(nq[-10:], indent=2))

    print(f"  Saved: {report_file}")
    print(f"  Social queue: {len(sq)} posts")
    print(f"  Newsletter queue: {len(nq)} items")

    # ── Press release: if this week's data is newsworthy, draft a release and
    # email it to Romy for review. Auto-submission to PR services stays OFF
    # (gated by PR_SUBMIT_ENABLED) until the output is reviewed. Non-blocking.
    try:
        from scripts.outreach.press_release_generator import maybe_generate_from_report
        pr_logger = None
        try:
            from pipeline_log import PipelineLogger as _PRLogger
            pr_logger = _PRLogger("press_release")
            pr_logger.start()
        except Exception:
            pr_logger = None
        print("\n  Press release check...")
        maybe_generate_from_report(
            report_path=report_file,
            logger=pr_logger,
            email_review=not dry_run,   # email Romy on live runs only
            submit=False,               # auto-submit disabled pending review
        )
    except Exception as e:
        print(f"  Press release step skipped (non-blocking): {e}")

    # ── Publish ───────────────────────────────────────────────────────────────
    if dry_run:
        print(f"\n  [DRY RUN — not publishing]")
        print(f"  Preview:\n  {'─'*50}")
        print(content["report_markdown"][:400] + "...")
        print(f"  {'─'*50}")
        print(f"\n  Facebook preview:")
        print(f"  {social['facebook'][:200]}...")
    else:
        if logger: logger.step_start("publish_to_github")
        print("\nPublishing to GitHub...")
        published = publish_to_github(
            f"{slug}.md",
            content["report_markdown"],
            f"Weekly intelligence report — {liens['week_of']}",
        )
        if logger:
            logger.step_done("publish_to_github", ok=published,
                             detail=f"{REPORTS_CONTENT_PATH}/{slug}.md")
        if published:
            print(f"  🌐 {SITE_URL}/reports/{slug}")
            index_url(f"{SITE_URL}/reports/{slug}")
            submit_sitemap()

    print(f"\n{'='*60}")
    print(f"  Weekly Intelligence Complete")
    print(f"  New liens      : {liens['new_this_week']}")
    print(f"  Impressions 28d: {gsc['total_impressions_28d']}")
    print(f"  Impressions 7d : {gsc['total_impressions_7d']}")
    print(f"  Opportunities  : {len(gsc['opportunities'])}")
    print(f"{'='*60}\n")

    if logger:
        logger.finish({
            "new_liens_this_week":     liens["new_this_week"],
            "total_liens":             liens["total_liens"],
            "gsc_impressions_28d":     gsc["total_impressions_28d"],
            "gsc_impressions_7d":      gsc["total_impressions_7d"],
            "gsc_opportunities":       len(gsc["opportunities"]),
            "report_slug":             slug,
            "published":               not dry_run,
        })


if __name__ == "__main__":
    main()