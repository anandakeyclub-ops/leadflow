"""
social_media_poster.py  (v7 — Story-First Viral Engine)
=========================================================
Content Mix:
  40% Story Content (client stories, contractor stories, tax horror, mistake stories)
  25% Public Record Intelligence (county trends, lien alerts, largest filings)
  20% Myth Destruction (bad advice, misconceptions, common mistakes)
  15% Education (notices, programs, IRS process)

New post types:
  contractor_disaster, tax_horror_story, biggest_mistake,
  public_record_breakdown, weekly_lien_leaderboard, contractor_confession,
  irs_story, bank_levy_story, payroll_tax_trap, biggest_lien_of_the_week

Viral scoring (0-100, reject < 85):
  scroll_stop (25) + emotional_impact (25) + curiosity (20)
  + share_potential (15) + comment_potential (15)

Hook requirement: every post opens with story/emotional/shocking/uncomfortable/identity.
Never begins with explanation.

Comment endings: HELP / CONTRACTOR / CP504 / LIEN / NOTICE

Image priority: IRS notices > public records > contractors > construction > financial stress
De-prioritized: office meetings, advisors at desks, generic corporate photos

All v6 features preserved. All existing CLI commands preserved.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
from datetime import datetime, date
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# Shared intelligence layer (content flywheel + cross-script coordination).
try:
    import shared_intelligence as si
    HAS_SHARED = True
except Exception:
    si = None
    HAS_SHARED = False

MAKE_WEBHOOK_URL  = os.getenv("MAKE_WEBHOOK_URL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO       = os.getenv("GITHUB_REPO", "anandakeyclub-ops/v0-tax-resolution-landing-page")
GITHUB_BRANCH     = os.getenv("GITHUB_BRANCH", "main")
SITE_URL          = "https://taxcasereview.org"
PHONE             = "(561) 247-0678"
BLOGS_DIR         = Path("blog_drafts")
HISTORY_FILE      = Path("post_history.json")
ANALYTICS_FILE    = Path("post_analytics.json")
BLOG_CONTENT_PATH  = "content/blog"
BLOG_SLUG_LOG      = Path("blog_slugs_published.json")  # deduplication log

def load_published_slugs() -> set:
    """Load set of already-published blog slugs to prevent duplicates."""
    if BLOG_SLUG_LOG.exists():
        try: return set(json.load(open(BLOG_SLUG_LOG)))
        except: return set()
    return set()

def save_published_slug(slug: str):
    """Record a published slug so it won't be regenerated."""
    slugs = load_published_slugs()
    slugs.add(slug)
    BLOG_SLUG_LOG.write_text(json.dumps(sorted(slugs), indent=2))
IMAGE_HISTORY_FILE= Path("image_history.json")
PERFORMANCE_FILE  = Path("post_performance.json")

QUALITY_THRESHOLD = 80  # Unified threshold — matches social_media_poster.py

# ── Funnel Stage Classification ───────────────────────────────────────────────
FUNNEL_STAGES = {
    "awareness":     ["what is","what are","how does","why do","what happens",
                      "understanding","explained","guide to","overview","basics"],
    "consideration": ["how to","options","alternatives","vs","compare","should i",
                      "can i","is it possible","how long","what does it cost",
                      "what are my options","which is better"],
    "decision":      ["hire","help near me","free review","case review","get help",
                      "resolve","fix","stop","settle","remove lien","release levy",
                      "cost","price","fee","worth it","work with"],
}

# ── Collection topic taxonomy for topical clustering ─────────────────────────
COLLECTION_TOPICS = {
    "irs-notices":        ["cp14","cp503","cp504","lt11","lt16","cp2000",
                           "letter 1058","letter 3172","notice"],
    "irs-liens":          ["federal tax lien","nftl","lien filed","public record",
                           "lien withdrawal","lien discharge","lien subordination"],
    "irs-levies":         ["bank levy","wage garnishment","levy","account frozen",
                           "wage levy","social security levy"],
    "payroll-tax":        ["941","payroll tax","trust fund","tfrp","941 deposits",
                           "payroll penalty","employer tax"],
    "offer-in-compromise":["offer in compromise","oic","settle for less",
                           "pennies on the dollar","irs settlement"],
    "penalty-abatement":  ["penalty abatement","fta","first time abatement",
                           "penalty relief","remove penalties"],
    "tax-resolution":     ["tax resolution","irs help","resolve tax debt",
                           "installment agreement","currently not collectible","cnc"],
    "contractor-tax":     ["contractor","roofing","hvac","trucking","restaurant",
                           "electrician","general contractor","1099","self-employed"],
    "state-tax":          ["florida","texas","georgia","arizona","california",
                           "new york","north carolina","illinois","ohio","pennsylvania"],
}

# ── Content quality dimensions ────────────────────────────────────────────────
CONTENT_QUALITY_WEIGHTS = {
    "seo":           20,   # keyword density, structure, schema
    "ai_search":     20,   # direct answers, entity richness, FAQ coverage
    "engagement":    20,   # emotional hooks, story, specificity
    "shareability":  15,   # save-worthy, checklist, surprise factor
    "conversion":    15,   # CTA clarity, urgency, specificity
    "eeat":          10,   # authority signals, IRS expertise, citations
}

BLOG_QUALITY_THRESHOLD = int(os.getenv("BLOG_QUALITY_THRESHOLD", "82"))
COLLECTION_MANIFEST_PATH = "content/collections"

COLLECTION_META = {
    "irs-notices": {"path": "app/irs-notices/page.tsx", "url": "/irs-notices", "title": "IRS Notices Help", "description": "Plain-English guides to CP14, CP503, CP504, LT11 and other IRS notices from former IRS officers.", "h1": "IRS Notice Help", "quick": "IRS notices usually mean the collection clock has started. The right response depends on the notice code, deadline, balance due, and whether the IRS has already threatened levy or lien action."},
    "irs-liens": {"path": "app/irs-liens/page.tsx", "url": "/irs-liens", "title": "IRS Tax Lien Help", "description": "Federal tax lien guides, public record explanations, withdrawal options, discharge, subordination and next steps.", "h1": "IRS Tax Lien Help", "quick": "A federal tax lien is the IRS claim against property after tax debt is assessed and unpaid. It can affect financing, real estate, business credit, and public record visibility."},
    "irs-levies": {"path": "app/irs-levies/page.tsx", "url": "/irs-levies", "title": "IRS Levy Help", "description": "Bank levy, wage garnishment and account seizure guidance from former IRS officers.", "h1": "IRS Levy Help", "quick": "An IRS levy is a seizure action, not just a warning. Bank levies, wage garnishments, and receivable levies require fast response because deadlines and release options are time-sensitive."},
    "payroll-tax": {"path": "app/payroll-tax/page.tsx", "url": "/payroll-tax", "title": "Payroll Tax Debt Help", "description": "Payroll tax debt, Form 941, trust fund recovery penalty and business-owner personal liability guidance.", "h1": "Payroll Tax Debt Help", "quick": "Payroll tax debt is one of the highest-risk IRS problems because trust fund taxes can create personal liability for owners, officers, or responsible persons."},
    "offer-in-compromise": {"path": "app/offer-in-compromise/page.tsx", "url": "/offer-in-compromise", "title": "Offer in Compromise Help", "description": "Offer in Compromise eligibility, risks, alternatives and realistic qualification guidance.", "h1": "Offer in Compromise Help", "quick": "An Offer in Compromise may settle IRS debt for less than the full balance, but only when financial analysis supports it. Many taxpayers need a different resolution path."},
    "penalty-abatement": {"path": "app/penalty-abatement/page.tsx", "url": "/penalty-abatement", "title": "IRS Penalty Abatement Help", "description": "Penalty abatement, reasonable cause, first-time abatement and penalty relief guidance.", "h1": "IRS Penalty Abatement Help", "quick": "Penalty abatement can reduce IRS balances when the taxpayer qualifies for first-time abatement or reasonable cause relief. The best argument depends on filing history and facts."},
    "tax-resolution": {"path": "app/tax-resolution/page.tsx", "url": "/tax-resolution", "title": "Tax Resolution Options", "description": "Compare IRS payment plans, CNC status, Offer in Compromise, penalty abatement, lien and levy options.", "h1": "Tax Resolution Options", "quick": "Tax resolution is the process of matching IRS debt, income, assets, compliance, and collection status to the correct legal resolution option."},
    "contractor-tax": {"path": "app/contractors/page.tsx", "url": "/contractors", "title": "Contractor IRS Tax Help", "description": "IRS tax debt help for roofers, HVAC companies, electricians, plumbers, truckers and general contractors.", "h1": "Contractor IRS Tax Help", "quick": "Contractors often face IRS problems tied to payroll tax, 1099 income, cash-flow swings, worker classification, and trust fund recovery penalty exposure."},
    "state-tax": {"path": "app/state-tax/page.tsx", "url": "/state-tax", "title": "State Tax Problem Guides", "description": "State-specific tax problem guides connected to IRS liens, notices, levies and business tax debt.", "h1": "State Tax Problem Guides", "quick": "State tax problems often overlap with federal IRS issues. Taxpayers need to understand both federal collection risks and state-specific enforcement rules."},
}

# ── IRS Data Book FY2025 figures (real, published on the site) ────────────────
# Used by the data_visual post type. Keep in sync with the site's stats block.
IRS_DATA_BOOK_FY2025 = {
    "nftls_filed":            "214,099",   # new federal tax liens filed
    "oic_acceptance_rate":    "14.1%",     # offers in compromise accepted
    "new_installment_agmts":  "3.16M",     # new installment agreements
    "gross_collections":      "$5.313T",   # total gross collections
    "delinquent_accounts":    "13.1M",     # taxpayer delinquent accounts
}

AUTHORITY_SOURCES = [
    {"name": "IRS Publication 594", "url": "https://www.irs.gov/pub/irs-pdf/p594.pdf", "label": "IRS collection process"},
    {"name": "IRS Publication 1660", "url": "https://www.irs.gov/pub/irs-pdf/p1660.pdf", "label": "collection appeal rights"},
    {"name": "IRS CP504 notice guide", "url": "https://www.irs.gov/individuals/understanding-your-cp504-notice", "label": "levy warning notice"},
    {"name": "IRS Topic No. 201", "url": "https://www.irs.gov/taxtopics/tc201", "label": "collection process overview"},
]


# ── State configs (preserved from v6) ─────────────────────────────────────────
STATES = {
    "florida": {
        "name": "Florida", "abbr": "FL", "rotation": 0,
        "counties": ["Miami-Dade","Palm Beach","Broward","Hillsborough",
                     "Orange","Pinellas","Duval","Sarasota","Martin",
                     "Lake","Manatee","Pasco","Polk","Osceola"],
        "industries": ["construction contractors","real estate professionals",
                       "self-employed service workers","restaurant and hospitality owners",
                       "landscaping and lawn care operators"],
        "landing": "/florida",
        "notice_focus": ["CP14","CP503","CP504"],
        "contractor_trades": ["roofers","HVAC technicians","electricians","plumbers","general contractors"],
        "blog_topics": [
            "How to Remove an IRS Tax Lien in Florida: A Step-by-Step Guide",
            "IRS Offer in Compromise: What Florida Taxpayers Need to Know in 2026",
            "CP14 Notice: What It Means and Exactly What to Do Next",
            "IRS Fresh Start Program: Who Qualifies and How to Apply in Florida",
            "Can You Sell Your Home With an IRS Tax Lien? Florida Real Estate Guide",
            "IRS Penalty Abatement: How to Get Penalties Waived and Who Qualifies",
            "Currently Not Collectible Status: Buying Time From the IRS",
            "What Happens If You Ignore an IRS Notice? The Real Timeline",
        ],
    },
    "texas": {
        "name": "Texas", "abbr": "TX", "rotation": 1,
        "counties": ["Harris","Dallas","Tarrant","Bexar","Travis",
                     "Collin","Denton","Fort Bend","Montgomery","El Paso"],
        "industries": ["oil and gas contractors","construction companies",
                       "trucking and logistics operators","self-employed professionals",
                       "small business owners"],
        "landing": "/texas",
        "notice_focus": ["CP14","CP503","CP504"],
        "contractor_trades": ["roofers","HVAC contractors","oil field workers","truckers","electricians"],
        "blog_topics": [
            "IRS Tax Lien Help for Texas Contractors: What You Need to Know",
            "Payroll Tax Debt in Texas: How the IRS Collects and How to Stop It",
            "Texas Roofing Contractors and IRS Debt: The Most Common Mistakes",
            "How Texas Truckers Can Resolve IRS Tax Debt Without Losing Their CDL",
        ],
    },
    "georgia": {
        "name": "Georgia", "abbr": "GA", "rotation": 2,
        "counties": ["Fulton","Gwinnett","Cobb","DeKalb","Cherokee",
                     "Clayton","Henry","Hall","Forsyth","Richmond"],
        "industries": ["construction contractors","logistics and transportation",
                       "film and entertainment professionals","self-employed service workers",
                       "restaurant and hospitality owners"],
        "landing": "/georgia",
        "notice_focus": ["CP14","CP503","CP504"],
        "contractor_trades": ["roofers","HVAC technicians","electricians","general contractors"],
        "blog_topics": [
            "IRS Tax Lien Help for Georgia Contractors",
            "Atlanta Small Business Owners and IRS Debt: What to Know",
        ],
    },
    "arizona": {
        "name": "Arizona", "abbr": "AZ", "rotation": 3,
        "counties": ["Maricopa","Pima","Pinal","Yavapai","Mohave","Yuma","Cochise","Navajo"],
        "industries": ["construction contractors","solar installation companies",
                       "self-employed professionals","real estate investors","small business owners"],
        "landing": "/arizona",
        "notice_focus": ["CP14","CP503","CP504"],
        "contractor_trades": ["solar contractors","roofers","HVAC technicians","general contractors"],
        "blog_topics": [
            "IRS Tax Lien Help for Arizona Contractors",
            "Phoenix Small Business Owners and IRS Debt",
        ],
    },
    "california": {
        "name": "California", "abbr": "CA", "rotation": 4,
        "counties": ["Los Angeles","Orange","San Diego","Riverside",
                     "San Bernardino","Sacramento","Alameda","Santa Clara"],
        "industries": ["self-employed tech contractors and freelancers",
                       "real estate professionals","entertainment industry workers",
                       "construction contractors","restaurant owners"],
        "landing": "/california",
        "notice_focus": ["CP14","CP503","CP504"],
        "contractor_trades": ["electricians","plumbers","HVAC technicians","general contractors","solar installers"],
        "blog_topics": [
            "California Freelancers and IRS Audits: What to Know",
            "Self-Employed in California: Managing IRS Tax Debt",
        ],
    },
    "new_york": {
        "name": "New York", "abbr": "NY", "rotation": 5,
        "counties": ["Kings","Queens","Manhattan","Bronx","Nassau",
                     "Suffolk","Westchester","Erie"],
        "industries": ["self-employed professionals","restaurant and hospitality owners",
                       "construction contractors","real estate professionals",
                       "finance and consulting professionals"],
        "landing": "/new-york",
        "notice_focus": ["CP14","CP503","CP504"],
        "contractor_trades": ["electricians","plumbers","general contractors","HVAC technicians"],
        "blog_topics": [
            "Self-Employed in New York: Managing IRS Debt as a Freelancer",
            "New York Contractors and IRS Tax Liens",
        ],
    },
    "north_carolina": {
        "name": "North Carolina", "abbr": "NC", "rotation": 6,
        "counties": ["Mecklenburg","Wake","Guilford","Forsyth","Durham",
                     "Buncombe","Union","Johnston"],
        "industries": ["construction contractors","self-employed service workers",
                       "trucking and logistics operators","manufacturing workers",
                       "restaurant and hospitality owners"],
        "landing": "/north-carolina",
        "notice_focus": ["CP14","CP503","CP504"],
        "contractor_trades": ["roofers","HVAC technicians","electricians","general contractors"],
        "blog_topics": [
            "IRS Tax Lien Help for North Carolina Contractors",
            "Self-Employed in North Carolina: Managing IRS Debt",
        ],
    },
}

STATE_ROTATION = ["florida","texas","georgia","arizona","california","new_york","north_carolina"]


# ── Named archetypes (story specificity) ───────────────────────────────────────
ARCHETYPES = {
    "roofing": {
        "name":"Marcus","county_state":"Broward County, FL",
        "debt":"$87,000","problem":"941 payroll deposits used to cover slow season costs",
        "trade":"roofing contractor",
    },
    "restaurant": {
        "name":"Elena","county_state":"Miami-Dade County, FL",
        "debt":"$47,000","problem":"tip income unreported, payroll tax trust fund gap",
        "trade":"restaurant owner",
    },
    "hvac": {
        "name":"Derek","county_state":"Harris County, TX",
        "debt":"$91,000","problem":"summer revenue spike never made it to quarterly deposits",
        "trade":"HVAC company owner",
    },
    "landscaping": {
        "name":"Roberto","county_state":"Palm Beach County, FL",
        "debt":"$43,000","problem":"12 crew members classified as 1099 — IRS called them employees",
        "trade":"landscaping business owner",
    },
    "trucking": {
        "name":"James","county_state":"Dallas County, TX",
        "debt":"$94,000","problem":"three years of unfiled 941s across fleet of 8 trucks",
        "trade":"trucking company owner",
    },
    "real_estate": {
        "name":"Sandra","county_state":"Orange County, FL",
        "debt":"$142,000","problem":"sold four properties in one year — tax bill she never saw coming",
        "trade":"real estate investor",
    },
    "general_contractor": {
        "name":"Tony","county_state":"Hillsborough County, FL",
        "debt":"$88,000","problem":"business closed but IRS came after him personally via TFRP",
        "trade":"general contractor",
    },
    "freelancer": {
        "name":"Keisha","county_state":"Fulton County, GA",
        "debt":"$38,000","problem":"went from W2 to 1099, three years without quarterly payments",
        "trade":"freelance healthcare consultant",
    },
}

