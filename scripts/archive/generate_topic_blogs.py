# generate_topic_blogs.py  (v2 — 10-state edition)
# Generates high-value, emotionally engaging blog posts for TaxCase Review
# Each post includes data visualizations, stats, and conversion-focused CTAs
# Run from: C:\Users\Dana\Desktop\leadflow

import os
import requests
import time
import base64
from datetime import date
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO       = os.getenv("GITHUB_REPO", "anandakeyclub-ops/v0-tax-resolution-landing-page")
GITHUB_BRANCH     = os.getenv("GITHUB_BRANCH", "main")

SITE_URL  = "https://taxcasereview.org"
PHONE     = "(888) 334-5052"
TODAY     = date.today().isoformat()

# ── Writing instructions injected into every prompt ──────────────────────────

WRITING_RULES = """
CRITICAL WRITING RULES — follow every one:
- Voice: You are Romy, a experienced Enrolled Agent writing for real taxpayers
- Tone: calm, authoritative, human — like a trusted friend who knows the IRS inside out
- NEVER use: em dashes, bullet walls, "navigate", "crucial", "it's important to note"
- NEVER sound like AI — vary sentence length, use natural transitions
- Short paragraphs — 2-3 sentences max per paragraph
- Use specific numbers, timelines, and real IRS form numbers
- One genuine human moment or story in every post
- Include 1 data visualization described in markdown table format
- Include 1 comparison table (e.g. lien vs levy, release vs withdrawal)
- Results vary disclaimer: include once naturally, not bolded, not at the end
- End every post with a low-pressure CTA linking to {site} and phone {phone}
- Add one mid-article inline CTA after the most urgent section: 
  "**Need help now? [See your options in 60 seconds]({site}/quiz) or call {phone}**"
- Add one "Pro Tip" callout box mid-article using: > 💡 **Pro Tip:** ...
""".format(site=SITE_URL, phone=PHONE)

FRONTMATTER = """
Format as markdown with this exact frontmatter:
---
title: "{title}"
date: "{today}"
slug: "{slug}"
metaDescription: "{meta}"
---

After frontmatter, include:
1. A "Quick Answer" callout box using blockquote markdown (> **Quick Answer:** ...)
2. The main article body with SHORT paragraphs (2-3 sentences max)
3. A visual data table with emoji indicators (✅ ❌ ⚠️) where relevant
4. A "By The Numbers" section with 3-4 key statistics in bold
5. A comparison table (e.g. "Lien vs Levy", "Release vs Withdrawal", "OIC vs Installment")
6. A timeline or process flow using numbered steps with bold headers
7. A highlighted warning box using blockquote: > ⚠️ **Warning:** ...
8. FAQ section (4 questions with concise answers)
9. A "Bottom Line" summary section
10. Final CTA section with phone number prominent

Return ONLY the markdown. No preamble. No explanation.
""".replace("{today}", TODAY)

# ── State-specific context ─────────────────────────────────────────────────────

