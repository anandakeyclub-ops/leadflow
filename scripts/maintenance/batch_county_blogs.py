"""
batch_county_blogs.py
=====================
Generates county-level blog posts for all 7 states and publishes
directly to GitHub → Vercel auto-deploys.

Target keyword per post: "IRS tax lien help [County] County [State]"

Usage:
  python batch_county_blogs.py --state florida --limit 5
  python batch_county_blogs.py --state texas --limit 10
  python batch_county_blogs.py --all --limit 3
  python batch_county_blogs.py --state florida --dry-run

Each post: 900 words, unique content, county-specific angle.
Published to: content/blog/[slug].md → taxcasereview.org/blog/md/[slug]
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import time
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO       = os.getenv("GITHUB_REPO", "anandakeyclub-ops/taxcasereview-web")
GITHUB_BRANCH     = os.getenv("GITHUB_BRANCH", "master")
BLOG_CONTENT_PATH = "content/blog"
SITE_URL          = "https://taxcasereview.org"
PHONE             = "(888) 334-5052"

# ── State + County data ───────────────────────────────────────────────────────

STATES = {
    "florida": {
        "name": "Florida", "abbr": "FL",
        "landing": "/florida",
        "counties": [
            ("Miami-Dade", "Miami", "miami-dade"),
            ("Broward", "Fort Lauderdale", "broward"),
            ("Palm Beach", "West Palm Beach", "palm-beach"),
            ("Hillsborough", "Tampa", "hillsborough"),
            ("Orange", "Orlando", "orange"),
            ("Pinellas", "St. Petersburg", "pinellas"),
            ("Duval", "Jacksonville", "duval"),
            ("Sarasota", "Sarasota", "sarasota"),
            ("Martin", "Stuart", "martin"),
            ("Lake", "Leesburg", "lake"),
            ("Manatee", "Bradenton", "manatee"),
            ("Pasco", "New Port Richey", "pasco"),
            ("Polk", "Lakeland", "polk"),
            ("Osceola", "Kissimmee", "osceola"),
            ("Collier", "Naples", "collier"),
            ("Brevard", "Melbourne", "brevard"),
            ("Volusia", "Daytona Beach", "volusia"),
            ("Seminole", "Sanford", "seminole"),
            ("Alachua", "Gainesville", "alachua"),
            ("St. Johns", "St. Augustine", "st-johns"),
        ],
    },
    "texas": {
        "name": "Texas", "abbr": "TX",
        "landing": "/texas",
        "counties": [
            ("Harris", "Houston", "harris"),
            ("Dallas", "Dallas", "dallas"),
            ("Tarrant", "Fort Worth", "tarrant"),
            ("Bexar", "San Antonio", "bexar"),
            ("Travis", "Austin", "travis"),
            ("Collin", "Plano", "collin"),
            ("Denton", "Denton", "denton"),
            ("Fort Bend", "Sugar Land", "fort-bend"),
            ("Montgomery", "Conroe", "montgomery"),
            ("El Paso", "El Paso", "el-paso"),
            ("Williamson", "Round Rock", "williamson"),
            ("Nueces", "Corpus Christi", "nueces"),
            ("Hidalgo", "McAllen", "hidalgo"),
            ("Cameron", "Brownsville", "cameron"),
            ("Galveston", "Galveston", "galveston"),
        ],
    },
    "georgia": {
        "name": "Georgia", "abbr": "GA",
        "landing": "/georgia",
        "counties": [
            ("Fulton", "Atlanta", "fulton"),
            ("Gwinnett", "Lawrenceville", "gwinnett"),
            ("Cobb", "Marietta", "cobb"),
            ("DeKalb", "Decatur", "dekalb"),
            ("Cherokee", "Canton", "cherokee"),
            ("Clayton", "Jonesboro", "clayton"),
            ("Henry", "McDonough", "henry"),
            ("Hall", "Gainesville", "hall"),
            ("Forsyth", "Cumming", "forsyth"),
            ("Richmond", "Augusta", "richmond"),
        ],
    },
    "arizona": {
        "name": "Arizona", "abbr": "AZ",
        "landing": "/arizona",
        "counties": [
            ("Maricopa", "Phoenix", "maricopa"),
            ("Pima", "Tucson", "pima"),
            ("Pinal", "Casa Grande", "pinal"),
            ("Yavapai", "Prescott", "yavapai"),
            ("Mohave", "Kingman", "mohave"),
            ("Yuma", "Yuma", "yuma"),
            ("Cochise", "Sierra Vista", "cochise"),
            ("Navajo", "Holbrook", "navajo"),
        ],
    },
    "california": {
        "name": "California", "abbr": "CA",
        "landing": "/california",
        "counties": [
            ("Los Angeles", "Los Angeles", "los-angeles"),
            ("San Diego", "San Diego", "san-diego"),
            ("Orange", "Anaheim", "orange"),
            ("Riverside", "Riverside", "riverside"),
            ("San Bernardino", "San Bernardino", "san-bernardino"),
            ("Santa Clara", "San Jose", "santa-clara"),
            ("Alameda", "Oakland", "alameda"),
            ("Sacramento", "Sacramento", "sacramento"),
            ("Contra Costa", "Martinez", "contra-costa"),
            ("Fresno", "Fresno", "fresno"),
        ],
    },
    "new_york": {
        "name": "New York", "abbr": "NY",
        "landing": "/new-york",
        "counties": [
            ("Kings", "Brooklyn", "kings"),
            ("Queens", "Queens", "queens"),
            ("New York", "Manhattan", "new-york-county"),
            ("Bronx", "Bronx", "bronx"),
            ("Nassau", "Mineola", "nassau"),
            ("Suffolk", "Riverhead", "suffolk"),
            ("Westchester", "White Plains", "westchester"),
            ("Erie", "Buffalo", "erie"),
            ("Monroe", "Rochester", "monroe"),
            ("Onondaga", "Syracuse", "onondaga"),
        ],
    },
    "north_carolina": {
        "name": "North Carolina", "abbr": "NC",
        "landing": "/north-carolina",
        "counties": [
            ("Mecklenburg", "Charlotte", "mecklenburg"),
            ("Wake", "Raleigh", "wake"),
            ("Guilford", "Greensboro", "guilford"),
            ("Forsyth", "Winston-Salem", "forsyth"),
            ("Cumberland", "Fayetteville", "cumberland"),
            ("Durham", "Durham", "durham"),
            ("Buncombe", "Asheville", "buncombe"),
            ("Union", "Monroe", "union"),
            ("Gaston", "Gastonia", "gaston"),
            ("Cabarrus", "Concord", "cabarrus"),
        ],
    },
}


# ── Claude API ────────────────────────────────────────────────────────────────

def generate_blog(county: str, city: str, state_name: str,
                  state_abbr: str, state_landing: str) -> dict:
    state_url = f"{SITE_URL}{state_landing}"
    slug      = f"irs-tax-lien-help-{county.lower().replace(' ', '-')}-county-{state_abbr.lower()}"

    prompt = f"""You are a experienced Enrolled Agent writing for taxpayers in {county} County, {state_name} who have received an IRS tax lien notice.