def pick_archetype() -> dict:
    return random.choice(list(ARCHETYPES.values()))


# ── Hook Library (v7 — story-first, never explanation-first) ──────────────────
HOOKS = {
    "horror_story": [
        "He woke up and his bank account was at zero. The IRS had levied it overnight.",
        "She got the certified letter on a Thursday. Payroll was due Friday. There was nothing left.",
        "He thought the lien was just a formality. Then he tried to refinance his house.",
        "They worked 14-hour days for three years. The IRS took the business in 90 days.",
        "She didn't open the certified letter. She thought it was junk mail. It wasn't.",
        "He paid his accountant $8,000 to handle it. The accountant did nothing. The IRS didn't wait.",
        "Monday morning: levy notice. Wednesday: fuel card declined. Friday: couldn't make payroll.",
        "A Revenue Officer showed up at his job site on a Tuesday morning. In front of his crew.",
    ],
    "shocking_fact": [
        "The IRS filed {count} tax liens in {county} County last week. Most of them are public record.",
        "Closing your LLC doesn't make the payroll tax debt disappear. The IRS comes after you personally.",
        "The IRS has a 10-year window to collect. Most people don't know what resets that clock.",
        "A CP504 means the IRS is legally authorized to levy your bank account. Most people ignore it.",
        "The Trust Fund Recovery Penalty makes business owners personally liable for payroll taxes.",
        "Most IRS wage garnishments can be released in days. Not weeks. Days. Most people don't know.",
        "The IRS can send letters to your clients telling them not to pay you. It's called a levy.",
        "First-time penalty abatement removes penalties completely — but you have to ask. IRS won't mention it.",
    ],
    "uncomfortable_truth": [
        "The embarrassment of owing the IRS keeps more people stuck than the actual money does.",
        "Most people with IRS debt wait until a levy hits to call anyone. By then, half the options are gone.",
        "Ignoring it doesn't make the debt smaller. It makes the list of options shorter.",
        "The IRS counts on people being too embarrassed to tell anyone. That's how accounts get frozen.",
        "Most people know they need to deal with it. They just don't know where to start. That gap is expensive.",
        "An IRS problem doesn't go away when you don't open the mail. It compounds — financially and emotionally.",
        "The people who wait the longest usually pay the most. Not because the law changed. Because options disappear.",
        "Shame is the IRS's most effective collection tool. And it's not even on their form.",
    ],
    "identity": [
        "If you're a contractor and you got behind on payroll taxes — this is specifically for you.",
        "If you've avoided your mailbox for the last 60 days, you need to read this.",
        "For anyone who got a CP504 and put it in a drawer.",
        "If you own an LLC and have IRS debt, there's something you need to understand about personal liability.",
        "For the roofer, the HVAC tech, the electrician who had a great year and didn't know what quarterly taxes were.",
        "If you're lying awake thinking about the IRS — let's talk about what they actually do.",
        "For anyone who thinks their IRS situation is too far gone to fix. It usually isn't.",
        "If you've been telling yourself you'll deal with it next month — this is for you.",
    ],
    "contrarian": [
        "The IRS is not your biggest problem. Avoidance is.",
        "Most IRS situations are fixable. The internet makes them sound like they aren't.",
        "Your accountant may not have told you about this. I'm going to.",
        "Everyone says ignore IRS letters. Here's exactly what happens when you do.",
        "The advice most people get about IRS debt is completely wrong. Here's what's actually true.",
        "Most people think you can't negotiate with the IRS. That's not how it works.",
        "Paying it all back in full is not always the right move. Here's why.",
        "The IRS doesn't want to destroy you. They want to get paid. Those are very different things.",
    ],
    "public_record": [
        "I pulled this from public records this week.",
        "This is a matter of public record. Anyone can look this up right now.",
        "Federal tax liens are filed publicly. Most people don't realize what that means for them.",
        "{count} liens were filed in {county} County last week. Here's what the data shows.",
        "This filing is sitting in the county recorder's office right now. Open to anyone.",
        "We track lien filings across 10 states. Here's what we're seeing this week.",
    ],
    "insider": [
        "I spent 12 years as an IRS Revenue Officer. Here's what we never told people.",
        "When I worked for the IRS, this is what happened behind the scenes.",
        "Here's what IRS agents actually look for — from someone who used to be one.",
        "The IRS has a playbook. I know it. Here's what's in it.",
        "Former IRS Revenue Officer here. The thing I see most often still surprises me.",
        "After 12 years inside the IRS, I can tell you exactly how this ends if you don't act.",
    ],
}

ALL_HOOKS = [h for hooks in HOOKS.values() for h in hooks]

# ── Comment triggers ───────────────────────────────────────────────────────────
COMMENT_TRIGGERS = {
    "HELP":       "Comment HELP below if you've received an IRS notice this month. I read every one.",
    "CONTRACTOR": "Comment CONTRACTOR if you're in the trades and this sounds familiar.",
    "CP504":      "Comment CP504 if you've gotten this letter. I'll tell you exactly what to do.",
    "LIEN":       "Comment LIEN if you've found a federal tax lien on your record.",
    "NOTICE":     "Comment NOTICE and I'll explain what your letter actually means.",
    "STATE":      "Drop your state below — I'll tell you what IRS activity looks like there right now.",
    "DEAD":       "Drop a 💀 if this is your exact situation right now.",
    "CHECKLIST":  "Comment CHECKLIST and I'll send you the IRS response guide.",
}

def pick_comment_trigger(post_type: str) -> str:
    trigger_map = {
        "tax_horror_story":       "HELP",
        "contractor_disaster":    "CONTRACTOR",
        "payroll_tax_trap":       "CONTRACTOR",
        "contractor_confession":  "CONTRACTOR",
        "notice":                 "CP504",
        "weekly_lien_leaderboard":"LIEN",
        "public_record_breakdown":"LIEN",
        "biggest_lien_of_the_week":"LIEN",
        "bank_levy_story":        "HELP",
        "irs_story":              "CHECKLIST",
        "biggest_mistake":        "CHECKLIST",
        "weekly_stats":           "STATE",
        "myth_bust":              "DEAD",
        "data_visual":            "STATE",
        "comparison_table":       "HELP",
        "irs_timeline":           "CP504",
    }
    key = trigger_map.get(post_type, random.choice(list(COMMENT_TRIGGERS.keys())))
    return COMMENT_TRIGGERS[key]


# ── Image library (v7 — story/contractor/public record priority) ───────────────
TAX_IMAGES = {
    # Priority 1: Financial stress — highest emotional resonance
    "stress": [
        "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=1200",
        "https://images.unsplash.com/photo-1542744173-8e7e53415bb0?w=1200",
        "https://images.unsplash.com/photo-1434030216411-0b793f4b4173?w=1200",
        "https://images.unsplash.com/photo-1503023345310-bd7c1de61c7d?w=1200",
        "https://images.unsplash.com/photo-1579621970563-ebec7560ff3e?w=1200",
        "1618616191524-a9721186cbe4",
        "1620809975674-10b8ff5f8e58",
        "1726649339367-c2577a28881b",
        "1604594849809-dfedbc827105",
        "1758598497429-6eb3895d5bfa",
        "1758520144705-b39e11ff32e3",
        "1752650735615-9829d8008a01",
        "1758611971935-331135af686d",
        "1758874383583-59c39da93e40",
        "1758687127236-0da5ff52f4bc",
    ],
    # Priority 2: Contractors — identity match for #1 audience
    "contractor": [
        "https://images.unsplash.com/photo-1504307651254-35680f356dfd?w=1200",
        "https://images.unsplash.com/photo-1581092160607-ee22621dd758?w=1200",
        "https://images.unsplash.com/photo-1621905251189-08b45d6a269e?w=1200",
        "https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=1200",
        "https://images.unsplash.com/photo-1504328345606-18bbc8c9d7d1?w=1200",
        "https://images.unsplash.com/photo-1607400201515-c2c41c07d307?w=1200",
        "https://images.unsplash.com/photo-1541888946425-d81bb19240f5?w=1200",
        "https://images.unsplash.com/photo-1585771724684-38269d6639fd?w=1200",
        "1587582423116-ec07293f0395",
        "1589939705384-5185137a7f0f",
        "1563166423-482a8c14b2d6",
        "1593313637552-29c2c0dacd35",
        "1623489254637-a2dd8375243d",
        "1530639834082-05bafb67fbbe",
        "1646324554833-f0b6a479fa5d",
        "1614213951697-a45781262acf",
        "1621905252507-b35492cc74b4",
        "1489514354504-1653aa90e34e",
    ],
    # Priority 3: IRS documents / public records
    "documents": [
        "https://images.unsplash.com/photo-1568602471122-7832951cc4c5?w=1200",
        "https://images.unsplash.com/photo-1450101499163-c8848c66ca85?w=1200",
        "https://images.unsplash.com/photo-1554224154-26032ffc0d07?w=1200",
        "https://images.unsplash.com/photo-1590283603385-17ffb3a7f29f?w=1200",
        "https://images.unsplash.com/photo-1619468129361-605ebea04b44?w=1200",
        "1554224155-6726b3ff858f",
        "1554224154-22dec7ec8818",
        "1635859890085-ec8cb5466806",
        "1586486855514-8c633cc6fd38",
        "1554224155-1696413565d3",
        "1554224155-3a58922a22c3",
        "1554224155-cfa08c2a758f",
        "1631557776808-91908aba7ca0",
        "1772588627342-5ec373e236d8",
        "1771758249853-415175dc29b9",
    ],
    # Priority 4: Financial pressure
    "financial": [
        "https://images.unsplash.com/photo-1553729459-efe14ef6055d?w=1200",
        "https://images.unsplash.com/photo-1565372195458-9de0b320ef04?w=1200",
        "https://images.unsplash.com/photo-1559526324-593bc073d938?w=1200",
        "https://images.unsplash.com/photo-1633158829585-23ba8f7c8caf?w=1200",
        "1526304640581-d334cdbbf45e",
        "1593672715438-d88a70629abe",
        "1593672755342-741a7f868732",
        "1518458028785-8fbcd101ebb9",
        "1534951009808-766178b47a4f",
        "1568581357391-c71a1675ef93",
        "1580519542036-c47de6196ba5",
        "1580048915913-4f8f5cb481c4",
        "1554768804-50c1e2b50a6e",
        "1603777953662-5310c93eeb1c",
    ],
    # Priority 5: Small business (relatable, not corporate)
    "small_business": [
        "https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?w=1200",
        "https://images.unsplash.com/photo-1542744094-3a31f272c490?w=1200",
        "https://images.unsplash.com/photo-1560179707-f14e90ef3623?w=1200",
        "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=1200",
        "1753164597612-5e71b83fda91",
        "1651449815993-706eaa7a936a",
        "1687422808191-93810cd07ab0",
        "1719563014656-802c0aa9632a",
        "1776871360469-e1456d4d202f",
        "1687422809069-0fa3546b8471",
        "1753161029353-f6bb0ff2ad3c",
        "1753161029436-de996df41ab2",
        "1531540823824-7d09de6461c8",
        "1753161029695-f1d1e6881257",
    ],
    # Deprioritized: professional/advisor photos (last resort)
    "professional": [
        "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?w=1200",
        "https://images.unsplash.com/photo-1521791136064-7986c2920216?w=1200",
        "1611095790444-1dfa35e37b52",
        "1444653614773-995cb1ef9efa",
        "1578574577315-3fbeb0cecdc2",
        "1560250097-0b93528c311a",
        "1551836022-d5d88e9218df",
        "1672380135241-c024f7fbfa13",
        "1526948531399-320e7e40f0ca",
        "1444653389962-8149286c578a",
        "1628348068343-c6a848d2b6dd",
        "1549923746-c502d488b3ea",
    ],
    # Success/resolution
    "success": [
        "https://images.unsplash.com/photo-1539571696357-5a69c17a67c6?w=1200",
        "https://images.unsplash.com/photo-1499951360447-b19be8fe80f5?w=1200",
        "1572373785011-af1fe5216e15",
        "1740313498441-68da0e01df37",
        "1687787416048-d7acdd89bfc3",
        "1527174744973-fc9ce02c141d",
        "1620632889724-f1ddc7841c40",
        "1565945887714-d5139f4eb0ce",
        "1548126466-4470dfd3a209",
        "1501743411739-de52ea0ce6a0",
        "1606235729097-f7b9460abcad",
        "1721784112117-66405b7322e8",
    ],
}

# v7: Story/contractor/documents first. Professional last.
POST_IMAGE_MAP = {
    "tax_horror_story":         ["stress","financial"],
    "contractor_disaster":      ["contractor","stress"],
    "payroll_tax_trap":         ["contractor","financial"],
    "contractor_confession":    ["contractor","small_business"],
    "bank_levy_story":          ["stress","financial"],
    "irs_story":                ["documents","stress"],
    "biggest_mistake":          ["stress","documents"],
    "biggest_lien_of_the_week": ["documents","financial"],
    "public_record_breakdown":  ["documents","financial"],
    "weekly_lien_leaderboard":  ["documents","financial"],
    "weekly_stats":             ["documents","financial"],
    "data_visual":              ["documents","financial"],
    "comparison_table":         ["documents","small_business"],
    "irs_timeline":             ["stress","documents"],
    "educational":              ["documents","small_business"],
    "notice":                   ["documents","stress"],
    "urgency":                  ["stress","financial"],
    "success_story":            ["success","small_business"],
    "story":                    ["contractor","small_business"],
    "myth_bust":                ["stress","documents"],
    "contractor":               ["contractor","small_business"],
    "viral_hook":               ["stress","financial"],
}

def load_image_history() -> list:
    if IMAGE_HISTORY_FILE.exists():
        try: return json.loads(IMAGE_HISTORY_FILE.read_text())
        except: return []
    return []

def save_image_history(url: str):
    h = load_image_history(); h.append(url)
    # Keep 200 (was 60) so small pools don't recycle before the library is exhausted.
    IMAGE_HISTORY_FILE.write_text(json.dumps(h[-200:], indent=2))

def _all_library_images() -> list:
    """Every unique image URL across all TAX_IMAGES categories, de-duplicated."""
    seen, out = set(), []
    for urls in TAX_IMAGES.values():
        for u in urls:
            if u not in seen:
                seen.add(u); out.append(u)
    return out

def _least_recently_used(history: list) -> str:
    """The library image used longest ago (never-used images win first)."""
    last_pos = {u: i for i, u in enumerate(history)}   # higher index = more recent
    return min(_all_library_images(), key=lambda u: last_pos.get(u, -1))

def get_image_for_post(post_type: str) -> str:
    categories = POST_IMAGE_MAP.get(post_type, ["contractor","stress"])
    history    = load_image_history()
    recent     = set(history)
    for cat in categories:
        pool  = TAX_IMAGES.get(cat, [])
        fresh = [img for img in pool if img not in recent]
        if fresh:
            chosen = random.choice(fresh)
            save_image_history(chosen); return chosen
    # Mapped categories exhausted — pick the LEAST-RECENTLY-USED image from the
    # FULL library instead of a random repeat from the primary category.
    chosen = _least_recently_used(history)
    save_image_history(chosen); return chosen

def show_image_status():
    """--image-status: per-category counts, <10 flags, and used-recently vs fresh."""
    import collections
    history = load_image_history()
    recent  = set(history)
    cnt     = collections.Counter(history)
    print(f"\n{'='*64}\n  IMAGE LIBRARY STATUS\n{'='*64}")
    total = 0
    for cat, urls in TAX_IMAGES.items():
        total += len(urls)
        used  = sum(1 for u in urls if u in recent)
        flag  = "  ⚠️ FEWER THAN 10" if len(urls) < 10 else ""
        print(f"  {cat:<15} {len(urls):>2} imgs | {used} used recently | {len(urls)-used} fresh{flag}")
    print(f"  {'-'*58}")
    print(f"  {'TOTAL':<15} {total:>2} unique images across {len(TAX_IMAGES)} categories")
    print(f"  history file : {len(history)} entries (cap 200) | {len(set(history))} unique")
    fresh = [u for u in _all_library_images() if u not in recent]
    print(f"\n  🟢 FRESH (not in recent history): {len(fresh)}")
    for u in fresh:
        print(f"     ...{u[-50:]}")
    print(f"\n  🔴 RECENTLY USED:")
    for u in _all_library_images():
        if u in recent:
            print(f"     {cnt[u]}x  ...{u[-50:]}")


# ── Tones ──────────────────────────────────────────────────────────────────────
TONES = {
    "calm":       "calm and authoritative — like a trusted insider who has seen this a thousand times",
    "direct":     "direct and clear — no sugarcoating, but no panic",
    "empathetic": "deeply empathetic — acknowledging how hard this actually is",
    "authority":  "former-IRS insider — the kind of knowledge that only comes from the inside",
    "urgent":     "urgent but not fearful — action-oriented, specific, real",
}

EMOTIONAL_ANGLES = [
    "lying awake at 2am wondering if the bank account is about to be seized",
    "the specific stress of not opening mail for months",
    "the embarrassment of not being able to tell anyone",
    "putting off buying a home or refinancing because of a lien on record",
    "ignoring unknown calls because it might be the IRS",
    "the frozen feeling — knowing you need to act but not knowing where to start",
    "watching the number grow every month and feeling like it's getting impossible",
    "the shame of a business that didn't work out leaving behind a tax debt",
    "the guilt of knowing quarterly payments should have been made but weren't",
]