STATE_CONTEXT = {
    "florida": {
        "name": "Florida", "abbr": "FL",
        "cities": "Miami, Orlando, Tampa, Jacksonville, Fort Lauderdale",
        "top_counties": "Miami-Dade, Broward, Palm Beach, Hillsborough, Orange",
        "industries": "construction contractors, real estate investors, restaurant owners, self-employed service workers",
        "lien_count": "17,500+",
        "url": "/florida",
    },
    "texas": {
        "name": "Texas", "abbr": "TX",
        "cities": "Houston, Dallas, San Antonio, Austin, Fort Worth",
        "top_counties": "Harris, Dallas, Tarrant, Bexar, Travis",
        "industries": "oil and gas contractors, construction companies, trucking operators, small business owners",
        "lien_count": "22,000+",
        "url": "/texas",
    },
    "georgia": {
        "name": "Georgia", "abbr": "GA",
        "cities": "Atlanta, Savannah, Augusta, Columbus, Macon",
        "top_counties": "Fulton, Gwinnett, Cobb, DeKalb, Cherokee",
        "industries": "construction contractors, logistics, film industry professionals, restaurant owners",
        "lien_count": "9,200+",
        "url": "/georgia",
    },
    "arizona": {
        "name": "Arizona", "abbr": "AZ",
        "cities": "Phoenix, Tucson, Scottsdale, Mesa, Chandler",
        "top_counties": "Maricopa, Pima, Pinal, Yavapai, Mohave",
        "industries": "construction contractors, solar installers, real estate investors, self-employed professionals",
        "lien_count": "7,800+",
        "url": "/arizona",
    },
    "california": {
        "name": "California", "abbr": "CA",
        "cities": "Los Angeles, San Diego, San Francisco, San Jose, Sacramento",
        "top_counties": "Los Angeles, Orange, San Diego, Riverside, San Bernardino",
        "industries": "self-employed tech contractors, entertainment professionals, construction contractors, restaurant owners",
        "lien_count": "31,000+",
        "url": "/california",
    },
    "new_york": {
        "name": "New York", "abbr": "NY",
        "cities": "New York City, Buffalo, Rochester, Albany, Syracuse",
        "top_counties": "Kings, Queens, Manhattan, Nassau, Suffolk",
        "industries": "self-employed professionals, construction contractors, restaurant and hospitality owners, finance professionals",
        "lien_count": "18,400+",
        "url": "/new-york",
    },
    "north_carolina": {
        "name": "North Carolina", "abbr": "NC",
        "cities": "Charlotte, Raleigh, Greensboro, Durham, Winston-Salem",
        "top_counties": "Mecklenburg, Wake, Guilford, Forsyth, Durham",
        "industries": "construction contractors, trucking operators, manufacturing workers, self-employed professionals",
        "lien_count": "8,100+",
        "url": "/north-carolina",
    },
    "illinois": {
        "name": "Illinois", "abbr": "IL",
        "cities": "Chicago, Aurora, Rockford, Joliet, Naperville",
        "top_counties": "Cook, DuPage, Lake, Will, Kane",
        "industries": "construction contractors, manufacturing workers, trucking operators, restaurant and hospitality owners",
        "lien_count": "12,400+",
        "url": "/illinois",
    },
    "ohio": {
        "name": "Ohio", "abbr": "OH",
        "cities": "Columbus, Cleveland, Cincinnati, Toledo, Akron",
        "top_counties": "Cuyahoga, Franklin, Hamilton, Summit, Montgomery",
        "industries": "manufacturing and auto industry workers, construction contractors, trucking operators, small business owners",
        "lien_count": "9,800+",
        "url": "/ohio",
    },
    "pennsylvania": {
        "name": "Pennsylvania", "abbr": "PA",
        "cities": "Philadelphia, Pittsburgh, Allentown, Erie, Reading",
        "top_counties": "Philadelphia, Allegheny, Montgomery, Bucks, Delaware",
        "industries": "construction contractors, trucking and logistics operators, manufacturing workers, restaurant owners",
        "lien_count": "11,200+",
        "url": "/pennsylvania",
    },
}

# ── Blog post definitions ──────────────────────────────────────────────────────