Write a 900-word blog post for TaxCase Review.

Target keyword: "IRS tax lien help {county} County {state_name}"
Primary city: {city}
State page: {state_url}
Phone: {PHONE}
Date: {date.today().strftime('%B %d, %Y')}

Format: Markdown

Structure:
---
title: "IRS Tax Lien Help in {county} County, {state_name}: What to Do Right Now"
date: "{date.today().isoformat()}"
slug: "{slug}"
state: "{state_name.lower().replace(' ', '_')}"
county: "{county}"
metaDescription: "IRS tax lien filed in {county} County, {state_name}? Experienced Enrolled Agents help {city} taxpayers resolve liens, stop levies, and negotiate with the IRS. Free case review."
---

# IRS Tax Lien Help in {county} County, {state_name}: What to Do Right Now

*[One sentence meta description in italics]*

## What an IRS Tax Lien Means for {county} County Residents

[150 words — explain what a federal tax lien is, how it affects credit/property in {county} County specifically, mention {city} as the county seat, make it local and specific]

## How Federal Tax Liens Work in {state_name}

[150 words — explain the IRS lien process, timeline from notice to lien filing, what happens if ignored, mention {state_name}-specific context like common industries or tax issues]

## Your Resolution Options

[200 words — cover these 5 options with 2-3 sentences each:
1. Installment Agreement — monthly payment plan
2. Offer in Compromise — settle for less
3. Penalty Abatement — remove penalties
4. Lien Withdrawal — remove from public record
5. Currently Not Collectible — temporary halt]

## Common Mistakes {county} County Taxpayers Make

[150 words — 3 specific mistakes: waiting too long, trying to handle it alone, ignoring notices. Make it feel like insider knowledge from an Enrolled Agent]

## Why Act Now: The {county} County Lien Timeline

[100 words — explain the urgency: interest accrues daily, levy can follow, affects ability to sell property or get financing in {city}]

## Get Help From a Licensed Enrolled Agent

[150 words — CTA section. Mention TaxCase Review serves all of {county} County including {city}. $399 flat fee. Experienced Enrolled Agents. No hourly billing. Link to {state_url}. Include phone {PHONE}. End with one clear CTA sentence.]