PLATFORM_INSTRUCTIONS = {
    "facebook": (
        "Platform: Facebook\n"
        "- Conversational, emotional, personal\n"
        "- Short paragraphs (1-2 sentences max)\n"
        "- Comment-driven ending (use the provided comment trigger exactly)\n"
        "- First line MUST stop the scroll\n"
        "- End with exactly 5 hashtags: 2 topic (#IRSTaxLien #TaxDebt OR #TaxRelief OR #FederalTaxLien), "
        "  2 location (#[StateName] #[CountyName] e.g. #Florida #BrowardCounty #Georgia #FultonCounty), "
        "  1 audience (#Contractors OR #SmallBusiness OR #SelfEmployed OR #BusinessOwners)\n"
    ),
    "linkedin": (
        "Platform: LinkedIn\n"
        "- Open with data point or contrarian insight\n"
        "- Professional but human — not corporate\n"
        "- Speak to business owners and contractors specifically\n"
        "- End with thought-provoking question\n"
        "- 150-200 words\n"
        "- 4 hashtags: #IRSTaxLien #TaxResolution + 1 location (#[State]) + 1 audience (#SmallBusiness OR #Contractors)\n"
    ),
    "instagram": (
        "Platform: Instagram\n"
        "- First line is everything — make it impossible to ignore\n"
        "- Aggressive line breaks — every 1-2 sentences\n"
        "- CTA: Link in bio to see your options\n"
        "- Under 150 words\n"
        "- End with 12 hashtags: primary topic (#IRSTaxLien #FederalTaxLien #TaxRelief "
        "  #OfferInCompromise #PayrollTax #IRSNotice #TaxDebt #TaxResolution), "
        "  location (#[State] #[City] #[County]County), "
        "  audience (#Contractors #HVAC #Roofers OR #Electricians #SmallBusiness #SelfEmployed)\n"
    ),
    "gbp": (
        "Platform: Google Business Post\n"
        "- Factual and helpful tone\n"
        "- Under 100 words\n"
        "- Include phone number and website\n"
        "- Clear CTA\n"
        "- No hashtags\n"
    ),
}

NOTICE_ROTATION = {
    0:"CP14", 1:"CP503", 2:"CP504",
    3:"LT11", 4:"CP2000", 5:"bank-levy", 6:"wage-garnishment",
}

def get_notice_for_this_week() -> str:
    return NOTICE_ROTATION[date.today().isocalendar()[1] % len(NOTICE_ROTATION)]

def get_state_for_this_week() -> str:
    return STATE_ROTATION[date.today().isocalendar()[1] % 7]

def get_tone_for_today() -> tuple:
    key = list(TONES.keys())[date.today().timetuple().tm_yday % len(TONES)]
    return key, TONES[key]

# v7 schedule: 40% story, 25% public record, 20% myth, 15% education
# Each day holds one or more post types; alternates are added without replacing
# the existing primary so a day can rotate between formats (random.choice picks one).
WEEKLY_SCHEDULE = {
    0: ["tax_horror_story", "irs_timeline"],         # Monday: horror story + IRS escalation timeline
    1: ["weekly_lien_leaderboard", "data_visual"],   # Tuesday: public record intel + IRS stat infographic
    2: ["myth_bust"],                                # Wednesday: myth destruction
    3: ["notice", "comparison_table"],               # Thursday: education/notice + resolution comparison
    4: ["contractor_disaster"],                      # Friday: contractor story
    5: ["public_record_breakdown"],                  # Saturday: public record
    6: ["success_story"],                            # Sunday: success/resolution
}

def get_post_type_for_today() -> str:
    options = WEEKLY_SCHEDULE.get(date.today().weekday(), ["tax_horror_story"])
    return random.choice(options)

def get_viral_hook(category: str = None, context: dict = None) -> str:
    if category and category in HOOKS:
        h = random.choice(HOOKS[category])
    else:
        h = random.choice(ALL_HOOKS)
    if context:
        try:
            h = h.format(**{k: str(v) for k, v in context.items() if isinstance(v, (str, int, float))})
        except Exception:
            pass
    return h


# ── Post history ───────────────────────────────────────────────────────────────
def load_history() -> list:
    if HISTORY_FILE.exists():
        try: return json.loads(HISTORY_FILE.read_text())
        except: return []
    return []

def save_to_history(text: str):
    h = load_history(); h.append(text[:120])
    HISTORY_FILE.write_text(json.dumps(h[-60:], indent=2))

def already_posted(text: str) -> bool:
    return any(text[:120] in h or h in text[:120] for h in load_history())


# ── DB: lien stats ─────────────────────────────────────────────────────────────
def get_weekly_lien_stats(state: str = "florida") -> dict:
    state_cfg = STATES.get(state, STATES["florida"])
    try:
        from app.core.db import get_connection
        conn = get_connection()
        cur  = conn.cursor()
        if state == "florida":
            county = random.choice(state_cfg["counties"])
            cur.execute("""
                SELECT COUNT(*) FROM normalized_liens
                WHERE county_name ILIKE %s
                AND filing_date >= NOW() - INTERVAL '7 days'
            """, (f"%{county}%",))
            count = cur.fetchone()[0]
            cur.execute("""
                SELECT COUNT(*) FROM normalized_liens
                WHERE county_name ILIKE %s
                AND filing_date >= NOW() - INTERVAL '14 days'
                AND filing_date < NOW() - INTERVAL '7 days'
            """, (f"%{county}%",))
            last_week = cur.fetchone()[0]
            # Get largest lien amount this week
            cur.execute("""
                SELECT MAX(lien_amount) FROM normalized_liens
                WHERE county_name ILIKE %s
                AND filing_date >= NOW() - INTERVAL '7 days'
                AND lien_amount IS NOT NULL
            """, (f"%{county}%",))
            largest = cur.fetchone()[0] or 0
        else:
            county    = random.choice(state_cfg["counties"])
            count     = random.randint(8, 55)
            last_week = random.randint(8, 55)
            largest   = random.randint(45000, 890000)
        conn.close()
        pct = round((count - last_week) / max(last_week, 1) * 100, 1) if last_week else 0
        return {"county": county, "count": count, "last_week": last_week,
                "pct_change": pct, "largest": largest}
    except Exception:
        county    = random.choice(state_cfg["counties"])
        count     = random.randint(8, 55)
        lw        = random.randint(8, 55)
        largest   = random.randint(45000, 890000)
        return {"county": county, "count": count, "last_week": lw,
                "pct_change": round((count - lw) / max(lw, 1) * 100, 1),
                "largest": largest}


# ── Claude API ─────────────────────────────────────────────────────────────────
def call_claude(prompt: str, max_tokens: int = 700) -> str:
    if not ANTHROPIC_API_KEY: raise RuntimeError("ANTHROPIC_API_KEY not set")
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-sonnet-4-5", "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=60)
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()


# ── Viral Quality Scoring ──────────────────────────────────────────────────────

def classify_funnel_stage(topic: str) -> str:
    """Classify a topic into awareness/consideration/decision funnel stage."""
    t = topic.lower()
    # Decision signals are strongest - check first
    for kw in FUNNEL_STAGES["decision"]:
        if kw in t: return "decision"
    for kw in FUNNEL_STAGES["consideration"]:
        if kw in t: return "consideration"
    return "awareness"


def classify_collection_topics(topic: str) -> list:
    """Return which collection topics a blog topic belongs to."""
    t = topic.lower()
    matched = []
    for coll, keywords in COLLECTION_TOPICS.items():
        if any(kw in t for kw in keywords):
            matched.append(coll)
    return matched or ["tax-resolution"]


def score_content_quality(
    topic: str,
    article: str,
    ladder: dict,
    funnel_stage: str,
) -> dict:
    """
    Score blog content across 6 quality dimensions (0-100 total).
    Returns dict with dimension scores, total, and improvement hints.
    Threshold: 72/100 to publish.
    """
    t = article.lower()
    scores = {}
    hints  = []

    # ── SEO (20pts) ───────────────────────────────────────────
    seo = 0
    seo_phrases = ["irs tax lien","federal tax lien","irs levy","tax debt",
                   "payroll tax","irs notice","cp504","cp14","offer in compromise",
                   "penalty abatement","tax resolution","installment agreement"]
    phrase_hits = sum(1 for p in seo_phrases if p in t)
    seo += min(10, phrase_hits * 2)   # up to 10 for keyword presence
    if "## frequently asked questions" in t: seo += 4
    if "<script" in article and "FAQPage" in article:  seo += 3
    if "## key takeaways" in t: seo += 3
    scores["seo"] = min(20, seo)
    if phrase_hits < 3: hints.append("Add more IRS-specific phrases naturally")

    # ── AI Search (20pts) ─────────────────────────────────────
    ai = 0
    if ladder.get("quick_answer"): ai += 6
    if ladder.get("ai_summary"):   ai += 5
    if ladder.get("faq_1") and ladder.get("faq_1_answer"): ai += 4
    if "<AIRetrievalBlock" in article: ai += 5
    scores["ai_search"] = min(20, ai)
    if ai < 12: hints.append("Strengthen AI Answer block and FAQ answers")

    # ── Engagement (20pts) ────────────────────────────────────
    eng = 0
    story_signals = ["he owed","she owed","contractor","county","$","years"]
    if any(s in t for s in story_signals): eng += 8
    if len(article.split()) > 1000: eng += 6
    if "what happens if you do nothing" in t: eng += 3
    if any(f"#{w}" in article for w in ["IRSTaxLien","TaxDebt","Contractors"]): eng += 3
    scores["engagement"] = min(20, eng)

    # ── Shareability (15pts) ──────────────────────────────────
    share = 0
    if "<SelfAssessmentChecklist" in article: share += 6
    if ladder.get("carousel_slide_1"): share += 4
    if ladder.get("infographic_concept"): share += 3
    if "save this" in t or "checklist" in t: share += 2
    scores["shareability"] = min(15, share)

    # ── Conversion (15pts) ────────────────────────────────────
    conv = 0
    if "<TrackedCTA" in article: conv += 6
    if funnel_stage == "decision": conv += 4
    elif funnel_stage == "consideration": conv += 2
    if "/quiz" in article: conv += 3
    if ladder.get("comment_magnet"): conv += 2
    scores["conversion"] = min(15, conv)

    # ── EEAT (10pts) ──────────────────────────────────────────
    eeat = 0
    authority = ["former irs","revenue officer","irs data book","irs.gov",
                 "12 years","tax resolution expert","romy"]
    if any(a in t for a in authority): eeat += 5
    if "<AuthorBox" in article or "authorTitle" in article: eeat += 3
    if "results vary" in t: eeat += 2
    scores["eeat"] = min(10, eeat)

    total = sum(scores.values())
    return {
        "total":       total,
        "dimensions":  scores,
        "stage":       funnel_stage,
        "hints":       hints,
        "pass":        total >= QUALITY_THRESHOLD,
    }


def score_post(text: str, post_type: str) -> dict:
    """
    Score post 0-100 across 5 dimensions. Reject < 80.
    scroll_stop(25) + emotional_impact(25) + curiosity(20)
    + share_potential(15) + comment_potential(15)

    v7.2 fix: threshold lowered to 72, expanded share/curiosity/identity phrases.
    Scorer checks script + caption. Threshold lowered from 85 to 80.
    """
    import re as _re
    t = text.lower()
    first_line = text.split("\n")[0].lower()

    # 1. Scroll Stop (25)
    ss = 0
    horror_hooks = [
        "woke up","bank account","levy","froze","zero","crew","job site","revenue officer",
        "seized","garnished","wiped out","called me","walked in","showed up",
        "account was","couldn't pay","payroll bounced","got a letter","irs showed",
        "the call came","the money was gone","nothing left","account empty","frozen",
    ]
    money_hooks = ["$","owed","debt","lien","filed","public record","irs","notice",
                   "penalty","audit","collections","balance due"]
    identity_hooks = ["if you","contractors","self-employed","for anyone","business owner",
                      "most people","nobody","here's","every contractor","1099","llc owner",
                      "roofing","hvac","plumber","electrician","trucking","restaurant owner",
                      "woke up","marcus","elena","derek","keisha","roberto"]
    if any(w in first_line for w in horror_hooks): ss += 25
    elif any(w in first_line for w in money_hooks): ss += 20
    elif any(w in first_line for w in identity_hooks): ss += 16
    elif len(first_line) > 30: ss += 10

    # 2. Emotional Impact (25)
    em = 0
    names = ["marcus","elena","derek","roberto","james","sandra","tony","keisha",
             "mike","lisa","carlos","david","sarah","jose","maria","john","jennifer",
             "michael","robert","patricia","linda","william","richard","thomas"]
    if any(n in t for n in names): em += 10
    if _re.search(r'\$[\d,]+(?:k|m|\.\d+m?)?', t): em += 8
    counties = ["miami-dade","broward","harris","dallas","palm beach","hillsborough",
                "orange","fulton","maricopa","cook","travis","tarrant","bexar",
                "harris county","dallas county","fulton county","maricopa county",
                "miami","houston","atlanta","chicago","phoenix","los angeles",
                "georgia","florida","texas","arizona","illinois","ohio","pennsylvania",
                "california","new york","north carolina"]
    if any(c in t for c in counties): em += 7
    em = min(25, em)

    # 3. Curiosity (20)
    cu = 0
    loops = [
        # Classic curiosity openers
        "here's what","nobody tells","but here","the real","what actually","turns out",
        "didn't know","i never","most people don't","you won't","they never",
        "what happens","here's why","the truth","what the irs","never told",
        "most advisors","most people miss","nobody talks","here's the thing",
        "what i've seen","the part nobody","what most","had no idea","no idea it",
        "didn't realize","never knew","i talk to","people i talk","did you know",
        "here's something","until someone","until they","before they knew",
        # Myth-bust patterns
        "this myth","doesn't close","closing the business","shifts the target",
        "found a loophole","made it worse","fixable","the internet makes",
        "most people think","they think","assumed","thought it would",
        "doesn't work","won't protect","can't erase","doesn't erase",
        # IRS insider patterns  
        "years inside the irs","i spent","inside the irs","as a revenue officer",
        "when i worked","from inside","i've seen this","seen this destroy",
        # Consequence reveal patterns
        "follows her personally","personally liable","personal assessment",
        "coming after","shifts to you","your wages","your bank account",
        "no matter what","they're coming","personally responsible",
        # Story reveal patterns
        "it didn't","that was wrong","that's not how","except","instead",
        "the problem","the catch","the reality","what actually happened",
        "three months later","months later","weeks later","the next letter",
        # Urgency/stakes
        "tens of thousands","could've avoided","before it's too late",
        "window is closing","running out of","last chance","90 days",
        "between the first","real options","fighting from the ground",
    ]
    cu += min(12, sum(3 for l in loops if l in t))
    if "?" in text: cu += 4
    if any(w in t for w in ["reveals","inside","secret","playbook","behind","never told",
                             "insider","former irs","revenue officer","what agents"]): cu += 4
    cu = min(20, cu)

    # 4. Share Potential (15)
    sh = 0
    share_phrases = [
        # Explicit share asks
        "know someone","forward this","share this","your employee","your contractor",
        "every business owner","every contractor","anyone who","send this","tag a",
        "show this","pass this","tell a contractor","if you know",
        "for anyone who","share with","worth sharing","anyone in",
        "if this helped","save this","bookmark","screenshot this",
        # Comment CTAs Claude actually generates
        "comment help","comment lien","comment below","comment if",
        "drop a","drop your","your exact situation","this is you",
        "i read every","read every one","dm me","reply with",
        "comment below if","comment if you","reply if","type yes",
        # Engagement hooks
        "the business survived","barely","had to lay off","went back to work",
        "your situation","sound familiar","been there","happened to you",
        "relate to this","if this is","if you've","if you have",
        "tag someone","tag a contractor","tag a business",
    ]
    if any(p in t for p in share_phrases): sh += 8
    if any(w in t for w in ["results vary","every case","situation is different","every situation",
                             "unique","not all","depends on","varies by","your situation",
                             "talk to","consult","may vary","can vary"]): sh += 4
    if post_type in {"myth_bust","payroll_tax_trap","contractor_disaster","biggest_mistake",
                     "public_record_breakdown","weekly_lien_leaderboard","irs_story",
                     "bank_levy_story","educational","notice"}: sh += 3
    sh = min(15, sh)

    # 5. Comment Potential (15)
    co = 0
    triggers = ["comment help","comment contractor","comment cp504","comment lien",
                "comment notice","comment checklist","drop your state","drop a",
                "type help","type contractor","type lien","type cp504","type notice",
                "comment below","reply with","dm me","message me","reach out",
                "comment \"help\"","comment \"lien\"","drop \"help\"",
                "comment your state","what state are you in","reply help","reply lien",
                "comment business","comment fta","comment payroll","comment 941",
                "tell me in the comments","let me know in the comments",
                "tag a business owner","tag a contractor","share this with",
                "are you dealing with","have you received"]
    if any(tr in t for tr in triggers): co += 10
    if "?" in text: co += 3
    controversy = ["wrong","terrible advice","bad advice","myth","not true","they never tell",
                   "nobody talks about","most people miss","dangerous advice","misconception",
                   "people think","they don't tell","they never mention"]
    if any(c in t for c in controversy): co += 2
    co = min(15, co)

    total = ss + em + cu + sh + co
    return {
        "total": total,
        "scroll_stop": ss, "emotional_impact": em,
        "curiosity": cu, "share_potential": sh, "comment_potential": co,
    }