def make_posts():
    posts = []

    # ── NATIONAL TOPIC POSTS ──────────────────────────────────────────────────

    posts += [
        {
            "slug": "how-long-does-irs-tax-lien-last",
            "title": "How Long Does an IRS Tax Lien Last? (And How to Remove It Faster)",
            "keyword": "how long does IRS tax lien last",
            "meta": "Enrolled Agent explains exactly how long an IRS tax lien lasts, what resets the clock, and how to remove it faster. Free case review at taxcasereview.org.",
            "prompt": (
                "Write a 950-word blog post for TaxCase Review targeting 'how long does IRS tax lien last'.\n"
                "You are Romy, a experienced Enrolled Agent.\n"
                "Cover:\n"
                "1) The 10-year Collection Statute Expiration Date (CSED) - what it means in plain English\n"
                "2) What actions restart or pause the 10-year clock (installment agreements, bankruptcy, OIC, military service, living abroad)\n"
                "3) Lien Release vs Lien Withdrawal - create a comparison table showing the difference\n"
                "4) IRS Fresh Start program - how it raises the lien filing threshold to $10,000\n"
                "5) A realistic timeline table: Year 1 through Year 10 showing what happens at each stage\n"
                "6) Three fastest legal ways to get a lien removed before 10 years\n"
                "Include a data table showing IRS lien filing statistics and average resolution timelines.\n"
                "One human story: a contractor who waited 8 years vs one who resolved in 14 months.\n"
            ) + WRITING_RULES,
        },
        {
            "slug": "irs-tax-lien-on-house",
            "title": "IRS Tax Lien on Your House: What Happens and What You Can Do",
            "keyword": "IRS tax lien on house",
            "meta": "Enrolled Agent explains exactly what happens when the IRS files a tax lien on your house, how it affects selling and refinancing, and your options. Free review at taxcasereview.org.",
            "prompt": (
                "Write a 950-word blog post for TaxCase Review targeting 'IRS tax lien on house'.\n"
                "You are Romy, a experienced Enrolled Agent.\n"
                "Cover:\n"
                "1) What a federal tax lien on your house actually means legally\n"
                "2) Can you sell your house with an IRS lien? Yes, but here is exactly how the closing works\n"
                "3) Can you refinance with an IRS lien? Lien subordination explained with a step-by-step process\n"
                "4) Lien discharge - how to remove the lien from one specific property\n"
                "5) Create a comparison table: Sell with lien vs subordination vs discharge vs pay in full\n"
                "6) A realistic timeline table: What happens at each stage if you ignore it\n"
                "7) Your 4 resolution options ranked by speed and cost\n"
                "One human story: a Florida homeowner who almost lost a sale but resolved the lien in 3 weeks.\n"
            ) + WRITING_RULES,
        },
        {
            "slug": "irs-fresh-start-program-explained",
            "title": "IRS Fresh Start Program Explained: Who Qualifies and How It Works in 2026",
            "keyword": "IRS Fresh Start program",
            "meta": "Enrolled Agent explains the IRS Fresh Start program, who qualifies in 2026, and how to apply. Real guidance, not generic tax advice. Free review at taxcasereview.org.",
            "prompt": (
                "Write a 950-word blog post for TaxCase Review targeting 'IRS Fresh Start program 2026'.\n"
                "You are Romy, a experienced Enrolled Agent.\n"
                "Cover:\n"
                "1) What the IRS Fresh Start program actually is - launched 2011, expanded since\n"
                "2) The 4 components with a comparison table: Streamlined Installment, OIC, Lien Threshold, Penalty Relief\n"
                "3) Qualification requirements table: income limits, debt thresholds, compliance requirements for each component\n"
                "4) The biggest misconception: Fresh Start is not automatic debt forgiveness\n"
                "5) OIC acceptance rate data table: acceptance rates by year 2018-2025\n"
                "6) Step-by-step: how to apply for each component\n"
                "7) Why 65% of self-filed OICs get rejected and what professionals do differently\n"
                "One human story: an HVAC contractor who qualified for Fresh Start but got rejected on his own, then succeeded with help.\n"
            ) + WRITING_RULES,
        },
        {
            "slug": "irs-penalty-abatement-letter",
            "title": "IRS Penalty Abatement Letter: How to Write One That Actually Works",
            "keyword": "IRS penalty abatement letter",
            "meta": "Enrolled Agent explains exactly how to write an IRS penalty abatement letter that works, what to include, and what not to say. Free review at taxcasereview.org.",
            "prompt": (
                "Write a 950-word blog post for TaxCase Review targeting 'IRS penalty abatement letter'.\n"
                "You are Romy, a experienced Enrolled Agent.\n"
                "Cover:\n"
                "1) Two types comparison table: First-Time Penalty Abatement vs Reasonable Cause\n"
                "2) First-Time Abatement: the 3 qualifications most people don't know they meet\n"
                "3) Reasonable Cause categories table: death, illness, natural disaster, IRS error, bad professional advice\n"
                "4) What to include in the letter: exact components with examples\n"
                "5) What NOT to say: 5 common mistakes that get letters rejected immediately\n"
                "6) Success rate table: FTA approval rates by penalty type\n"
                "7) Timeline table: what happens after you submit (2 weeks, 6 weeks, 3 months)\n"
                "One human story: a restaurant owner who got $34,000 in penalties removed with a one-page letter.\n"
            ) + WRITING_RULES,
        },
        {
            "slug": "how-to-remove-irs-tax-lien-from-credit-report",
            "title": "How to Remove an IRS Tax Lien From Your Credit Report",
            "keyword": "how to remove IRS tax lien from credit report",
            "meta": "Enrolled Agent explains the exact process to remove an IRS tax lien from your credit report, including Form 12277 and the withdrawal process. Free review at taxcasereview.org.",
            "prompt": (
                "Write a 950-word blog post for TaxCase Review targeting 'how to remove IRS tax lien from credit report'.\n"
                "You are Romy, a experienced Enrolled Agent.\n"
                "Cover:\n"
                "1) How IRS liens appear in public records (not credit bureaus directly, but they find them)\n"
                "2) Comparison table: Lien Release vs Lien Withdrawal - the critical difference\n"
                "3) Withdrawal qualification table: 4 ways to qualify for lien withdrawal\n"
                "4) Step-by-step Form 12277 process with timeline\n"
                "5) Timeline table: how long removal takes at each credit bureau (30, 45, 60 days)\n"
                "6) The dispute process if it stays after withdrawal\n"
                "7) One thing most people miss: withdrawal vs release affects whether it shows up in public records searches\n"
                "One human story: a self-employed graphic designer who got a lien withdrawn and off her credit in 47 days.\n"
            ) + WRITING_RULES,
        },
        {
            "slug": "irs-froze-bank-account-what-to-do",
            "title": "IRS Froze My Bank Account: What to Do in the Next 21 Days",
            "keyword": "IRS froze my bank account",
            "meta": "Enrolled Agent explains exactly what to do when the IRS freezes your bank account. You have 21 days. Here is what to do right now. Call (888) 334-5052.",
            "prompt": (
                "Write a 950-word URGENT blog post for TaxCase Review targeting 'IRS froze my bank account'.\n"
                "You are Romy, a experienced Enrolled Agent.\n"
                "Tone: calm urgency. Like a trusted friend who used to work at the IRS calling you immediately.\n"
                "Cover:\n"
                "1) What just happened - bank levy explained in 3 sentences\n"
                "2) The 21-day hold period table: Day 1, Day 7, Day 14, Day 21 - what happens at each point\n"
                "3) What to do in the next 2 hours (specific action steps)\n"
                "4) How to request a levy release - Form 668-D, CDP hearing rights\n"
                "5) Reasons the IRS will release a levy (table format)\n"
                "6) What happens if you do nothing - the money transfer timeline\n"
                "7) The 3 fastest resolution paths ranked by speed\n"
                "Make the phone number (888) 334-5052 appear prominently twice.\n"
                "One human story: a trucking company owner who got his $28,000 bank levy released in 4 days.\n"
            ) + WRITING_RULES,
        },
        {
            "slug": "trust-fund-recovery-penalty",
            "title": "IRS Trust Fund Recovery Penalty: What Business Owners Must Know",
            "keyword": "trust fund recovery penalty",
            "meta": "Enrolled Agent explains the Trust Fund Recovery Penalty, who is personally liable, and how to fight it. Free case review at taxcasereview.org.",
            "prompt": (
                "Write a 950-word blog post for TaxCase Review targeting 'trust fund recovery penalty'.\n"
                "You are Romy, a experienced Enrolled Agent.\n"
                "Cover:\n"
                "1) What the Trust Fund Recovery Penalty is - explain payroll taxes as money held in trust\n"
                "2) Who is personally liable table: owners, officers, bookkeepers, check signers\n"
                "3) The IRS Form 4180 interview - what they ask and what you should not say\n"
                "4) Defenses table: 4 legitimate defenses to fight TFRP assessment\n"
                "5) Settlement options comparison table: payment plan vs OIC vs innocent spouse equivalent\n"
                "6) Timeline table: from IRS notice to personal assessment (typical 6-18 months)\n"
                "7) Why TFRP cannot be discharged in bankruptcy (most business owners don't know this)\n"
                "One human story: an HVAC company owner whose bookkeeper caused $180,000 in unpaid payroll taxes.\n"
            ) + WRITING_RULES,
        },
        {
            "slug": "irs-payment-plan-rejected",
            "title": "IRS Payment Plan Rejected: Why It Happens and What to Do Next",
            "keyword": "IRS payment plan rejected",
            "meta": "Enrolled Agent explains why the IRS rejects payment plans and exactly what to do next. Free case review at taxcasereview.org or call (888) 334-5052.",
            "prompt": (
                "Write a 950-word blog post for TaxCase Review targeting 'IRS payment plan rejected'.\n"
                "You are Romy, a experienced Enrolled Agent.\n"
                "Cover:\n"
                "1) Top 5 rejection reasons table with percentage of cases each represents\n"
                "2) Unfiled returns - why this is the #1 reason and how to fix it fast\n"
                "3) What happens after rejection - IRS acceleration timeline table\n"
                "4) How to appeal: CDP hearing rights and the 30-day window\n"
                "5) Alternative options comparison table: OIC, CNC status, partial pay installment, PPIA\n"
                "6) How to reapply successfully - the 5 things that must change\n"
                "7) Warning signs your new application will also be rejected\n"
                "One human story: a roofing contractor whose payment plan was rejected twice then accepted the third time.\n"
            ) + WRITING_RULES,
        },
        {
            "slug": "irs-tax-debt-self-employed",
            "title": "Self-Employed and Owe the IRS? Here Is What Actually Happens Next",
            "keyword": "self employed IRS tax debt",
            "meta": "Enrolled Agent explains what the IRS actually does to self-employed taxpayers with tax debt. Real options for contractors, freelancers, and gig workers. Free review at taxcasereview.org.",
            "prompt": (
                "Write a 950-word blog post for TaxCase Review targeting 'self employed IRS tax debt'.\n"
                "You are Romy, a experienced Enrolled Agent.\n"
                "Write directly to contractors, freelancers, and gig workers.\n"
                "Cover:\n"
                "1) Why self-employed people fall behind - the quarterly tax trap with a calendar table\n"
                "2) Self-employment tax rate comparison table: W-2 employee vs self-employed\n"
                "3) What the IRS actually does first, second, third - enforcement timeline table\n"
                "4) Can IRS levy 1099 payments from clients? Yes - how this works and how to stop it\n"
                "5) Resolution options comparison table ranked by suitability for self-employed\n"
                "6) The compliance path: how to get current and stay current\n"
                "7) One thing self-employed people do that makes IRS situations much worse\n"
                "One human story: a freelance electrician who owed $67,000 from 3 great years with no quarterlies.\n"
            ) + WRITING_RULES,
        },
        {
            "slug": "irs-tax-lien-on-llc",
            "title": "IRS Tax Lien on Your LLC: What It Means for Your Business and Personal Assets",
            "keyword": "IRS tax lien LLC",
            "meta": "Enrolled Agent explains how IRS tax liens affect LLCs, when personal assets are at risk, and your options. Free case review at taxcasereview.org.",
            "prompt": (
                "Write a 950-word blog post for TaxCase Review targeting 'IRS tax lien LLC'.\n"
                "You are Romy, a experienced Enrolled Agent.\n"
                "Cover:\n"
                "1) How IRS liens attach to LLC assets - equipment, receivables, bank accounts, real property\n"
                "2) Personal liability comparison table: Single-member LLC vs Multi-member LLC vs S-Corp\n"
                "3) How an IRS lien affects LLC financing - lender reaction table\n"
                "4) Can IRS seize an LLC? The seizure process timeline table\n"
                "5) The dissolution myth: what actually happens to debt when you close an LLC\n"
                "6) Asset protection options comparison table: resolution programs ranked by effectiveness\n"
                "7) One action that makes LLC tax debt worse immediately (most owners do this)\n"
                "One human story: a roofing LLC owner who tried to close the business but still owed $94,000 personally.\n"
            ) + WRITING_RULES,
        },
    ]

    # ── STATE-SPECIFIC POSTS ───────────────────────────────────────────────────

    state_topics = [
        {
            "topic_slug":    "irs-tax-lien-help-contractors",
            "topic_title":   "IRS Tax Lien Help for {state} Contractors: What You Need to Know",
            "keyword":       "IRS tax lien help {state} contractors",
            "meta":          "Enrolled Agent explains IRS tax lien help for {state} contractors. Real options for {cities} business owners. Free review at taxcasereview.org.",
            "prompt_body":   (
                "Write a 900-word blog post for TaxCase Review targeting 'IRS tax lien help {state} contractors'.\n"
                "You are Romy, a experienced Enrolled Agent.\n"
                "Focus specifically on contractors in {state} ({cities}).\n"
                "Cover:\n"
                "1) Why {state} contractors specifically face IRS liens - state economy context ({industries})\n"
                "2) The payroll tax trap for {state} contractors with seasonal work - timeline table\n"
                "3) Current IRS lien activity in {state}: approximately {lien_count} active federal liens\n"
                "4) County-specific context for {top_counties}\n"
                "5) Resolution options comparison table tailored to contractor cash flow\n"
                "6) IRS levy on contractor client payments - how it works in {state}\n"
                "7) Trust Fund Recovery Penalty risk for {state} contractors with employees\n"
                "One story: a {state} contractor (use a common trade in {state}) who resolved a lien.\n"
                "Link to {url} for {state}-specific help.\n"
            ),
        },
        {
            "topic_slug":    "small-business-irs-debt",
            "topic_title":   "{state} Small Business IRS Debt: Your Real Options in 2026",
            "keyword":       "{state} small business IRS tax debt",
            "meta":          "Enrolled Agent explains real options for {state} small business owners with IRS debt. {cities} businesses. Free review at taxcasereview.org.",
            "prompt_body":   (
                "Write a 900-word blog post for TaxCase Review targeting '{state} small business IRS tax debt'.\n"
                "You are Romy, a experienced Enrolled Agent.\n"
                "Focus on small business owners in {state} ({cities}).\n"
                "Cover:\n"
                "1) The unique IRS challenges facing {state} small businesses in {industries}\n"
                "2) IRS enforcement timeline table: notice to levy for {state} businesses\n"
                "3) Business vs personal liability table for {state} LLCs and sole proprietors\n"
                "4) Resolution options comparison table: which programs work best for {state} businesses\n"
                "5) The {state} economic context: why {lien_count} active liens signals opportunity for resolution\n"
                "6) How to protect business credit while resolving IRS debt\n"
                "7) Immediate steps for {state} business owners with unfiled returns\n"
                "One story: a small business owner in {top_counties} who resolved IRS debt without closing.\n"
                "Link to {url} for {state}-specific help.\n"
            ),
        },
        {
            "topic_slug":    "irs-levy-wage-garnishment",
            "topic_title":   "IRS Wage Garnishment in {state}: How to Stop It Before It Starts",
            "keyword":       "IRS wage garnishment {state}",
            "meta":          "Enrolled Agent explains how to stop IRS wage garnishment in {state}. {cities} taxpayers. Free review at taxcasereview.org.",
            "prompt_body":   (
                "Write a 900-word URGENT blog post for TaxCase Review targeting 'IRS wage garnishment {state}'.\n"
                "You are Romy, a experienced Enrolled Agent.\n"
                "Focus on {state} taxpayers in {cities}.\n"
                "Cover:\n"
                "1) How IRS wage garnishment works in {state} - what your employer receives\n"
                "2) Garnishment calculation table: how much the IRS takes based on filing status and dependents\n"
                "3) The timeline from CP504 notice to active garnishment in {state} - table format\n"
                "4) How to stop garnishment: 5 methods comparison table ranked by speed\n"
                "5) CDP hearing: your right to challenge garnishment before it starts\n"
                "6) {state}-specific employer obligations when they receive a levy notice\n"
                "7) What happens to {state} self-employed workers getting 1099 income levied\n"
                "Tone: calm urgency throughout. Phone number prominent.\n"
                "One story: a {state} worker in {top_counties} who got garnishment released in 6 days.\n"
                "Link to {url} for {state}-specific help.\n"
            ),
        },
    ]

    for sc_key, sc in STATE_CONTEXT.items():
        for topic in state_topics:
            def fmt(s, sc=sc):
                return (s
                    .replace("{state}", sc["name"])
                    .replace("{abbr}", sc["abbr"])
                    .replace("{cities}", sc["cities"])
                    .replace("{top_counties}", sc["top_counties"])
                    .replace("{industries}", sc["industries"])
                    .replace("{lien_count}", sc["lien_count"])
                    .replace("{url}", SITE_URL + sc["url"])
                )

            slug  = f"{sc_key.replace('_','-')}-{topic['topic_slug']}"
            title = fmt(topic["topic_title"])
            kw    = fmt(topic["keyword"])
            meta  = fmt(topic["meta"])
            prompt_body = fmt(topic["prompt_body"])

            posts.append({
                "slug":    slug,
                "title":   title,
                "keyword": kw,
                "meta":    meta,
                "prompt":  prompt_body + WRITING_RULES,
            })

    return posts