RULES:
- Write in plain English — no jargon
- Every section gives real, actionable information
- Never guarantee outcomes
- Include "Results vary. Every situation is unique." once
- Naturally use keyword "IRS tax lien {county} County" 3-4 times
- 850-950 words total
- Return ONLY the markdown, no preamble"""

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      "claude-sonnet-4-5",
            "max_tokens": 2000,
            "messages":   [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    r.raise_for_status()
    content = r.json()["content"][0]["text"].strip()

    return {
        "slug":     slug,
        "filename": f"{slug}.md",
        "content":  content,
        "county":   county,
        "state":    state_name,
    }


# ── GitHub publisher ──────────────────────────────────────────────────────────

def publish_to_github(blog: dict) -> bool:
    if not GITHUB_TOKEN:
        print("  ⚠ GITHUB_TOKEN not set")
        return False

    file_path = f"{BLOG_CONTENT_PATH}/{blog['filename']}"
    api_url   = (f"https://api.github.com/repos/{GITHUB_REPO}"
                 f"/contents/{file_path}")
    headers   = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github.v3+json",
    }

    sha = None
    try:
        check = requests.get(api_url, headers=headers, timeout=10)
        if check.status_code == 200:
            sha = check.json().get("sha")
            print(f"  ↺ Updating existing post")
    except Exception:
        pass

    content_b64 = base64.b64encode(
        blog["content"].encode("utf-8")).decode()
    payload = {
        "message": (f"Blog: IRS Tax Lien Help {blog['county']} County "
                    f"{blog['state']} [{date.today().isoformat()}]"),
        "content": content_b64,
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(api_url, headers=headers,
                     json=payload, timeout=30)
    if r.status_code in (200, 201):
        url = f"{SITE_URL}/blog/md/{blog['slug']}"
        print(f"  ✅ {url}")
        return True
    print(f"  ❌ GitHub {r.status_code}: {r.text[:100]}")
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Batch County Blog Generator — All 7 States")
    parser.add_argument("--state",   default=None,
                        choices=list(STATES.keys()),
                        help="Generate for one state")
    parser.add_argument("--all",     action="store_true",
                        help="Generate for all 7 states")
    parser.add_argument("--limit",   type=int, default=5,
                        help="Max posts per state (default 5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate but don't publish")
    parser.add_argument("--county",  default=None,
                        help="Generate for specific county only")
    args = parser.parse_args()

    if not args.state and not args.all:
        parser.print_help()
        return

    states_to_run = list(STATES.keys()) if args.all else [args.state]
    total_published = 0

    print(f"\n{'='*60}")
    print(f"  Batch County Blog Generator")
    print(f"  {date.today().strftime('%A %B %d, %Y')}")
    print(f"  States : {', '.join(s.upper() for s in states_to_run)}")
    print(f"  Limit  : {args.limit} per state")
    print(f"  {'DRY RUN' if args.dry_run else 'LIVE → publishing to GitHub'}")
    print(f"{'='*60}\n")

    # Save locally
    out_dir = Path("blog_drafts/county_pages")
    out_dir.mkdir(parents=True, exist_ok=True)

    for state_key in states_to_run:
        state   = STATES[state_key]
        counties = state["counties"]

        # Filter to specific county if requested
        if args.county:
            counties = [c for c in counties
                        if args.county.lower() in c[0].lower()]

        print(f"\n── {state['name']} ({len(counties)} counties, "
              f"generating {min(args.limit, len(counties))}) ──")

        count = 0
        for county_name, city, slug_part in counties:
            if count >= args.limit:
                break

            print(f"\n  [{count+1}] {county_name} County, {state['name']} "
                  f"({city})...")

            try:
                blog = generate_blog(
                    county      = county_name,
                    city        = city,
                    state_name  = state["name"],
                    state_abbr  = state["abbr"],
                    state_landing = state["landing"],
                )

                # Save locally
                local = out_dir / blog["filename"]
                local.write_text(blog["content"], encoding="utf-8")
                print(f"  💾 Saved: {local}")

                # Publish
                if not args.dry_run:
                    published = publish_to_github(blog)
                    if published:
                        total_published += 1
                else:
                    print(f"  [DRY RUN] Would publish: {blog['slug']}")
                    total_published += 1

                count += 1
                time.sleep(2)  # Polite delay between Claude calls

            except Exception as e:
                print(f"  ❌ Error: {e}")
                continue

    print(f"\n{'='*60}")
    print(f"  Complete: {total_published} posts published")
    print(f"  Local drafts: blog_drafts/county_pages/")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