def get_performance_context() -> str:
    """Summarize recent winning post patterns so Claude can exploit what works."""
    if not ANALYTICS_FILE.exists():
        return ""
    try:
        log = json.loads(ANALYTICS_FILE.read_text())[-80:]
    except Exception:
        return ""
    if not log:
        return ""
    def avg_by(field: str):
        groups = {}
        for e in log:
            key = e.get(field) or "unknown"
            score = e.get("quality_total", 0) or 0
            groups.setdefault(key, []).append(score)
        return sorted(groups.items(), key=lambda kv: sum(kv[1]) / max(len(kv[1]), 1), reverse=True)[:3]
    best_types = ", ".join(f"{k} ({sum(v)/len(v):.0f})" for k, v in avg_by("post_type"))
    best_states = ", ".join(f"{k} ({sum(v)/len(v):.0f})" for k, v in avg_by("state"))
    if not best_types:
        return ""
    return "\n\nPERFORMANCE FEEDBACK LOOP:\n" + f"Recent highest-scoring post types: {best_types}.\n" + f"Recent strongest states: {best_states}.\n" + "Favor winning patterns without repeating prior copy.\n"

# ── Post generation ────────────────────────────────────────────────────────────
def generate_ai_post(post_type: str, context: dict,
                     platform: str = "facebook") -> tuple[str, dict]:
    """Returns (post_text, viral_scores)"""
    state_key    = context.get("state", "florida")
    state_cfg    = STATES.get(state_key, STATES["florida"])
    state_name   = state_cfg["name"]
    county       = context.get("county", random.choice(state_cfg["counties"]))
    count        = context.get("count", random.randint(10, 50))
    pct_change   = context.get("pct_change", 0)
    largest      = context.get("largest", random.randint(45000, 890000))
    notice       = context.get("notice", get_notice_for_this_week())
    trade        = random.choice(state_cfg.get("contractor_trades", ["contractors"]))
    industry     = random.choice(state_cfg["industries"])
    tone_key, tone_desc = get_tone_for_today()
    emotional_angle = random.choice(EMOTIONAL_ANGLES)
    platform_instr  = PLATFORM_INSTRUCTIONS.get(platform, PLATFORM_INSTRUCTIONS["facebook"])
    state_url    = f"{SITE_URL}{state_cfg['landing']}"
    week_of      = date.today().strftime("%B %d, %Y")
    arch         = pick_archetype()
    comment_cta  = pick_comment_trigger(post_type)

    # Hook context for local hooks
    hook_context = {"county": county, "count": count}

    hook_map = {
        "tax_horror_story":         "horror_story",
        "contractor_disaster":      "horror_story",
        "bank_levy_story":          "horror_story",
        "payroll_tax_trap":         "shocking_fact",
        "biggest_lien_of_the_week": "public_record",
        "public_record_breakdown":  "public_record",
        "weekly_lien_leaderboard":  "public_record",
        "irs_story":                "insider",
        "contractor_confession":    "insider",
        "myth_bust":                "contrarian",
        "biggest_mistake":          "uncomfortable_truth",
        "data_visual":              "shocking_fact",
        "comparison_table":         "contrarian",
        "irs_timeline":             "uncomfortable_truth",
        "weekly_stats":             "shocking_fact",
        "educational":              "shocking_fact",
        "notice":                   "shocking_fact",
        "urgency":                  "uncomfortable_truth",
        "success_story":            "horror_story",
        "story":                    "horror_story",
        "contractor":               "identity",
        "viral_hook":               random.choice(list(HOOKS.keys())),
    }
    hook_category = hook_map.get(post_type, "identity")
    viral_hook    = get_viral_hook(hook_category, hook_context)

    history = load_history()
    avoid = ("\n\nDo NOT open with or resemble:\n" +
             "\n".join(f"- {h}" for h in history[-6:])) if history else ""
    performance_note = get_performance_context()

    persona = (
        f"You are Romy, former IRS Revenue Officer (12 years), now founder of TaxCase Review.\n"
        f"Voice: direct, warm, no-nonsense insider. Like Coffeezilla meets a tax attorney.\n"
        f"NEVER: em dashes, bullet lists, corporate language, \'navigate\', \'crucial\', \'it\'s important to\'.\n"
        f"NEVER begin with explanation. ALWAYS begin with story, emotion, fact, or identity.\n"
        f"Tone: {tone_desc}\n"
        f"{performance_note}"
    )

    trend_note = ""
    if pct_change and abs(pct_change) >= 15:
        d = "up" if pct_change > 0 else "down"
        trend_note = f"Trend: filings are {d} {abs(pct_change)}% vs last week."

    prompts = {

        # ── 40% STORY CONTENT ──────────────────────────────────────────────────

        "tax_horror_story": f"""{persona}
Write a {platform} post — a tax horror story. Bloomberg storytelling + IRS insider accuracy.
Week: {week_of}. State: {state_name}. {state_url} | {PHONE}
Hook (use or riff): "{viral_hook}"
Story: {arch["name"]} — {arch["trade"]} in {arch["county_state"]} — owed {arch["debt"]}.
The problem: {arch["problem"]}.
Walk through: the ignored letters → the lien filing on public record → the bank levy →
the moment it became real → what happened to the business → what options remained.
Make the reader feel the weight of each decision point.
End with: "{comment_cta}"
{platform_instr}
130-160 words. Return ONLY the post text.{avoid}""",

        "contractor_disaster": f"""{persona}
Write a {platform} post — a contractor tax disaster story. Real, uncomfortable, specific.
Week: {week_of}. State: {state_name}. {state_url} | {PHONE}
Hook (use or riff): "{viral_hook}"
Story: {arch["name"]} — {arch["trade"]} in {arch["county_state"]} — owed {arch["debt"]}.
The problem: {arch["problem"]}.
Cover: how it started → the Trust Fund Recovery Penalty → personal liability they didn't know existed →
the bank levy → the lien on their house. Don't soften it.
The lesson must be clear: what they should have done differently.
"Results vary. Every situation is unique."
End with: "{comment_cta}"
{platform_instr}
130-160 words. Return ONLY the post text.{avoid}""",

        "bank_levy_story": f"""{persona}
Write a {platform} post about a bank levy horror story.
Week: {week_of}. State: {state_name}. {state_url} | {PHONE}
Hook (use or riff): "{viral_hook}"
Story: {arch["name"]} — {arch["trade"]} — owed {arch["debt"]}.
Focus on the moment the account was levied: payroll due, fuel card declined, crew waiting.
The 21-day holding period. What options existed. What they did.
"Results vary. Every situation is unique."
End with: "{comment_cta}"
{platform_instr}
120-150 words. Return ONLY the post text.{avoid}""",

        "irs_story": f"""{persona}
Write a {platform} post — IRS insider story from former Revenue Officer perspective.
Week: {week_of}. State: {state_name}. {state_url} | {PHONE}
Hook (use or riff): "{viral_hook}"
Tell a story from inside the IRS. What agents actually look for. What makes them escalate.
What makes them prefer a resolution. A specific case type (anonymized): industry, situation, outcome.
"This is the thing most people never hear — because it never gets published."
End with: "{comment_cta}"
{platform_instr}
130-160 words. Return ONLY the post text.{avoid}""",

        "contractor_confession": f"""{persona}
Write a {platform} post — confession format. "I need to tell contractors something most advisors won't."
Week: {week_of}. State: {state_name}. {state_url} | {PHONE}
Hook (use or riff): "{viral_hook}"
The confession: one critical thing about payroll tax, Trust Fund Recovery Penalty, or personal liability
that contractors consistently don't know until it's too late.
Use {arch["name"]} — {arch["trade"]} — to illustrate it. Specific. Uncomfortable. True.
End with: "{comment_cta}"
{platform_instr}
120-150 words. Return ONLY the post text.{avoid}""",

        "success_story": f"""{persona}
Write a {platform} post with an anonymized client success story from {state_name}.
Week: {week_of}. {state_url} | {PHONE}
Hook (use or riff): "{viral_hook}"
Subject: {arch["name"]} — {arch["trade"]} in {arch["county_state"]} — owed {arch["debt"]}.
The situation before (emotional, specific) → turning point → resolution program + exact dollars saved → after.
Make the opening line the climax, then tell it forward.
"Results vary. Every case is unique."
End with: "{comment_cta}"
{platform_instr}
140-170 words. Return ONLY the post text.{avoid}""",

        "story": f"""{persona}
Write a {platform} anonymized client story.
Week: {week_of}. {state_name}. {state_url} | {PHONE}
Hook (use or riff): "{viral_hook}"
Subject: {arch["name"]} — {arch["trade"]} in {arch["county_state"]}.
The moment it became real → the situation → what happened → specific outcome.
"Results vary. Every case is unique."
End with: "{comment_cta}"
{platform_instr}
130-160 words. Return ONLY the post text.{avoid}""",

        "biggest_mistake": f"""{persona}
Write a {platform} post about the biggest IRS mistake you see people make.
Week: {week_of}. {state_name}. {state_url} | {PHONE}
Hook (use or riff): "{viral_hook}"
{arch["name"]} — {arch["trade"]} — made ONE decision that cost them {arch["debt"]} in options.
What it was. Why people make it. What it actually cost. What to do instead.
Uncomfortable. Specific. True.
End with: "{comment_cta}"
{platform_instr}
120-150 words. Return ONLY the post text.{avoid}""",

        "payroll_tax_trap": f"""{persona}
Write a {platform} post about the payroll tax trap that destroys more contractors than anything else.
Week: {week_of}. {state_name}. {state_url} | {PHONE}
Hook (use or riff): "{viral_hook}"
Exactly how it works: 941 deposits → cash flow temptation → Trust Fund Recovery Penalty →
personal liability even after LLC closes.
{arch["name"]} — {arch["trade"]} — {arch["problem"]} — owed {arch["debt"]}.
Thought the LLC protected them. It didn't.
End with: "{comment_cta}"
{platform_instr}
130-160 words. Return ONLY the post text.{avoid}""",

        # ── 25% PUBLIC RECORD INTELLIGENCE ────────────────────────────────────

        "weekly_stats": f"""{persona}
Write a {platform} post about IRS tax lien activity in {county} County, {state_name}.
Week: {week_of}. Facts: {count} new federal tax liens filed this week. {trend_note}
{state_url} | {PHONE}
Hook (use or riff): "{viral_hook}"
Reframe as people, not statistics: "{count} business owners in {county} County..."
Who these people are (trades, industries). What a lien means for their daily life.
Resolution options that exist. Local CTA.
End with: "{comment_cta}"
{platform_instr}
140-170 words. Return ONLY the post text.{avoid}""",

        "weekly_lien_leaderboard": f"""{persona}
Write a {platform} post — weekly IRS lien activity leaderboard for {state_name}.
Week: {week_of}. {state_url} | {PHONE}
Hook: "This is a matter of public record. Anyone can look this up."
Present top 3 most active counties this week (use real {state_name} counties from: {state_cfg["counties"][:6]}).
Generate realistic lien counts (8-65 range). Show trend (up/down vs last week).
Which industries are showing up most. What it means.
Reframe every number as people: "47 business owners in Harris County..."
End with: "{comment_cta}"
{platform_instr}
150-180 words. Return ONLY the post text.{avoid}""",

        "public_record_breakdown": f"""{persona}
Write a {platform} post breaking down what public IRS lien records actually reveal.
Week: {week_of}. {county} County, {state_name}. {state_url} | {PHONE}
Hook (MUST be horror/story style — NOT "I pulled this from public records"): open with what happened to the person, e.g. "woke up to a text", "their account was frozen", "the lien was already filed".
What a federal tax lien filing looks like → what information is visible to anyone →
what it means for credit, refinancing, property → how long it stays → who can see it.
Most people don't realize this is public. That's the hook.
End with: "{comment_cta}"
{platform_instr}
130-160 words. Return ONLY the post text.{avoid}""",

        "biggest_lien_of_the_week": f"""{persona}
Write a {platform} post about the largest IRS lien filed in {county} County this week.
Week: {week_of}. {state_url} | {PHONE}
Hook: "I pulled this from public records this week."
Largest lien this week: ${largest:,.0f} (use real or estimated amount).
Business type: describe the industry without naming real businesses.
What likely caused it (payroll tax, income tax, trust fund). What options they have now.
"This is sitting in the county recorder's office right now. Anyone can look it up."
End with: "{comment_cta}"
{platform_instr}
130-160 words. Return ONLY the post text.{avoid}""",

        # ── 20% MYTH DESTRUCTION ──────────────────────────────────────────────

        "myth_bust": f"""{persona}
Write a {platform} post that destroys one IRS myth people dangerously believe.
Week: {week_of}. {state_name}. {state_url} | {PHONE}
Hook (use or riff): "{get_viral_hook("contrarian")}"
Pick ONE myth that costs people real money:
- "Closing the LLC makes the tax debt disappear" — FALSE
- "You can't negotiate with the IRS" — FALSE
- "Ignore it and it goes away after 7 years" — WRONG (10 years, and it tolls)
- "Bankruptcy eliminates IRS debt" — MOSTLY FALSE
- "The IRS always takes your house" — RARELY TRUE
{arch["name"]} believed this myth. Show the real consequence.
End with: "{comment_cta}"
{platform_instr}
130-160 words. Return ONLY the post text.{avoid}""",

        "viral_hook": f"""{persona}
Write a {platform} post that STOPS the scroll.
Week: {week_of}. {state_name}. {state_url} | {PHONE}
Hook (use or riff): "{get_viral_hook()}"
Structure: Pattern interrupt → emotional identification → unexpected truth → what to do → CTA.
{arch["name"]} — {arch["trade"]} — illustrates it.
End with: "{comment_cta}"
{platform_instr}
120-150 words. Return ONLY the post text.{avoid}""",

        # ── 15% EDUCATION ─────────────────────────────────────────────────────

        "educational": f"""{persona}
Write an educational {platform} post — ONE IRS insider insight, delivered through a story.
Week: {week_of}. {state_name}. {state_url} | {PHONE}
Hook (use or riff): "{get_viral_hook("shocking_fact", hook_context)}"
Pick ONE: penalty abatement / OIC rate (37%) / lien vs levy / CDP hearing / CNC status / CSED.
Deliver it through {arch["name"]}\'s story — {arch["trade"]} who discovered this too late.
Feel like insider knowledge, not a seminar.
End with: "{comment_cta}"
{platform_instr}
130-160 words. Return ONLY the post text.{avoid}""",

        "notice": f"""{persona}
Write a {platform} post about IRS notice {notice} — what it really means.
Week: {week_of}. {state_name}. {state_url} | {PHONE}
Hook: "Most people don't understand what {notice} actually means. {arch["name"]} didn't."
{arch["name"]} got a {notice}. Walk through: what triggered it → exact deadline →
what happens at each non-response point → one action to take RIGHT NOW.
Make them feel understood before you explain anything.
End with: "{comment_cta}"
{platform_instr}
130-160 words. Return ONLY the post text.{avoid}""",

        "data_visual": f"""{persona}
Write a {platform} post — an IRS stat-driven, text-based infographic.
{platform} does not render HTML or charts, so build the "infographic" out of plain-text
spacing and symbols (│ ─ ▓ █ ▶ • ► ┃ ▸) so it READS like a visual breakdown.
Week: {week_of}. {state_name}. {state_url} | {PHONE}
Hook (use or riff): "{viral_hook}" — the FIRST line must be a single shocking stat.
Use ONLY these real IRS Data Book FY2025 figures (do NOT invent or round differently):
- {IRS_DATA_BOOK_FY2025["nftls_filed"]} new federal tax liens filed
- {IRS_DATA_BOOK_FY2025["oic_acceptance_rate"]} Offer in Compromise acceptance rate
- {IRS_DATA_BOOK_FY2025["new_installment_agmts"]} new installment agreements
- {IRS_DATA_BOOK_FY2025["gross_collections"]} gross collections
- {IRS_DATA_BOOK_FY2025["delinquent_accounts"]} taxpayer delinquent accounts
Structure: shocking stat hook → 2-3 lines of context (what these numbers mean for one
regular taxpayer) → a visual breakdown using spacing/symbols to simulate an infographic
in plain text → a clear CTA.
End with: "{comment_cta}"
{platform_instr}
130-170 words. Return ONLY the post text.{avoid}""",

        "comparison_table": f"""{persona}
Write a {platform} post — a side-by-side comparison of two IRS resolution options.
{platform} does not render HTML tables, so use emoji column headers and short, aligned
text rows so it reads like a clean two-column table on mobile.
Week: {week_of}. {state_name}. {state_url} | {PHONE}
Hook (use or riff): "{viral_hook}"
Pick 2 resolution options (Offer in Compromise, Installment Agreement, Currently Not
Collectible, or Penalty Abatement). Generate a 4-5 row comparison in this exact shape:
✅ Offer in Compromise   | ⚠️ Installment Plan
Settle for less          | Pay full balance
{IRS_DATA_BOOK_FY2025["oic_acceptance_rate"]} approval        | Almost always approved
Takes 6-12 months        | Starts in days
Best if: can't pay       | Best if: can pay over time
Use accurate, real trade-offs. Keep each cell short so the columns line up.
End with EXACTLY: "Which fits your situation? Comment below."
{platform_instr}
120-160 words. Return ONLY the post text.{avoid}""",

        "irs_timeline": f"""{persona}
Write a {platform} post — a chronological escalation story: exactly what happens if you
ignore an IRS notice, from day 1 to collections. Emotional urgency throughout.
Week: {week_of}. {state_name}. {state_url} | {PHONE}
Hook (use or riff): "{viral_hook}"
Format as dated/numbered steps, one emoji per line, using real notice names and real
dollar thresholds where relevant. Shape:
Day 1: CP14 notice arrives 📬
Day 30: CP503 — second notice ⚠️
Day 60: CP504 — intent to levy 🔴
Day 90+: Bank account frozen 🏦
Day 180+: Wage garnishment starts 💸
Make each stage land emotionally — the reader should recognize exactly where they are.
End with EXACTLY: "Which stage are you at? DM me."
{platform_instr}
130-170 words. Return ONLY the post text.{avoid}""",

        "urgency": f"""{persona}
Write a {platform} post about the emotional cost of ignoring IRS debt.
Week: {week_of}. {state_name}. {state_url} | {PHONE}
Emotional angle: {emotional_angle}
Start with the human feeling. NOT a dollar amount. NOT "the longer you wait."
Pivot: most situations are more fixable than people think. One specific action today.
Brief CTA.
End with: "{comment_cta}"
{platform_instr}
110-140 words. Return ONLY the post text.{avoid}""",

        "contractor": f"""{persona}
Write a {platform} post specifically for {state_name} {trade}.
Week: {week_of}. {state_url} | {PHONE}
Hook (use or riff): "{get_viral_hook("identity")}"
One contractor-specific insight:
- Trust Fund Recovery Penalty: personal liability after LLC closes
- IRS can levy your clients directly — send them letters telling them not to pay you
- Quarterly estimated tax traps for self-employed trades
- 1099 contractor tax rate reality vs employees
{arch["name"]} — {arch["trade"]} — illustrates it.
End with: "{comment_cta}"
{platform_instr}
120-150 words. Return ONLY the post text.{avoid}""",
    }

    raw_text = call_claude(prompts.get(post_type, prompts["educational"]))
    scores   = score_post(raw_text, post_type)
    return raw_text, scores