# ── Generation helpers ────────────────────────────────────────────────────────

def build_prompt(post: dict) -> str:
    fm = FRONTMATTER.format(
        title=post["title"],
        today=TODAY,
        slug=post["slug"],
        meta=post["meta"],
    )
    return post["prompt"] + "\n\n" + fm


def generate_post(post: dict) -> str:
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":          ANTHROPIC_API_KEY,
            "anthropic-version":  "2023-06-01",
            "content-type":       "application/json",
        },
        json={
            "model":      "claude-sonnet-4-5",
            "max_tokens": 2500,
            "messages":   [{"role": "user", "content": build_prompt(post)}],
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()


def publish_to_github(slug: str, content: str) -> bool:
    if not GITHUB_TOKEN:
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
        "message": f"Blog: {slug} [{TODAY}]",
        "content": content_b64,
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(api_url, headers=headers, json=payload, timeout=30)
    return r.status_code in (200, 201)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="TaxCase Review Blog Generator v2")
    parser.add_argument("--state",   default=None,
                        help="Only generate posts for this state key (e.g. illinois)")
    parser.add_argument("--topic",   action="store_true",
                        help="Only generate national topic posts (no state posts)")
    parser.add_argument("--slug",    default=None,
                        help="Generate only one specific post by slug")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate content but do not publish to GitHub")
    parser.add_argument("--limit",   type=int, default=None,
                        help="Max number of posts to generate")
    args = parser.parse_args()

    all_posts = make_posts()

    # Filter
    if args.slug:
        posts = [p for p in all_posts if p["slug"] == args.slug]
    elif args.topic:
        posts = [p for p in all_posts if not any(
            sc in p["slug"] for sc in STATE_CONTEXT.keys()
        )]
    elif args.state:
        state_key = args.state.lower().replace("-", "_").replace(" ", "_")
        posts = [p for p in all_posts if p["slug"].startswith(state_key.replace("_", "-"))]
    else:
        posts = all_posts

    if args.limit:
        posts = posts[:args.limit]

    out_dir = Path("blog_drafts/topic_posts")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nTaxCase Review Blog Generator v2")
    print(f"Generating {len(posts)} posts...")
    print(f"Dry run: {args.dry_run}\n")

    success = failed = 0

    for i, post in enumerate(posts):
        print(f"  [{i+1}/{len(posts)}] {post['title'][:60]}...")
        try:
            content = generate_post(post)
            local = out_dir / f"{post['slug']}.md"
            local.write_text(content, encoding="utf-8")

            if args.dry_run:
                print(f"  [DRY RUN] Saved locally: {local}")
                success += 1
            else:
                ok = publish_to_github(post["slug"], content)
                if ok:
                    print(f"  Live: {SITE_URL}/blog/md/{post['slug']}")
                    success += 1
                else:
                    print(f"  GitHub failed - saved locally: {local}")
                    failed += 1

            time.sleep(2)

        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    print(f"\nDone. {success} published, {failed} failed.")
    print(f"Check: blog_drafts/topic_posts/")
    print(f"\nUsage tips:")
    print(f"  Generate only Illinois posts:  python generate_topic_blogs.py --state illinois")
    print(f"  Generate only national topics: python generate_topic_blogs.py --topic")
    print(f"  Test one post:                 python generate_topic_blogs.py --slug irs-froze-bank-account-what-to-do --dry-run")
    print(f"  Limit to 5 posts:              python generate_topic_blogs.py --limit 5")


if __name__ == "__main__":
    main()