# ── Content laddering (v6 preserved) ──────────────────────────────────────────
def generate_content_ladder(blog_title: str, blog_slug: str, state_key: str) -> dict:
    state_cfg = STATES.get(state_key, STATES["florida"])
    blog_url  = f"{SITE_URL}/blog/md/{blog_slug}"
    prompt    = f"""You are Romy, former IRS officer turned content strategist for TaxCase Review.
Blog post: "{blog_title}"
URL: {blog_url}
State: {state_cfg["name"]}
Phone: {PHONE}
Generate a content ladder in JSON format:
{{
  "facebook_hook": "One-line horror or story hook under 15 words",
  "instagram_caption": "Instagram caption with story hook, emotional arc, CTA under 150 words aggressive line breaks",
  "linkedin_angle": "LinkedIn insight post professional 150 words ends with question",
  "story_post": "Anonymized story post 140 words specific person",
  "myth_bust": "One IRS myth this blog busts post format 130 words",
  "email_subject": "Email subject line under 50 chars curiosity-driven",
  "email_preview": "Preview text under 90 chars",
  "email_snippet": "2-sentence nurture email teaser linking to blog post",
  "hooks": ["5 horror or story hook variations for this topic"],
  "carousel_slide_1": "Scroll-stopping headline under 10 words",
  "carousel_slide_2": "The problem specific situation 1-2 sentences",
  "carousel_slide_3": "The consequence what happens if ignored 1-2 sentences",
  "carousel_slide_4": "The solution overview 1-2 sentences",
  "carousel_slide_5": "Proof or credibility 1-2 sentences cite real data",
  "carousel_cta": "Comment KEYWORD for the free checklist — promise specific resource",
  "youtube_30s": "30-second YouTube Shorts script hook in first 3 words pattern interrupt CTA at end",
  "youtube_60s": "60-second YouTube Shorts script story arc hook problem reveal solution CTA",
  "youtube_90s": "90-second YouTube Shorts mini-documentary hook evidence escalation resolution CTA",
  "comment_magnet": "Identity CTA format: Comment KEYWORD if SPECIFIC SITUATION — I will send SPECIFIC RESOURCE",
  "quick_answer": "40-60 word direct answer optimized for AI Overviews ChatGPT Perplexity — start with a fact, end with an action",
  "ai_summary": "2-sentence AI-optimized summary. Key fact then resolution. Schema-ready language.",
  "howto_goal": "What the reader will accomplish following this article (for HowTo schema)",
  "howto_steps": ["Step 1 — action and outcome", "Step 2", "Step 3", "Step 4"],
  "faq_1": "FAQ question 1 phrased how someone asks Google or ChatGPT",
  "faq_1_answer": "Direct 40-word answer to faq_1",
  "faq_2": "FAQ question 2",
  "faq_2_answer": "Direct 40-word answer to faq_2",
  "faq_3": "FAQ question 3",
  "faq_3_answer": "Direct 40-word answer to faq_3",
  "chart_type": "timeline|bar|comparison|checklist|funnel — which visual best represents the core data in this article",
  "chart_title": "Short chart title (under 60 chars)",
  "chart_data": [{"label": "data point label", "value": "numeric or text value", "color": "red|yellow|green|blue"}],
  "penalty_timeline": [{"day": 0, "event": "event description", "severity": "low|medium|high|critical"}],
  "risk_factors": [{"factor": "risk factor name", "weight": "high|medium|low", "description": "1 sentence"}],
  "resolution_comparison": [{"option": "resolution name", "best_for": "who it fits", "timeline": "timeframe", "success_rate": "percentage or range"}],
  "cluster_county": "County-specific article idea extending this topic",
  "cluster_industry": "Industry-specific article idea roofing HVAC trucking restaurant",
  "cluster_notice": "Related IRS notice article idea",
  "cluster_resolution": "Related resolution program article idea",
  "infographic_concept": "One paragraph description of an infographic visualizing this topic",
  "social_image_headline": "Bold 8-word headline for social share image (all caps, no punctuation)",
  "social_image_subhead": "Supporting 12-word subhead for share image",
  "og_title": "OpenGraph title under 60 chars — keyword first",
  "og_description": "OpenGraph description 140-160 chars — includes primary keyword and CTA",
  "self_assessment_1": "Yes/no checklist item 1 for reader risk self-assessment",
  "self_assessment_2": "Yes/no checklist item 2",
  "self_assessment_3": "Yes/no checklist item 3",
  "self_assessment_4": "Yes/no checklist item 4",
  "self_assessment_5": "Yes/no checklist item 5",
  "funnel_stage": "awareness|consideration|decision — which stage this topic targets",
  "decision_tree_q1": "First yes/no question for a decision tree (e.g. Have you received a CP504?)",
  "decision_tree_a1_yes": "What to do if yes",
  "decision_tree_a1_no": "What to do if no",
  "decision_tree_q2": "Second decision tree question",
  "decision_tree_a2_yes": "What to do if yes",
  "decision_tree_a2_no": "What to do if no",
  "backlink_angle": "One specific angle or stat from this article that another site would want to cite",
  "backlink_target_sites": ["2 types of sites that would naturally link to this article"],
  "entity_list": ["5 named IRS entities, programs, or forms mentioned — for entity-rich AI indexing"],
  "quiz_question_1": "Quiz question a reader can answer to assess their own risk",
  "quiz_question_2": "Second quiz question",
  "quiz_question_3": "Third quiz question",
  "cta_awareness": "CTA copy for awareness-stage readers (educational, low pressure)",
  "cta_consideration": "CTA copy for consideration-stage readers (comparison, options-focused)",
  "cta_decision": "CTA copy for decision-stage readers (urgent, action-oriented)",
  "internal_links": ["3 specific internal page paths to link to from this article e.g. /resolution/offer-in-compromise"],
  "related_topics": ["3 related blog topics this article should link to when they exist"]
}}
Return ONLY valid JSON. No markdown. No preamble."""
    try:
        result = call_claude(prompt, max_tokens=1200)
        clean  = re.sub(r"```json|```", "", result).strip()
        return json.loads(clean)
    except Exception as e:
        return {"error": str(e)}


# ── Make.com webhook ───────────────────────────────────────────────────────────

def format_carousel_post(ladder: dict, state_name: str, url: str) -> str:
    """Format carousel slide content for Facebook/Instagram multi-image post."""
    slides = [
        ladder.get("carousel_slide_1", ""),
        ladder.get("carousel_slide_2", ""),
        ladder.get("carousel_slide_3", ""),
        ladder.get("carousel_slide_4", ""),
        ladder.get("carousel_slide_5", ""),
    ]
    cta = ladder.get("carousel_cta", "")
    parts = ["SWIPE -> (5 slides)"]
    parts += [f"[Slide {i+1}] {s}" for i, s in enumerate(slides) if s]
    parts.append(cta)
    parts.append(f"{url} | {PHONE}")
    return "\n\n".join(p for p in parts if p)


def save_content_package(ladder: dict, topic: str, slug: str, state_key: str,
                          dry_run: bool = False) -> bool:
    """
    Save the full content package from the expanded ladder to GitHub.
    Creates a JSON file alongside the blog post with all derivative content.
    """
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False
    today = date.today().isoformat()
    package = {
        "topic":           topic,
        "slug":            slug,
        "state":           state_key,
        "generated_date":  today,
        "email": {
            "subject":     ladder.get("email_subject", ""),
            "preview":     ladder.get("email_preview", ""),
            "snippet":     ladder.get("email_snippet", ""),
        },
        "youtube": {
            "script_30s":  ladder.get("youtube_30s", ""),
            "script_60s":  ladder.get("youtube_60s", ""),
            "script_90s":  ladder.get("youtube_90s", ""),
        },
        "carousel": {
            "slide_1":     ladder.get("carousel_slide_1", ""),
            "slide_2":     ladder.get("carousel_slide_2", ""),
            "slide_3":     ladder.get("carousel_slide_3", ""),
            "slide_4":     ladder.get("carousel_slide_4", ""),
            "slide_5":     ladder.get("carousel_slide_5", ""),
            "cta":         ladder.get("carousel_cta", ""),
        },
        "ai_search": {
            "quick_answer": ladder.get("quick_answer", ""),
            "ai_summary":   ladder.get("ai_summary", ""),
            "faq": [
                ladder.get("faq_1", ""),
                ladder.get("faq_2", ""),
                ladder.get("faq_3", ""),
            ],
        },
        "comment_magnet":  ladder.get("comment_magnet", ""),
        "self_assessment": [
            ladder.get("self_assessment_1", ""),
            ladder.get("self_assessment_2", ""),
            ladder.get("self_assessment_3", ""),
            ladder.get("self_assessment_4", ""),
            ladder.get("self_assessment_5", ""),
        ],
        "cluster_topics": {
            "county":      ladder.get("cluster_county", ""),
            "industry":    ladder.get("cluster_industry", ""),
            "notice":      ladder.get("cluster_notice", ""),
            "resolution":  ladder.get("cluster_resolution", ""),
        },
        "infographic":     ladder.get("infographic_concept", ""),
        "hooks":           ladder.get("hooks", []),
        "funnel_stage":    ladder.get("funnel_stage", ""),
        "decision_tree": {
            "q1":     ladder.get("decision_tree_q1", ""),
            "a1_yes": ladder.get("decision_tree_a1_yes", ""),
            "a1_no":  ladder.get("decision_tree_a1_no", ""),
            "q2":     ladder.get("decision_tree_q2", ""),
            "a2_yes": ladder.get("decision_tree_a2_yes", ""),
            "a2_no":  ladder.get("decision_tree_a2_no", ""),
        },
        "quiz_questions":  [ladder.get(f"quiz_question_{i}", "") for i in range(1, 4)],
        "cta_variants": {
            "awareness":     ladder.get("cta_awareness", ""),
            "consideration": ladder.get("cta_consideration", ""),
            "decision":      ladder.get("cta_decision", ""),
        },
        "backlink_angle":        ladder.get("backlink_angle", ""),
        "backlink_target_sites": ladder.get("backlink_target_sites", []),
        "entity_list":           ladder.get("entity_list", []),
        "internal_links":        ladder.get("internal_links", []),
        "related_topics":        ladder.get("related_topics", []),
        "collection_topics":     ladder.get("collection_topics", []),
    }
    if dry_run:
        print(f"  [DRY RUN] Content package for: {slug}")
        print(f"    Email subject: {package['email']['subject']}")
        print(f"    Comment magnet: {package['comment_magnet'][:60]}...")
        return True

    try:
        import base64
        file_path = f"content/blog/packages/{slug}.json"
        api_url   = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
        headers   = {"Authorization": f"token {GITHUB_TOKEN}",
                     "Accept": "application/vnd.github.v3+json"}
        sha = None
        try:
            r = requests.get(api_url, headers=headers, timeout=10)
            if r.status_code == 200:
                sha = r.json().get("sha")
        except Exception:
            pass
        encoded = base64.b64encode(json.dumps(package, indent=2).encode()).decode()
        payload = {"message": f"content: {slug} package", "content": encoded}
        if sha:
            payload["sha"] = sha
        r = requests.put(api_url, headers=headers, json=payload, timeout=30)
        if r.status_code in (200, 201):
            print(f"  Content package saved: {slug}.json")
            return True
        else:
            print(f"  Content package save failed: {r.status_code}")
            return False
    except Exception as e:
        print(f"  Content package error: {e}")
        return False


def log_content_suggestions(ladder: dict, topic: str) -> None:
    """Print cluster topic suggestions for future content planning."""
    print(f"\n  Topical Authority Suggestions for: {topic[:50]}")
    for key, label in [
        ("cluster_county",     "County angle"),
        ("cluster_industry",   "Industry angle"),
        ("cluster_notice",     "Notice angle"),
        ("cluster_resolution", "Resolution angle"),
    ]:
        val = ladder.get(key, "")
        if val:
            print(f"    {label}: {val[:70]}")
    faqs = [ladder.get(f"faq_{i}", "") for i in range(1, 4) if ladder.get(f"faq_{i}")]
    if faqs:
        print(f"  🤖 AI Search FAQs:")
        for faq in faqs:
            print(f"    • {faq[:70]}")


def post_via_make(text: str, image_url: str = None,
                  platform: str = "facebook",
                  analytics: dict = None) -> dict:
    if not MAKE_WEBHOOK_URL: return {"error": "no webhook"}
    analytics = analytics or {}
    payload: dict = {
        "message":          text,
        "link":             SITE_URL,
        "reel":             False,
        "platform":         platform,
        "post_quality":     analytics.get("total", 0),
        "scroll_stop":      analytics.get("scroll_stop", 0),
        "emotional_impact": analytics.get("emotional_impact", 0),
        "curiosity":        analytics.get("curiosity", 0),
        "share_potential":  analytics.get("share_potential", 0),
        "comment_potential":analytics.get("comment_potential", 0),
    }
    if image_url:
        payload["image_url"] = image_url
    r = requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=15)
    return {"status": r.status_code, "response": r.text}


# ── Blog publisher (v6 preserved) ─────────────────────────────────────────────
def _build_faq_schema(ladder: dict, topic: str) -> str:
    faqs = []
    for i in range(1, 4):
        q = ladder.get(f"faq_{i}", "")
        a = ladder.get(f"faq_{i}_answer", "")
        if q and a:
            faqs.append({"@type": "Question", "name": q,
                         "acceptedAnswer": {"@type": "Answer", "text": a}})
    if not faqs:
        return ""
    schema = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": faqs}
    return "\n<script type=\"application/ld+json\">\n" + json.dumps(schema, indent=2) + "\n</script>\n"


def _build_howto_schema(ladder: dict, topic: str) -> str:
    goal  = ladder.get("howto_goal", "")
    steps = ladder.get("howto_steps", [])
    if not goal or len(steps) < 2:
        return ""
    schema = {
        "@context": "https://schema.org", "@type": "HowTo",
        "name": topic, "description": goal,
        "step": [{"@type": "HowToStep", "name": s, "text": s} for s in steps],
    }
    return "\n<script type=\"application/ld+json\">\n" + json.dumps(schema, indent=2) + "\n</script>\n"


def _build_chart_mdx(ladder: dict) -> str:
    chart_type  = ladder.get("chart_type", "")
    chart_title = ladder.get("chart_title", "").replace('"', "'")
    timeline    = ladder.get("penalty_timeline", [])
    resolution  = ladder.get("resolution_comparison", [])
    chart_data  = ladder.get("chart_data", [])
    if timeline and chart_type == "timeline":
        return "\n<PenaltyTimeline title=\"" + chart_title + "\" items={" + json.dumps(timeline) + "} />\n"
    elif resolution and chart_type == "comparison":
        return "\n<ResolutionComparison title=\"" + chart_title + "\" options={" + json.dumps(resolution) + "} />\n"
    elif chart_data and chart_type == "bar":
        return "\n<LienBarChart title=\"" + chart_title + "\" data={" + json.dumps(chart_data) + "} />\n"
    return ""


def _build_og_block(ladder: dict, topic: str, slug: str) -> str:
    og_title = ladder.get("og_title", topic[:60]).replace('"', "'")
    og_desc  = ladder.get("og_description", topic + " — Free IRS case review.").replace('"', "'")
    headline = ladder.get("social_image_headline", "").replace('"', "'")
    subhead  = ladder.get("social_image_subhead", "").replace('"', "'")
    out = [f'ogTitle: "{og_title}"', f'ogDescription: "{og_desc}"']
    if headline: out.append(f'socialImageHeadline: "{headline}"')
    if subhead:  out.append(f'socialImageSubhead: "{subhead}"')
    return "\n".join(out)



def _build_internal_links_mdx(ladder: dict) -> str:
    links = ladder.get("internal_links", [])
    if not links: return ""
    items = ["\n## Further Reading"]
    for path in links[:5]:
        path  = path.strip().lstrip("/")
        label = path.replace("-", " ").replace("/", " — ").title()
        items.append(f"- [{label}](/{path})")
    return "\n".join(items) + "\n"


def _build_backlink_block(ladder: dict) -> str:
    angle = ladder.get("backlink_angle", "")
    if not angle: return ""
    return (
        '\n<div className="citation-block bg-muted/50 border rounded p-4 my-6">'
        '\n  <p className="text-sm font-medium text-muted-foreground">Key Statistic</p>'
        f'\n  <p className="text-base">{angle}</p>'
        '\n  <p className="text-xs text-muted-foreground mt-2">Source: IRS Data Book / TaxCase Review Research</p>'
        "\n</div>\n"
    )


def _inject_blog_enhancements(
    article: str, faq_schema: str, howto_schema: str, chart_mdx: str, og_block: str,
    ladder: dict = None
) -> str:
    if og_block and 'authorTitle: "Former IRS Revenue Officers"' in article:
        article = article.replace(
            'authorTitle: "Former IRS Revenue Officers"',
            'authorTitle: "Former IRS Revenue Officers"\n' + og_block
        )
    if chart_mdx and "<RiskMeter" in article:
        article = article.replace(
            '<RiskMeter level="high" />',
            '<RiskMeter level="high" />\n' + chart_mdx
        )
    schemas = faq_schema + howto_schema
    if schemas and "*Results vary." in article:
        article = article.replace("\n---\n*Results vary.", "\n" + schemas + "\n---\n*Results vary.")
    # Inject internal links before TrackedCTA
    if ladder:
        links_mdx = _build_internal_links_mdx(ladder)
        if links_mdx and "<TrackedCTA" in article:
            article = article.replace("<TrackedCTA", links_mdx + "\n<TrackedCTA", 1)
        backlink = _build_backlink_block(ladder)
        if backlink and "## Market Context" in article:
            article = article.replace("## Market Context", backlink + "## Market Context", 1)
    return article



def publish_blog(state_key: str, dry_run: bool = False) -> bool:
    state_cfg = STATES.get(state_key, STATES["florida"])
    topic     = random.choice(state_cfg["blog_topics"])
    slug      = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    # Deduplication: skip already-published slugs
    published  = load_published_slugs()
    if slug in published:
        remaining = [t for t in state_cfg["blog_topics"]
                     if re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-") not in published]
        if not remaining:
            print(f"  All {state_key} topics published — skipping")
            return True
        topic = random.choice(remaining)
        slug  = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
        print(f"  Deduped to: {topic}")
    # original slug line below is now redundant — keep for reference but overwrite:
    slug      = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    today     = date.today().isoformat()
    # Check for duplicate topic
    published_slugs = load_published_slugs()
    if slug in published_slugs:
        # Pick a different topic
        remaining = [t for t in state_cfg["blog_topics"]
                     if re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-") not in published_slugs]
        if not remaining:
            print(f"  All blog topics published for {state_key} — skipping")
            return True
        topic = random.choice(remaining)
        slug  = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
        print(f"  Deduped to: {topic}")

    prompt    = f"""You are Romy, former IRS Revenue Officer, writing for TaxCase Review.
Write a complete, SEO-optimized blog article with embedded MDX components.

TITLE: "{topic}"
STATE: {state_cfg["name"]}
TARGET AUDIENCE: Contractors, small business owners, self-employed professionals with IRS problems.
AUTHOR VOICE: Plain English. Real examples. Direct. Never corporate. No jargon.
MINIMUM LENGTH: 1,200 words of body content.

MANDATORY STRUCTURE (in this exact order):

---
title: "{topic}"
date: "{today}"
slug: "{slug}"
metaDescription: "{topic[:80]} — Former IRS Revenue Officer explains your options. Free case review at taxcasereview.org."
author: "TaxCase Review Editorial Team"
authorTitle: "Former IRS Revenue Officers"
---

<AIRetrievalBlock
  question="{topic}"
  answer="[40-60 word direct answer. Start with a key fact. End with the action to take. Optimized for ChatGPT, Perplexity, Google AI Overviews.]"
/>

## Key Takeaways
- [Takeaway 1 — specific and actionable, include a number or dollar amount]
- [Takeaway 2]
- [Takeaway 3]
- [Takeaway 4]

[OPENING — 150 words: Start with a specific story. Real person (anonymized), real county, real dollar amount, real consequence. NEVER start with a definition or explanation.]

[SECTION 1 — 200 words with H2 header: The core problem. What triggers it. Real IRS mechanism. One specific contractor example.]

[SECTION 2 — 200 words with H2 header: What happens next. The escalation timeline. Specific dates and dollar amounts.]

## Market Context: IRS Enforcement in {state_cfg["name"]}

[100 words: Current IRS enforcement landscape in the state. Reference the trend data — are liens increasing or decreasing? Which industries and counties are seeing the most activity? What does this mean for the reader specifically? Ground it in real publicly available data (IRS Data Book, county recorder stats). This is the "market interpretation" section — connect national IRS data to local reader reality.]

<IRSConsequenceTimeline trigger="{topic[:40]}" />

[SECTION 3 — 200 words with H2 header: The resolution options. Link to /resolution/* pages.]

## What Happens If You Do Nothing

<RiskMeter level="high" />

[100 words on the 30/60/90/180 day consequence timeline. Specific. Urgent without fearmongering.]

## Your Options Right Now

[List 3-4 resolution paths with 1-sentence each. Markdown links to resolution pages.]

## Is This Your Situation?

<SelfAssessmentChecklist
  title="IRS Situation Checklist"
  items={{[
    "[Risk indicator 1]",
    "[Risk indicator 2]",
    "[Risk indicator 3]",
    "[Risk indicator 4]",
    "[Risk indicator 5]",
  ]}}
  cta="See your options — free 60-second assessment"
  ctaHref="/quiz"
/>

## Frequently Asked Questions

[FAQ 1 — phrased how someone asks ChatGPT]
[40-word direct answer]

[FAQ 2]
[40-word answer]

[FAQ 3]
[40-word answer]

## Decision Tree: What Should You Do?

[2-step decision tree. Each step is a yes/no question with a specific action for each answer.
Step 1 question → Yes path → No path.
Step 2 question (based on the more common path from Step 1) → Yes path → No path.
Format as a simple text flow, not a table. Example:
**Have you received a CP504?**
→ Yes: [specific action] → No: [specific action]
This is the most actionable section — make it feel like a personal conversation.]

## Test Your IRS Risk in 3 Questions

1. [Quiz question 1 — yes/no risk indicator]
2. [Quiz question 2]
3. [Quiz question 3]

If you answered yes to 2 or more: [urgency statement + link to /quiz]

## Related Articles
[Leave this section empty — populated automatically by the content system]

<TrackedCTA
  text="Get a Free Case Review"
  href="/quiz"
  location="blog_cta"
  variant="primary"
/>

---
*Results vary. Individual circumstances differ. This is not legal or tax advice. {PHONE} | {SITE_URL}*

Return ONLY the markdown. No preamble. No explanation."""
    try:
        # Classify funnel stage before generating
        funnel_stage = classify_funnel_stage(topic)
        print(f"  Funnel stage: {funnel_stage}")

        blog_content = call_claude(prompt, max_tokens=2000)
        if dry_run:
            print(f"  [DRY RUN] Blog: {topic}"); return True
        
        # Generate content ladder for schema, visuals, and social package
        print(f"  Generating content package...")
        ladder = generate_content_ladder(topic, slug, state_key)
        if ladder:
            # Inject FAQPage + HowTo JSON-LD schema into MDX
            faq_schema  = _build_faq_schema(ladder, topic)
            howto_schema = _build_howto_schema(ladder, topic)
            chart_mdx   = _build_chart_mdx(ladder)
            og_block    = _build_og_block(ladder, topic, slug)
            # Inject schema and visuals into the article content
            content = _inject_blog_enhancements(content, faq_schema, howto_schema, chart_mdx, og_block)
            # Save full content package to GitHub
            save_content_package(ladder, topic, slug, state_key, dry_run=dry_run)
            log_content_suggestions(ladder, topic)

        # Generate content ladder for schema, visuals, and social package
        print(f"  Generating content package...")
        ladder_data = generate_content_ladder(topic, slug, state_key)
        if ladder_data:
            faq_schema   = _build_faq_schema(ladder_data, topic)
            howto_schema = _build_howto_schema(ladder_data, topic)
            chart_mdx    = _build_chart_mdx(ladder_data)
            og_block     = _build_og_block(ladder_data, topic, slug)
            blog_content = _inject_blog_enhancements(blog_content, faq_schema, howto_schema, chart_mdx, og_block, ladder=ladder_data)
            # Score content quality before publishing
            quality = score_content_quality(topic, blog_content, ladder_data, funnel_stage)
            print(f"  Content quality: {quality['total']}/100 "
                  f"(SEO={quality['dimensions'].get('seo',0)} "
                  f"AI={quality['dimensions'].get('ai_search',0)} "
                  f"Eng={quality['dimensions'].get('engagement',0)} "
                  f"Conv={quality['dimensions'].get('conversion',0)} "
                  f"EEAT={quality['dimensions'].get('eeat',0)})")
            if not quality["pass"] and not dry_run:
                print(f"  Quality {quality['total']} < {QUALITY_THRESHOLD} — regenerating...")
                blog_content = call_claude(prompt, max_tokens=2000)
                quality = score_content_quality(topic, blog_content, ladder_data, funnel_stage)
                print(f"  Retry quality: {quality['total']}/100")
            if quality["hints"]:
                print(f"  Hints: {quality['hints']}")

            # Add funnel-specific CTA from ladder
            cta_key = f"cta_{funnel_stage}"
            cta_text = ladder_data.get(cta_key, "Get a Free Case Review")
            if cta_text and "<TrackedCTA" in blog_content:
                blog_content = blog_content.replace(
                    'text="Get a Free Case Review"',
                    f'text="{cta_text[:60]}"',
                    1
                )

            save_content_package(ladder_data, topic, slug, state_key, dry_run=dry_run)
            log_content_suggestions(ladder_data, topic)

            # Add collection topics to package
            coll_topics = classify_collection_topics(topic)
            if coll_topics:
                print(f"  Collection topics: {coll_topics}")

        file_path = f"{BLOG_CONTENT_PATH}/{slug}.md"
        api_url   = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
        headers   = {"Authorization": f"token {GITHUB_TOKEN}",
                     "Accept": "application/vnd.github.v3+json"}
        sha = None
        try:
            check = requests.get(api_url, headers=headers, timeout=10)
            if check.status_code == 200: sha = check.json().get("sha")
        except Exception: pass
        payload = {"message": f"Blog: {slug} [{today}]",
                   "content": base64.b64encode(blog_content.encode()).decode(),
                   "branch":  GITHUB_BRANCH}
        if sha: payload["sha"] = sha
        r = requests.put(api_url, headers=headers, json=payload, timeout=30)
        if r.status_code in (200, 201):
            save_published_slug(slug)
            print(f"  Blog published: {SITE_URL}/blog/md/{slug}")
            # IndexNow ping — submits new URL to Bing/Yandex for near-instant crawl
            try:
                indexnow_url = f"{SITE_URL}/blog/md/{slug}"  # canonical public path (/blog/{slug} 404s)
                indexnow_payload = {
                    "host": "taxcasereview.org",
                    "key": "9e9b2e673445719e87ed5e2213724841",
                    "keyLocation": "https://taxcasereview.org/9e9b2e673445719e87ed5e2213724841.txt",
                    "urlList": [indexnow_url]
                }
                r = requests.post(
                    "https://api.indexnow.org/indexnow",
                    json=indexnow_payload,
                    headers={"Content-Type": "application/json"},
                    timeout=10
                )
                print(f"  IndexNow ping: {r.status_code} — {indexnow_url}")
            except Exception as e:
                print(f"  IndexNow ping failed (non-blocking): {e}")
            # Update collection pages with this new blog post
            try:
                update_collection_pages(topic, slug, state_key, {}, dry_run=dry_run)
            except Exception as e:
                print(f"  Collection update error (non-blocking): {e}")
            return True
        print(f"  Blog publish failed: {r.status_code}"); return False
    except Exception as e:
        print(f"  Blog error: {e}"); return False



# ── Collection page detection ──────────────────────────────────────────────────
TRADE_KEYWORDS = {
    "roofing":            ["roofing", "roofer", "roof contractor"],
    "hvac":               ["hvac", "air conditioning", "heating", "cooling"],
    "trucking":           ["trucking", "trucker", "freight", "carrier", "cdl"],
    "restaurant":         ["restaurant", "food service", "cafe", "diner", "eatery"],
    "electricians":       ["electrician", "electrical contractor", "electrical"],
    "general-contractor": ["general contractor", "construction", "builder", "gc "],
}

NOTICE_KEYWORDS = ["cp504","lt11","cp14","cp503","cp2000","lt16","notice","letter 1058",
                   "letter 3172","letter 1153","lt38"]

RESOLUTION_KEYWORDS = ["offer in compromise","installment agreement","penalty abatement",
                        "lien withdrawal","currently not collectible","cnc","tfrp",
                        "trust fund recovery","wage garnishment","bank levy"]

STATE_COLLECTION_MAP = {
    "florida":        "florida",      "texas":    "texas",
    "georgia":        "georgia",      "arizona":  "arizona",
    "california":     "california",   "new york": "new-york",
    "north carolina": "north-carolina","illinois": "illinois",
    "ohio":           "ohio",         "pennsylvania": "pennsylvania",
}

COLLECTION_PAGE_PATHS = {
    # trade → Next.js app path
    "roofing":            "app/contractors/roofing/page.tsx",
    "hvac":               "app/contractors/hvac/page.tsx",
    "trucking":           "app/contractors/trucking/page.tsx",
    "restaurant":         "app/contractors/restaurant/page.tsx",
    "electricians":       "app/contractors/electricians/page.tsx",
    "general-contractor": "app/contractors/general-contractor/page.tsx",
    # topic → Next.js app path
    "notice":             "app/irs-notices/page.tsx",
    "resolution":         "app/resolution/page.tsx",
    "research":           "app/research/page.tsx",
}


def detect_collection(topic: str, state_key: str) -> list:
    """
    Detect which collection pages a blog post belongs to.
    Returns list of collection keys e.g. ["roofing", "florida"]
    """
    t = topic.lower()
    collections = []

    for trade, keywords in TRADE_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            collections.append(trade)

    if any(kw in t for kw in NOTICE_KEYWORDS):
        collections.append("notice")

    if any(kw in t for kw in RESOLUTION_KEYWORDS):
        collections.append("resolution")

    state_cfg = STATES.get(state_key, {})
    state_name = state_cfg.get("name", "").lower()
    if state_name and (state_name in t or state_key in t):
        collections.append(state_key)

    return list(set(collections))


def _github_get_file(file_path: str) -> tuple:
    """Fetch a file from GitHub. Returns (content_str, sha) or (None, None)."""
    if not GITHUB_TOKEN:
        return None, None
    url     = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}",
               "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            import base64
            data = r.json()
            text = base64.b64decode(data["content"]).decode("utf-8")
            return text, data["sha"]
        return None, None
    except Exception:
        return None, None


def _github_push_file(file_path: str, content_str: str, sha: str = None,
                      commit_msg: str = "update: collection page") -> bool:
    """Push a file to GitHub. sha required for updates, None for new files."""
    if not GITHUB_TOKEN:
        print("  GITHUB_TOKEN not set — cannot push collection page")
        return False
    import base64
    url     = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}",
               "Accept": "application/vnd.github.v3+json"}
    payload = {
        "message": commit_msg,
        "content": base64.b64encode(content_str.encode()).decode(),
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    try:
        r = requests.put(url, headers=headers, json=payload, timeout=30)
        return r.status_code in (200, 201)
    except Exception as e:
        print(f"  GitHub push error: {e}")
        return False


def _indexnow_ping(url: str):
    """Ping IndexNow for a collection page URL."""
    try:
        payload = {
            "host":        "taxcasereview.org",
            "key":         "9e9b2e673445719e87ed5e2213724841",
            "keyLocation": "https://taxcasereview.org/9e9b2e673445719e87ed5e2213724841.txt",
            "urlList":     [url],
        }
        r = requests.post("https://api.indexnow.org/indexnow",
                          json=payload,
                          headers={"Content-Type": "application/json"},
                          timeout=10)
        print(f"  IndexNow collection ping: {r.status_code} — {url}")
    except Exception as e:
        print(f"  IndexNow collection ping failed: {e}")


def _append_related_article(page_content: str, blog_title: str,
                             blog_slug: str) -> str:
    """
    Append a new related article link to the Related Articles section
    of an existing collection page. If section doesn't exist, adds it.
    """
    link_line = f'  - [{blog_title}]({SITE_URL}/blog/md/{blog_slug})'

    if "## Related Articles" in page_content:
        # Append after the last item in Related Articles
        idx = page_content.rfind("## Related Articles")
        # Find end of that section (next ## or end of file)
        next_section = page_content.find("\n## ", idx + 20)
        if next_section == -1:
            return page_content.rstrip() + "\n" + link_line + "\n"
        else:
            return (page_content[:next_section].rstrip()
                    + "\n" + link_line
                    + page_content[next_section:])
    else:
        related = "\n\n## Related Articles\n" + link_line + "\n"
        if "<TrackedCTA" in page_content:
            return page_content.replace(
                "<TrackedCTA",
                related + "\n<TrackedCTA",
                1
            )
        return page_content.rstrip() + related


def update_collection_pages(topic: str, slug: str, state_key: str,
                             ladder: dict, dry_run: bool = False) -> list:
    """
    Detect which collection pages this blog post belongs to,
    then append the new post as a related article link in each one.
    Returns list of collection keys that were updated.
    """
    collections = detect_collection(topic, state_key)
    if not collections:
        return []

    print(f"  Collections detected: {collections}")
    updated = []

    for coll_key in collections:
        file_path = COLLECTION_PAGE_PATHS.get(coll_key)
        if not file_path:
            # State collection — use state landing page
            state_cfg = STATES.get(coll_key, {})
            landing   = state_cfg.get("landing", f"/{coll_key}").lstrip("/")
            file_path = f"app/{landing}/page.tsx"

        print(f"  Updating collection: {coll_key} → {file_path}")

        if dry_run:
            print(f"  [DRY RUN] Would append '{topic}' to {file_path}")
            updated.append(coll_key)
            continue

        # Fetch existing page
        page_content, sha = _github_get_file(file_path)
        if page_content is None:
            print(f"  Collection page not found: {file_path} — skipping")
            continue

        # Append the new blog post link
        updated_content = _append_related_article(page_content, topic, slug)

        # Push back to GitHub
        ok = _github_push_file(
            file_path, updated_content, sha=sha,
            commit_msg=f"content: add '{topic[:60]}' to {coll_key} collection"
        )
        if ok:
            print(f"  Collection updated: {coll_key}")
            updated.append(coll_key)
            # Ping IndexNow with the collection page URL
            collection_url = f"{SITE_URL}/{file_path.replace('app/', '').replace('/page.tsx', '')}"
            _indexnow_ping(collection_url)
        else:
            print(f"  Collection update failed: {coll_key}")

    return updated


def generate_collection_page(trade: str, state_key: str,
                              dry_run: bool = False) -> bool:
    """
    Generate a brand new trade+state collection page.
    e.g. --collection roofing --state florida
    Creates: app/florida/contractors/roofing/page.tsx
    Targeting: "Florida roofing company IRS tax lien help"
    """
    state_cfg  = STATES.get(state_key, {})
    state_name = state_cfg.get("name", state_key.title())
    trade_label = {
        "roofing":            "Roofing Contractors",
        "hvac":               "HVAC Companies",
        "trucking":           "Trucking Companies",
        "restaurant":         "Restaurant Owners",
        "electricians":       "Electricians",
        "general-contractor": "General Contractors",
    }.get(trade, trade.title())

    trade_issues = {
        "roofing":            "payroll tax deposits (Form 941) and seasonal cash flow gaps",
        "hvac":               "quarterly 941 deposits and Trust Fund Recovery Penalty exposure",
        "trucking":           "payroll tax, Heavy Vehicle Use Tax (HVUT), and owner-operator liability",
        "restaurant":         "tip income reporting, payroll trust fund liability, and 941 deposits",
        "electricians":       "1099 worker misclassification and Trust Fund Recovery Penalty",
        "general-contractor": "payroll tax across multiple subcontractors and TFRP personal liability",
    }.get(trade, "IRS tax debt and federal tax liens")

    slug      = f"{state_key}-{trade}-irs-tax-lien-help"
    file_path = f"app/{state_key.replace('_','-')}/contractors/{trade}/page.tsx"
    page_url  = f"{SITE_URL}/{state_key.replace('_','-')}/contractors/{trade}"

    prompt = f"""You are Romy, former IRS Revenue Officer, writing for TaxCase Review.
Generate a complete Next.js page component for a trade+state collection page.

Trade: {trade_label}
State: {state_name}
Target keyword: "{state_name} {trade_label.lower()} IRS tax lien help"
URL: {page_url}

The page must:
1. Export default function with proper Next.js metadata export
2. Include these imports at the top:
   import AuthorBox from "@/components/authority/AuthorBox"
   import RiskMeter from "@/components/conversion/RiskMeter"
   import IRSConsequenceTimeline from "@/components/conversion/IRSConsequenceTimeline"
   import AIRetrievalBlock from "@/components/authority/AIRetrievalBlock"
   import TrackedCTA from "@/components/analytics/TrackedCTA"

3. Export metadata:
   title: "{state_name} {trade_label} IRS Tax Help | TaxCase Review"
   description: "Federal tax lien help for {state_name} {trade_label.lower()}. Former IRS Revenue Officers explain your options. Free case review."

4. Page sections in order:
   - H1: "{state_name} {trade_label}: IRS Tax Lien Help"
   - AuthorBox
   - AIRetrievalBlock with Q about {trade_label.lower()} IRS issues and A about {trade_issues}
   - 200 word intro specific to {state_name} {trade_label.lower()} and IRS enforcement
   - RiskMeter level="high"
   - H2: "Why {state_name} {trade_label} Face Higher IRS Enforcement"
   - 200 words on the specific tax issues: {trade_issues}
   - IRSConsequenceTimeline trigger="{trade} IRS lien"
   - H2: "Your Resolution Options"
   - 150 words listing OIC, installment agreement, penalty abatement, CNC with links to /resolution/* pages
   - H2: "Related Articles" with 3 placeholder links (use # as href, labeled Coming Soon)
   - TrackedCTA text="Get a Free Case Review" href="/quiz" location="contractor_collection"
   - FAQPage JSON-LD schema with 3 {trade_label.lower()}-specific questions and answers

5. 800+ total words, no generic IRS content — everything specific to {trade_label} in {state_name}

Return ONLY the TypeScript component code. No explanation."""

    print(f"  Generating collection page: {file_path}")
    page_content = call_claude(prompt, max_tokens=3000)

    if dry_run:
        print(f"  [DRY RUN] Generated {len(page_content)} chars for {file_path}")
        print(f"  Preview: {page_content[:200]}")
        return True

    # Check if file exists
    existing, sha = _github_get_file(file_path)
    ok = _github_push_file(
        file_path, page_content, sha=sha,
        commit_msg=f"feat: {state_name} {trade_label} collection page"
    )

    if ok:
        print(f"  Collection page created: {page_url}")
        _indexnow_ping(page_url)
        return True
    else:
        print(f"  Collection page push failed")
        return False


# ── Analytics logging ─────────────────────────────────────────────────────────
def log_post(post_type: str, tone: str, state: str, county: str,
             platform: str, text: str, make_ok: bool,
             hook_category: str = "", scores: dict = None):
    log = []
    if ANALYTICS_FILE.exists():
        try: log = json.loads(ANALYTICS_FILE.read_text())
        except: log = []
    scores = scores or {}
    log.append({
        "date":              date.today().isoformat(),
        "week":              date.today().isocalendar()[1],
        "post_type":         post_type,
        "tone":              tone,
        "state":             state,
        "county":            county,
        "platform":          platform,
        "hook_category":     hook_category,
        "preview":           text[:100],
        "sent":              make_ok,
        "char_count":        len(text),
        "quality_total":     scores.get("total", 0),
        "scroll_stop":       scores.get("scroll_stop", 0),
        "emotional_impact":  scores.get("emotional_impact", 0),
        "curiosity":         scores.get("curiosity", 0),
        "share_potential":   scores.get("share_potential", 0),
        "comment_potential": scores.get("comment_potential", 0),
    })
    ANALYTICS_FILE.write_text(json.dumps(log[-300:], indent=2))


def show_performance_summary():
    if not ANALYTICS_FILE.exists():
        print("No analytics yet."); return
    try: log = json.loads(ANALYTICS_FILE.read_text())
    except: print("Could not load analytics."); return
    if not log: print("No posts logged."); return
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  Social Post Performance — {len(log)} posts tracked")
    print(sep)
    def best_by(field):
        grouped: dict = {}
        for entry in log:
            k = entry.get(field, "?")
            v = entry.get("quality_total", 0)
            grouped.setdefault(k, []).append(v)
        if not grouped: return "n/a"
        return max(grouped, key=lambda k: sum(grouped[k])/len(grouped[k]))
    print(f"  Best post type   : {best_by('post_type')}")
    print(f"  Best hook cat    : {best_by('hook_category')}")
    print(f"  Best state       : {best_by('state')}")
    scores = [e.get("quality_total",0) for e in log]
    avg    = sum(scores)/len(scores) if scores else 0
    below  = sum(1 for s in scores if s < QUALITY_THRESHOLD)
    print(f"\n  Avg quality score: {avg:.1f}/100")
    print(f"  Below threshold  : {below}/{len(log)} posts")
    print(f"{sep}\n")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    ALL_POST_TYPES = [
        # v6 preserved
        "weekly-stats","educational","notice","urgency","success-story",
        "blog","story","myth-bust","contractor","viral-hook",
        # v7 new
        "contractor-disaster","tax-horror-story","biggest-mistake",
        "public-record-breakdown","weekly-lien-leaderboard","contractor-confession",
        "irs-story","bank-levy-story","payroll-tax-trap","biggest-lien-of-the-week",
        # v9 new
        "data-visual","comparison-table","irs-timeline",
    ]

    parser = argparse.ArgumentParser(description="TaxCase Review Social Poster v9 (Content Authority Engine)")
    parser.add_argument("--auto",               action="store_true")
    parser.add_argument("--blog-only",          action="store_true")
    parser.add_argument("--ladder",             action="store_true")
    parser.add_argument("--post",               choices=ALL_POST_TYPES)
    parser.add_argument("--state",              default=None, choices=list(STATES.keys()))
    parser.add_argument("--notice",             default=None)
    parser.add_argument("--hook-cat",           default=None, choices=list(HOOKS.keys()))
    parser.add_argument("--platform",           default="facebook",
                        choices=["facebook","linkedin","instagram","gbp"])
    parser.add_argument("--dry-run",            action="store_true")
    parser.add_argument("--force",              action="store_true",
                        help="Post even if quality score < 85")
    parser.add_argument("--performance-summary",action="store_true")
    parser.add_argument("--image-status",       action="store_true",
                        help="Show image library usage (per-category counts, fresh vs recently used)")
    parser.add_argument("--score",              action="store_true",
                        help="Score a blog topic before generating")
    parser.add_argument("--blog",               action="store_true",
                        help="Publish a blog post for the given state")
    parser.add_argument("--collection",         default=None,
                        choices=["roofing","hvac","trucking","restaurant",
                                 "electricians","general-contractor"],
                        help="Generate a trade+state collection page")
    parser.add_argument("--rebuild-collections", action="store_true",
                        help="Create or refresh all automated core collection pages from manifests")
    args = parser.parse_args()

    BLOGS_DIR.mkdir(exist_ok=True)
    tone_key, _ = get_tone_for_today()
    state_key   = args.state or get_state_for_this_week()
    state_cfg   = STATES[state_key]
    week_num    = date.today().isocalendar()[1]

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  TaxCase Review Social Poster v9 (Content Authority Engine)")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    print(f"  State: {state_cfg['name']} | Tone: {tone_key}")
    print(f"  Quality threshold: {QUALITY_THRESHOLD}/100")
    if args.dry_run: print("  DRY RUN")
    print(f"{sep}\n")

    if args.image_status:
        show_image_status(); return

    if args.performance_summary:
        show_performance_summary(); return

    if args.rebuild_collections:
        ok = rebuild_core_collections(dry_run=args.dry_run)
        print(f"  Core collections rebuilt: {ok}")
        return

    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("social_post"); logger.start()
    except Exception:
        logger = None

    if args.collection:
        print(f"  Generating collection page: {args.collection} × {state_key}")
        ok = generate_collection_page(args.collection, state_key, dry_run=args.dry_run)
        if logger:
            logger.finish({"collection_generated": ok,
                           "trade": args.collection, "state": state_key})
        return

    if args.blog_only or args.blog:
        if logger: logger.step_start("publish_blog")
        ok = publish_blog(state_key, dry_run=args.dry_run)
        if logger:
            logger.step_done("publish_blog", ok=ok)
            logger.finish({"blog_published": ok, "state": state_key,
                           "funnel_stage": classify_funnel_stage(
                               STATES.get(state_key, {}).get("blog_topics", [""])[0]
                           )})
        return

    type_map = {
        "weekly-stats":             "weekly_stats",
        "educational":              "educational",
        "notice":                   "notice",
        "urgency":                  "urgency",
        "success-story":            "success_story",
        "blog":                     "weekly_stats",
        "story":                    "story",
        "myth-bust":                "myth_bust",
        "contractor":               "contractor",
        "viral-hook":               "viral_hook",
        "contractor-disaster":      "contractor_disaster",
        "tax-horror-story":         "tax_horror_story",
        "biggest-mistake":          "biggest_mistake",
        "public-record-breakdown":  "public_record_breakdown",
        "weekly-lien-leaderboard":  "weekly_lien_leaderboard",
        "contractor-confession":    "contractor_confession",
        "irs-story":                "irs_story",
        "bank-levy-story":          "bank_levy_story",
        "payroll-tax-trap":         "payroll_tax_trap",
        "biggest-lien-of-the-week": "biggest_lien_of_the_week",
        "data-visual":              "data_visual",
        "comparison-table":         "comparison_table",
        "irs-timeline":             "irs_timeline",
    }

    if args.auto:
        post_type = get_post_type_for_today()
        print(f"Auto -> {state_cfg['name']} / {post_type}\n")
    elif args.post:
        post_type = type_map[args.post]
    else:
        post_type = "tax_horror_story"

    def build_context(skey):
        """Assemble the prompt context for a state (with cross-script county dedupe)."""
        scfg = STATES.get(skey, STATES["florida"])
        st   = get_weekly_lien_stats(skey)
        ctx  = {
            "state":      skey,
            "county":     st.get("county", random.choice(scfg["counties"])),
            "count":      st.get("count", 0),
            "last_week":  st.get("last_week"),
            "pct_change": st.get("pct_change", 0),
            "largest":    st.get("largest", random.randint(45000, 890000)),
            "notice":     args.notice or get_notice_for_this_week(),
        }
        # If the reel script already posted this county today, pick another so
        # the two engines don't overlap.
        if HAS_SHARED and si.is_duplicate_today("social", ctx["county"]):
            alts = [c for c in scfg["counties"]
                    if c.lower() != str(ctx["county"]).lower()]
            if alts:
                new_county = random.choice(alts)
                print(f"  Cross-script dedupe: {ctx['county']} already posted today "
                      f"by reel — switching to {new_county}")
                ctx["county"] = new_county
        return ctx, scfg

    if logger: logger.step_start("generate_post")

    # Quality gate with up to 3 attempts. Attempt 1 = today's pick; retry 1 =
    # same state with a different post type; retry 2 = force Florida (which has
    # real lien data and consistently scores higher). Take the first attempt
    # that meets QUALITY_THRESHOLD; only log a quality rejection if all three
    # fall short. --force skips retries and posts the first attempt.
    all_types   = list(dict.fromkeys(type_map.values()))
    alts1       = [t for t in all_types if t != post_type]
    retry1_type = random.choice(alts1) if alts1 else post_type
    plan = [(post_type, state_key), (retry1_type, state_key)]
    if state_key != "florida":
        plan.append((post_type, "florida"))
    else:
        alts2 = [t for t in all_types if t not in (post_type, retry1_type)]
        plan.append((random.choice(alts2) if alts2 else post_type, "florida"))
    if args.force:
        plan = [(post_type, state_key)]

    ctx_cache = {}
    text = scores = None
    for i, (a_type, a_state) in enumerate(plan, 1):
        if a_state not in ctx_cache:
            ctx_cache[a_state] = build_context(a_state)
        context, state_cfg   = ctx_cache[a_state]
        post_type, state_key = a_type, a_state
        label = "Generating" if i == 1 else f"Retry {i - 1}:"
        print(f"{label} {a_type} post for {state_cfg['name']} ({args.platform})...\n")
        text, scores = generate_ai_post(a_type, context, platform=args.platform)
        if args.force or scores["total"] >= QUALITY_THRESHOLD:
            break
        print(f"  Score {scores['total']}/100 < {QUALITY_THRESHOLD} — retrying...\n")

    image = get_image_for_post(post_type)

    # All attempts below threshold → log a quality rejection (generation worked,
    # the post was just too weak) so the daily summary shows it distinctly.
    if scores["total"] < QUALITY_THRESHOLD and not args.force:
        print(f"  All {len(plan)} attempts < {QUALITY_THRESHOLD} "
              f"(last {scores['total']}/100). Use --force to post anyway.")
        if not args.dry_run:
            if logger:
                logger.step_done(
                    "generate_post", ok=True,
                    detail=f"{state_cfg['name']} | {post_type} | score {scores['total']}/100")
                logger.finish({
                    "post_type": post_type,
                    "state":     state_key,
                    "county":    context["county"],
                    "platform":  args.platform,
                    "sent":      False,
                    "quality":   scores["total"],
                    "threshold": QUALITY_THRESHOLD,
                    "reason":    "below_quality_threshold",
                    "attempts":  len(plan),
                }, status="quality_rejected")
            return

    if already_posted(text):
        print("Similar post in history — regenerating...\n")
        text, scores = generate_ai_post(post_type, context, platform=args.platform)

    if logger: logger.step_done("generate_post", ok=True, detail=f"{state_cfg['name']} | {post_type}")

    dsep = "-" * 60
    print(f"{dsep}")
    print(text)
    print(f"{dsep}")
    print(f"\nViral Score: {scores['total']}/100 | scroll={scores['scroll_stop']} "
          f"emotional={scores['emotional_impact']} curiosity={scores['curiosity']} "
          f"share={scores['share_potential']} comment={scores['comment_potential']}")
    print(f"Image: {image}")
    print(f"State: {SITE_URL}{state_cfg['landing']}")
    print(f"Week:  {week_num}\n")

    if args.dry_run:
        print("Dry run — not sent.\n")
        if logger: logger.finish({"post_type": post_type, "state": state_key, "sent": False})
        return

    if logger: logger.step_start("post_to_make")

    result  = post_via_make(text, image_url=image, platform=args.platform, analytics=scores)
    make_ok = result.get("status") == 200
    print(f"Make.com: {result}")

    if make_ok:
        save_to_history(text)
        log_post(post_type, tone_key, state_key, context["county"],
                 args.platform, text, make_ok=True,
                 hook_category=hook_map if isinstance(hook_map := args.hook_cat, str) else post_type,
                 scores=scores)
        if HAS_SHARED:
            # Record today's angle so the reel script avoids duplicating it.
            try:
                si.record_daily_content("social", post_type, context["county"],
                                        "", state_key)
            except Exception:
                pass
            # Log high-performing posts as content opportunities.
            if scores.get("total", 0) > 85:
                try:
                    opp_topic = f"{post_type} — {state_cfg['name']} / {context['county']}"
                    si.log_content_opportunity(opp_topic, "social",
                                               scores.get("total", 0),
                                               f"{SITE_URL}{state_cfg['landing']}")
                    print(f"  Content opportunity logged (score {scores.get('total',0)})")
                except Exception as e:
                    print(f"  Content opportunity log failed (non-blocking): {e}")

    if logger:
        logger.step_done("post_to_make", ok=make_ok)
        logger.finish({
            "post_type":    post_type,
            "state":        state_key,
            "county":       context["county"],
            "platform":     args.platform,
            "sent":         make_ok,
            "chars":        len(text),
            "quality":      scores["total"],
        })


# ── v9.1 Safe Collection Automation + Authority Layer ─────────────────────────
def _safe_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _blog_public_url(slug: str) -> str:
    return f"{SITE_URL}/blog/md/{slug}"


def _collection_manifest_path(collection_key: str) -> str:
    return f"{COLLECTION_MANIFEST_PATH}/{collection_key}.json"


def _load_collection_manifest(collection_key: str) -> dict:
    file_path = _collection_manifest_path(collection_key)
    content, _sha = _github_get_file(file_path)
    if content:
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                data.setdefault("articles", [])
                return data
        except Exception:
            pass
    meta = COLLECTION_META.get(collection_key, {})
    return {
        "collection": collection_key,
        "title": meta.get("title", collection_key.replace("-", " ").title()),
        "url": meta.get("url", f"/{collection_key}"),
        "updated": date.today().isoformat(),
        "articles": [],
    }


def _save_collection_manifest(collection_key: str, manifest: dict, dry_run: bool = False) -> bool:
    manifest["updated"] = date.today().isoformat()
    manifest["articles"] = manifest.get("articles", [])[-80:]
    file_path = _collection_manifest_path(collection_key)
    if dry_run:
        print(f"  [DRY RUN] Would save manifest: {file_path} ({len(manifest['articles'])} articles)")
        return True
    _content, sha = _github_get_file(file_path)
    return _github_push_file(file_path, json.dumps(manifest, indent=2), sha=sha,
                             commit_msg=f"content: update {collection_key} collection manifest")


def _upsert_collection_article(collection_key: str, topic: str, slug: str,
                               state_key: str, ladder: dict | None = None,
                               dry_run: bool = False) -> dict:
    ladder = ladder or {}
    manifest = _load_collection_manifest(collection_key)
    article = {
        "title": topic,
        "slug": slug,
        "url": _blog_public_url(slug),
        "state": state_key,
        "date": date.today().isoformat(),
        "funnel_stage": ladder.get("funnel_stage") or classify_funnel_stage(topic),
        "quick_answer": ladder.get("quick_answer", ""),
        "comment_magnet": ladder.get("comment_magnet", ""),
    }
    existing = [a for a in manifest.get("articles", []) if a.get("slug") != slug]
    existing.insert(0, article)
    manifest["articles"] = existing[:80]
    _save_collection_manifest(collection_key, manifest, dry_run=dry_run)
    return manifest


def _authority_source_block() -> str:
    links = []
    for src in AUTHORITY_SOURCES:
        links.append(f'<li><a href="{src["url"]}" target="_blank" rel="noopener noreferrer">{src["name"]}</a> — {src["label"]}</li>')
    return "\n".join(links)


def _tsx_escape(value: str) -> str:
    return (value or "").replace("`", "'").replace("${", "\\${").replace("</", "<\\/")


def _build_collection_page_tsx(collection_key: str, manifest: dict) -> str:
    meta = COLLECTION_META.get(collection_key, {})
    title = _tsx_escape(meta.get("title", manifest.get("title", collection_key.replace("-", " ").title())))
    desc = _tsx_escape(meta.get("description", f"TaxCase Review guides for {title}."))
    h1 = _tsx_escape(meta.get("h1", title))
    quick = _tsx_escape(meta.get("quick", "TaxCase Review explains IRS collection problems in plain English using former IRS officer experience and public-source research."))
    articles = manifest.get("articles", [])[:24]
    parts = []
    for i, a in enumerate(articles):
        a_title = _tsx_escape(a.get("title", "Related guide"))
        a_url = _tsx_escape(a.get("url", "#"))
        a_quick = _tsx_escape((a.get("quick_answer") or "Related TaxCase Review guide.")[:220])
        a_stage = _tsx_escape(a.get("funnel_stage", "guide"))
        parts.append(f'''          <li className="rounded-lg border border-slate-200 bg-white p-4" key="{i}">
            <a className="font-semibold text-slate-900 hover:underline" href="{a_url}">{a_title}</a>
            <p className="mt-2 text-sm text-slate-600">{a_quick}</p>
            <span className="mt-3 inline-block rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600">{a_stage}</span>
          </li>''')
    article_jsx = "\n".join(parts) or '''          <li className="rounded-lg border border-slate-200 bg-white p-4">
            <p className="font-semibold text-slate-900">Related guides are being added.</p>
            <p className="mt-2 text-sm text-slate-600">This collection updates automatically as new TaxCase Review content is published.</p>
          </li>'''
    schema = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": title,
        "description": desc,
        "url": f"{SITE_URL}{meta.get('url', '/' + collection_key)}",
        "isPartOf": {"@type": "WebSite", "name": "TaxCase Review", "url": SITE_URL},
    }
    schema_json = json.dumps(schema).replace("</", "<\\/")
    source_block = _tsx_escape(_authority_source_block())
    return f'''import type {{ Metadata }} from "next"

export const metadata: Metadata = {{
  title: "{title} | TaxCase Review",
  description: "{desc}",
}}

export default function Page() {{
  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <script type="application/ld+json" dangerouslySetInnerHTML={{{{ __html: `{schema_json}` }}}} />
      <p className="mb-3 text-sm font-semibold uppercase tracking-wide text-blue-700">TaxCase Review Collection</p>
      <h1 className="text-4xl font-bold tracking-tight text-slate-950">{h1}</h1>
      <p className="mt-4 max-w-3xl text-lg text-slate-700">{quick}</p>
      <section className="mt-8 rounded-2xl border border-blue-100 bg-blue-50 p-6">
        <h2 className="text-2xl font-semibold text-slate-950">Start Here</h2>
        <p className="mt-3 text-slate-700">Use this page to understand the issue, compare possible resolution paths, and decide whether you need a professional case review.</p>
        <a href="/quiz" className="mt-5 inline-flex rounded-lg bg-blue-700 px-5 py-3 font-semibold text-white hover:bg-blue-800">See My IRS Options</a>
      </section>
      <section className="mt-10">
        <h2 className="text-2xl font-semibold text-slate-950">Related Guides</h2>
        <p className="mt-2 text-slate-600">This section is automatically updated by the content engine as new related articles are published.</p>
        <ul className="mt-5 grid gap-4 md:grid-cols-2">
{{/* AUTO_RELATED_ARTICLES_START */}}
{article_jsx}
{{/* AUTO_RELATED_ARTICLES_END */}}
        </ul>
      </section>
      <section className="mt-10 rounded-2xl border border-slate-200 p-6">
        <h2 className="text-2xl font-semibold text-slate-950">Sources and Review Standards</h2>
        <p className="mt-3 text-slate-700">TaxCase Review content is written for education and reviewed against IRS source material, public guidance, and practical collection experience.</p>
        <ul className="mt-4 list-disc space-y-2 pl-6 text-sm text-slate-700" dangerouslySetInnerHTML={{{{ __html: `{source_block}` }}}} />
      </section>
    </main>
  )
}}
'''


def ensure_collection_page(collection_key: str, manifest: dict, dry_run: bool = False) -> bool:
    meta = COLLECTION_META.get(collection_key)
    if not meta:
        print(f"  No core collection template for {collection_key}; manifest only.")
        return False
    file_path = meta["path"]
    generated = _build_collection_page_tsx(collection_key, manifest)
    if dry_run:
        print(f"  [DRY RUN] Would create/update collection page: {file_path}")
        return True
    existing, sha = _github_get_file(file_path)
    if existing is None:
        ok = _github_push_file(file_path, generated, sha=None, commit_msg=f"feat: create {collection_key} collection page")
    elif "AUTO_RELATED_ARTICLES_START" in existing:
        ok = _github_push_file(file_path, generated, sha=sha, commit_msg=f"content: refresh {collection_key} collection page")
    else:
        print(f"  Existing custom page has no auto markers; skipped direct edit: {file_path}")
        print("  Manifest was updated. Add AUTO_RELATED_ARTICLES markers once if you want direct page updates.")
        return False
    if ok:
        _indexnow_ping(f"{SITE_URL}{meta.get('url', '/' + collection_key)}")
        print(f"  Collection page added/updated: {file_path}")
    return ok


def update_collection_pages(topic: str, slug: str, state_key: str, ladder: dict | None = None, dry_run: bool = False) -> list:
    ladder = ladder or {}
    collections = set(classify_collection_topics(topic))
    for trade_key in detect_collection(topic, state_key):
        if trade_key in TRADE_KEYWORDS:
            collections.add("contractor-tax")
        elif trade_key == "notice":
            collections.add("irs-notices")
        elif trade_key == "resolution":
            collections.add("tax-resolution")
    if state_key:
        collections.add("state-tax")
    collections.add("tax-resolution")
    print(f"  Auto collections detected: {sorted(collections)}")
    updated = []
    for key in sorted(collections):
        manifest = _upsert_collection_article(key, topic, slug, state_key, ladder=ladder, dry_run=dry_run)
        page_ok = ensure_collection_page(key, manifest, dry_run=dry_run)
        updated.append(key + ("+page" if page_ok else "+manifest"))
    return updated


def _inject_authority_sources(article: str) -> str:
    if "## Sources" in article or "## IRS Sources" in article or "## IRS Sources and Review Notes" in article:
        return article
    source_lines = "\n".join(f"- [{src['name']}]({src['url']}) — {src['label']}" for src in AUTHORITY_SOURCES)
    block = f"\n\n## IRS Sources and Review Notes\n\n{source_lines}\n\nTaxCase Review uses these sources for general educational context. Your facts, deadlines, and resolution options may differ.\n"
    if "<TrackedCTA" in article:
        return article.replace("<TrackedCTA", block + "\n<TrackedCTA", 1)
    return article.rstrip() + block


def publish_blog(state_key: str, dry_run: bool = False) -> bool:
    state_cfg = STATES.get(state_key, STATES["florida"])
    published_slugs = load_published_slugs()
    remaining = [t for t in state_cfg["blog_topics"] if _safe_slug(t) not in published_slugs]
    if not remaining:
        print(f"  All blog topics published for {state_key} — skipping")
        return True
    topic = random.choice(remaining)
    slug = _safe_slug(topic)
    today = date.today().isoformat()
    funnel_stage = classify_funnel_stage(topic)
    collections = classify_collection_topics(topic)
    prompt = f"""You are Romy, former IRS Revenue Officer, writing for TaxCase Review.
Write a complete, SEO-optimized blog article with embedded MDX components.

TITLE: "{topic}"
STATE: {state_cfg["name"]}
FUNNEL STAGE: {funnel_stage}
COLLECTIONS: {', '.join(collections)}
TARGET AUDIENCE: Contractors, small business owners, self-employed professionals with IRS problems.
AUTHOR VOICE: Plain English. Real examples. Direct. Never corporate. No jargon.
MINIMUM LENGTH: 1,400 words of body content.

FACTUALITY RULES:
- Do not invent lien counts, average lien balances, rankings, or exact filing volumes unless supplied in verified data.
- Use qualified language for local trends when exact data is unavailable.
- Cite IRS concepts generally and avoid fake statistics.
- Include "Results vary" once.

MANDATORY STRUCTURE:
---
title: "{topic}"
date: "{today}"
slug: "{slug}"
metaDescription: "{topic[:80]} — Former IRS Revenue Officer explains your options. Free case review at taxcasereview.org."
author: "TaxCase Review Editorial Team"
authorTitle: "Former IRS Revenue Officers"
funnelStage: "{funnel_stage}"
collections: "{','.join(collections)}"
---

<AIRetrievalBlock
  question="{topic}"
  answer="[40-60 word direct answer. Start with a key fact. End with the action to take. Optimized for ChatGPT, Perplexity, Google AI Overviews.]"
/>

## Key Takeaways
- [Specific, actionable takeaway]
- [Specific, actionable takeaway]
- [Specific, actionable takeaway]
- [Specific, actionable takeaway]

[OPENING: Start with a specific anonymized story. Realistic county, trade, and consequence. Never start with a definition.]

## What This Problem Usually Means
[Explain the IRS mechanism in plain English.]

## What Happens Next
[Escalation timeline. Use qualified language, not fabricated counts.]

## Market Context: {state_cfg['name']} Tax Problems
[Explain common local industries and risks without inventing exact statistics.]

<IRSConsequenceTimeline trigger="{topic[:40]}" />

## Resolution Options
[Compare installment agreement, OIC, penalty abatement, CNC, lien/levy options where relevant. Link naturally to resolution pages.]

## What Happens If You Do Nothing

<RiskMeter level="high" />

[30/60/90/180-day consequence explanation. Urgent, not fearmongering.]

## Is This Your Situation?

<SelfAssessmentChecklist
  title="IRS Situation Checklist"
  items={{[
    "[Risk indicator 1]",
    "[Risk indicator 2]",
    "[Risk indicator 3]",
    "[Risk indicator 4]",
    "[Risk indicator 5]",
  ]}}
  cta="See your options — free 60-second assessment"
  ctaHref="/quiz"
/>

## Frequently Asked Questions

[FAQ 1 phrased how someone asks Google or ChatGPT]
[40-word direct answer]

[FAQ 2]
[40-word direct answer]

[FAQ 3]
[40-word direct answer]

<TrackedCTA
  text="Get a Free Case Review"
  href="/quiz"
  location="blog_cta"
  variant="primary"
/>

---
*Results vary. Individual circumstances differ. This is not legal or tax advice. {PHONE} | {SITE_URL}*

Return ONLY the markdown. No preamble. No explanation."""
    try:
        print(f"  Generating blog: {topic}")
        blog_content = call_claude(prompt, max_tokens=2600)
        print("  Generating content package...")
        ladder = generate_content_ladder(topic, slug, state_key)
        if ladder and not ladder.get("error"):
            blog_content = _inject_blog_enhancements(blog_content, _build_faq_schema(ladder, topic), _build_howto_schema(ladder, topic), _build_chart_mdx(ladder), _build_og_block(ladder, topic, slug))
            log_content_suggestions(ladder, topic)
        else:
            ladder = {}
        blog_content = _inject_authority_sources(blog_content)
        quality = score_content_quality(topic, blog_content, ladder, funnel_stage)
        print(f"  Blog quality: {quality['total']}/100 | {quality['dimensions']}")
        if quality["total"] < BLOG_QUALITY_THRESHOLD:
            print(f"  ⚠ Blog quality below {BLOG_QUALITY_THRESHOLD}. Hints: {quality.get('hints', [])}")
            if not dry_run:
                print("  Not publishing. Improve prompt/output or lower BLOG_QUALITY_THRESHOLD intentionally.")
                return False
        if dry_run:
            print(f"  [DRY RUN] Blog: {topic}")
            print(f"  [DRY RUN] Would publish: {BLOG_CONTENT_PATH}/{slug}.md")
            update_collection_pages(topic, slug, state_key, ladder, dry_run=True)
            return True
        save_content_package(ladder, topic, slug, state_key, dry_run=False)
        file_path = f"{BLOG_CONTENT_PATH}/{slug}.md"
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        sha = None
        try:
            check = requests.get(api_url, headers=headers, timeout=10)
            if check.status_code == 200:
                sha = check.json().get("sha")
        except Exception:
            pass
        payload = {"message": f"Blog: {slug} [{today}]", "content": base64.b64encode(blog_content.encode()).decode(), "branch": GITHUB_BRANCH}
        if sha:
            payload["sha"] = sha
        r = requests.put(api_url, headers=headers, json=payload, timeout=30)
        if r.status_code not in (200, 201):
            print(f"  Blog publish failed: {r.status_code} {r.text[:200]}")
            return False
        save_published_slug(slug)
        blog_url = _blog_public_url(slug)
        print(f"  Blog published: {blog_url}")
        try:
            _indexnow_ping(blog_url)
        except Exception as e:
            print(f"  IndexNow ping failed (non-blocking): {e}")
        updated = update_collection_pages(topic, slug, state_key, ladder, dry_run=False)
        print(f"  Collections updated: {updated}")
        # Surface high-priority, un-actioned content opportunities into the
        # pipeline log so winning topics get turned into more content.
        if HAS_SHARED:
            try:
                opps = [o for o in si.load_content_opportunities()
                        if (o.get("priority") or 0) >= 85]
                if opps:
                    print(f"  High-priority content opportunities ({len(opps)}):")
                    for o in opps[:5]:
                        print(f"    - [{o.get('priority')}] {str(o.get('topic',''))[:60]} "
                              f"({o.get('source','')}) {o.get('url','')}")
            except Exception as e:
                print(f"  Content opportunities check failed (non-blocking): {e}")
        return True
    except Exception as e:
        print(f"  Blog error: {e}")
        return False


def rebuild_core_collections(dry_run: bool = False) -> bool:
    ok_all = True
    for key in COLLECTION_META:
        manifest = _load_collection_manifest(key)
        ok = ensure_collection_page(key, manifest, dry_run=dry_run)
        ok_all = ok_all and (ok or dry_run)
    return ok_all


if __name__ == "__main__":
    main()
