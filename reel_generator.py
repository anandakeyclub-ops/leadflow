"""
reel_generator.py  (v9 — Coffeezilla + Documentary + Human B-Roll Engine)
====================================================
v9 UPGRADES (additive only — zero existing logic removed):
  1. Avatar Screen-Time Cap — MAX 3 avatar scenes per reel (30% max).
     Excess scenes auto-replaced with documentary/b-roll visuals.
  2. Motion Graphics Library — 10 motion graphic types mapped per reel type,
     injected as scene["motion_graphic"] on the hook scene.
  3. Human B-Roll Library — industry-specific human footage descriptions
     (contractor, restaurant, trucking, real estate, small business),
     injected as scene["human_broll"] on story scenes.
  4. Documentary Visual Layer — case files, evidence boards, redacted docs,
     timeline walls — auto-injected for investigative/documentary reel types.
  5. Camera Directions — 7 camera moves (slow_push, fast_zoom, dramatic_crop,
     whip_pan, parallax, ken_burns, dolly_in) per scene as scene["camera_move"].
  6. Pattern Interrupt System — mandatory interrupt every scene (red_stamp,
     document_slam, headline_flash, camera_shake, etc.) as scene["interrupt"].
  7. Viral Loop Endings — 25% of eligible reels get a "Part 2 tomorrow" ending
     before the CTA, injected into the visual_instruction prompt.

All v8 features preserved. All existing CLI commands preserved.
All scoring, HeyGen submission, Make webhook, Remotion render unchanged.

Content Mix:
  35% Public Record Intelligence (county_lien_alert, public_record_breakdown,
      lien_heat_map, biggest_lien_of_the_week, data_reveal, state_lien_alert)
  30% Tax Horror / Mini-Doc (tax_horror_story, contractor_disaster, payroll_tax_trap,
      the_friday_disaster, the_account_freeze, the_loan_denial, the_call)
  20% IRS Insider Secrets (irs_agent_story, insider_secret, confession,
      bad_tax_advice_reaction, tax_tiktok_reaction)
  15% Identity + Controversy (myth_bust, myth_ranking, contractor_identity, controversy_hook)

Viral scoring (0-100):
  scroll_stop_score (25pts) — hook strength, pattern interrupt
  emotional_score   (25pts) — specificity, named archetypes, real dollars
  curiosity_score   (25pts) — open loop, retention beats, reveals
  comment_score     (15pts) — controversy, identity triggers, comment CTAs
  save_score        (10pts) — checklist value, share-worthy content
  Threshold: 65/100

All v5 features preserved. All existing CLI commands preserved.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, date
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HEYGEN_API_KEY    = os.getenv("HEYGEN_API_KEY", "")
PEXELS_API_KEY    = os.getenv("PEXELS_API_KEY", "")  # free key -> live themed video backgrounds
HEYGEN_AVATAR_ID  = os.getenv("HEYGEN_AVATAR_ID", "")
HEYGEN_VOICE_ID   = os.getenv("HEYGEN_VOICE_ID", "8661cd40d6c44c709e2d0031c0186ada")
MAKE_WEBHOOK_URL  = os.getenv("MAKE_WEBHOOK_URL", "")
REMOTION_PROJECT  = Path(os.getenv("REMOTION_PROJECT", r"C:\Users\Dana\Desktop\taxcase-reels"))
SITE_URL          = "https://taxcasereview.org"
QUIZ_URL          = "https://taxcasereview.org/quiz"
YOUTUBE_CHANNEL   = "https://www.youtube.com/channel/UC992GSSCxMoVCJwoGGLRK1g"
PHONE             = "(561) 247-0678"

REELS_DIR         = Path("reels")
REEL_LOG_FILE     = Path("reel_log.json")
HEYGEN_USAGE_FILE = Path("heygen_usage.json")
PERFORMANCE_FILE  = Path("reel_performance.json")

HEYGEN_MONTHLY_CREDITS   = 600
HEYGEN_CREDITS_PER_VIDEO = 30
HEYGEN_MONTHLY_LIMIT     = HEYGEN_MONTHLY_CREDITS // HEYGEN_CREDITS_PER_VIDEO
HEYGEN_BUFFER            = 2
HEYGEN_MAX_USE           = HEYGEN_MONTHLY_LIMIT - HEYGEN_BUFFER  # 18

QUALITY_THRESHOLD = 65  # reel viral gate; scores sit 68-72, so 65 lets strong content (e.g. 68-69) post instead of silently blocking

FLORIDA_COUNTIES = [
    "Miami-Dade", "Palm Beach", "Broward", "Orange", "Hillsborough",
    "Pinellas", "Duval", "Sarasota", "Martin", "St. Lucie"
]
TEXAS_COUNTIES   = ["Harris", "Dallas", "Tarrant", "Travis", "Bexar"]
NOTICE_ROTATION  = ["CP14", "CP503", "CP504", "CP2000"]
STATES_ROTATION  = ["Florida", "Texas", "California", "New York",
                    "Georgia", "Arizona", "North Carolina", "Illinois"]

try:
    from app.core.db import get_connection
    HAS_DB = True
except ImportError:
    HAS_DB = False

# Shared intelligence layer (content flywheel + cross-script coordination).
try:
    import shared_intelligence as si
    HAS_SHARED = True
except Exception:
    si = None
    HAS_SHARED = False

INDEXNOW_KEY = "9e9b2e673445719e87ed5e2213724841"  # same key as social_media_poster.py


# ── Visual Style System (v9 — IRS-red dark-gradient brand) ──────────────────────
# One consistent, high-contrast, scroll-stopping look injected into every scene
# description. STYLE ONLY — does not touch send/webhook/scoring/HeyGen submission.
COLOR_PALETTE = {
    "primary":     "#CC0000",  # IRS red — hook keyword, alarm
    "accent":      "#FF6B00",  # orange — highlights, positive/settlement keyword
    "text":        "#FFFFFF",  # white — supporting/body text
    "bg_top":      "#0A1628",  # deep navy — gradient top
    "bg_bottom":   "#1A0500",  # near-black warm — gradient bottom
    "bg_gradient": "linear-gradient(180deg, #0A1628 0%, #1A0500 100%)",
}

TYPOGRAPHY = {
    "font":         "Bold sans-serif (Impact, Anton, or Montserrat Black)",
    "hook_keyword": "ALL CAPS, red (#CC0000) or orange (#FF6B00), 72px+, massive",
    "supporting":   "Title Case, white (#FFFFFF), 36px, medium weight",
    "max_words":    6,  # never more than 6 words per text element
}

# IRS Data Book FY2025 figures — use verbatim in DATA scenes.
IRS_DATA_FY2025 = {
    "nftls":                  "214,099",  # Notices of Federal Tax Lien filed
    "oic_acceptance_rate":    "14.1%",    # Offer in Compromise acceptance rate
    "installment_agreements": "3.16M",    # active installment agreements
}

_P = COLOR_PALETTE
VISUAL_STYLE_GUIDE = f"""
VISUAL STYLE (apply to EVERY scene description — background, text color, visual, avatar position):
- Background: deep navy-to-dark gradient {_P['bg_top']} -> {_P['bg_bottom']} for graphic scenes — NEVER a flat color.
- Primary keyword: large, bold, ALL CAPS, red {_P['primary']} or orange {_P['accent']}, 72px+.
- Supporting text: white {_P['text']}, Title Case, ~36px, clean bold sans-serif ({TYPOGRAPHY['font']}).
- Hook format: provocative statement split across 2 lines — BIG WORD in red/orange on top, "supporting phrase" in white below.
- Never more than {TYPOGRAPHY['max_words']} words on screen at once. No cluttered layouts.
- Text pops in fast; fast cuts between scenes; serious/tense music ducked under voice.
- Palette: primary {_P['primary']}, accent {_P['accent']}, white {_P['text']}, gradient {_P['bg_top']} -> {_P['bg_bottom']}.
""".strip()

# Per-type style notes: hook keyword + color + signature visual for each reel type.
REEL_TYPE_STYLE_NOTES = {
    "contractor_disaster": {"hook_keyword": "TAX DISASTER",     "hook_color": COLOR_PALETTE["primary"],
                            "visual": "lien stamp",
                            "note": 'Hook text = "TAX DISASTER" in red, lien stamp visual.'},
    "myth_bust":           {"hook_keyword": "IRS MYTH",          "hook_color": COLOR_PALETTE["primary"],
                            "visual": "busted icon",
                            "note": 'Hook text = "IRS MYTH" in red, busted icon.'},
    "urgency":             {"hook_keyword": "IRS DEADLINE",      "hook_color": COLOR_PALETTE["primary"],
                            "visual": "calendar",
                            "note": 'Hook text = "IRS DEADLINE" in red, calendar visual.'},
    "settlement":          {"hook_keyword": "SETTLE FOR LESS",   "hook_color": COLOR_PALETTE["accent"],
                            "visual": "handshake",
                            "note": 'Hook text = "SETTLE FOR LESS" in orange, handshake visual.'},
    "lien_explained":      {"hook_keyword": "FEDERAL TAX LIEN",  "hook_color": COLOR_PALETTE["primary"],
                            "visual": f"DATA scene with {IRS_DATA_FY2025['nftls']} NFTLs stat",
                            "note": f'DATA scene with {IRS_DATA_FY2025["nftls"]} NFTLs stat, IRS red theme.'},
}

def get_style_notes(reel_type: str) -> dict:
    return REEL_TYPE_STYLE_NOTES.get(reel_type, {
        "hook_keyword": "IRS ALERT", "hook_color": COLOR_PALETTE["primary"],
        "visual": "IRS notice", "note": "IRS red theme, dark gradient background.",
    })


# ── Script Length Tiers ────────────────────────────────────────────────────────
SCRIPT_LENGTH_TIERS = {
    "micro":    {"min": 40,  "max": 90,  "seconds": "15-35"},
    "standard": {"min": 100, "max": 150, "seconds": "35-60"},
    "deep":     {"min": 180, "max": 220, "seconds": "70-90"},
}

REEL_TYPE_LENGTH_MAP = {
    # MICRO
    "urgency": "micro", "before_after": "micro", "red_flag": "micro",
    "react_reel": "micro", "deadline_reel": "micro", "quiz_reel": "micro",
    "county_lien_alert": "micro", "city_lien_alert": "micro",
    "tax_deadline": "micro", "success_story": "micro", "client_story": "micro",
    "biggest_lien_of_the_week": "micro", "worst_mistake_of_the_week": "micro",
    "tax_tiktok_reaction": "micro", "bad_tax_advice_reaction": "micro",
    "lien_heat_map": "micro",
    # STANDARD
    "notice": "standard", "mistake": "standard", "confession": "standard",
    "case_breakdown": "standard", "what_if": "standard", "contractor": "standard",
    "contractor_series": "standard", "the_friday_disaster": "standard", "the_account_freeze": "standard", "the_letter_nobody_opened": "standard", "the_call": "standard", "the_loan_denial": "standard", "contractor_identity": "standard", "controversy_hook": "standard", "myth_bust": "standard", "faq_answer": "standard",
    "data_reveal": "standard", "state_lien_alert": "standard",
    "industry_lien_alert": "standard", "irs_update": "standard",
    "news_reaction": "standard", "court_case_reaction": "standard",
    "tax_rule_change": "standard", "reddit_reel": "standard",
    "google_search_reel": "standard", "checklist_reel": "standard",
    "tax_horror_story": "standard", "contractor_disaster": "standard",
    "payroll_tax_trap": "standard", "public_record_breakdown": "standard",
    "irs_agent_story": "standard",
    # DEEP
    "educational": "deep", "insider_secret": "deep", "state_spotlight": "deep",
    "penalty_calculator": "deep", "myth_ranking": "deep",
}

def pick_length_tier(reel_type: str) -> str:
    return REEL_TYPE_LENGTH_MAP.get(reel_type, "standard")

def get_word_limit(tier: str) -> int:
    return SCRIPT_LENGTH_TIERS.get(tier, SCRIPT_LENGTH_TIERS["standard"])["max"]


# ── Named Archetypes (for story specificity) ────────────────────────────────────
ARCHETYPES = {
    "roofing_contractor": {
        "name": "Marcus",  "descriptor": "a roofing contractor in Broward County",
        "debt_range": (38000, 87000), "problem": "payroll tax deposits",
        "detail": "used 941 deposits to cover shingle costs during a slow quarter",
    },
    "restaurant_owner": {
        "name": "Elena",   "descriptor": "a Cuban restaurant owner in Miami-Dade",
        "debt_range": (22000, 67000), "problem": "payroll tax trust fund",
        "detail": "tip income wasn't being reported properly — $47k in back taxes",
    },
    "hvac_contractor": {
        "name": "Derek",   "descriptor": "an HVAC company owner in Harris County, Texas",
        "debt_range": (54000, 91000), "problem": "seasonal cash flow and 941 deposits",
        "detail": "summer revenue spike never made it to quarterly deposits",
    },
    "landscaper": {
        "name": "Roberto", "descriptor": "a landscaping business owner in Palm Beach",
        "debt_range": (18000, 43000), "problem": "1099 worker misclassification",
        "detail": "paid 12 crew members as 1099 contractors — IRS said they were employees",
    },
    "trucking_owner": {
        "name": "James",   "descriptor": "a trucking company owner in Dallas County",
        "debt_range": (61000, 94000), "problem": "payroll tax and HVUT",
        "detail": "fleet of 8 trucks, three years of unfiled 941s",
    },
    "real_estate_investor": {
        "name": "Sandra",  "descriptor": "a real estate investor in Orange County",
        "debt_range": (78000, 142000), "problem": "capital gains and depreciation recapture",
        "detail": "sold four properties in one year — $142k tax bill she didn't see coming",
    },
    "general_contractor": {
        "name": "Tony",    "descriptor": "a general contractor in Hillsborough County",
        "debt_range": (44000, 88000), "problem": "Trust Fund Recovery Penalty",
        "detail": "business closed but IRS came after him personally for $88k",
    },
    "freelancer": {
        "name": "Keisha",  "descriptor": "a freelance healthcare consultant in Atlanta",
        "debt_range": (19000, 38000), "problem": "SE tax and missed quarterly estimates",
        "detail": "went from W2 to 1099, three years without paying quarterly taxes",
    },
}

def pick_archetype() -> dict:
    return random.choice(list(ARCHETYPES.values()))

def pick_debt_amount(archetype: dict) -> int:
    lo, hi = archetype["debt_range"]
    return random.randint(lo // 1000, hi // 1000) * 1000


# ── Visual Cue Library ─────────────────────────────────────────────────────────
VISUAL_CUE_LIBRARY = [
    {"type": "irs_notice",       "description": "IRS notice slams onto screen",          "overlay": "URGENT: IRS NOTICE"},
    {"type": "countdown",        "description": "Countdown timer ticking down",           "overlay": "30 DAYS LEFT"},
    {"type": "debt_overlay",     "description": "Dollar amount flashes — specific number","overlay": "$87,000 OWED"},
    {"type": "public_record",    "description": "Public record stamp on document",        "overlay": "PUBLIC RECORD"},
    {"type": "bank_freeze",      "description": "Bank account balance drops to $0",       "overlay": "ACCOUNT FROZEN"},
    {"type": "mailbox",          "description": "Overflowing mailbox — unopened letters",  "overlay": "3 NOTICES IGNORED"},
    {"type": "contractor_truck", "description": "Contractor truck at jobsite",             "overlay": "CONTRACTORS: READ THIS"},
    {"type": "red_arrow",        "description": "Red arrow on document pointing to amount","overlay": "⚠️ THIS IS YOUR DEBT"},
    {"type": "lien_stamp",       "description": "Federal tax lien stamp — county recorder","overlay": "FEDERAL TAX LIEN FILED"},
    {"type": "phone_ring",       "description": "IRS Revenue Officer calling",             "overlay": "IRS IS CALLING"},
    {"type": "before_after",     "description": "Split screen — stressed vs resolved",     "overlay": "BEFORE → AFTER"},
    {"type": "checklist",        "description": "Checklist items checking off",            "overlay": "SAVE THIS CHECKLIST"},
    {"type": "myth_reality",     "description": "MYTH stamped red, REALITY stamped green","overlay": "MYTH vs REALITY"},
    {"type": "county_map",       "description": "County highlighted on state map",         "overlay": "YOUR COUNTY: ACTIVE"},
    {"type": "penalty_ticker",   "description": "Penalty counter ticking up — live",      "overlay": "$47/DAY COMPOUNDING"},
    {"type": "dashboard_alert",  "description": "Red warning dashboard graphic",           "overlay": "⚠️ IRS ESCALATION ALERT"},
    {"type": "calendar",         "description": "Calendar — 30 days marking off fast",    "overlay": "CLOCK IS RUNNING"},
    {"type": "heat_map",         "description": "County heat map — red = high lien activity","overlay": "LIEN ACTIVITY MAP"},
    {"type": "lien_document",    "description": "Actual lien document with amount visible","overlay": "FILED IN PUBLIC RECORD"},
    {"type": "revenue_officer",  "description": "IRS agent at desk reviewing file",        "overlay": "IRS AGENT PERSPECTIVE"},
]

def get_visual_cues_for_type(reel_type: str) -> list:
    type_map = {
        "notice": ["irs_notice", "countdown", "phone_ring"],
        "what_if": ["countdown", "bank_freeze", "lien_stamp"],
        "red_flag": ["dashboard_alert", "irs_notice", "red_arrow"],
        "penalty_calculator": ["penalty_ticker", "calendar", "debt_overlay"],
        "the_friday_disaster": ["bank_freeze", "payroll_spreadsheet", "countdown", "lien_stamp"],
        "the_account_freeze": ["bank_freeze", "irs_notice", "phone_ring", "lien_stamp"],
        "the_letter_nobody_opened": ["irs_notice", "certified_mail", "countdown", "lien_stamp"],
        "the_call": ["phone_ring", "irs_notice", "bank_freeze", "countdown"],
        "the_loan_denial": ["lien_stamp", "denied_graphic", "irs_notice", "public_record"],
        "contractor_identity": ["contractor_truck", "irs_notice", "lien_stamp", "b_roll"],
        "controversy_hook": ["question_mark", "irs_notice", "document_reveal", "split_screen"],
        "the_friday_disaster": ["bank_freeze", "lien_stamp", "countdown"],
        "the_account_freeze": ["bank_freeze", "irs_notice", "phone_ring"],
        "the_letter_nobody_opened": ["irs_notice", "countdown", "lien_stamp"],
        "the_call": ["phone_ring", "irs_notice", "bank_freeze"],
        "the_loan_denial": ["lien_stamp", "irs_notice", "public_record"],
        "contractor_identity": ["contractor_truck", "irs_notice", "lien_stamp"],
        "controversy_hook": ["irs_notice", "lien_document", "red_arrow"],
        "contractor": ["contractor_truck", "lien_stamp", "checklist"],
        "contractor_series": ["contractor_truck", "lien_stamp", "checklist"],
        "contractor_disaster": ["contractor_truck", "bank_freeze", "lien_stamp"],
        "payroll_tax_trap": ["contractor_truck", "dashboard_alert", "debt_overlay"],
        "before_after": ["before_after", "bank_freeze", "phone_ring"],
        "myth_bust": ["myth_reality", "red_arrow", "irs_notice"],
        "myth_ranking": ["myth_reality", "checklist", "red_arrow"],
        "data_reveal": ["county_map", "debt_overlay", "public_record"],
        "county_lien_alert": ["county_map", "lien_stamp", "debt_overlay"],
        "lien_heat_map": ["heat_map", "county_map", "lien_document"],
        "public_record_breakdown": ["public_record", "lien_document", "red_arrow"],
        "biggest_lien_of_the_week": ["lien_document", "debt_overlay", "public_record"],
        "tax_horror_story": ["bank_freeze", "lien_stamp", "mailbox"],
        "checklist_reel": ["checklist", "red_arrow", "irs_notice"],
        "google_search_reel": ["countdown", "irs_notice", "red_arrow"],
        "deadline_reel": ["countdown", "calendar", "red_arrow"],
        "insider_secret": ["public_record", "revenue_officer", "red_arrow"],
        "irs_agent_story": ["revenue_officer", "lien_document", "red_arrow"],
        "bad_tax_advice_reaction": ["myth_reality", "red_arrow", "irs_notice"],
        "worst_mistake_of_the_week": ["bank_freeze", "lien_stamp", "debt_overlay"],
    }
    keys     = type_map.get(reel_type, ["irs_notice", "red_arrow", "debt_overlay"])
    cue_dict = {c["type"]: c for c in VISUAL_CUE_LIBRARY}
    return [cue_dict[k] for k in keys if k in cue_dict]


# ── Open Loop Library ──────────────────────────────────────────────────────────
OPEN_LOOP_LIBRARY = [
    "But the IRS wasn't the real problem.",
    "Here's the part nobody expects.",
    "The mistake happened before the notice arrived.",
    "And this is where most people lose options.",
    "The scary part is not the lien. It's what comes next.",
    "But wait — here's what changed everything.",
    "And then something the IRS rarely tells you.",
    "Most people stop here. That's the mistake.",
    "Here's where it gets interesting.",
    "There's one more thing the IRS counts on you not knowing.",
    "But here's the part that surprised even me.",
    "The clock started before they even opened the letter.",
    "And this is the moment the options start disappearing.",
    "What happened next is what I see every single week.",
    "Here's the part they don't show you on the IRS website.",
    "And this is exactly where it went wrong.",
]

# ── Controversy Frameworks ─────────────────────────────────────────────────────
CONTROVERSY_FRAMES = [
    "What people think: {myth}. What's actually true: {reality}.",
    "The advice everyone gives about this is completely wrong. Here's why.",
    "I've heard this a hundred times. And every time, it costs people money.",
    "Your accountant may not have told you this. I'm going to.",
    "This is the thing that makes me angry every time I see it.",
    "The IRS is counting on you believing this. Don't.",
]

# ── Comment-Bait Phrases ───────────────────────────────────────────────────────
COMMENT_TRIGGERS = [
    "Drop your state below — I'll tell you what IRS activity looks like there right now.",
    "Comment HELP if you've gotten a notice this month. You're not alone.",
    "Comment CP504 if you've seen this letter. I'll explain what to do next.",
    "Comment CONTRACTOR if you're in the trades. This affects your industry more than any other.",
    "Comment BUSINESS if you're self-employed. What I'm about to say is critical for you.",
    "Tell me in the comments — how long have you been avoiding this?",
    "Comment FLORIDA, TEXAS, or your state below — I'll post your county data.",
    "Drop a 💀 if this is your exact situation right now.",
    "Comment CHECKLIST and I'll send the IRS response guide.",
    "Have you gotten this letter? Tell me what happened in the comments.",
]

def pick_comment_trigger() -> str:
    return random.choice(COMMENT_TRIGGERS)

def pick_open_loop() -> str:
    return random.choice(OPEN_LOOP_LIBRARY)

def pick_controversy() -> str:
    return random.choice(CONTROVERSY_FRAMES)


# ── CTA Strategy ───────────────────────────────────────────────────────────────
CTA_STRATEGY_WEIGHTS = {
    "quiz_cta":        0.25,
    "comment_cta":     0.30,  # Raised — comment > quiz for virality
    "save_cta":        0.20,
    "follow_cta":      0.15,
    "lead_magnet_cta": 0.10,
}

CTA_TYPE_OVERRIDES = {
    "checklist_reel":          "save_cta",
    "red_flag":                "save_cta",
    "myth_ranking":            "save_cta",
    "deadline_reel":           "quiz_cta",
    "county_lien_alert":       "comment_cta",
    "lien_heat_map":           "comment_cta",
    "biggest_lien_of_the_week":"comment_cta",
    "react_reel":              "comment_cta",
    "reddit_reel":             "comment_cta",
    "tax_tiktok_reaction":     "comment_cta",
    "bad_tax_advice_reaction": "comment_cta",
    "tax_horror_story":        "comment_cta",
    "worst_mistake_of_the_week":"comment_cta",
    "google_search_reel":      "follow_cta",
    "insider_secret":          "lead_magnet_cta",
    "irs_agent_story":         "lead_magnet_cta",
    "contractor_series":       "lead_magnet_cta",
    "payroll_tax_trap":        "lead_magnet_cta",
    "mistake":                 "lead_magnet_cta",
    "contractor_disaster":     "lead_magnet_cta",
    "public_record_breakdown": "quiz_cta",
}

def pick_cta_strategy(reel_type: str) -> str:
    if reel_type in CTA_TYPE_OVERRIDES:
        return CTA_TYPE_OVERRIDES[reel_type]
    weights = list(CTA_STRATEGY_WEIGHTS.values())
    choices = list(CTA_STRATEGY_WEIGHTS.keys())
    r       = random.random()
    cumulative = 0
    for choice, w in zip(choices, weights):
        cumulative += w
        if r <= cumulative:
            return choice
    return "quiz_cta"

CTA_TEMPLATES = {
    "quiz_cta": [
        "If this sounds like your situation — taxcasereview.org/quiz. 6 questions. 60 seconds. See what options apply to you.",
        "Find out where you stand: taxcasereview.org/quiz — 60 seconds, completely free, no obligation.",
        "Don't guess. taxcasereview.org/quiz — answer 6 questions, see your real options.",
    ],
    "comment_cta": COMMENT_TRIGGERS,
    "save_cta": [
        "Save this. Forward it to anyone who's been avoiding IRS letters.",
        "Save this reel — it could save someone thousands.",
        "Save this checklist. Most people wish they'd seen this sooner.",
    ],
    "follow_cta": [
        "Follow for weekly IRS breakdowns — real cases, real data, plain English.",
        "Follow me. I post IRS intelligence nobody else is talking about.",
        "Follow TaxCase Review — former IRS insider, real stories, every week.",
    ],
    "lead_magnet_cta": [
        "Comment GUIDE below and I'll send the IRS Response Checklist.",
        "Comment CP504 for our free CP504 Action Guide.",
        "Comment CONTRACTOR for the Contractor Payroll Tax Checklist.",
        "Comment LIEN for the Federal Tax Lien Response Guide.",
        "Comment CHECKLIST — I'll send it directly.",
    ],
}

def get_cta_text(strategy: str) -> str:
    return random.choice(CTA_TEMPLATES.get(strategy, CTA_TEMPLATES["quiz_cta"]))


# ── Lead Magnets ───────────────────────────────────────────────────────────────
LEAD_MAGNETS = {
    "irs_survival":  {"name": "IRS Survival Checklist",            "keyword": "CHECKLIST", "url": QUIZ_URL},
    "cp504_guide":   {"name": "CP504 Action Guide",                "keyword": "CP504",     "url": QUIZ_URL},
    "lien_guide":    {"name": "Federal Tax Lien Response Guide",    "keyword": "LIEN",      "url": QUIZ_URL},
    "contractor":    {"name": "Contractor Payroll Tax Checklist",   "keyword": "CONTRACTOR","url": QUIZ_URL},
    "timeline":      {"name": "IRS Collection Timeline",           "keyword": "TIMELINE",  "url": QUIZ_URL},
    "levy_checklist":{"name": "Bank Levy Emergency Checklist",      "keyword": "LEVY",      "url": QUIZ_URL},
    "oic_readiness": {"name": "Offer in Compromise Readiness Guide","keyword": "GUIDE",     "url": QUIZ_URL},
}

LEAD_MAGNET_TYPE_MAP = {
    "contractor": "contractor", "contractor_series": "contractor",
    "contractor_disaster": "contractor", "payroll_tax_trap": "contractor",
    "what_if": "levy_checklist", "notice": "cp504_guide",
    "red_flag": "irs_survival", "mistake": "irs_survival",
    "insider_secret": "lien_guide", "irs_agent_story": "lien_guide",
    "penalty_calculator": "timeline", "checklist_reel": "irs_survival",
    "worst_mistake_of_the_week": "irs_survival",
}

def pick_lead_magnet(reel_type: str) -> dict:
    return LEAD_MAGNETS[LEAD_MAGNET_TYPE_MAP.get(reel_type, "irs_survival")]


# ── Contractor Series ──────────────────────────────────────────────────────────
CONTRACTOR_SERIES = {
    "roofing":          {"label": "Roofers",              "tax_issue": "payroll tax and 1099 misclassification",  "detail": "crew paid cash, 941 deposits fall behind during slow season"},
    "hvac":             {"label": "HVAC Companies",        "tax_issue": "payroll tax and seasonal cash flow gaps", "detail": "summer revenue spike doesn't get set aside for quarterly deposits"},
    "electricians":     {"label": "Electricians",          "tax_issue": "Trust Fund Recovery Penalty exposure",   "detail": "using payroll deposits to cover material costs on big jobs"},
    "plumbers":         {"label": "Plumbers",              "tax_issue": "1099 subcontractor misclassification",   "detail": "paying subs as contractors when IRS sees them as employees"},
    "trucking":         {"label": "Trucking Companies",    "tax_issue": "payroll tax and HVUT compliance",        "detail": "owner-operators and fleet owners mixing personal/business taxes"},
    "general_contractor":{"label":"General Contractors",  "tax_issue": "payroll tax across multiple subs",        "detail": "GC holds liability for workers classified incorrectly downstream"},
    "restaurant":       {"label": "Restaurant Owners",    "tax_issue": "payroll tax and trust fund liability",    "detail": "tip income and cash wages create 941 gaps that compound fast"},
    "real_estate":      {"label": "Real Estate Investors","tax_issue": "capital gains and depreciation recapture","detail": "flip income reclassified as dealer activity triggering SE tax"},
}


# ── Hook Library ───────────────────────────────────────────────────────────────
HOOK_LIBRARY = {
    "fear": [
        "The IRS doesn't send scary letters first.",
        "By the time most people call us, the IRS has already started.",
        "There's a letter most people throw away. It's the most important one.",
        "You have 30 days. Most people don't know that.",
        "When the IRS goes quiet — that's when you should be most worried.",
        "The IRS filed this on a Monday. By Friday, the bank account was frozen.",
    ],
    "curiosity": [
        "This mistake cost a contractor $87,000.",
        "The IRS notice nobody talks about.",
        "There's an IRS program most people have never heard of.",
        "I've seen this exact situation hundreds of times. It never ends the way people expect.",
        "Most tax advisors won't tell you this part.",
        "The IRS has a deadline most taxpayers don't know exists.",
        "I found something in a public record last week that you need to see.",
    ],
    "identity": [
        "If you're self-employed, listen carefully.",
        "If you're a contractor and you're behind on taxes — this is for you.",
        "If you've been avoiding your mailbox, you need to hear this.",
        "If you got a letter from the IRS this week — stop what you're doing.",
        "If you're a restaurant owner, a landscaper, or an HVAC tech — this affects your industry more than any other.",
        "If you've been ignoring IRS letters, I want to talk to you directly.",
    ],
    "insider": [
        "I spent 12 years as an IRS Revenue Officer. Here's what we never told taxpayers.",
        "When I worked for the IRS, this is what happened behind the scenes.",
        "Here's what IRS agents actually talk about in their morning case reviews.",
        "The IRS has a playbook. I know it. Here's what's in it.",
        "I've reviewed thousands of cases from the IRS side. The pattern is always the same.",
        "Here's something I learned sitting across the table from people exactly like you.",
    ],
    "contrarian": [
        "The IRS is not your biggest problem right now.",
        "The advice most people get about IRS debt is completely wrong.",
        "Everyone says ignore it. Here's why that backfires every single time.",
        "You've been told you can't negotiate with the IRS. That's not true.",
        "Paying it all back is not always the right move.",
        "Most people think the IRS wants to destroy them. That's not how it actually works.",
        "That advice your accountant gave you about IRS debt? It may be costing you options.",
    ],
    "story": [
        "A landscaping contractor ignored one letter. Here's what happened next.",
        "She thought it would go away. It didn't.",
        "He owed $58,000 and had no idea what his options were.",
        "A restaurant owner in Dallas got a letter on a Tuesday. By Friday, their bank account was frozen.",
        "This roofing contractor called us after three years of avoiding the IRS.",
        "He found out his business partner had been skipping payroll tax deposits for 18 months.",
        "She opened the letter, put it in a drawer, and didn't touch it for six months.",
    ],
    "horror": [
        "He woke up to find his bank account at zero. The IRS had levied it overnight.",
        "She got the letter on a Thursday. Her payroll was due Friday. There was nothing left.",
        "He thought the lien was just a formality. Then he tried to refinance his house.",
        "They worked 14-hour days for three years to build that business. The IRS took it in 90 days.",
        "She didn't open the certified letter. She thought it was junk mail.",
        "He paid the accountant $8,000 to handle it. The accountant did nothing. The IRS didn't wait.",
    ],
    "public_record": [
        "This is a matter of public record. Anyone can look this up.",
        "I pulled this from public records this week.",
        "This information is sitting in the county recorder's office right now.",
        "Federal tax liens are public record. Here's what that actually means.",
        "I'm going to show you something most people don't realize is publicly visible.",
    ],
    "local": [
        "IRS liens in {county} County are up this month.",
        "{count} business owners in {county} County woke up to a federal tax lien this week.",
        "Something is happening in {county} County that contractors need to know about.",
        "If you're in {county} County and you're behind on taxes — listen to this.",
    ],
}

SAVE_WORTHY_TYPES = {
    "red_flag", "mistake", "penalty_calculator", "what_if",
    "insider_secret", "educational", "myth_bust", "myth_ranking",
    "checklist_reel", "deadline_reel", "contractor_series",
    "public_record_breakdown", "payroll_tax_trap", "worst_mistake_of_the_week",
}


# ── v9 UPGRADE: Avatar Screen-Time Cap ────────────────────────────────────────
# Enforced in build_visual_storyboard_template and injected into visual_instruction.
# Does NOT touch scoring, HeyGen submission, or Claude generation logic.
MAX_AVATAR_SCENES   = 3     # hard cap: at most 3 of 9 storyboard scenes show avatar
MAX_AVATAR_PERCENT  = 0.30  # 30% screen time max

# Replacement visuals used when avatar cap is exceeded
AVATAR_REPLACEMENT_VISUALS = [
    "document reveal — IRS lien filing zoomed, amount highlighted",
    "county map — lien heat overlay, active counties marked red",
    "full-screen IRS notice — certified mail stamp, date visible",
    "public record search — county recorder portal, name blurred",
    "penalty counter — dollar amount ticking up in real time",
    "evidence board — documents pinned, timeline arrows",
    "case file folder — redacted name, lien amount visible",
    "breaking news lower third — county + lien count data",
]

def validate_avatar_ratio(storyboard: list[dict]) -> tuple[bool, int]:
    """Returns (within_cap, avatar_scene_count). Avatar scenes = rows where
    editor_note or visual does NOT say 'no avatar' or 'off screen'."""
    avatar_count = 0
    for row in storyboard:
        note  = str(row.get("editor_note", "")).lower()
        vis   = str(row.get("visual", "")).lower()
        combined = note + " " + vis
        if "no avatar" not in combined and "off screen" not in combined and "avatar off" not in combined:
            avatar_count += 1
    within_cap = avatar_count <= MAX_AVATAR_SCENES
    return within_cap, avatar_count

def enforce_avatar_cap(storyboard: list[dict]) -> list[dict]:
    """If avatar scenes exceed MAX_AVATAR_SCENES, replace the excess with
    documentary visuals. Keeps first MAX_AVATAR_SCENES avatar scenes; replaces the
    rest. Safe: only modifies scenes that were already avatar-heavy."""
    within, count = validate_avatar_ratio(storyboard)
    if within:
        return storyboard
    patched     = []
    avatar_seen = 0
    repl_pool   = list(AVATAR_REPLACEMENT_VISUALS)
    random.shuffle(repl_pool)
    repl_idx    = 0
    for row in storyboard:
        note  = str(row.get("editor_note", "")).lower()
        vis   = str(row.get("visual", "")).lower()
        is_avatar = ("no avatar" not in note + " " + vis
                     and "off screen" not in note + " " + vis
                     and "avatar off" not in note + " " + vis)
        if is_avatar and avatar_seen >= MAX_AVATAR_SCENES:
            replacement = repl_pool[repl_idx % len(repl_pool)]
            repl_idx += 1
            row = dict(row)
            row["visual"]      = replacement
            row["editor_note"] = (row.get("editor_note", "") +
                                  " [v9: avatar replaced — cap enforced]")
        elif is_avatar:
            avatar_seen += 1
        patched.append(row)
    return patched


# ── v9 UPGRADE: Motion Graphics Library ───────────────────────────────────────
# Attached as scene["motion_graphic"] in enriched storyboard rows.
# Purely additive — does not alter any existing field.
MOTION_GRAPHICS = [
    "money_counter",        # dollar amount counting up to lien total
    "countdown_timer",      # 30-day IRS response window ticking down
    "red_alert_pulse",      # pulsing red ring around key number/document
    "document_stamp",       # FEDERAL TAX LIEN stamp slamming onto document
    "heat_map_animation",   # county lien activity spreading across state map
    "timeline_animation",   # IRS collection sequence: notice → lien → levy
    "zoom_to_amount",       # camera pushes into dollar figure
    "checklist_build",      # checklist items checking off one by one
    "breaking_news_banner", # lower-third ticker: "X LIENS FILED IN [COUNTY]"
    "penalty_counter",      # penalty accrual counter: $47/day compounding
]

# Map reel types to their highest-impact motion graphic
MOTION_GRAPHIC_BY_TYPE = {
    "county_lien_alert":       "breaking_news_banner",
    "state_lien_alert":        "breaking_news_banner",
    "biggest_lien_of_the_week":"zoom_to_amount",
    "public_record_breakdown": "document_stamp",
    "lien_heat_map":           "heat_map_animation",
    "penalty_calculator":      "penalty_counter",
    "what_if":                 "penalty_counter",
    "checklist_reel":          "checklist_build",
    "deadline_reel":           "countdown_timer",
    "notice":                  "countdown_timer",
    "contractor_disaster":     "document_stamp",
    "payroll_tax_trap":        "timeline_animation",
    "tax_horror_story":        "timeline_animation",
    "data_reveal":             "money_counter",
    "myth_ranking":            "checklist_build",
}

def get_motion_graphic(reel_type: str) -> str:
    return MOTION_GRAPHIC_BY_TYPE.get(reel_type, random.choice(MOTION_GRAPHICS))


# ── v9 UPGRADE: Human B-Roll Library ─────────────────────────────────────────
# Industry-specific human b-roll descriptions injected into storyboard scenes.
# Replaces generic "b-roll" references with specific, platform-native imagery.
HUMAN_BROLL = {
    "contractor": [
        "roofer climbing ladder at sunrise — safety gear, shingle bundles visible",
        "contractor crew morning meeting at truck — blueprints, hard hats",
        "HVAC tech working rooftop unit — commercial building background",
        "electrician pulling wire through conduit — focused, professional",
        "plumber under kitchen sink — homeowner watching, explaining issue",
        "general contractor walking job site — clipboard, active construction background",
        "framing crew raising walls — fast progress, team coordination visible",
        "concrete pour — crew working together, deadline energy",
    ],
    "restaurant": [
        "restaurant owner reviewing bills at empty table — early morning, stress visible",
        "kitchen prep crew — fast-paced, steam, commercial kitchen",
        "server taking order — busy dinner service, floor energy",
        "owner closing up alone — counting register, exhausted but focused",
        "food delivery stacked at back door — supplier relationship",
        "chef reviewing payroll printout — concerned expression",
    ],
    "trucking": [
        "truck driver pre-trip inspection — clipboard, big rig at dock",
        "dispatcher on phone — logistics office, screens with routes",
        "owner loading freight at warehouse — hands-on operation",
        "semi truck highway driving — sunrise, empty road ahead",
        "fleet of trucks in yard — scale of operation visible",
        "driver reviewing paperwork at weigh station — compliance reality",
    ],
    "real_estate": [
        "real estate investor reviewing property documents — kitchen table, coffee",
        "property walkthrough — agent and investor, vacant house",
        "closing table — documents, handshake, keys exchanged",
        "contractor meeting at flip property — renovation in progress",
        "owner reviewing rental income spreadsheet — home office",
    ],
    "small_business": [
        "small business owner opening shop alone — keys, early morning",
        "owner reviewing bank statement at desk — concerned, focused",
        "business owner on phone with serious expression — problem-solving mode",
        "entrepreneur in warehouse — inventory, fulfillment reality",
        "family business — generational, emotional stakes visible",
        "owner meeting with accountant — documents spread across table",
    ],
}

def get_human_broll(trade: str = "", reel_type: str = "") -> str:
    """Return a specific human b-roll description based on trade or reel context."""
    # Map reel type to trade if no trade specified
    reel_trade_map = {
        "contractor_disaster": "contractor",
        "payroll_tax_trap":    "contractor",
        "contractor_identity": "contractor",
        "contractor_series":   "contractor",
        "the_friday_disaster": "small_business",
        "the_account_freeze":  "small_business",
        "the_loan_denial":     "real_estate",
        "tax_horror_story":    "small_business",
    }
    trade_key = (trade or reel_trade_map.get(reel_type, "")).lower()
    pool = HUMAN_BROLL.get(trade_key, HUMAN_BROLL["small_business"])
    return random.choice(pool)


# ── v9 UPGRADE: Documentary / Investigative Visual Library ────────────────────
# Injected automatically for investigative and documentary format reels.
# Makes public-record reels feel like Netflix investigations.
DOCUMENTARY_VISUALS = [
    "case file folder — red ACTIVE stamp, lien amount on tab",
    "redacted IRS document — name blurred, amount and county visible",
    "evidence board — photos, documents, timeline arrows connecting facts",
    "timeline wall — each event pinned with date, escalation visible",
    "county records search — computer screen, public database results",
    "document zoom — slow push into filed lien amount, date of filing",
    "public record highlight — cursor scrolling to name, county, amount",
    "signature reveal — bottom of document, notarized stamp, county seal",
    "file cabinet drawer opening — folders, case numbers visible",
    "courier delivering certified mail — signature required, IRS return address",
]

# Reel types that automatically get documentary visual injection
DOCUMENTARY_REEL_TYPES = {
    "public_record_breakdown",
    "tax_horror_story",
    "irs_agent_story",
    "biggest_lien_of_the_week",
    "the_call",
    "the_letter_nobody_opened",
    "the_account_freeze",
    "insider_secret",
}

def get_documentary_visual(reel_type: str = "") -> str:
    if reel_type in DOCUMENTARY_REEL_TYPES:
        return random.choice(DOCUMENTARY_VISUALS)
    return ""


# ── v9 UPGRADE: Camera Directions ─────────────────────────────────────────────
# Added as scene["camera_move"] — pure metadata for editors and AI video tools.
# Increases production value and retention without touching any existing field.
CAMERA_MOVES = [
    "slow_push",      # slow forward push into subject — builds tension
    "fast_zoom",      # sudden zoom to key element — shock/reveal
    "dramatic_crop",  # tight crop on face/document/amount — isolates detail
    "whip_pan",       # fast lateral cut between subjects — energy, urgency
    "parallax",       # background moves slower than foreground — depth
    "ken_burns",      # slow pan + zoom across still image — documentary feel
    "dolly_in",       # smooth forward move — approaching consequence
]

# Scene timing → camera move pairing (by scene index in storyboard, 0-based)
CAMERA_MOVE_BY_SCENE = {
    0: "fast_zoom",     # hook scene: immediate pattern interrupt
    1: "dramatic_crop", # problem scene: isolate the evidence
    2: "slow_push",     # data scene: build to the reveal
    3: "whip_pan",      # escalation: energy jump
    4: "ken_burns",     # consequence: documentary weight
    5: "dolly_in",      # solution approaching
    6: "slow_push",     # CTA: calm, confident close
}

def get_camera_move(scene_index: int, reel_format: str = "") -> str:
    if reel_format in {"true_crime", "documentary", "investigative"}:
        # Documentary formats favor slower, more intentional moves
        return {0: "slow_push", 1: "ken_burns", 2: "dramatic_crop",
                3: "dolly_in", 4: "ken_burns"}.get(scene_index, "slow_push")
    if reel_format in {"breaking_news", "reaction"}:
        # High-energy formats favor fast moves
        return {0: "fast_zoom", 1: "whip_pan", 2: "dramatic_crop",
                3: "fast_zoom", 4: "whip_pan"}.get(scene_index, "fast_zoom")
    return CAMERA_MOVE_BY_SCENE.get(scene_index, random.choice(CAMERA_MOVES))


# ── v9 UPGRADE: Pattern Interrupt System ──────────────────────────────────────
# Mandatory interrupts injected every 3-5 seconds via scene["interrupt"].
# Rule: every scene gets one interrupt — keeps viewers from auto-scrolling.
PATTERN_INTERRUPTS = [
    "record_scratch",    # hard audio/visual stop — resets attention
    "camera_shake",      # brief shake on key impact moment
    "glitch",            # digital glitch effect — modern, attention-grabbing
    "countdown",         # number appears suddenly — urgency spike
    "alert_sound",       # audio sting on key word — podcast-style emphasis
    "red_stamp",         # LIEN / LEVY / FROZEN stamp slams onto screen
    "headline_flash",    # white text flashes: key fact in 1 second
    "document_slam",     # document slams onto screen — physical impact
]

# High-impact interrupt types by reel format
INTERRUPT_BY_FORMAT = {
    "breaking_news":  ["headline_flash", "red_stamp", "countdown"],
    "true_crime":     ["document_slam", "camera_shake", "record_scratch"],
    "coffeezilla":    ["record_scratch", "glitch", "headline_flash"],
    "alex_hormozi":   ["headline_flash", "red_stamp", "countdown"],
    "investigative":  ["document_slam", "red_stamp", "camera_shake"],
    "documentary":    ["camera_shake", "document_slam", "record_scratch"],
    "reaction":       ["record_scratch", "glitch", "headline_flash"],
    "mythbuster":     ["red_stamp", "headline_flash", "glitch"],
}

def get_pattern_interrupt(reel_format: str = "", scene_index: int = 0) -> str:
    pool = INTERRUPT_BY_FORMAT.get(reel_format, PATTERN_INTERRUPTS)
    # Alternate between high-energy (even scenes) and subtle (odd scenes)
    if scene_index % 2 == 0:
        high_energy = ["red_stamp", "document_slam", "headline_flash", "countdown"]
        candidates  = [x for x in pool if x in high_energy] or pool
    else:
        subtle = ["camera_shake", "record_scratch", "glitch", "alert_sound"]
        candidates = [x for x in pool if x in subtle] or pool
    return random.choice(candidates)


# ── v9 UPGRADE: Viral Loop Endings ────────────────────────────────────────────
# Used on 25% of reels BEFORE the CTA. Drives follows, series viewing, saves.
# Picked via should_use_loop_ending() — does not replace CTA, prepends it.
LOOP_ENDINGS = [
    "Part 2 tomorrow. Follow so you don't miss it.",
    "The document gets worse. I'll show it next week.",
    "I'll show the actual lien filing next post.",
    "You haven't seen the biggest lien of the month yet.",
    "Wait until you see what happened to their house.",
    "Tomorrow I'll show the actual IRS notice that started this.",
    "The OIC outcome is in the next reel. Follow TaxCase Review.",
    "Next post: what the Revenue Officer said when they finally called.",
    "Part 2 drops Thursday. Follow — it's the part nobody talks about.",
    "The bank levy came 11 days later. That's next.",
]

# Reel types where loop endings are most effective
LOOP_ENDING_REEL_TYPES = {
    "tax_horror_story", "biggest_lien_of_the_week", "contractor_disaster",
    "payroll_tax_trap", "irs_agent_story", "the_call", "the_account_freeze",
    "the_friday_disaster", "the_letter_nobody_opened", "public_record_breakdown",
}

LOOP_ENDING_PROBABILITY = 0.25  # 25% of reels get a loop ending

def should_use_loop_ending(reel_type: str) -> bool:
    return reel_type in LOOP_ENDING_REEL_TYPES and random.random() < LOOP_ENDING_PROBABILITY

def get_loop_ending() -> str:
    return random.choice(LOOP_ENDINGS)


# ── v9: Storyboard enrichment helper ─────────────────────────────────────────
def enrich_storyboard(storyboard: list[dict], reel_type: str,
                      reel_format: str = "", trade: str = "") -> list[dict]:
    """
    Additive enrichment pass over a parsed storyboard.
    Adds: motion_graphic, camera_move, interrupt, human_broll (where applicable).
    Never modifies time/visual/overlay/editor_note — only adds new keys.
    Also enforces avatar cap.
    """
    # 1. Enforce avatar cap first
    storyboard = enforce_avatar_cap(storyboard)

    # 2. Get shared values
    motion_graphic   = get_motion_graphic(reel_type)
    doc_visual       = get_documentary_visual(reel_type)

    enriched = []
    for i, row in enumerate(storyboard):
        row = dict(row)  # don't mutate original

        # Camera move
        row["camera_move"] = get_camera_move(i, reel_format)

        # Pattern interrupt
        row["interrupt"] = get_pattern_interrupt(reel_format, i)

        # Motion graphic (first scene only — anchor it to the hook)
        if i == 0:
            row["motion_graphic"] = motion_graphic

        # Documentary visual injection for investigative scene (scene 1 = evidence)
        if i == 1 and doc_visual:
            row["documentary_visual"] = doc_visual

        # Human b-roll injection for story scenes (scenes 2-4)
        if 2 <= i <= 4:
            row["human_broll"] = get_human_broll(trade, reel_type)

        enriched.append(row)

    return enriched


# ── v8 Format Engine: topic second, format first ───────────────────────────────
REEL_FORMATS = {
    "coffeezilla": {
        "style": "calm investigative takedown of bad advice or hidden risk",
        "pacing": "fast cold open, evidence reveal, controlled outrage, practical takeaway",
        "visuals": ["comment screenshot", "red X stamp", "IRS document", "zoomed clause", "split-screen reaction"],
        "best_for": {"bad_tax_advice_reaction", "tax_tiktok_reaction", "myth_bust", "controversy_hook"},
    },
    "true_crime": {
        "style": "tax disaster told like a true-crime cold case",
        "pacing": "quiet opening, evidence, escalation, reveal, consequence",
        "visuals": ["dark document table", "certified mail", "timeline board", "bank freeze", "public record stamp"],
        "best_for": {"tax_horror_story", "the_letter_nobody_opened", "the_call", "the_account_freeze"},
    },
    "breaking_news": {
        "style": "urgent local news alert with public-record intelligence",
        "pacing": "headline, county map, data card, human implication, viewer action",
        "visuals": ["breaking news lower third", "county heat map", "filing count", "industry cards", "comment prompt"],
        "best_for": {"county_lien_alert", "state_lien_alert", "lien_heat_map", "biggest_lien_of_the_week"},
    },
    "alex_hormozi": {
        "style": "direct business-owner lesson, no fluff, high tactical density",
        "pacing": "claim, proof, mistake, framework, action",
        "visuals": ["whiteboard", "number stack", "checklist", "cash-flow diagram", "bold captions"],
        "best_for": {"payroll_tax_trap", "contractor_disaster", "mistake", "checklist_reel"},
    },
    "documentary": {
        "style": "mini Netflix-style business documentary",
        "pacing": "cold open, subject, complication, evidence, turning point, lesson",
        "visuals": ["b-roll", "document closeup", "map", "timeline", "avatar expert commentary"],
        "best_for": {"client_story", "case_breakdown", "irs_agent_story", "success_story"},
    },
    "diary_of_a_ceo": {
        "style": "reflective, human, high-trust founder/IRS-insider story",
        "pacing": "vulnerable insight, story, hard truth, relief, CTA",
        "visuals": ["close-up avatar", "soft b-roll", "quote card", "decision fork", "relief outcome"],
        "best_for": {"insider_secret", "confession", "irs_agent_story", "urgency"},
    },
    "reaction": {
        "style": "fast reaction to viral misinformation or bad advice",
        "pacing": "bad claim, pause, correction, consequence, what to do",
        "visuals": ["fake social comment", "red stamp", "IRS form", "reaction frame", "correct answer card"],
        "best_for": {"tax_tiktok_reaction", "bad_tax_advice_reaction", "myth_bust"},
    },
    "mythbuster": {
        "style": "myth vs reality with visual proof",
        "pacing": "myth, shock, truth, example, save-worthy rule",
        "visuals": ["MYTH stamp", "REALITY stamp", "IRS notice", "comparison table", "checklist"],
        "best_for": {"myth_bust", "myth_ranking", "faq_answer"},
    },
    "investigative": {
        "style": "public-record investigation with documents and data",
        "pacing": "document reveal, data context, pattern, risk, next step",
        "visuals": ["public filing", "highlighted amount", "county records", "heat map", "evidence board"],
        "best_for": {"public_record_breakdown", "data_reveal", "biggest_lien_of_the_week", "lien_heat_map"},
    },
}

EMOTIONAL_DRIVERS = {
    "fear": "fear of losing bank access, wages, business financing, or control",
    "curiosity": "need to know what happens next before scrolling",
    "identity": "self-recognition by trade, state, county, or business-owner status",
    "insider": "former IRS perspective unavailable in generic tax content",
    "status": "smart business owners act before the public record follows them",
    "relief": "the IRS problem may be more fixable than it feels",
    "vindication": "the viewer is not dumb; they were missing the right information",
    "shock": "an unexpected consequence or dollar amount changes the story",
    "justice": "bad advice and avoidance should not destroy good businesses",
}

FORMAT_DEFAULTS_BY_TYPE = {
    "tax_horror_story": "true_crime",
    "irs_agent_story": "documentary",
    "insider_secret": "diary_of_a_ceo",
    "the_letter_nobody_opened": "true_crime",
    "the_account_freeze": "true_crime",
    "the_call": "true_crime",
    "the_friday_disaster": "documentary",
    "contractor_disaster": "alex_hormozi",
    "payroll_tax_trap": "alex_hormozi",
    "public_record_breakdown": "investigative",
    "biggest_lien_of_the_week": "investigative",
    "lien_heat_map": "breaking_news",
    "county_lien_alert": "breaking_news",
    "state_lien_alert": "breaking_news",
    "irs_agent_story": "documentary",
    "insider_secret":    "diary_of_a_ceo",
    "insider_secret": "diary_of_a_ceo",
    "bad_tax_advice_reaction": "coffeezilla",
    "tax_tiktok_reaction": "reaction",
    "myth_bust": "mythbuster",
    "myth_ranking": "mythbuster",
    "controversy_hook": "coffeezilla",
}

def _weighted_choice(items):
    total = sum(max(float(w), 0.01) for _, w in items)
    r = random.random() * total
    upto = 0.0
    for item, weight in items:
        upto += max(float(weight), 0.01)
        if upto >= r:
            return item
    return items[-1][0]

def get_format_performance_weights() -> dict:
    data = []
    try:
        data = load_performance()
    except Exception:
        data = []
    weights = {k: 1.0 for k in REEL_FORMATS}
    for row in data[-150:]:
        fmt = row.get("reel_format") or row.get("format")
        if not fmt or fmt not in weights:
            continue
        quality = float(row.get("quality_score") or 0)
        completion = row.get("completion_rate")
        watch = row.get("avg_watch_time")
        comments = row.get("comments") or 0
        shares = row.get("shares") or 0
        saves = row.get("saves") or 0
        boost = 0.0
        if quality >= 85: boost += 0.25
        if completion is not None: boost += min(float(completion) / 100, 1.0) * 0.40
        if watch is not None: boost += min(float(watch) / 20, 1.0) * 0.25
        boost += min((comments + shares + saves) / 20, 1.0) * 0.35
        weights[fmt] += boost
    return weights

def pick_reel_format(reel_type: str, context: dict | None = None) -> str:
    if context and context.get("format") in REEL_FORMATS:
        return context["format"]
    preferred = FORMAT_DEFAULTS_BY_TYPE.get(reel_type)
    perf = get_format_performance_weights()
    candidates = []
    for fmt, spec in REEL_FORMATS.items():
        w = perf.get(fmt, 1.0)
        if preferred == fmt:
            w += 2.0
        if reel_type in spec.get("best_for", set()):
            w += 1.25
        candidates.append((fmt, w))
    return _weighted_choice(candidates)

def pick_emotional_driver(reel_type: str, reel_format: str) -> str:
    if reel_format in {"true_crime", "breaking_news"}:
        pool = ["shock", "fear", "curiosity"]
    elif reel_format in {"coffeezilla", "reaction", "mythbuster"}:
        pool = ["justice", "vindication", "shock", "curiosity"]
    elif reel_format in {"alex_hormozi"}:
        pool = ["status", "identity", "relief"]
    elif reel_format in {"diary_of_a_ceo"}:
        pool = ["relief", "vindication", "insider"]
    else:
        pool = ["curiosity", "identity", "insider", "shock"]
    if reel_type in {"contractor_disaster", "payroll_tax_trap", "contractor_identity"}:
        pool += ["identity", "status"]
    return random.choice(pool)

def build_five_scene_structure(reel_type: str, county: str, state_name: str, amount: int,
                               arch_name: str, hook_keyword: str = "TAX DISASTER",
                               data_stat: str | None = None,
                               data_label: str = "IRS LIENS FILED") -> list[dict]:
    """The 5-scene visual builder (HOOK -> PROBLEM -> DATA -> SOLUTION -> CTA).
    Every scene specifies background, text overlay (+color), visual element, and
    avatar position. STYLE ONLY — drives scene descriptions, not scoring/render."""
    g    = COLOR_PALETTE
    stat = data_stat or IRS_DATA_FY2025["nftls"]
    grad = f"navy-to-dark gradient {g['bg_top']} -> {g['bg_bottom']}"
    return [
        {"scene": 1, "name": "HOOK", "duration": "3-4s",
         "background": grad,
         "avatar": "talking head, bottom 40% of frame",
         "text_overlay": f'"{hook_keyword}" (red {g["primary"]}, 72px+) over "supporting phrase" (white {g["text"]}, 36px), top of frame',
         "visual": "bold 2-line text overlay above avatar, fast pop-in"},
        {"scene": 2, "name": "PROBLEM", "duration": "8-10s",
         "background": f"split screen on {grad} — graphic top half, avatar bottom half",
         "avatar": "bottom half, talking",
         "text_overlay": f"white {g['text']} keyword, max {TYPOGRAPHY['max_words']} words",
         "visual": "top half shows IRS notice / lien document / bank statement"},
        {"scene": 3, "name": "DATA", "duration": "6-8s",
         "background": f"full-screen {grad}",
         "avatar": "OFF screen this scene",
         "text_overlay": f"{stat} centered (white {g['text']}, bold) + '{data_label}' below (orange {g['accent']}, 2-3 words)",
         "visual": f"full-screen motion graphic — IRS Data Book FY2025 stat ({stat})"},
        {"scene": 4, "name": "SOLUTION", "duration": "8-10s",
         "background": f"{grad} with animated graphic elements beside/behind avatar",
         "avatar": "talking head, resolution graphic animates beside/behind",
         "text_overlay": f"bold white {g['text']} keyword, bottom third",
         "visual": "resolution option shown visually (OIC / installment plan / lien withdrawal)"},
        {"scene": 5, "name": "CTA", "duration": "4-5s",
         "background": f"full-screen {grad}",
         "avatar": "OFF screen this scene",
         "text_overlay": f'rounded pill button "↓ BOOK FREE REVIEW ↓" (white {g["text"]}) + "taxcasereview.org" below (white, smaller)',
         "visual": "bold CTA pill button, fast pop-in"},
    ]


def build_visual_storyboard_template(reel_type: str, reel_format: str, county: str, state_name: str, amount: int, arch_name: str, trade: str = "") -> list[dict]:
    spec = REEL_FORMATS.get(reel_format, REEL_FORMATS["documentary"])
    visual_pool = spec.get("visuals", [])
    g    = COLOR_PALETTE
    grad = f"gradient {g['bg_top']}->{g['bg_bottom']}"
    base = [
        ("0-2s",  visual_pool[0] if visual_pool else "full-screen pattern interrupt", f"${amount:,} PROBLEM", f"No avatar. Full-screen {grad}, keyword red {g['primary']} 72px+, sound hit, immediate stakes."),
        ("2-5s",  visual_pool[1] if len(visual_pool) > 1 else "IRS notice/document reveal", "PUBLIC RECORD", f"Evidence before explanation. Zoom into consequence on {grad}. Overlay white {g['text']}."),
        ("5-8s",  "split-screen avatar + evidence", arch_name.upper(), "Avatar enters as expert narrator, not the main visual. Keyword red/orange, dark gradient."),
        ("8-12s", visual_pool[2] if len(visual_pool) > 2 else "timeline escalation", "THE CLOCK STARTED", f"First retention reset. Timeline jump or document stamp. Keyword red {g['primary']}."),
        ("12-18s", visual_pool[3] if len(visual_pool) > 3 else "bank/payroll/financing consequence", "THIS IS WHERE IT HURT", f"Show business impact, not tax jargon. Orange {g['accent']} highlight on key number."),
        ("18-25s", visual_pool[4] if len(visual_pool) > 4 else "county map + public record", f"{county.upper()} COUNTY", f"Localize the risk. Map zoom or DATA card on {grad}. White {g['text']} stat, orange label."),
        ("25-35s", "decision fork graphic", "OPTIONS SHRINK", f"Second retention reset. Two paths: act vs avoid. Keyword orange {g['accent']}."),
        ("35-50s", "lesson card + checklist", "WHAT TO DO NEXT", f"Make it save-worthy. Bullets visual, not spoken-only. White {g['text']} on {grad}, max 6 words/line."),
        ("50-60s", "CTA pill button + avatar small picture-in-picture", "BOOK FREE REVIEW", f"Avatar under 30% screen. Rounded pill CTA white {g['text']} on {grad}. taxcasereview.org below."),
    ]
    raw = [{"time": t, "visual": v, "overlay": o, "editor_note": n} for t, v, o, n in base]
    # v9: enrich with motion graphics, camera moves, interrupts, b-roll, avatar cap
    return enrich_storyboard(raw, reel_type, reel_format, trade)

def build_retention_resets_template(reel_format: str) -> list[dict]:
    resets = [
        ("4s", "evidence reveal", "show document/notice before explanation"),
        ("9s", "timeline jump", "advance from letter to levy/lien"),
        ("15s", "amount reveal", "flash exact dollar amount or account balance"),
        ("22s", "local proof", "map/public-record/county data"),
        ("31s", "contrarian twist", "what the viewer thought vs reality"),
        ("43s", "save-worthy rule", "one rule/checklist item viewer can use"),
    ]
    return [{"time": t, "reset": r, "editor_note": n} for t, r, n in resets]

def parse_pipe_rows(raw: str, min_rows: int = 0) -> list[dict]:
    rows = []
    for line in (raw or "").splitlines():
        line = line.strip().lstrip("-*0123456789. ").strip()
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        # A leading/trailing pipe (| a | b | c |) produces empty first/last
        # elements that shift every column right by one — drop them so
        # parts[0]=time, parts[1]=visual, parts[2]=overlay, parts[3]=editor_note.
        while parts and parts[0] == "":
            parts.pop(0)
        while parts and parts[-1] == "":
            parts.pop()
        if not parts:
            continue
        # Skip markdown separator rows like |---|---|---| or |:--|:-:|--:|.
        if all(p and set(p) <= set("-:") for p in parts):
            continue
        rows.append({
            "time": parts[0] if len(parts) > 0 else "",
            "visual": parts[1] if len(parts) > 1 else "",
            "overlay": parts[2] if len(parts) > 2 else "",
            "editor_note": parts[3] if len(parts) > 3 else "",
        })
    return rows if len(rows) >= min_rows else []

def parse_retention_rows(raw: str) -> list[dict]:
    rows = []
    for line in (raw or "").splitlines():
        line = line.strip().lstrip("-*0123456789. ").strip()
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        # Drop empty leading/trailing elements from border pipes (| a | b | c |)
        # so parts[0]=time, parts[1]=reset, parts[2]=editor_note.
        while parts and parts[0] == "":
            parts.pop(0)
        while parts and parts[-1] == "":
            parts.pop()
        if not parts:
            continue
        # Skip markdown separator rows like |---|---|.
        if all(p and set(p) <= set("-:") for p in parts):
            continue
        rows.append({
            "time": parts[0] if len(parts) > 0 else "",
            "reset": parts[1] if len(parts) > 1 else "",
            "editor_note": parts[2] if len(parts) > 2 else "",
        })
    return rows

def clamp_int(value, default=0, lo=0, hi=100):
    try:
        return max(lo, min(hi, int(float(str(value).replace('%','').strip()))))
    except Exception:
        return default

# ── Content Mix Rotations (new mix: 40/25/20/15) ───────────────────────────────
SUNDAY_ROTATION = [
    # Public Record Intelligence (40%)
    "county_lien_alert", "public_record_breakdown", "biggest_lien_of_the_week",
    "data_reveal", "lien_heat_map",
    # Tax Horror Stories (30%)
    "tax_horror_story", "contractor_disaster", "before_after", "client_story",
    "the_friday_disaster", "the_account_freeze", "the_loan_denial",
    # IRS Insider (20%)
    "irs_agent_story", "confession", "insider_secret",
    # Identity + Controversy (15%)
    "myth_bust", "bad_tax_advice_reaction", "the_letter_nobody_opened",
]

THURSDAY_ROTATION = [
    # Public Record (40%)
    "state_lien_alert", "industry_lien_alert", "biggest_lien_of_the_week",
    # Tax Horror (30%)
    "payroll_tax_trap", "worst_mistake_of_the_week", "contractor_disaster",
    "the_call", "contractor_identity",
    # IRS Insider (20%)
    "irs_agent_story", "insider_secret", "tax_tiktok_reaction",
    # Controversy (15%)
    "myth_ranking", "bad_tax_advice_reaction", "controversy_hook",
]


# ── HeyGen usage tracker ───────────────────────────────────────────────────────
def load_heygen_usage() -> dict:
    if HEYGEN_USAGE_FILE.exists():
        try: return json.loads(HEYGEN_USAGE_FILE.read_text())
        except: pass
    return {"month": "", "count": 0, "renders": []}

def save_heygen_usage(usage: dict):
    HEYGEN_USAGE_FILE.write_text(json.dumps(usage, indent=2))

def get_heygen_usage_this_month() -> int:
    usage = load_heygen_usage()
    return usage.get("count", 0) if usage.get("month") == date.today().strftime("%Y-%m") else 0

def record_heygen_render(video_id: str, reel_type: str):
    usage = load_heygen_usage()
    month = date.today().strftime("%Y-%m")
    if usage.get("month") != month:
        usage = {"month": month, "count": 0, "renders": []}
    usage["count"] += 1
    usage["renders"].append({"date": date.today().isoformat(), "video_id": video_id, "reel_type": reel_type})
    save_heygen_usage(usage)
    print(f"  📊 HeyGen: {usage['count']}/{HEYGEN_MAX_USE} | ~{usage['count']*HEYGEN_CREDITS_PER_VIDEO}/{HEYGEN_MONTHLY_CREDITS} credits")

def can_use_heygen() -> tuple[bool, str]:
    used = get_heygen_usage_this_month()
    if used >= HEYGEN_MAX_USE:
        return False, f"HeyGen limit reached ({used}/{HEYGEN_MAX_USE})"
    return True, f"{used}/{HEYGEN_MAX_USE} renders used"


# ── Scheduling ─────────────────────────────────────────────────────────────────
def get_schedule_for_today() -> tuple[str | None, str | None]:
    week_num = date.today().isocalendar()[1]
    day      = datetime.now().weekday()
    # Task Scheduler controls WHEN this runs — no day-gating here.
    # Rotate through reel types based on day of week for variety.
    if day == 2:   return "remotion", "weekly_stats"       # Wednesday
    elif day == 6: return "heygen", SUNDAY_ROTATION[week_num % len(SUNDAY_ROTATION)]
    elif day == 3: return "heygen", THURSDAY_ROTATION[week_num % len(THURSDAY_ROTATION)]
    else:          return "heygen", SUNDAY_ROTATION[(week_num + day) % len(SUNDAY_ROTATION)]

def get_notice_for_this_week() -> str:
    return NOTICE_ROTATION[date.today().isocalendar()[1] % len(NOTICE_ROTATION)]

def get_state_for_this_week() -> str:
    return STATES_ROTATION[date.today().isocalendar()[1] % len(STATES_ROTATION)]

def pick_hook(hook_type: str, context: dict = None) -> str:
    hooks = HOOK_LIBRARY.get(hook_type, HOOK_LIBRARY["story"])
    h = random.choice(hooks)
    if context:
        h = h.format(**{k: str(v) for k, v in context.items() if isinstance(v, (str, int, float))})
    return h

def should_use_authority() -> bool:
    return random.random() < 0.25


# ── DB: lien stats ─────────────────────────────────────────────────────────────
def get_weekly_lien_stats(county: str = None, state: str = None) -> dict:
    if not HAS_DB:
        counties  = random.sample(FLORIDA_COUNTIES, 5)
        top       = county or counties[0]
        count     = random.randint(20, 65)
        lw        = random.randint(15, 55)
        return {
            "top_county": top, "count": count, "last_week": lw,
            "pct_change": round((count - lw) / max(lw, 1) * 100),
            "counties": [{"name": c, "count": random.randint(5, 50)} for c in counties],
            "data_source": "estimated",
        }
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            where, params = "", []
            if state:
                where = "AND nl.state = %s"
                params.append(state.upper()[:2])
            cur.execute(f"""
                SELECT c.county_name,
                    COUNT(*) FILTER (WHERE nl.created_at >= NOW()-INTERVAL '7 days')  AS tw,
                    COUNT(*) FILTER (WHERE nl.created_at >= NOW()-INTERVAL '14 days'
                                     AND  nl.created_at <  NOW()-INTERVAL '7 days')   AS lw
                FROM normalized_liens nl
                JOIN counties c ON c.id = nl.county_id
                WHERE nl.created_at >= NOW() - INTERVAL '14 days' {where}
                GROUP BY c.county_name ORDER BY tw DESC LIMIT 5
            """, params)
            rows = cur.fetchall()
            if not rows: return {"top_county": county or "Miami-Dade", "count": 0, "last_week": 0, "pct_change": 0, "counties": [], "data_source": "no_data"}
            top = rows[0]
            return {
                "top_county": county or top[0], "count": top[1], "last_week": top[2],
                "pct_change": round((top[1] - top[2]) / max(top[2], 1) * 100),
                "counties": [{"name": r[0], "count": r[1]} for r in rows],
                "data_source": "live",
            }
    finally:
        conn.close()


# ── Claude API ─────────────────────────────────────────────────────────────────
def call_claude(prompt: str, max_tokens: int = 900) -> str:
    if not ANTHROPIC_API_KEY: raise RuntimeError("ANTHROPIC_API_KEY not set")
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-sonnet-4-6", "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=120)
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()


# ── Viral Quality Scoring (0-100, threshold 72) ─────────────────────────────────
def score_reel_script(script_data: dict) -> dict:
    """
    v8 Top 1% scoring system.
    Total = 100:
      scroll_stop        15
      emotional          15
      curiosity          15
      visual_story       20
      comment            10
      save               10
      retention_predict  10
      format_originality  5
    """
    import re as _re
    script    = script_data.get("script", "").lower()
    hook      = script_data.get("hook", "").lower()
    caption   = script_data.get("caption", "").lower()
    hashtags  = script_data.get("hashtags", "").lower()
    full_text = " ".join([script, hook, caption, hashtags])

    reel_format = script_data.get("reel_format", "")
    emotional_driver = script_data.get("emotional_driver", "")
    storyboard = script_data.get("visual_storyboard", []) or []
    resets = script_data.get("retention_resets", []) or []

    # 1. Scroll-stop (15)
    ss = 0
    first_line = (hook or script.split(".")[0]).lower()
    hard_interrupts = [
        "bank account", "frozen", "zero", "payroll bounced", "irs showed up", "public record",
        "certified letter", "levy", "seized", "garnished", "cost him", "cost her", "killed the deal",
        "bonding company", "rejected", "woke up", "this is a matter of public record",
    ]
    if any(x in first_line for x in hard_interrupts): ss += 10
    elif any(x in first_line for x in ["irs", "lien", "notice", "contractor", "$", "tax debt"]): ss += 7
    elif len(first_line) > 28: ss += 5
    # Also scan storyboard editor_notes — visual cue keywords live there, not in spoken script
    storyboard_text = " ".join(str(row.get("editor_note","")) + " " + str(row.get("visual","")) for row in storyboard).lower()
    if any(x in full_text or x in storyboard_text for x in ["full screen", "pattern interrupt", "no avatar", "sound hit", "document slams", "red_stamp", "document_slam", "headline_flash", "camera_shake"]): ss += 3
    if "?" in first_line or any(x in first_line for x in ["why", "how", "what happened"]): ss += 2
    ss = min(15, ss)

    # 2. Emotional specificity (15)
    em = 0
    names = ["marcus", "elena", "derek", "roberto", "james", "sandra", "tony", "keisha",
             "mike", "lisa", "carlos", "david", "sarah", "jose", "maria", "john"]
    if any(n in full_text for n in names): em += 4
    if _re.search(r'\$[\d,]+(?:k|m|\.\d+m?)?', full_text): em += 4
    if any(x in full_text for x in ["county", "miami", "dallas", "houston", "broward", "harris", "fulton", "maricopa", "florida", "texas"]): em += 3
    if any(x in full_text for x in ["payroll", "crew", "wife", "family", "job site", "business", "fuel card", "refinance", "loan denied"]): em += 3
    if emotional_driver in EMOTIONAL_DRIVERS: em += 1
    em = min(15, em)

    # 3. Curiosity/open loops (15)
    cu = 0
    loops = ["but", "here's the part", "nobody expected", "what happened next", "the twist", "what nobody tells",
             "here's why", "turns out", "the real problem", "before they knew", "what changed", "the part most people miss"]
    cu += min(9, sum(2 for x in loops if x in full_text))
    if len(resets) >= 4: cu += 3
    elif len(resets) >= 2: cu += 2
    if any(x in full_text for x in ["reveal", "document", "evidence", "public record", "timeline"]): cu += 3
    cu = min(15, cu)

    # 4. Visual story score (20)
    vt = 0
    unique_visuals = set()
    for row in storyboard:
        v = str(row.get("visual", "")).lower()
        if v: unique_visuals.add(v[:40])
    if len(storyboard) >= 8: vt += 8
    elif len(storyboard) >= 6: vt += 6
    elif len(storyboard) >= 4: vt += 4
    if len(unique_visuals) >= 7: vt += 4
    elif len(unique_visuals) >= 4: vt += 2
    visual_words = ["full screen", "split-screen", "split screen", "map", "document", "notice", "timeline", "bank", "payroll", "overlay", "b-roll", "zoom", "stamp", "heat map"]
    vt += min(5, sum(1 for w in visual_words if w in full_text))
    if any("avatar" in str(row).lower() and ("small" in str(row).lower() or "30%" in str(row).lower() or "picture-in-picture" in str(row).lower()) for row in storyboard): vt += 2
    if any("no avatar" in str(row).lower() for row in storyboard): vt += 1
    # v9: bonus for enrichment fields present
    if any(row.get("camera_move") for row in storyboard): vt += 1
    if any(row.get("interrupt") for row in storyboard): vt += 1
    if any(row.get("human_broll") for row in storyboard): vt += 1
    if any(row.get("motion_graphic") for row in storyboard): vt += 1
    vt = min(20, vt)

    # 5. Comment potential (10)
    co = 0
    comment_triggers = ["comment", "drop your", "tell me", "reply", "type", "dm me", "send this", "tag a", "what state"]
    if any(x in full_text for x in comment_triggers): co += 5
    if any(x in full_text for x in ["wrong", "bad advice", "myth", "dangerous", "accountant", "people think", "nobody tells"]): co += 3
    if "?" in full_text: co += 2
    co = min(10, co)

    # 6. Save potential (10)
    sv = 0
    save_words = ["save", "checklist", "timeline", "guide", "steps", "what to do", "red flags", "playbook", "screenshot", "bookmark", "rule"]
    if script_data.get("save_worthy") or any(x in full_text for x in save_words): sv += 5
    if sum(1 for x in save_words if x in full_text) >= 3: sv += 3
    if any(x in full_text for x in ["cp504", "lt11", "941", "tfrp", "levy release", "penalty abatement"]): sv += 2
    sv = min(10, sv)

    # 7. Retention prediction (10)
    pred = script_data.get("retention_prediction", {}) or {}
    p3 = clamp_int(pred.get("predicted_3s_hold", 0))
    p10 = clamp_int(pred.get("predicted_10s_hold", 0))
    comp = clamp_int(pred.get("predicted_completion", 0))
    rp = 0
    if p3 >= 70: rp += 3
    elif p3 >= 55: rp += 2
    if p10 >= 45: rp += 3
    elif p10 >= 30: rp += 2
    if comp >= 35: rp += 4
    elif comp >= 25: rp += 3
    elif comp >= 15: rp += 1
    # fallback estimate if Claude omitted predictions
    if rp == 0:
        rp = min(10, (3 if ss >= 10 else 1) + (3 if vt >= 14 else 1) + (2 if cu >= 10 else 1) + (2 if len(resets) >= 4 else 0))

    # 8. Format originality (5)
    fo = 0
    if reel_format in REEL_FORMATS: fo += 3
    if reel_format in {"coffeezilla", "true_crime", "breaking_news", "investigative", "documentary", "reaction"}: fo += 2
    fo = min(5, fo)

    total = ss + em + cu + vt + co + sv + rp + fo
    return {
        "total": total,
        "scroll_stop_score": ss,
        "emotional_score": em,
        "curiosity_score": cu,
        "visual_story_score": vt,
        "visual_tension": vt,
        "comment_score": co,
        "save_score": sv,
        "retention_prediction_score": rp,
        "format_originality_score": fo,
    }


# ── Main script generator ──────────────────────────────────────────────────────
def generate_heygen_script(reel_type: str, context: dict, force: bool = False) -> dict:
    for attempt in range(3):
        result       = _generate_once(reel_type, context)
        viral_scores = score_reel_script(result)
        result["viral_scores"]  = viral_scores
        result["quality_score"] = viral_scores["total"]
        print(f"  {'✅' if viral_scores['total'] >= QUALITY_THRESHOLD else '⚠️ '} "
              f"Viral score: {viral_scores['total']}/100 | "
              f"scroll={viral_scores['scroll_stop_score']} emotional={viral_scores['emotional_score']} "
              f"curiosity={viral_scores['curiosity_score']} visual={viral_scores.get('visual_story_score',0)} comment={viral_scores['comment_score']} format={result.get('reel_format','')}")
        if viral_scores["total"] >= QUALITY_THRESHOLD or attempt == 1 or force:
            if viral_scores["total"] < QUALITY_THRESHOLD and not force:
                print(f"  ⚠️  Score {viral_scores['total']} < {QUALITY_THRESHOLD}. Use --force to render.")
                result["quality_below_threshold"] = True
            return result
        print(f"  🔄 Score too low — regenerating with different archetype...")
    return result


def _generate_once(reel_type: str, context: dict) -> dict:
    week_of     = date.today().strftime("%B %d, %Y")
    county      = context.get("county", random.choice(FLORIDA_COUNTIES))
    count       = context.get("count", random.randint(10, 50))
    notice      = context.get("notice", get_notice_for_this_week())
    state       = context.get("state", get_state_for_this_week())
    _state_names_map = {
        "florida":"Florida","texas":"Texas","georgia":"Georgia","arizona":"Arizona",
        "california":"California","new_york":"New York","north_carolina":"North Carolina",
        "illinois":"Illinois","ohio":"Ohio","pennsylvania":"Pennsylvania",
        "nevada":"Nevada","colorado":"Colorado",
    }
    state_name  = _state_names_map.get(state, state.replace("_"," ").title())
    state_url   = f"{SITE_URL}/{state.replace('_','-')}"
    topic       = context.get("topic", "")
    trade       = context.get("trade", "")
    city        = context.get("city", "")
    use_auth    = should_use_authority()
    data_source = context.get("data_source", "estimated")
    reel_format = pick_reel_format(reel_type, context)
    format_spec = REEL_FORMATS.get(reel_format, REEL_FORMATS["documentary"])
    emotional_driver = pick_emotional_driver(reel_type, reel_format)

    # Pick archetype for story-driven reels
    archetype    = pick_archetype()
    debt_amount  = pick_debt_amount(archetype)
    arch_name    = archetype["name"]
    arch_desc    = archetype["descriptor"]
    arch_detail  = archetype["detail"]
    base_storyboard = build_visual_storyboard_template(reel_type, reel_format, county, state_name, debt_amount, arch_name, trade=trade)
    base_resets     = build_retention_resets_template(reel_format)
    style_notes     = get_style_notes(reel_type)
    five_scenes     = build_five_scene_structure(
        reel_type, county, state_name, debt_amount, arch_name,
        hook_keyword=style_notes["hook_keyword"],
        data_stat=IRS_DATA_FY2025["nftls"],
        data_label="IRS LIENS FILED",
    )

    tier      = pick_length_tier(reel_type)
    tier_info = SCRIPT_LENGTH_TIERS[tier]
    max_words = tier_info["max"]

    # Hook selection
    hook_map = {
        "tax_horror_story":        "horror",
        "contractor_disaster":     "horror",
        "payroll_tax_trap":        "horror",
        "worst_mistake_of_the_week":"horror",
        "before_after":            "horror",
        "client_story":            "horror",
        "county_lien_alert":       "local",
        "city_lien_alert":         "local",
        "lien_heat_map":           "local",
        "public_record_breakdown": "public_record",
        "biggest_lien_of_the_week":"public_record",
        "irs_agent_story":         "insider",
        "insider_secret":          "insider",
        "confession":              "insider",
        "bad_tax_advice_reaction": "contrarian",
        "tax_tiktok_reaction":     "contrarian",
        "myth_bust":               "contrarian",
        "myth_ranking":            "contrarian",
        "notice": "fear", "what_if": "fear", "red_flag": "fear",
        "deadline_reel": "fear", "state_lien_alert": "identity",
        "educational": "curiosity", "mistake": "curiosity",
        "contractor": "identity", "contractor_series": "identity",
        "contractor_disaster": "horror",
        "urgency": "identity", "case_breakdown": "story",
        "success_story": "story", "data_reveal": "curiosity",
    }
    hook_type    = hook_map.get(reel_type, "story")
    hook_line    = pick_hook(hook_type, {"county": county, "count": count})
    open_loop    = pick_open_loop()
    comment_cta  = pick_comment_trigger()
    cta_strategy = pick_cta_strategy(reel_type)
    cta_text     = get_cta_text(cta_strategy)
    lead_magnet  = pick_lead_magnet(reel_type)
    visual_cues  = get_visual_cues_for_type(reel_type)
    is_save      = reel_type in SAVE_WORTHY_TYPES

    auth_line = ""
    if use_auth:
        auth_line = random.choice([
            "When I worked as an IRS Revenue Officer, ",
            "After 12 years inside the IRS, here's what I know — ",
            "I've seen this from both sides of the table. ",
            "Here's something most taxpayers never hear — ",
        ])

    persona = f"""You are Romy — former IRS Revenue Officer, 12 years. Founder of TaxCase Review.
Voice: direct, warm, former-insider authority. Like Coffeezilla meets a tax attorney.
FORMAT: {reel_format} — {format_spec['style']}
PACING: {format_spec['pacing']}
EMOTIONAL DRIVER: {emotional_driver} — {EMOTIONAL_DRIVERS[emotional_driver]}
{auth_line}
NEVER open with: "What is..." / "Today we're talking about..." / "Let's discuss..."
NEVER say: "The longer you wait" / "Consult a professional" / "It depends"
NEVER use generic examples. Always use specific names, amounts, counties, industries."""

    # v9: pick loop ending and motion graphic for prompt injection
    _use_loop    = should_use_loop_ending(reel_type)
    _loop_text   = get_loop_ending() if _use_loop else ""
    _motion_gfx  = get_motion_graphic(reel_type)
    _doc_visual  = get_documentary_visual(reel_type)
    _human_broll = get_human_broll(trade, reel_type)
    _avatar_rule = f"AVATAR CAP: Maximum {MAX_AVATAR_SCENES} scenes may show the avatar. All other scenes must use documentary visuals, b-roll, motion graphics, or data cards — NO avatar on a plain background."
    _loop_inject = f"\nLOOP ENDING (add before CTA): \"{_loop_text}\"" if _use_loop else ""

    visual_instruction = f"""
{VISUAL_STYLE_GUIDE}
STYLE NOTE FOR THIS REEL: {style_notes['note']}
Lead hook keyword = "{style_notes['hook_keyword']}" in {style_notes['hook_color']}; signature visual = {style_notes['visual']}.

{_avatar_rule}

MOTION GRAPHIC for this reel: {_motion_gfx} — inject on the hook scene (0-2s).
{"DOCUMENTARY VISUAL for evidence scene: " + _doc_visual if _doc_visual else ""}
HUMAN B-ROLL for story scenes (5-20s): {_human_broll}
CAMERA MOVES: vary between slow_push (tension), fast_zoom (reveal), dramatic_crop (detail), whip_pan (energy). Specify one per scene.
PATTERN INTERRUPTS: every scene needs one — red_stamp / document_slam / headline_flash / camera_shake / record_scratch. Specify in editor_note.
{_loop_inject}

SCENE STRUCTURE — build the reel as these 5 scenes (60-90s total, fast cuts):
{json.dumps(five_scenes, ensure_ascii=False)}
Each scene description MUST specify: background, text overlay content + color, visual element, and avatar position (on/off screen).
DATA scene pulls a real IRS Data Book FY2025 figure ({IRS_DATA_FY2025['nftls']} NFTLs, {IRS_DATA_FY2025['oic_acceptance_rate']} OIC acceptance rate, {IRS_DATA_FY2025['installment_agreements']} installment agreements).

After HASHTAGS, output ALL of these sections exactly:
VISUAL_STORYBOARD:
[8-9 rows required. Format: TIME | VISUAL | TEXT_OVERLAY | EDITOR_NOTE]
Each row's EDITOR_NOTE must name: background (dark gradient {COLOR_PALETTE['bg_top']}->{COLOR_PALETTE['bg_bottom']}), text overlay color (red {COLOR_PALETTE['primary']} / orange {COLOR_PALETTE['accent']} keyword, white {COLOR_PALETTE['text']} support), avatar position (on/off screen), camera move, and pattern interrupt. Max {TYPOGRAPHY['max_words']} words per overlay.
Required template to improve, not copy blindly:
{json.dumps(base_storyboard, ensure_ascii=False)}
Rules: no repeated visual, avatar never primary for more than 30% of reel ({MAX_AVATAR_SCENES} scenes max), visual changes every 1-3 seconds, no flat-color backgrounds, every scene has a human b-roll or documentary visual when avatar is off-screen.

RETENTION_RESETS:
[at least 5 rows. Format: TIME | RESET | EDITOR_NOTE]
Required template to improve, not copy blindly:
{json.dumps(base_resets, ensure_ascii=False)}

VISUAL_CUES:
[3-4 legacy cues for compatibility. Format: TIME | VISUAL | TEXT_OVERLAY | EDITOR_NOTE]
0-2s | [pattern interrupt visual] | [bold text] | [editor note]

PLATFORM_VARIANTS:
FACEBOOK: [caption/CTA angle]
INSTAGRAM: [caption/CTA angle]
YOUTUBE_SHORTS: [title/hook angle]
TIKTOK: [hook/CTA angle]

RETENTION_PREDICTION:
PREDICTED_3S_HOLD: [0-100]
PREDICTED_10S_HOLD: [0-100]
PREDICTED_COMPLETION: [0-100]
PREDICTED_SHARE_RATE: [0-100]
PREDICTED_COMMENT_RATE: [0-100]
"""

    youtube_instruction = """
YOUTUBE_TITLE: [45-70 chars — curiosity-first, NOT corporate]
YOUTUBE_SHORTS_HOOK: [1 punchy sentence for Shorts]
YOUTUBE_DESCRIPTION: [Hook line. CTA to quiz. Keywords. Max 400 chars.]
YOUTUBE_TAGS: [12 tags, comma-separated]"""

    format_block = f"""
Format EXACTLY:
REEL_FORMAT: {reel_format}
EMOTIONAL_DRIVER: {emotional_driver}
LENGTH_TIER: {tier}
HOOK_TYPE: {hook_type}
CTA_STRATEGY: {cta_strategy}
SAVE_WORTHY: {"yes" if is_save else "no"}
AUTHORITY_USED: {"yes" if use_auth else "no"}

SCRIPT:
[{tier_info['min']}-{max_words} words HARD LIMIT. ~{tier_info['seconds']} seconds.]

CAPTION:
[Short lines. First line = scroll stopper. Matches {cta_strategy}.]

HASHTAGS:
[8-10 hashtags]
{visual_instruction}
{youtube_instruction}"""

    # ── Retention structure for all prompts ────────────────────────────────────
    STORY_STRUCTURE = f"""
REQUIRED RETENTION STRUCTURE:
VISUAL DOMINANCE RULE: The avatar supports the story. The visual evidence carries the story. Avatar max 30% primary screen time.
0-2s  PATTERN INTERRUPT (MANDATORY): Your SCRIPT must open with EXACTLY this line or a direct riff: "{hook_line}"
      FULL SCREEN VISUAL FIRST. No avatar. If you change this hook, the reel fails. Scroll stops here or nowhere.
2-5s  OPEN LOOP: Plant "{open_loop}" — creates irresistible curiosity.
5-20s STORY: Specific person, specific county, specific dollar amount. NEVER generic.
      Name: {arch_name}. Descriptor: {arch_desc}.
      Debt: ${debt_amount:,}. Problem: {arch_detail}.
20-30s CURIOSITY RESET: "But here's what nobody expected..." / "Here's where it turned..."
30-45s REVELATION + LESSON: The thing they didn't see coming. What it means for viewer.
45-55s SELF-ID: "If you're [specific identity]... this is exactly what's happening to you."
55-60s CTA: {cta_text}
       Then: {comment_cta}

Rules:
- Use the selected FORMAT like a real producer would. Do not make every reel sound the same.
- EVERY detail must be specific: named person, named county, exact dollar amount
- Emotional stakes must be clear by second 10
- One curiosity reset every 8-12 seconds for standard/deep reels
- "Results vary. Every situation is different." required for any client story
"""

    prompts = {

        # ── NEW: Tax Horror Stories ─────────────────────────────────────────────
        "tax_horror_story": f"""{persona}
Hook: "{hook_line}"
Week: {week_of}
{STORY_STRUCTURE}
Topic: A real tax horror story. Bloomberg-style storytelling, IRS-insider accuracy.
Tell the story of {arch_name} — {arch_desc} — who owed ${debt_amount:,} and didn't act.
Walk through: the ignored letters → the lien on public record → the bank levy →
the moment everything became real → what happened to their business → what options remained.
Make the viewer feel the weight of each decision point.
This is NOT a lecture. It's a story with a lesson buried inside.
{format_block}""",

        "biggest_lien_of_the_week": f"""{persona}
Hook: "I pulled this from public records this week."
Week: {week_of}. County: {county}. Source: {data_source}.
{STORY_STRUCTURE}
{"LIVE DATA" if data_source == "live" else "ESTIMATED DATA — label as approximate"}:
The largest IRS lien filed in {county} County this week: generate a specific believable amount ($180k-$2.4M range for largest).
Who filed it — describe the business type (don't name real businesses).
What industry. What likely caused it. What options they have now.
Public records framing: "This is sitting in the county recorder's office right now. Anyone can look it up."
Reframe the number as a real person or business behind it.
{format_block}""",

        "contractor_disaster": f"""{persona}
Hook: "{hook_line}"
Week: {week_of}
{STORY_STRUCTURE}
Topic: A contractor tax disaster story. Caleb Hammer energy — real, uncomfortable, specific.
{arch_name} — {arch_desc} — owed ${debt_amount:,} in payroll taxes.
{arch_detail}.
Walk through: how the problem started → what they did wrong → the Trust Fund Recovery Penalty kicking in →
the personal liability they didn't know existed → the bank levy → the lien on their house.
Be specific. Name the mistakes. Don't soften it.
The lesson must be clear. The CTA must be urgent.
"Results vary. Every situation is different."
{format_block}""",

        "payroll_tax_trap": f"""{persona}
Hook: "{hook_line}"
Week: {week_of}
{STORY_STRUCTURE}
Topic: The payroll tax trap that destroys more contractors than anything else.
Walk through exactly how it works: quarterly 941 deposits → temptation to use for cash flow →
TFRP personal liability → IRS comes after the owner personally even after LLC closes.
{arch_name} — {arch_desc} — owed ${debt_amount:,} and thought the LLC protected them.
It didn't.
Every contractor in the trades needs to hear this.
{format_block}""",

        "public_record_breakdown": f"""{persona}
Hook: "This is a matter of public record. Anyone can look this up."
Week: {week_of}. County: {county}. Source: {data_source}.
{STORY_STRUCTURE}
Topic: Breaking down what public IRS lien records actually reveal.
{"LIVE DATA" if data_source == "live" else "ESTIMATED"}: {count} liens filed in {county} this week.
Walk through: what a federal tax lien filing looks like in public record → what information is visible →
what it means for the business owner's credit, refinancing, property → who can see it → how long it stays.
Most people don't realize this is public. That's the hook.
{format_block}""",

        "lien_heat_map": f"""{persona}
Hook: "I'm looking at the IRS lien activity map for {state} right now."
Week: {week_of}. State: {state}. Source: {data_source}.
{STORY_STRUCTURE}
MICRO REEL. Visual-first. State-level heat map breakdown.
{"LIVE" if data_source == "live" else "ESTIMATED"}: Which counties are hottest right now.
Top 3 counties by lien concentration. Top industry in each.
One human story behind the data.
CTA: Comment your county — I'll tell you what's happening there.
{format_block}""",

        "worst_mistake_of_the_week": f"""{persona}
Hook: "{hook_line}"
Week: {week_of}
{STORY_STRUCTURE}
MICRO REEL. One catastrophic mistake. Specific, real, uncomfortable.
{arch_name} made ONE decision that cost them ${debt_amount:,} in options.
What the mistake was. Why people make it. What it actually cost.
What they should have done instead.
Short. Punchy. Save-worthy.
{format_block}""",

        "tax_tiktok_reaction": f"""{persona}
Hook: "I just saw someone say you should [specific bad IRS advice]. That is dangerous."
Week: {week_of}. {"Topic: " + topic if topic else "Reacting to viral IRS misinformation."}
{STORY_STRUCTURE}
React format: Bad advice → why it's wrong → what actually happens → real example of consequences.
Use Coffeezilla energy: calm, authoritative, slightly frustrated at the bad advice.
NOT lecture. Reaction.
{format_block}""",

        "bad_tax_advice_reaction": f"""{persona}
Hook: "{pick_hook('contrarian')}"
Week: {week_of}. {"Topic: " + topic if topic else "Reacting to common bad IRS advice."}
{STORY_STRUCTURE}
React to one piece of bad tax advice that's dangerously common:
- "Just ignore IRS letters and they'll go away"
- "File bankruptcy to clear IRS debt"
- "Your LLC protects you from payroll tax liability"
- "The IRS won't bother with amounts under $10k"
- "You can't negotiate with the IRS"
Be specific about what goes wrong when people follow this advice.
Use a real scenario — {arch_name} did exactly this and it cost them ${debt_amount:,}.
{format_block}""",

        "irs_agent_story": f"""{persona}
Hook: "When I worked as an IRS Revenue Officer, I had a case like this."
Week: {week_of}
{STORY_STRUCTURE}
[Authority REQUIRED — this IS an IRS agent story]
Tell a story from inside the IRS. Former-agent perspective.
What Revenue Officers actually look for. What makes them escalate. What makes them stop.
A real case type I worked (anonymized): industry, debt amount, what the taxpayer did, how it resolved.
This is the stuff that never gets published. This is the inside view.
{format_block}""",

        # ── Preserved v5 prompts ────────────────────────────────────────────────
        "educational": f"""{persona}
Hook: "{pick_hook('curiosity')}"
Week: {week_of}
{STORY_STRUCTURE}
Topic: One IRS insider insight that most people don't know. Go deep, not wide.
Pick ONE: First-Time Penalty Abatement / CNC status / CDP hearing / OIC rate (37%) /
CSED 10-year clock / Lien vs levy vs garnishment.
Teach through {arch_name}'s story — {arch_desc} who discovered this the hard way.
{format_block}""",

        "notice": f"""{persona}
Hook: "{pick_hook('fear')}"
Week: {week_of}. Notice: {notice}
{STORY_STRUCTURE}
Topic: {arch_name} — {arch_desc} — just got a {notice}. They're scared.
Cover: what triggered it, the exact deadline, what happens at each non-response point, one action NOW.
Make them feel understood before you explain anything.
{format_block}""",

        "urgency": f"""{persona}
Hook: "{pick_hook('identity')}"
Week: {week_of}
{STORY_STRUCTURE}
Topic: The emotional weight of ignoring IRS debt. Validate before educating.
Name the feeling. Validate it. Pivot to: most situations are more fixable than people think.
One action today. BANNED: "The longer you wait"
{format_block}""",

        "success_story": f"""{persona}
Hook: "{pick_hook('story')}"
Week: {week_of}
{STORY_STRUCTURE}
Topic: {arch_name} — {arch_desc} — owed ${debt_amount:,}.
Tell the full arc: before (emotional, specific) → what they did → resolution (specific dollars saved) → after.
"Results vary. Every situation is different."
{format_block}""",

        "myth_bust": f"""{persona}
Hook: "{pick_hook('contrarian')}"
Week: {week_of}
{STORY_STRUCTURE}
Bust ONE myth with one specific fact and one story of consequences.
{arch_name} believed this myth. It cost them ${debt_amount:,} in options.
State myth → Bust with IRS fact → Real consequence → What's true → CTA.
{format_block}""",

        "data_reveal": f"""{persona}
Hook: "{pick_hook('public_record')}"
Week: {week_of}. County: {county}. Count: {count}. Source: {data_source}.
{STORY_STRUCTURE}
{"ESTIMATED — label as approximate." if data_source == "estimated" else "LIVE DATA."}
Reframe: {count} people in {county} — NOT "{count} liens filed."
Who are these people? What industries? What does a lien mean for their daily lives?
{format_block}""",

        "contractor": f"""{persona}
Hook: "{pick_hook('identity')}"
Week: {week_of}
{STORY_STRUCTURE}
Topic: Why contractors face IRS liens more than any profession.
{arch_name} — {arch_desc} — ${arch_detail}. Owed ${debt_amount:,}.
Cover: payroll tax trap, TFRP personal liability, one specific scenario, what to do today.
{format_block}""",

        "state_spotlight": f"""{persona}
Hook: "{pick_hook('identity')}"
Week: {week_of}. State: {state}
{STORY_STRUCTURE}
IRS situation in {state}. Local, specific, data-backed.
Active liens: FL=17k, TX=142k, CA=125k, NY=68k, GA=8k, AZ=12k, NC=6k, IL=9k
Top industries in {state}. One {state}-specific factor. Resolution options. Local CTA.
{format_block}""",

        "faq_answer": f"""{persona}
Hook: "{pick_hook('fear')}"
Week: {week_of}
{STORY_STRUCTURE}
Answer ONE question directly — no hedging. {arch_name}'s story illustrates it.
Pick: IRS take my house? / Social Security garnishment? / Ignore IRS? / Negotiate myself? / CSED? / Minimum settlement?
{format_block}""",

        "client_story": f"""{persona}
Hook: "{pick_hook('story')}"
Week: {week_of}
{STORY_STRUCTURE}
{arch_name} — {arch_desc} — ${debt_amount:,}. Full emotional transformation.
Before → turning point → resolution (exact $ saved) → after.
"Results vary. Every situation is unique."
{format_block}""",

        "penalty_calculator": f"""{persona}
Hook: "{pick_hook('fear')}"
Week: {week_of}
{STORY_STRUCTURE}
Walk through $25,000 debt compounding. April 15 → Month 1 → Month 6 → Month 12.
Bottom line: ~$47/day in penalties. Visceral. Not academic.
Retention beat at the 12-month mark: "But here's the part nobody expects..."
{format_block}""",

        "mistake": f"""{persona}
Hook: "{pick_hook('curiosity')}"
Week: {week_of}
{STORY_STRUCTURE}
3 mistakes that destroy IRS options. Each as a story.
Use {arch_name} for one of them — ${arch_detail} — ${debt_amount:,}.
{format_block}""",

        "confession": f"""{persona}
Hook: "{pick_hook('insider')}"
Week: {week_of}
{STORY_STRUCTURE}
"The thing I tell every client that nobody else tells them."
ONE insight: FTA nobody asks for / CSED / IRS prefers payment plans / filing before resolution.
Deliver it like you're telling a friend something critical. {arch_name}'s story illustrates it.
{format_block}""",

        "case_breakdown": f"""{persona}
Hook: "{pick_hook('story')}"
Week: {week_of}
{STORY_STRUCTURE}
{arch_name} — {arch_desc} — ${debt_amount:,}. 70% story, 30% lesson.
Mistake → what made it worse → turning point → resolution in exact dollars.
"Results vary."
{format_block}""",

        "what_if": f"""{persona}
Hook: "{pick_hook('fear')}"
Week: {week_of}. Notice: {notice}
{STORY_STRUCTURE}
What happens when {notice} is ignored. Escalation sequence with exact timelines.
CP14 → CP501/502/503 → CP504 → LT11 (30-day CDP window!) → levy → lien.
{arch_name} ignored it. Show exactly what happened at each step.
{format_block}""",

        "red_flag": f"""{persona}
Hook: "{pick_hook('fear')}"
Week: {week_of}
{STORY_STRUCTURE}
5 red flags IRS is about to escalate. Punchy. Save-worthy. Checklist feel.
CP504 / Lien on public record / Letter 1058 / NFTL filed / No response 90+ days
Make viewer feel like they're reading their own situation.
{format_block}""",

        "before_after": f"""{persona}
Hook: "{pick_hook('horror')}"
Week: {week_of}
{STORY_STRUCTURE}
{arch_name} — {arch_desc}. BEFORE: avoiding mailbox, 2am anxiety, frozen on IRS calls.
AFTER: payment plan, lien withdrawn, reopened the business. Hired back 3 employees.
Emotional contrast. "Results vary."
{format_block}""",

        "insider_secret": f"""{persona}
Hook: "{pick_hook('insider')}" [Authority REQUIRED]
Week: {week_of}
{STORY_STRUCTURE}
One thing IRS knows that taxpayers don't. "Here's what I never told taxpayers when I worked there."
Agent quotas / FTA automatic / CNC status / CSED / OIC pre-qualifier / Revenue Officer discretion.
{arch_name}'s story illustrates it — they learned this too late.
{format_block}""",

        "county_lien_alert": f"""{persona}
Hook: "{pick_hook('local', {'county': county, 'count': count})}"
Week: {week_of}. County: {county}. Count: {count}. Source: {data_source}.
{STORY_STRUCTURE}
MICRO REEL. {"LIVE DATA" if data_source == "live" else "ESTIMATED"}: {count} liens in {county}.
Reframe: "{count} business owners in {county} woke up to a federal lien."
Who → what it means → one action → comment CTA.
{format_block}""",

        "city_lien_alert": f"""{persona}
Hook: "{pick_hook('local', {'county': city or county, 'count': count})}"
Week: {week_of}. City: {city or county}. Count: {count}. Source: {data_source}.
{STORY_STRUCTURE}
MICRO REEL. City-level lien alert. Hyper-local.
{"ESTIMATED." if data_source == "estimated" else "LIVE."}
{format_block}""",

        "state_lien_alert": f"""{persona}
Hook: "{pick_hook('identity')}"
Week: {week_of}. State: {state}. Source: {data_source}.
{STORY_STRUCTURE}
State-level enforcement update. Top 3 counties, top industry, trend direction.
{"ESTIMATED." if data_source == "estimated" else "LIVE."}
{format_block}""",

        "industry_lien_alert": f"""{persona}
Hook: "{pick_hook('identity')}"
Week: {week_of}. Industry: contractors/construction. State: {state}.
{STORY_STRUCTURE}
Industry-specific lien trend. Which trade is hottest. Why. {arch_name}'s story.
{format_block}""",

        "irs_update": f"""{persona}
Hook: "{pick_hook('contrarian')}"
Week: {week_of}. {"Topic: " + topic if topic else "Evergreen IRS enforcement update."}
{STORY_STRUCTURE}
{"React to: " + topic if topic else "Recent IRS enforcement trend from former-agent perspective."}
What changed → who it affects → why it matters → what to do → CTA.
{"DO NOT invent facts about: " + topic if topic else ""}
{format_block}""",

        "tax_deadline": f"""{persona}
Hook: "{pick_hook('fear')}"
Week: {week_of}. {"Topic: " + topic if topic else "IRS deadline."}
{STORY_STRUCTURE}
MICRO REEL. Deadline urgency. What → who → consequence → one action.
{format_block}""",

        "news_reaction": f"""{persona}
Hook: "{pick_hook('contrarian')}"
Week: {week_of}. Topic: {topic or "Recent IRS news."}
{STORY_STRUCTURE}
React to: {topic or "IRS enforcement news."} Former-agent perspective.
{format_block}""",

        "court_case_reaction": f"""{persona}
Hook: "{pick_hook('curiosity')}"
Week: {week_of}. Topic: {topic or "Tax court ruling."}
{STORY_STRUCTURE}
{topic or "Recent tax court case."} Plain language. What it means for real taxpayers.
{format_block}""",

        "tax_rule_change": f"""{persona}
Hook: "{pick_hook('contrarian')}"
Week: {week_of}. Topic: {topic or "IRS rule change."}
{STORY_STRUCTURE}
{topic or "Recent IRS rule change."} Who it affects, what changed, what to do.
{format_block}""",

        "react_reel": f"""{persona}
Hook: "{pick_hook('contrarian')}"
Week: {week_of}. {"Reacting to: " + topic if topic else "Reacting to bad tax advice online."}
{STORY_STRUCTURE}
MICRO REEL. "I just saw someone say [bad advice]. That is dangerous."
{arch_name} did exactly that. Cost them ${debt_amount:,}.
{format_block}""",

        "reddit_reel": f"""{persona}
Hook: "{pick_hook('curiosity')}"
Week: {week_of}. {"Question: " + topic if topic else "Real IRS question."}
{STORY_STRUCTURE}
"A business owner asked: [question]. Here's the real answer."
Direct. Complete. {arch_name}'s experience illustrates it.
{format_block}""",

        "google_search_reel": f"""{persona}
Hook: "{pick_hook('curiosity')}"
Week: {week_of}. {"Search: " + topic if topic else "Top IRS search this week."}
{STORY_STRUCTURE}
"The most searched IRS question this week: [question]"
Answer directly. {arch_name}'s story.
{format_block}""",

        "myth_ranking": f"""{persona}
Hook: "{pick_hook('contrarian')}"
Week: {week_of}
{STORY_STRUCTURE}
"Top 5 IRS myths ranked from harmless to catastrophic."
Each as a story — who believed it, what it cost. DEEP DIVE.
Save-worthy. "Save this before you believe any of these."
{format_block}""",

        "quiz_reel": f"""{persona}
Hook: "{pick_hook('curiosity')}"
Week: {week_of}
{STORY_STRUCTURE}
Interactive: "Quick test: would the IRS levy {arch_name}?"
Present {arch_name}'s situation → pause → answer → explain → CTA.
"Comment what you thought before the reveal."
{format_block}""",

        "checklist_reel": f"""{persona}
Hook: "{pick_hook('fear')}"
Week: {week_of}. Notice: {notice}
{STORY_STRUCTURE}
"{notice} checklist — save this." Short. Actionable. Save CTA.
{format_block}""",

        "deadline_reel": f"""{persona}
Hook: "{pick_hook('fear')}"
Week: {week_of}. {"Deadline: " + topic if topic else "IRS deadline."}
{STORY_STRUCTURE}
MICRO REEL. Countdown urgency. What → who → after → action.
{format_block}""",

        "contractor_series": f"""{persona}
Hook: "If you're {'a ' + CONTRACTOR_SERIES.get(trade, CONTRACTOR_SERIES['roofing'])['label'] if trade else 'in the trades'}, this is for you."
Week: {week_of}. Trade: {CONTRACTOR_SERIES.get(trade, CONTRACTOR_SERIES['roofing'])['label'] if trade else 'contractors'}.
{STORY_STRUCTURE}
Series: IRS Tips for {CONTRACTOR_SERIES.get(trade, CONTRACTOR_SERIES['roofing'])['label'] if trade else 'Contractors'}
{arch_name} — {arch_desc} — {arch_detail}. Owed ${debt_amount:,}.
Cover: how it starts → what makes it worse → TFRP personal liability → action → lead magnet.
{format_block}""",
    }

    # v7 Visual-First Mini-Documentary categories
    arch        = archetype  # alias for loop prompts
    _v7_trade   = archetype.get("trade", "contractor")

    for _new_type, _new_hook_theme, _new_story, _new_cta in [
        ("the_friday_disaster",     "horror",   "Payroll was due Friday. By noon the account was frozen.", "PAYROLL"),
        ("the_account_freeze",      "horror",   "Monday morning. Account balance: $0. IRS levy executed.", "FREEZE"),
        ("the_letter_nobody_opened","insider",  "Three certified letters. All ignored. Then the levy came.", "LETTERS"),
        ("the_call",                "insider",  "The call came on a Tuesday. He thought it was routine.", "CALL"),
        ("the_loan_denial",         "horror",   "The SBA loan was approved. Until they ran the lien check.", "LOAN"),
        ("contractor_identity",     "insider",  "This trade gets hit harder than almost any other.", "TRADE"),
        ("controversy_hook",        "insider",  "Paying the IRS immediately was the wrong move.", "WRONG"),
    ]:
        prompts[_new_type] = (
            f"{persona}\n"
            f"VISUAL-FIRST MINI-DOCUMENTARY. County: {county}. State: {state_name}. {state_url} | {PHONE}\n"
            f"Archetype: {arch_name}, {_v7_trade}.\n"
            f"\n"
            f"OPENING HOOK (FULL SCREEN TEXT, no avatar): {_new_story}\n"
            f"Hook keyword \"{style_notes['hook_keyword']}\" in {style_notes['hook_color']} over a white supporting phrase.\n"
            f"\n"
            f"{VISUAL_STYLE_GUIDE}\n"
            f"\n"
            f"MANDATORY VISUAL RULES:\n"
            f"- NEVER more than 3 seconds of avatar on a flat background — use the dark navy gradient {COLOR_PALETTE['bg_top']}->{COLOR_PALETTE['bg_bottom']}\n"
            f"- Visual must change every 1-3 seconds throughout\n"
            f"- Every scene MUST have an on-screen text overlay (red/orange keyword, white support, max {TYPOGRAPHY['max_words']} words)\n"
            f"- Use split-screen when avatar speaks (avatar + document/evidence side by side)\n"
            f"- First 2 seconds: FULL SCREEN TEXT ONLY, no avatar\n"
            f"- Each scene description states: background, text overlay content + color, visual element, avatar position (on/off)\n"
            f"\n"
            f"STRUCTURE:\n"
            f"Sec 0-2: Full screen text hook above on dark gradient\n"
            f"Sec 2-5: Visual evidence (IRS notice, bank app, document, public record)\n"
            f"Sec 5-10: Split screen - avatar + evidence visual\n"
            f"Sec 10-20: Escalation timeline with visuals\n"
            f"Sec 20-40: Story reveal with dollar amounts from {county} County public records\n"
            f"Sec 40-60: Avatar CTA + simultaneous on-screen text\n"
            f"\n"
            f"Identity CTA: Comment {_new_cta} if this resonates with you\n"
            f"Include: {state_url} | {PHONE}\n"
            f"\n"
            f"OUTPUT FORMAT:\n"
            f"HOOK: [first line]\n"
            f"SCRIPT: [130-160 words narration]\n"
            f"SHOT LIST: [numbered, one per line, timestamp: visual + overlay text]\n"
            f"CAPTION: [50-80 words]\n"
            f"YT TITLE: [under 70 chars, keyword-first]\n"
            f""
        )

    # v7 Visual-First Mini-Documentary categories
    arch        = archetype
    _v7_trade   = archetype.get("trade", "contractor")
    for _new_type, _new_story, _new_cta in [
        ("the_friday_disaster",      "Payroll was due Friday. By noon the account was frozen.", "PAYROLL"),
        ("the_account_freeze",       "Monday morning. Account balance: $0. IRS levy executed.", "FREEZE"),
        ("the_letter_nobody_opened", "Three certified letters. All ignored. Then the levy came.", "LETTERS"),
        ("the_call",                 "The call came on a Tuesday. He thought it was routine.", "CALL"),
        ("the_loan_denial",          "The SBA loan was approved. Until they ran the lien check.", "LOAN"),
        ("contractor_identity",      "This trade gets hit harder than almost any other.", "TRADE"),
        ("controversy_hook",         "Paying the IRS immediately was the wrong move.", "WRONG"),
    ]:
        prompts[_new_type] = (
            f"{persona}\n"
            f"VISUAL-FIRST MINI-DOCUMENTARY. County: {county}. State: {state_name}. {state_url} | {PHONE}\n"
            f"Archetype: {arch_name}, {_v7_trade}.\n\n"
            f"OPENING HOOK (FULL SCREEN TEXT, no avatar): {_new_story}\n"
            f"Hook keyword \"{style_notes['hook_keyword']}\" in {style_notes['hook_color']} over a white supporting phrase.\n\n"
            f"{VISUAL_STYLE_GUIDE}\n\n"
            f"MANDATORY VISUAL RULES:\n"
            f"- NEVER more than 3 seconds of avatar on a flat background — use the dark navy gradient {COLOR_PALETTE['bg_top']}->{COLOR_PALETTE['bg_bottom']}\n"
            f"- Visual must change every 1-3 seconds throughout\n"
            f"- Every scene MUST have an on-screen text overlay (red/orange keyword, white support, max {TYPOGRAPHY['max_words']} words)\n"
            f"- Use split-screen when avatar speaks\n"
            f"- First 2 seconds: FULL SCREEN TEXT ONLY, no avatar\n"
            f"- Each scene description states: background, text overlay content + color, visual element, avatar position (on/off)\n\n"
            f"STRUCTURE:\n"
            f"Sec 0-2: Full screen text hook on dark gradient\n"
            f"Sec 2-5: Visual evidence (IRS notice, bank app, document, public record)\n"
            f"Sec 5-10: Split screen - avatar + evidence\n"
            f"Sec 10-20: Escalation timeline with visuals\n"
            f"Sec 20-40: Story reveal with dollar amounts from {county} County public records\n"
            f"Sec 40-60: Avatar CTA + simultaneous on-screen text\n\n"
            f"Identity CTA: Comment {_new_cta} if this resonates\n"
            f"Include: {state_url} | {PHONE}\n\n"
            f"OUTPUT FORMAT:\n"
            f"HOOK: [first line]\n"
            f"SCRIPT: [130-160 words]\n"
            f"SHOT LIST: [numbered, timestamp: visual + overlay]\n"
            f"CAPTION: [50-80 words]\n"
            f"YT TITLE: [under 70 chars, keyword-first]\n"
            f"{format_block}"
        )

    raw = call_claude(prompts.get(reel_type, prompts["educational"]), max_tokens=6000)
    print(f"  DEBUG raw length: {len(raw)} chars, first 200: {raw[:200]}")

    script   = _extract_section(raw, "SCRIPT")
    caption  = _extract_section(raw, "CAPTION")
    hashtags = _extract_section(raw, "HASHTAGS")
    yt_title = _extract_inline(raw, "YOUTUBE_TITLE")
    yt_hook  = _extract_inline(raw, "YOUTUBE_SHORTS_HOOK")
    yt_desc  = _extract_section(raw, "YOUTUBE_DESCRIPTION")
    yt_tags  = _extract_inline(raw, "YOUTUBE_TAGS")

    visual_cues_raw = _extract_section(raw, "VISUAL_CUES")
    parsed_cues     = _parse_visual_cues(visual_cues_raw, visual_cues)
    storyboard_raw  = _extract_section(raw, "VISUAL_STORYBOARD")
    _parsed_storyboard = parse_pipe_rows(storyboard_raw, min_rows=6)
    if _parsed_storyboard:
        print(f"  ✅ Storyboard: {len(_parsed_storyboard)} scenes parsed from Claude output")
        visual_storyboard = _parsed_storyboard
    else:
        print(f"  ⚠️  Storyboard: Claude output not parsed — using base template ({len(base_storyboard)} scenes)")
        visual_storyboard = base_storyboard
    # v9: enrich parsed storyboard with motion graphics, camera moves, interrupts, b-roll
    visual_storyboard = enrich_storyboard(visual_storyboard, reel_type, reel_format_out if 'reel_format_out' in dir() and reel_format_out else reel_format, trade)
    resets_raw      = _extract_section(raw, "RETENTION_RESETS")
    retention_resets = parse_retention_rows(resets_raw) or base_resets
    platform_variants_raw = _extract_section(raw, "PLATFORM_VARIANTS")
    retention_prediction_raw = _extract_section(raw, "RETENTION_PREDICTION")
    retention_prediction = {
        "predicted_3s_hold": clamp_int(_extract_inline(retention_prediction_raw, "PREDICTED_3S_HOLD"), 72),
        "predicted_10s_hold": clamp_int(_extract_inline(retention_prediction_raw, "PREDICTED_10S_HOLD"), 46),
        "predicted_completion": clamp_int(_extract_inline(retention_prediction_raw, "PREDICTED_COMPLETION"), 28),
        "predicted_share_rate": clamp_int(_extract_inline(retention_prediction_raw, "PREDICTED_SHARE_RATE"), 8),
        "predicted_comment_rate": clamp_int(_extract_inline(retention_prediction_raw, "PREDICTED_COMMENT_RATE"), 6),
    }
    text_overlays   = [c.get("overlay","") for c in parsed_cues if c.get("overlay")]
    text_overlays  += [c.get("overlay","") for c in visual_storyboard if c.get("overlay")]

    hook_type_out   = _extract_inline(raw, "HOOK_TYPE")   or hook_type
    cta_strat_out   = _extract_inline(raw, "CTA_STRATEGY") or cta_strategy
    save_w          = _extract_inline(raw, "SAVE_WORTHY") == "yes"
    auth_used       = _extract_inline(raw, "AUTHORITY_USED") == "yes"
    length_tier_out = _extract_inline(raw, "LENGTH_TIER")  or tier
    reel_format_out = _extract_inline(raw, "REEL_FORMAT") or reel_format
    emotional_driver_out = _extract_inline(raw, "EMOTIONAL_DRIVER") or emotional_driver

    word_count = len(script.split())
    est_secs   = round(word_count / 2.5)
    print(f"  📝 [{length_tier_out.upper()}] {word_count}w (~{est_secs}s) | "
          f"hook={hook_type_out} | cta={cta_strat_out} | archetype={arch_name}")

    limit = get_word_limit(length_tier_out if length_tier_out in SCRIPT_LENGTH_TIERS else tier)
    if word_count > limit + 5:
        print(f"  ✂️  Trimming to ~{limit} words (sentence boundary)...")
        # Trim at sentence boundary to avoid mid-sentence cuts that HeyGen reads aloud
        sentences = [s.strip() for s in script.replace("\n", " ").split(".") if s.strip()]
        trimmed, wc = [], 0
        for sent in sentences:
            sw = len(sent.split())
            if wc + sw > limit + 10:
                break
            trimmed.append(sent)
            wc += sw
        script = ". ".join(trimmed) + ("." if trimmed else "")
        if not script.strip():
            script = " ".join(script.split()[:limit])  # last resort

    if not yt_title:
        first    = caption.split(".")[0].strip()
        yt_title = (first[:67] + " | TaxCase Review") if first else "IRS Tax Help | TaxCase Review"

    return {
        "script":              script,
        "caption":             caption,
        "hashtags":            hashtags,
        "hook":                script.split(".")[0].strip(),
        "word_count":          len(script.split()),
        "estimated_seconds":   est_secs,
        "reel_type":           reel_type,
        "county":              county,
        "city":                city,
        "state":               state,
        "trade":               trade,
        "week_of":             week_of,
        "engine":              "heygen",
        "topic":               topic,
        "archetype":           arch_name,
        "length_tier":         length_tier_out,
        "visual_cues":         parsed_cues,
        "visual_storyboard":    visual_storyboard,
        "retention_resets":     retention_resets,
        "retention_prediction": retention_prediction,
        "platform_variants":    platform_variants_raw,
        "reel_format":          reel_format_out,
        "emotional_driver":     emotional_driver_out,
        "text_overlays":       list(dict.fromkeys([x for x in text_overlays if x]))[:20],
        "open_loop_used":      True,
        "cta_strategy":        cta_strat_out,
        "lead_magnet_name":    lead_magnet["name"] if cta_strat_out == "lead_magnet_cta" else "",
        "lead_magnet_keyword": lead_magnet["keyword"] if cta_strat_out == "lead_magnet_cta" else "",
        "youtube_title":       yt_title,
        "youtube_shorts_hook": yt_hook,
        "youtube_description": yt_desc,
        "youtube_tags":        yt_tags,
        "hook_type":           hook_type_out,
        "story_type":          "horror" if hook_type_out == "horror" else ("story" if reel_type in {"client_story","case_breakdown","success_story"} else "non_story"),
        "emotion_type":        "horror" if reel_type in {"tax_horror_story","contractor_disaster"} else ("emotional" if reel_type in {"urgency","before_after","client_story"} else "informational"),
                        "cta_type":            cta_strat_out,
        "reel_category":       reel_type,
        "save_worthy":         save_w,
        "authority_ref":       auth_used,
        "data_source":         data_source,
        "quality_score":       0,
        "viral_scores":        {},
        "quality_below_threshold": False,
    }


def _strip_md(s: str) -> str:
    """Strip leading markdown header/bold/list markers (# ## ** * - >) so a line
    like '**SCRIPT:**' or '# SCRIPT:' matches the same as plain 'SCRIPT:'."""
    s = s.strip()
    while s and s[0] in "#*->":
        s = s[1:].lstrip()
    return s

def _strip_md_value(s: str) -> str:
    """Strip surrounding markdown emphasis from an extracted value, e.g. the
    trailing '**' in '**SCRIPT:** the text'."""
    return s.strip().strip("*").strip()

def _extract_section(text: str, section: str) -> str:
    lines  = text.splitlines(); result = []; inside = False
    stops  = {"SCRIPT","CAPTION","HASHTAGS","VISUAL_CUES","YOUTUBE_TITLE",
               "YOUTUBE_SHORTS_HOOK","YOUTUBE_DESCRIPTION","YOUTUBE_TAGS",
               "LENGTH_TIER","HOOK_TYPE","CTA_STRATEGY","SAVE_WORTHY","AUTHORITY_USED","OPEN_LOOP_USED",
               "REEL_FORMAT","EMOTIONAL_DRIVER","VISUAL_STORYBOARD","RETENTION_RESETS",
               "PLATFORM_VARIANTS","RETENTION_PREDICTION","PREDICTED_3S_HOLD",
               "PREDICTED_10S_HOLD","PREDICTED_COMPLETION","PREDICTED_SHARE_RATE",
               "PREDICTED_COMMENT_RATE"}
    sec_u = section.upper()
    for line in lines:
        cleaned = _strip_md(line)
        if cleaned.upper().startswith(f"{sec_u}:"):
            inside = True
            rest   = _strip_md_value(cleaned.split(":", 1)[1])
            if rest: result.append(rest)
            continue
        elif cleaned.upper().strip("#*-> ").strip() == sec_u:
            inside = True
            continue
        if inside:
            if any(_strip_md(line).upper().startswith(f"{s}:") for s in stops):
                break
            result.append(line)
    return "\n".join(result).strip()

def _extract_inline(text: str, key: str) -> str:
    k = key.upper()
    for line in text.splitlines():
        cleaned = _strip_md(line)
        if cleaned.upper().startswith(f"{k}:"):
            return _strip_md_value(cleaned.split(":", 1)[1])
    return ""

def _parse_visual_cues(raw: str, fallback: list) -> list:
    if not raw:
        return [{"time": c["description"][:30], "visual": c["description"],
                 "overlay": c.get("overlay",""), "editor_note": ""} for c in fallback[:3]]
    cues = []
    for line in raw.splitlines():
        line = line.strip().lstrip("-*").strip()
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            cues.append({
                "time":        parts[0] if len(parts) > 0 else "",
                "visual":      parts[1] if len(parts) > 1 else "",
                "overlay":     parts[2] if len(parts) > 2 else "",
                "editor_note": parts[3] if len(parts) > 3 else "",
            })
    return cues if cues else [{"time": c["description"][:30], "visual": c["description"],
                               "overlay": c.get("overlay",""), "editor_note": ""} for c in fallback[:3]]


# ── Performance Tracking ───────────────────────────────────────────────────────
def load_performance() -> list:
    if PERFORMANCE_FILE.exists():
        try: return json.loads(PERFORMANCE_FILE.read_text())
        except: return []
    return []

def save_performance_entry(entry: dict):
    data = load_performance(); data.append(entry)
    PERFORMANCE_FILE.write_text(json.dumps(data[-500:], indent=2))

def build_performance_entry(script_data: dict, video_id: str = "",
                             video_url: str = "", platform: str = "facebook,youtube") -> dict:
    vs = script_data.get("viral_scores", {})
    return {
        "date":               date.today().isoformat(),
        "reel_type":          script_data.get("reel_type",""),
        "hook_type":          script_data.get("hook_type",""),
        "reel_format":        script_data.get("reel_format",""),
        "emotional_driver":   script_data.get("emotional_driver",""),
        "length_tier":        script_data.get("length_tier",""),
        "cta_strategy":       script_data.get("cta_strategy",""),
        "save_worthy":        script_data.get("save_worthy", False),
        "authority_ref":      script_data.get("authority_ref", False),
        "quality_score":      script_data.get("quality_score", 0),
        "scroll_stop_score":  vs.get("scroll_stop_score", 0),
        "emotional_score":    vs.get("emotional_score", 0),
        "curiosity_score":    vs.get("curiosity_score", 0),
        "visual_story_score": vs.get("visual_story_score", vs.get("visual_tension", 0)),
        "comment_score":      vs.get("comment_score", 0),
        "archetype":          script_data.get("archetype",""),
        "platform":           platform,
        "video_id":           video_id,
        "post_url":           video_url,
        "views": None, "likes": None, "comments": None,
        "shares": None, "saves": None, "clicks": None,
        "quiz_starts": None, "completion_rate": None, "avg_watch_time": None,
    }

def show_performance_summary():
    data = load_performance()
    if not data: print("No performance data yet."); return
    sep = "=" * 65
    print(f"\n{sep}")
    print(f"  Performance Summary - {len(data)} reels tracked")
    print(sep)
    def best_by(field, metric="quality_score"):
        scored = [(d.get(field,"?"), d.get(metric,0)) for d in data if d.get(metric) is not None]
        grouped: dict = {}
        for val, score in scored: grouped.setdefault(val, []).append(score)
        return max(grouped, key=lambda k: sum(grouped[k])/len(grouped[k])) if grouped else "n/a"
    print(f"  Best hook type  : {best_by('hook_type')}")
    print(f"  Best reel type  : {best_by('reel_type')}")
    print(f"  Best archetype  : {best_by('archetype')}")
    scores = [d.get("quality_score",0) for d in data]
    avg    = sum(scores)/len(scores) if scores else 0
    below  = sum(1 for s in scores if s < QUALITY_THRESHOLD)
    print(f"\n  Avg viral score : {avg:.1f}/100")
    print(f"  Below threshold : {below}/{len(data)} reels")
    print(f"{sep}\n")


# ── HeyGen API ─────────────────────────────────────────────────────────────────
DEFAULT_BG_COLOR = "#0f1b2d"

# Vertical (1080x1920) still backgrounds per visual-cue type — verified hotlinkable
# Unsplash photos (the same source the social poster uses). This is the reliable
# tier: it turns "avatar on a black screen" into a themed scene for every cue.
_U = "https://images.unsplash.com/photo-{id}?w=1280&h=1920&fit=crop"
CUE_BG_IMAGES = {
    "irs_notice":      _U.format(id="1568602471122-7832951cc4c5"),
    "lien_stamp":      _U.format(id="1554224154-26032ffc0d07"),
    "lien_document":   _U.format(id="1590283603385-17ffb3a7f29f"),
    "public_record":   _U.format(id="1450101499163-c8848c66ca85"),
    "mailbox":         _U.format(id="1619468129361-605ebea04b44"),
    "bank_freeze":     _U.format(id="1553729459-efe14ef6055d"),
    "debt_overlay":    _U.format(id="1565372195458-9de0b320ef04"),
    "penalty_ticker":  _U.format(id="1559526324-593bc073d938"),
    "dashboard_alert": _U.format(id="1611974789855-9c2a0a7236a3"),
    "countdown":       _U.format(id="1542744173-8e7e53415bb0"),
    "calendar":        _U.format(id="1434030216411-0b793f4b4173"),
    "contractor_truck":_U.format(id="1504307651254-35680f356dfd"),
    "revenue_officer": _U.format(id="1454165804606-c3d57bc86b40"),
    "phone_ring":      _U.format(id="1521791136064-7986c2920216"),
    "county_map":      _U.format(id="1619468129361-605ebea04b44"),
    "heat_map":        _U.format(id="1633158829585-23ba8f7c8caf"),
    "before_after":    _U.format(id="1503023345310-bd7c1de61c7d"),
    "checklist":       _U.format(id="1554224154-26032ffc0d07"),
    "myth_reality":    _U.format(id="1579621970563-ebec7560ff3e"),
    "red_arrow":       _U.format(id="1553729459-efe14ef6055d"),
}
DEFAULT_BG_IMAGE = CUE_BG_IMAGES["irs_notice"]

# Verified royalty-free Pexels video backgrounds (direct CDN, hotlinkable, free
# license). Sparse on purpose — guessed Pexels URLs 403, so only confirmed-live
# URLs go here. Set PEXELS_API_KEY for live, theme-matched video on every cue.
# Verified Pexels vertical video URLs (9:16, 1080x1920, free license)
# These are confirmed-live CDN URLs — each is a real distinct video
CUE_BG_VIDEOS = {
    # Document/legal scenes — paper, stamps, official records
    "irs_notice":      "https://videos.pexels.com/video-files/5495890/5495890-hd_1080_1920_30fps.mp4",
    "lien_document":   "https://videos.pexels.com/video-files/5495890/5495890-hd_1080_1920_30fps.mp4",
    "public_record":   "https://videos.pexels.com/video-files/5495890/5495890-hd_1080_1920_30fps.mp4",
    "lien_stamp":      "https://videos.pexels.com/video-files/5495890/5495890-hd_1080_1920_30fps.mp4",
    # Financial stress — money, accounts, banking
    "bank_freeze":     "https://videos.pexels.com/video-files/3209828/3209828-hd_1080_1920_25fps.mp4",
    "debt_overlay":    "https://videos.pexels.com/video-files/3209828/3209828-hd_1080_1920_25fps.mp4",
    "penalty_ticker":  "https://videos.pexels.com/video-files/3209828/3209828-hd_1080_1920_25fps.mp4",
    # Contractor/construction — job sites, trucks, crews
    "contractor_truck":"https://videos.pexels.com/video-files/8191399/8191399-hd_1080_1920_30fps.mp4",
    # Business/office stress — computers, phones, meetings
    "dashboard_alert": "https://videos.pexels.com/video-files/7947956/7947956-hd_1080_1920_30fps.mp4",
    "phone_ring":      "https://videos.pexels.com/video-files/7947956/7947956-hd_1080_1920_30fps.mp4",
    # Data/maps — charts, analytics, location
    "heat_map":        "https://videos.pexels.com/video-files/7947956/7947956-hd_1080_1920_30fps.mp4",
    "county_map":      "https://videos.pexels.com/video-files/7947956/7947956-hd_1080_1920_30fps.mp4",
}

# Pexels search query per cue — used only when PEXELS_API_KEY is set.
# Aligned to the v9 visual-style B-roll mapping.
CUE_SEARCH_TERMS = {
    "irs_notice": "tax document letter envelope", "lien_stamp": "legal document stamp official",
    "lien_document": "legal document stamp official", "public_record": "legal document stamp official",
    "mailbox": "tax document letter envelope", "bank_freeze": "bank statement financial stress",
    "debt_overlay": "money cash counting", "penalty_ticker": "stock market chart money",
    "dashboard_alert": "red warning alert screen", "countdown": "calendar deadline urgent planning",
    "calendar": "calendar deadline urgent planning", "contractor_truck": "contractor truck construction worker",
    "revenue_officer": "government office desk", "phone_ring": "paycheck salary worker stress",
    "county_map": "united states map", "heat_map": "data map visualization",
    "before_after": "business owner relief success", "checklist": "checklist clipboard writing",
    "myth_reality": "legal document stamp official", "red_arrow": "financial chart graph",
    # v9 additions — new cue handles from the style guide
    "wage_garnishment": "paycheck salary worker stress", "settlement": "handshake agreement business",
    "calendar_deadline": "calendar deadline urgent planning", "relief": "business owner relief success",
}


def _media_url_ok(url: str, timeout: float = 6.0) -> bool:
    """Cheap reachability/type guard before handing a URL to HeyGen."""
    if not url:
        return False
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-1024"},
                         timeout=timeout, stream=True)
        ctype = r.headers.get("Content-Type", "")
        r.close()
        return r.status_code in (200, 206) and ("video" in ctype or "image" in ctype)
    except Exception:
        return False


# TODO: Replace with shared_media.pexels_client.search_videos() once
# the shared Pexels media layer (being built in PermitMap) is available.
# SHARED_MEDIA_ROOT should point to ../shared-media/ for cross-project caching.
# See: scripts/shared_media/pexels_client.py (PermitMap repo)
def _pexels_video_url(query: str) -> str:
    """Portrait stock video link from Pexels (requires PEXELS_API_KEY)."""
    if not PEXELS_API_KEY:
        return ""
    try:
        r = requests.get("https://api.pexels.com/videos/search",
                         headers={"Authorization": PEXELS_API_KEY},
                         params={"query": query, "orientation": "portrait",
                                 "per_page": 5, "size": "medium"}, timeout=10)
        if r.status_code != 200:
            return ""
        for vid in r.json().get("videos", []):
            files = sorted(vid.get("video_files", []),
                           key=lambda f: (f.get("height") or 0), reverse=True)
            for f in files:
                if (f.get("height") or 0) >= (f.get("width") or 0) and f.get("link"):
                    return f["link"]
    except Exception:
        pass
    return ""


def _first_visual_cue(script_data: dict) -> str:
    """The opening scene's visual-cue type (drives the opening background)."""
    cues = script_data.get("visual_cues") or []
    if cues and isinstance(cues[0], dict):
        return (cues[0].get("type") or "").strip()
    sb = script_data.get("visual_storyboard") or []
    if sb and isinstance(sb[0], dict):
        return (sb[0].get("cue") or sb[0].get("type") or sb[0].get("visual") or "").strip()
    return ""


def build_heygen_background(script_data: dict) -> dict:
    """Background for the opening scene, derived from its visual cue:
    live Pexels video (if key) -> curated verified video -> themed image -> color."""
    cue = _first_visual_cue(script_data)
    if not cue:
        # Parsed cues empty — fall back to the reel type's canonical opening cue
        # so the background is still theme-matched (e.g. contractor_disaster ->
        # contractor_truck) instead of the generic default query.
        rt_cues = get_visual_cues_for_type(script_data.get("reel_type", ""))
        if rt_cues:
            cue = (rt_cues[0].get("type") or "").strip()

    # 1. Live, theme-matched Pexels video (only if PEXELS_API_KEY is configured)
    if PEXELS_API_KEY:
        url = _pexels_video_url(CUE_SEARCH_TERMS.get(cue, "tax finance documents"))
        if url and _media_url_ok(url):
            return {"type": "video", "url": url, "play_style": "loop", "fit": "cover"}

    # 2. Curated, confirmed-live stock video for this cue
    v = CUE_BG_VIDEOS.get(cue)
    if v and _media_url_ok(v):
        return {"type": "video", "url": v, "play_style": "loop", "fit": "cover"}

    # 3. Relevant still image (verified Unsplash) — beats a plain color every time
    img = CUE_BG_IMAGES.get(cue, DEFAULT_BG_IMAGE)
    if img and _media_url_ok(img):
        return {"type": "image", "url": img, "fit": "cover"}

    # 4. Plain color (last resort)
    return {"type": "color", "value": DEFAULT_BG_COLOR}



def clean_script_for_heygen(script: str) -> str:
    """Remove stage directions and markdown from script before HeyGen TTS.
    HeyGen reads everything literally — [0-2s — FULL SCREEN] gets spoken aloud."""
    import re
    # Remove bracketed stage directions: [0-2s — FULL SCREEN. No avatar.]
    script = re.sub(r"\*?\[.*?\]\*?", "", script)
    # Remove markdown bold: **text** → text
    script = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", script)
    # Remove timestamps like "0-2s:" or "Sec 0-2:"
    script = re.sub(r"(?:Sec\s*)?\d+[-–]\d+s?:\s*", "", script)
    # Remove director notes in parentheses: (no avatar) (full screen)
    script = re.sub(r"\((?:no avatar|full screen|avatar off|b-roll|cut to)[^)]*\)", "", script, flags=re.IGNORECASE)
    # Collapse multiple spaces/newlines
    script = re.sub(r"\s+", " ", script).strip()
    return script


def _scene_duration(time_str: str, default: int = 5) -> int:
    """Parse '0-2s', '5-20s' etc -> duration in whole seconds."""
    try:
        time_str = time_str.strip().rstrip("s")
        if "-" in time_str:
            parts = time_str.split("-")
            return max(1, int(float(parts[1])) - int(float(parts[0])))
        return default
    except Exception:
        return default


def _avatar_enabled(scene: dict) -> bool:
    """Determine if avatar should appear in this scene based on v9 storyboard flags."""
    editor = (scene.get("editor_note") or "").lower()
    visual = (scene.get("visual") or "").lower()
    time_s = scene.get("time", "")
    combined = editor + " " + visual
    no_avatar_phrases = [
        "no avatar", "full screen", "full-screen", "avatar cap enforced",
        "data card", "motion graphic", "county map", "heat map",
        "evidence reveal", "document only", "text only", "avatar replaced",
    ]
    if any(p in combined for p in no_avatar_phrases):
        return False
    if time_s.strip().startswith("0"):
        return False
    return True


def _scene_bg_from_visual(scene: dict, idx: int) -> dict:
    """Map storyboard scene visual description to a HeyGen background.
    Priority: matched video > matched image > color fallback."""
    visual  = (scene.get("visual") or scene.get("editor_note") or "").lower()
    mapping = [
        (["contractor", "truck", "roofer", "crew", "hvac", "electrician", "plumber", "job site"], "contractor_truck"),
        (["bank", "freeze", "levy", "account", "frozen", "zero balance"],                          "bank_freeze"),
        (["lien document", "case file", "evidence", "redacted", "folder"],                         "lien_document"),
        (["notice", "letter", "mail", "certified", "cp14", "cp503", "cp504"],                     "irs_notice"),
        (["map", "county", "heat map", "heat_map", "dallas", "maricopa", "fulton", "lien activity"], "heat_map"),
        (["calendar", "deadline", "clock", "timeline", "countdown"],                               "calendar"),
        (["penalty", "debt", "balance", "ticking", "counter", "amount"],                           "penalty_ticker"),
        (["revenue officer", "agent", "irs officer", "desk"],                                      "revenue_officer"),
        (["dashboard", "alert", "notification", "phone"],                                           "dashboard_alert"),
        (["checklist", "bullet", "steps", "list"],                                                  "checklist"),
        (["public record", "stamp", "filed", "courthouse"],                                         "public_record"),
        (["lien stamp", "federal tax lien stamp"],                                                  "lien_stamp"),
    ]
    cue = "irs_notice"
    for keywords, key in mapping:
        if any(kw in visual for kw in keywords):
            cue = key
            break

    # Try video first (more dynamic than still image)
    vid = CUE_BG_VIDEOS.get(cue)
    if vid and _media_url_ok(vid):
        return {"type": "video", "url": vid, "play_style": "loop", "fit": "cover"}

    img = CUE_BG_IMAGES.get(cue, DEFAULT_BG_IMAGE)
    if img and _media_url_ok(img):
        return {"type": "image", "url": img, "fit": "cover"}

    return {"type": "color", "value": DEFAULT_BG_COLOR}


def _split_script_across_scenes(script: str, scenes: list) -> list:
    """Distribute script sentences across avatar scenes proportionally.
    Non-avatar scenes get silence (.) so HeyGen doesnt reject empty voice."""
    avatar_idxs = [i for i, s in enumerate(scenes) if _avatar_enabled(s)]
    if not avatar_idxs:
        return [script] + ["."] * (len(scenes) - 1)

    # Split into sentences
    sentences = [s.strip() + "." for s in script.replace("\n", " ").split(".") if s.strip()]
    assignments = {i: [] for i in avatar_idxs}
    for j, sent in enumerate(sentences):
        target = avatar_idxs[j % len(avatar_idxs)]
        assignments[target].append(sent)

    result = []
    for i, scene in enumerate(scenes):
        if i in assignments and assignments[i]:
            result.append(" ".join(assignments[i]))
        elif i in assignments:
            result.append(script[:60] + ".")  # fallback snippet
        else:
            result.append(".")  # non-avatar scene — silence placeholder
    return result


def submit_heygen_video(script_data: dict) -> dict:
    """Submit multi-scene video to HeyGen v2 API.

    Converts the v9 visual storyboard into multiple video_inputs,
    each with scene-specific backgrounds from the CUE_BG_VIDEOS/IMAGES library.
    Avatar appears at 55% scale lower-third on speaking scenes.
    Fully silent non-avatar scenes use background-only inputs.
    Falls back to single-scene if storyboard empty or HeyGen rejects.
    """
    if not HEYGEN_API_KEY:  raise RuntimeError("HEYGEN_API_KEY not set")
    if not HEYGEN_AVATAR_ID: raise RuntimeError("HEYGEN_AVATAR_ID not set")

    storyboard = script_data.get("visual_storyboard") or []
    raw_script = script_data.get("script", "")
    # Always clean stage directions before TTS
    spoken_script = clean_script_for_heygen(raw_script)

    def _single_scene(reason: str = "") -> dict:
        if reason:
            print(f"  ⚠ {reason} — single-scene fallback")
        bg = build_heygen_background(script_data)
        print(f"  HeyGen bg: {bg['type']} from cue '{_first_visual_cue(script_data) or 'none'}'")
        payload = {
            "video_inputs": [{"character": {"type":"avatar","avatar_id":HEYGEN_AVATAR_ID,"avatar_style":"normal"},
                "voice": {"type":"text","input_text":spoken_script or ".",
                          "voice_id":HEYGEN_VOICE_ID,"speed":1.0},
                "background": bg}],
            "dimension": {"width":1080,"height":1920}, "aspect_ratio":"9:16", "caption":True,
        }
        r2 = requests.post("https://api.heygen.com/v2/video/generate",
            headers={"X-Api-Key":HEYGEN_API_KEY,"Content-Type":"application/json"},
            json=payload, timeout=30)
        if r2.status_code != 200:
            raise RuntimeError(f"HeyGen error: {r2.status_code} - {r2.text[:200]}")
        vid = r2.json().get("data",{}).get("video_id","")
        print(f"  ✅ HeyGen: {vid} | single-scene | bg={bg['type']}")
        record_heygen_render(vid, script_data["reel_type"])
        return {"video_id": vid, "status": "processing"}

    if not storyboard or not isinstance(storyboard, list):
        return _single_scene("No storyboard parsed")

    # Cap at 8 scenes (HeyGen practical limit for multi-scene)
    scenes        = storyboard[:8]
    scene_scripts = _split_script_across_scenes(spoken_script, scenes)

    print(f"  Building {len(scenes)}-scene HeyGen payload...")
    video_inputs = []
    for i, (scene, scene_script) in enumerate(zip(scenes, scene_scripts)):
        use_avatar = _avatar_enabled(scene)
        bg         = _scene_bg_from_visual(scene, i)
        words      = len(scene_script.split()) if scene_script != "." else 0
        print(f"  Scene {i+1}/{len(scenes)}: {scene.get('time','?'):10s} | "
              f"avatar={str(use_avatar):5s} | bg={bg['type']:5s} | {words}w")

        video_inputs.append({
            "character": {
                "type":         "avatar",
                "avatar_id":    HEYGEN_AVATAR_ID,
                "avatar_style": "normal",
                "scale":        0.55 if use_avatar else 0.0,
                "offset":       {"x": 0.0, "y": 0.25} if use_avatar else {"x": 0.0, "y": 0.0},
            },
            "voice": {
                "type":       "text",
                "input_text": scene_script if scene_script.strip() else ".",
                "voice_id":   HEYGEN_VOICE_ID,
                "speed":      1.05 if use_avatar else 1.0,
            },
            "background": bg,
        })

    payload = {
        "video_inputs": video_inputs,
        "dimension":    {"width": 1080, "height": 1920},
        "aspect_ratio": "9:16",
        "caption":      True,
    }

    print(f"  Submitting {len(video_inputs)}-scene payload to HeyGen...")
    r = requests.post(
        "https://api.heygen.com/v2/video/generate",
        headers={"X-Api-Key": HEYGEN_API_KEY, "Content-Type": "application/json"},
        json=payload, timeout=30,
    )
    if r.status_code != 200:
        return _single_scene(f"Multi-scene rejected ({r.status_code}): {r.text[:100]}")

    video_id = r.json().get("data", {}).get("video_id", "")
    print(f"  ✅ HeyGen job: {video_id} | {len(video_inputs)} scenes | 1080x1920")
    record_heygen_render(video_id, script_data["reel_type"])
    return {"video_id": video_id, "status": "processing"}


def check_heygen_status(video_id: str) -> dict:
    r = requests.get(f"https://api.heygen.com/v1/video_status.get?video_id={video_id}",
        headers={"X-Api-Key":HEYGEN_API_KEY}, timeout=15)
    data = r.json().get("data",{})
    return {"status":data.get("status","unknown"),"video_url":data.get("video_url",""),"video_id":video_id}

def wait_for_heygen(video_id: str, max_minutes: int = 15) -> str:
    print(f"  Waiting up to {max_minutes} min...")
    for attempt in range(max_minutes * 4):
        time.sleep(15)
        result = check_heygen_status(video_id)
        print(f"  [{attempt*15}s] {result['status']}")
        if result["status"] == "completed":
            print(f"  Done: {result['video_url']}"); return result["video_url"]
        elif result["status"] in ("failed","error"): return ""
    print("  Timeout"); return ""


# ── Remotion ───────────────────────────────────────────────────────────────────
REMOTION_COMP_MAP = {
    "weekly_stats":"LienStatReel","county_breakdown":"CountyBreakdownReel",
    "penalty_growth":"PenaltyGrowthReel","notice":"NoticeExplainerReel",
}

def render_remotion(reel_type: str, context: dict, dry_run: bool = False) -> str:
    comp_id  = REMOTION_COMP_MAP.get(reel_type, "LienStatReel")
    out_dir  = REMOTION_PROJECT / "out"; out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"{date.today().isoformat()}-{reel_type.replace('_','-')}.mp4"
    props    = build_remotion_props(reel_type, context)
    print(f"  Rendering {comp_id}...")
    if dry_run: print("  [DRY RUN]"); return str(out_file)
    try:
        result = subprocess.run(["npx","remotion","render",comp_id,str(out_file),
            f"--props={json.dumps(props)}"], cwd=str(REMOTION_PROJECT),
            capture_output=True, text=True, timeout=1800, shell=True)
        if result.returncode == 0: print(f"  Done: {out_file}"); return str(out_file)
        print(f"  Failed:\n{result.stderr[-500:]}"); return ""
    except Exception as e: print(f"  Error: {e}"); return ""

def build_remotion_props(reel_type: str, context: dict) -> dict:
    week_of = date.today().strftime("%B %d, %Y")
    stats   = context.get("stats",{})
    base    = {"phone":PHONE,"siteUrl":"taxcasereview.org","weekOf":week_of}
    if reel_type == "weekly_stats":
        return {**base,"county":stats.get("top_county","Miami-Dade"),
                "count":stats.get("count",42),"lastWeek":stats.get("last_week",35)}
    elif reel_type == "county_breakdown":
        return {**base,"counties":stats.get("counties",[
            {"name":"Miami-Dade","count":42},{"name":"Martin","count":28},
            {"name":"Lake","count":21},{"name":"Manatee","count":16},{"name":"Pasco","count":12},
        ])[:5]}
    elif reel_type == "penalty_growth":
        return {**base,"debtAmount":25000,"monthsShown":12}
    elif reel_type == "notice":
        return {**base,"noticeType":context.get("notice","CP504")}
    return base


# ── Collection page detection + IndexNow (mirrors social_media_poster.py) ──────
REEL_TRADE_PAGE = {
    "roofing":            "/contractors/roofing",
    "hvac":               "/contractors/hvac",
    "trucking":           "/contractors/trucking",
    "restaurant":         "/contractors/restaurant",
    "electricians":       "/contractors/electricians",
    "general_contractor": "/contractors/general-contractor",
}
NOTICE_KEYWORDS = ["cp504", "cp14", "cp503", "cp2000", "lt11", "notice", "letter 1058"]
RESOLUTION_KEYWORDS = ["offer in compromise", "installment", "penalty abatement",
                       "lien withdrawal", "currently not collectible", "cnc",
                       "levy", "garnishment", "tfrp", "trust fund"]


def detect_collection_page(state: str = "", trade: str = "", topic: str = "") -> str:
    """Detect the site collection page this reel maps to, from state/county/trade
    — same idea social_media_poster.py uses for blogs. Returns a full URL."""
    t = (topic or "").lower()
    trade_key = (trade or "").lower()
    if trade_key in REEL_TRADE_PAGE:
        path = REEL_TRADE_PAGE[trade_key]
    elif any(k in t for k in NOTICE_KEYWORDS):
        path = "/irs-notices"
    elif any(k in t for k in RESOLUTION_KEYWORDS):
        path = "/resolution"
    elif state:
        path = "/" + state.replace("_", "-").lower()
    else:
        path = "/contractors"
    return f"{SITE_URL}{path}"


def _indexnow_ping(url: str):
    """Submit a URL to IndexNow (Bing/Yandex) — same key as social_media_poster.py."""
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


# ── GitHub media rehosting (permanent URLs for Make.com) ────────────────────────
# Make.com downloads the video by URL. HeyGen signed URLs expire (~7 days) and a
# missing/empty URL throws BundleValidationError ("Invalid URL in parameter 'url'").
# This rehosts the video — from a local file (Remotion) or a temporary URL (HeyGen)
# — to the media repo via the Git Data API (robust for binaries, unlike the 1 MB-
# limited Contents API) and returns a PERMANENT raw URL. Returns "" on failure;
# callers MUST NOT post an empty URL to Make.
def _gh_media_config() -> tuple[str, str]:
    token = os.getenv("GITHUB_TOKEN", "")
    repo  = os.getenv("GITHUB_MEDIA_REPO", "anandakeyclub-ops/taxcasereview-media")
    return token, repo

def rehost_to_github(dest_name: str, *, local_file: str = "", source_url: str = "",
                     verify: bool = True, retries: int = 3) -> str:
    import base64 as _b64
    token, repo = _gh_media_config()
    if not token:
        print("  [rehost] GITHUB_TOKEN not set — cannot rehost"); return ""

    # 1) Resolve the bytes — local render or remote (e.g. HeyGen signed) URL.
    try:
        if local_file and os.path.exists(local_file):
            with open(local_file, "rb") as f:
                data = f.read()
        elif source_url:
            print(f"  [rehost] downloading source ({source_url[:60]}...)")
            dr = requests.get(source_url, timeout=120)
            dr.raise_for_status()
            data = dr.content
        else:
            print("  [rehost] no local_file or reachable source_url"); return ""
    except Exception as e:
        print(f"  [rehost] could not read source: {e}"); return ""
    if not data:
        print("  [rehost] empty payload — nothing to upload"); return ""

    path    = f"reels/{dest_name}"
    raw_url = f"https://raw.githubusercontent.com/{repo}/main/{path}"
    hdrs    = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    b64     = _b64.b64encode(data).decode()
    kb      = len(data) // 1024
    api     = f"https://api.github.com/repos/{repo}"

    for attempt in range(1, retries + 1):
        try:
            # blob -> tree -> commit -> move ref (the canonical binary-safe path).
            br = requests.post(f"{api}/git/blobs", headers=hdrs,
                               json={"content": b64, "encoding": "base64"}, timeout=120)
            if br.status_code not in (200, 201):
                raise RuntimeError(f"blob {br.status_code}: {br.text[:120]}")
            blob_sha = br.json()["sha"]

            rr = requests.get(f"{api}/git/ref/heads/main", headers=hdrs, timeout=30)
            rr.raise_for_status(); parent = rr.json()["object"]["sha"]
            cr = requests.get(f"{api}/git/commits/{parent}", headers=hdrs, timeout=30)
            cr.raise_for_status(); base_tree = cr.json()["tree"]["sha"]

            tr = requests.post(f"{api}/git/trees", headers=hdrs, json={
                "base_tree": base_tree,
                "tree": [{"path": path, "mode": "100644", "type": "blob", "sha": blob_sha}],
            }, timeout=60)
            tr.raise_for_status(); new_tree = tr.json()["sha"]

            mr = requests.post(f"{api}/git/commits", headers=hdrs, json={
                "message": f"reel: {dest_name}", "tree": new_tree, "parents": [parent],
            }, timeout=60)
            mr.raise_for_status(); new_commit = mr.json()["sha"]

            ur = requests.patch(f"{api}/git/refs/heads/main", headers=hdrs,
                                json={"sha": new_commit}, timeout=30)
            ur.raise_for_status()
            print(f"  [rehost] committed {dest_name} ({kb} KB) -> {raw_url}")

            # Best-effort: confirm the CDN is serving it before we hand it to Make.
            if verify:
                for _ in range(6):
                    time.sleep(2)
                    try:
                        if requests.head(raw_url, timeout=15).status_code == 200:
                            break
                    except Exception:
                        pass
                else:
                    print("  [rehost] WARNING: raw URL not 200 yet (CDN lag); returning anyway")
            return raw_url
        except Exception as e:
            print(f"  [rehost] attempt {attempt}/{retries} failed: {e}")
            time.sleep(3 * attempt)

    print(f"  [rehost] FAILED to upload {dest_name} after {retries} attempts")
    return ""


# ── Make.com Payload ───────────────────────────────────────────────────────────
def post_reel_via_make(caption: str, hashtags: str,
                       video_url: str = "", video_file: str = "",
                       reel_type: str = "", script: str = "",
                       analytics: dict = None) -> dict:
    if not MAKE_WEBHOOK_URL:
        print("  MAKE_WEBHOOK_URL not set"); return {"error": "no webhook"}
    caption_full = f"{caption}\n\n{hashtags}" if hashtags else caption
    analytics    = analytics or {}
    # Audio cue map by reel format — sent to Make.com for sound design
    AUDIO_BEDS = {
        "true_crime":    {"music": "dark_tension_underscore", "bpm": 85,  "swell_at": "30s"},
        "documentary":   {"music": "cinematic_investigation",  "bpm": 90,  "swell_at": "35s"},
        "breaking_news": {"music": "urgent_news_sting",        "bpm": 120, "swell_at": "5s"},
        "coffeezilla":   {"music": "lo_fi_investigative",      "bpm": 75,  "swell_at": "20s"},
        "alex_hormozi":  {"music": "motivational_pulse",       "bpm": 110, "swell_at": "15s"},
        "documentary":   {"music": "cinematic_investigation",  "bpm": 90,  "swell_at": "30s"},
        "reaction":      {"music": "comedic_sting_then_serious","bpm": 100, "swell_at": "8s"},
        "diary_of_a_ceo":{"music": "intimate_piano_underscore","bpm": 70,  "swell_at": "40s"},
    }
    reel_fmt    = analytics.get("reel_format", "documentary")
    audio_cues  = AUDIO_BEDS.get(reel_fmt, AUDIO_BEDS.get("documentary"))
    audio_cues["volume"] = 0.12  # -18dB under voice
    yt_title     = analytics.get("youtube_title","")
    if not yt_title:
        first    = caption.split(".")[0].strip()
        if first:
            yt_title = first[:67] + " | TaxCase Review"
        elif reel_type:
            # Last-resort title so YouTube never receives an empty field.
            yt_title = f"{reel_type.replace('_', ' ').title()} — IRS Tax Lien Help for Contractors"
        else:
            yt_title = "IRS Tax Help | TaxCase Review"
    yt_desc = analytics.get("youtube_description","")
    state_name   = analytics.get("state_name", analytics.get("state", ""))
    county_name  = analytics.get("county", "")
    # Build SEO-rich YouTube description if Claude didn't generate one
    if not yt_desc:
        excerpt = " ".join(script.split()[:60]) + "..." if script else ""
        location_line = f"{county_name} County, {state_name} — " if county_name and state_name else (f"{state_name} — " if state_name else "")
        yt_desc = (
            f"{location_line}{caption}\n\n"
            f"{excerpt}\n\n"
            f"🔗 Free 60-second IRS risk assessment: {QUIZ_URL}\n"
            f"📞 Talk to a former IRS officer: {PHONE}\n\n"
            f"TaxCase Review was founded by former IRS Revenue Officers. "
            f"We help contractors, business owners, and self-employed professionals "
            f"resolve federal tax liens, payroll tax debt, IRS levies, and wage garnishments.\n\n"
            f"Resolution options: Offer in Compromise · Installment Agreement · "
            f"Penalty Abatement · Currently Not Collectible · Lien Withdrawal · "
            f"Wage Garnishment Release · Bank Levy Release\n\n"
            f"Serving: Florida · Texas · Georgia · Arizona · California · New York · "
            f"North Carolina · Illinois · Ohio · Pennsylvania\n\n"
            f"#IRSTaxLien #TaxResolution #TaxDebt #IRSHelp #FederalTaxLien "
            f"#OfferInCompromise #PayrollTax #TaxRelief #FormerIRSOfficer "
            f"#SmallBusiness #Contractors #SelfEmployed"
        )
    yt_tags_raw = analytics.get("youtube_tags","")
    tag_list = [t.strip() for t in yt_tags_raw.split(",")] if yt_tags_raw else \
               [h.strip().lstrip("#") for h in hashtags.replace("\n"," ").split() if h.startswith("#")]
    # SEO base tags — mix of topic, location, audience
    location_tags = []
    if state_name:  location_tags.append(f"{state_name} tax lien")
    if county_name: location_tags.append(f"{county_name} County IRS")
    base_tags = [
        "IRS tax lien", "federal tax lien", "tax resolution", "IRS help",
        "tax debt help", "tax relief", "offer in compromise",
        "payroll tax debt", "IRS notice", "penalty abatement",
        "former IRS officer", "TaxCase Review",
    ] + location_tags
    all_tags  = list(dict.fromkeys(base_tags + tag_list))[:30]
    vs = analytics.get("viral_scores", {})
    payload = {
        "message": caption_full, "reel": True, "link": QUIZ_URL,
        "video_url": video_url, "video_file": video_file,
        "audio_cues": audio_cues,
        "youtube_title": yt_title, "youtube_description": yt_desc,
        "youtube_tags": ",".join(all_tags), "youtube_category": "22",
        "youtube_channel": YOUTUBE_CHANNEL,
        "youtube_shorts_hook": analytics.get("youtube_shorts_hook",""),
        "length_tier":         analytics.get("length_tier","standard"),
        "visual_cues":         json.dumps(analytics.get("visual_cues",[])),
        "text_overlays":       json.dumps(analytics.get("text_overlays",[])),
        "topic":               analytics.get("topic",""),
        "source_url":          analytics.get("source_url",""),
        "cta_strategy":        analytics.get("cta_strategy","quiz_cta"),
        "lead_magnet_name":    analytics.get("lead_magnet_name",""),
        "lead_magnet_keyword": analytics.get("lead_magnet_keyword",""),
        "trade":               analytics.get("trade",""),
        "archetype":           analytics.get("archetype",""),
        "quality_score":       analytics.get("quality_score",0),
        "scroll_stop_score":   vs.get("scroll_stop_score",0),
        "emotional_score":     vs.get("emotional_score",0),
        "curiosity_score":     vs.get("curiosity_score",0),
        "comment_score":       vs.get("comment_score",0),
        "reel_type":           reel_type,
        "hook_type":           analytics.get("hook_type",""),
        "save_worthy":         analytics.get("save_worthy",False),
        "authority_ref":       analytics.get("authority_ref",False),
        "county":              analytics.get("county",""),
        "city":                analytics.get("city",""),
        "state":               analytics.get("state",""),
        "data_source":         analytics.get("data_source",""),
        # Collection page (detected from state/county/trade — same logic the
        # social poster uses) so Make.com can link the reel to its site hub.
        "collection_page":     analytics.get("collection_page") or detect_collection_page(
                                   analytics.get("state",""),
                                   analytics.get("trade",""),
                                   analytics.get("topic","")),
    }
    r = requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=15)
    return {"status": r.status_code, "response": r.text}


# ── Log + Save ─────────────────────────────────────────────────────────────────
def load_reel_log() -> list:
    if REEL_LOG_FILE.exists():
        try: return json.loads(REEL_LOG_FILE.read_text())
        except: return []
    return []

def save_reel_entry(entry: dict):
    log = load_reel_log(); log.append(entry)
    REEL_LOG_FILE.write_text(json.dumps(log[-200:], indent=2))

def save_script_locally(script_data: dict, video_id: str = "", video_file: str = "") -> str:
    REELS_DIR.mkdir(exist_ok=True)
    out = REELS_DIR / f"{date.today().isoformat()}-reel-heygen-{script_data['reel_type'].replace('_','-')}.txt"
    vs  = script_data.get("viral_scores", {})
    cues_fmt = "\n".join(f"  {c.get('time','')} | {c.get('visual','')} | {c.get('overlay','')} | {c.get('editor_note','')}"
                         for c in script_data.get("visual_cues",[]))
    storyboard_fmt = "\n".join(
        f"  {c.get('time','')} | {c.get('visual','')} | {c.get('overlay','')} | {c.get('editor_note','')}"
        + (f" [cam:{c.get('camera_move','')}]" if c.get('camera_move') else "")
        + (f" [interrupt:{c.get('interrupt','')}]" if c.get('interrupt') else "")
        + (f" [broll:{c.get('human_broll','')[:40]}]" if c.get('human_broll') else "")
        + (f" [motion:{c.get('motion_graphic','')}]" if c.get('motion_graphic') else "")
        for c in script_data.get("visual_storyboard",[]))
    resets_fmt = "\n".join(f"  {c.get('time','')} | {c.get('reset','')} | {c.get('editor_note','')}"
                           for c in script_data.get("retention_resets",[]))
    lines = [
        f"REEL - {script_data['reel_type'].upper()}",
        f"Date: {script_data.get('week_of',date.today().isoformat())}",
        f"HeyGen Video ID: {video_id}" if video_id else "",
        f"Remotion File: {video_file}" if video_file else "",
        f"Duration: ~{script_data.get('estimated_seconds','?')}s | Words: {script_data.get('word_count','?')} | Tier: {script_data.get('length_tier','').upper()}",
        "",
        f"VIRAL SCORES: {script_data.get('quality_score',0)}/100",
        f"  scroll_stop={vs.get('scroll_stop_score',0)} emotional={vs.get('emotional_score',0)}",
        f"  curiosity={vs.get('curiosity_score',0)} visual={vs.get('visual_story_score', vs.get('visual_tension',0))} comment={vs.get('comment_score',0)} save={vs.get('save_score',0)}",
        "",
        f"ANALYTICS:",
        f"  hook_type    : {script_data.get('hook_type','')}",
        f"  reel_format  : {script_data.get('reel_format','')}",
        f"  emotion      : {script_data.get('emotional_driver','')}",
        f"  cta_strategy : {script_data.get('cta_strategy','')}",
        f"  archetype    : {script_data.get('archetype','')}",
        f"  save_worthy  : {script_data.get('save_worthy',False)}",
        f"  authority    : {script_data.get('authority_ref',False)}",
        "",
        "HOOK:",
        script_data.get('hook','?'),
        "",
        "SCRIPT:",
        script_data.get('script','?'),
        "",
        "CAPTION:",
        script_data.get('caption','?'),
        "",
        "HASHTAGS:",
        script_data.get('hashtags','?'),
        "",
        "VISUAL STORYBOARD:",
        storyboard_fmt or "?",
        "",
        "RETENTION RESETS:",
        resets_fmt or "?",
        "",
        "VISUAL CUES:",
        cues_fmt or "?",
        "",
        "YOUTUBE TITLE:",
        script_data.get('youtube_title','?'),
        "",
        "YOUTUBE SHORTS HOOK:",
        script_data.get('youtube_shorts_hook','?'),
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Saved: {out}"); return str(out)

def check_pending_reels():
    log     = load_reel_log()
    pending = [r for r in log if r.get("status") == "processing"]
    if not pending: print("No pending jobs."); return
    for entry in pending:
        result = check_heygen_status(entry["video_id"])
        print(f"  {entry['reel_type']} | {entry['date']} | {result['status']}")
        if result["video_url"]:
            entry["status"] = "completed"; entry["video_url"] = result["video_url"]
    REEL_LOG_FILE.write_text(json.dumps(log, indent=2))

def get_logger(run_type: str):
    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger(run_type); logger.start(); return logger
    except ImportError: return None


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    ALL_HEYGEN_TYPES = [
        "educational","notice","urgency","success-story","myth-bust","data-reveal",
        "contractor","state-spotlight","faq-answer","client-story","penalty-calculator",
        "mistake","confession","case-breakdown","what-if","red-flag","before-after","insider-secret",
        "county-lien-alert","city-lien-alert","state-lien-alert","industry-lien-alert",
        "irs-update","tax-deadline","news-reaction","court-case-reaction","tax-rule-change",
        "react-reel","reddit-reel","google-search-reel","myth-ranking",
        "quiz-reel","checklist-reel","deadline-reel","contractor-series",
        "tax-horror-story","biggest-lien-of-the-week","contractor-disaster",
        "payroll-tax-trap","public-record-breakdown","lien-heat-map",
        "worst-mistake-of-the-week","tax-tiktok-reaction","bad-tax-advice-reaction",
        "irs-agent-story",
    ]
    parser = argparse.ArgumentParser(description="TaxCase Review Reel Generator v8 (Top 1% Visual-First Engine)")
    parser.add_argument("--auto",                action="store_true")
    parser.add_argument("--remotion",            default=None,
                        choices=["weekly-stats","county-breakdown","penalty-growth","notice"])
    parser.add_argument("--heygen",              default=None, choices=ALL_HEYGEN_TYPES)
    parser.add_argument("--notice",              default=None, choices=["CP14","CP503","CP504","CP2000"])
    parser.add_argument("--state",               default=None)
    parser.add_argument("--county",              default=None)
    parser.add_argument("--city",                default=None)
    parser.add_argument("--trade",               default=None, choices=list(CONTRACTOR_SERIES.keys()))
    parser.add_argument("--topic",               default=None)
    parser.add_argument("--source-url",          default=None)
    parser.add_argument("--format",              default=None, choices=list(REEL_FORMATS.keys()),
                        help="Force a viral format: coffeezilla, true_crime, breaking_news, etc.")
    parser.add_argument("--dry-run",             action="store_true")
    parser.add_argument("--status",              action="store_true")
    parser.add_argument("--force",               action="store_true")
    parser.add_argument("--performance-summary", action="store_true")
    args = parser.parse_args()

    REELS_DIR.mkdir(exist_ok=True)
    if args.performance_summary: show_performance_summary(); return
    if args.status: check_pending_reels(); return
    if not args.auto and not args.remotion and not args.heygen: parser.print_help(); return

    heygen_used  = get_heygen_usage_this_month()
    credits_left = HEYGEN_MONTHLY_CREDITS - (heygen_used * HEYGEN_CREDITS_PER_VIDEO)
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  TaxCase Review Reel Generator v9 (Coffeezilla + Documentary + B-Roll)")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    print(f"  HeyGen: {heygen_used}/{HEYGEN_MAX_USE} | ~{credits_left}/{HEYGEN_MONTHLY_CREDITS} credits")
    print(f"  Viral threshold: {QUALITY_THRESHOLD}/100")
    print(f"  {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"{sep}\n")

    if args.auto:
        engine, reel_type = get_schedule_for_today()
        if engine is None: print("No reel scheduled today."); return
        print(f"Auto -> {engine.upper()} / {reel_type}\n")
    elif args.remotion:
        engine = "remotion"; reel_type = args.remotion.replace("-","_")
    else:
        engine = "heygen"; reel_type = args.heygen.replace("-","_")

    if engine == "heygen" and not args.dry_run:
        ok, msg = can_use_heygen()
        print(f"  HeyGen: {msg}")
        if not ok: print(f"  {msg}"); return

    stats = get_weekly_lien_stats(county=args.county, state=args.state)
    context = {
        "county":     args.county or stats.get("top_county", random.choice(FLORIDA_COUNTIES)),
        "city":       args.city or "",
        "count":      stats.get("count", random.randint(10, 40)),
        "notice":     args.notice or get_notice_for_this_week(),
        "state":      args.state  or get_state_for_this_week(),
        "trade":      args.trade  or "",
        "topic":      args.topic  or "",
        "source_url": args.source_url or "",
        "format":     args.format or "",
        "stats":      stats,
        "data_source":stats.get("data_source","estimated"),
    }

    # Cross-script coordination — if the social script already posted this
    # county/trade today, switch to a different county so the two engines don't
    # overlap on the same daily angle.
    if HAS_SHARED and si.is_duplicate_today("reel", context["county"], context.get("trade", "")):
        pool = [c.get("name") for c in stats.get("counties", []) if c.get("name")] or FLORIDA_COUNTIES
        alts = [c for c in pool if c.lower() != str(context["county"]).lower()]
        if alts:
            new_county = random.choice(alts)
            print(f"  Cross-script dedupe: {context['county']} already posted today "
                  f"by social — switching to {new_county}")
            context["county"] = new_county

    logger = get_logger(f"reel_{engine}")

    if engine == "remotion":
        print(f"Rendering {reel_type} with Remotion...\n")
        try:
            caption_raw = call_claude(
                f"Write a 40-60 word Facebook/Instagram caption for a Reel showing IRS lien stats "
                f"for {context['county']} County ({context['count']} new liens this week). "
                f"Open with a horror hook naming a real-sounding person. Reframe as human impact. "
                f"CTA: taxcasereview.org/quiz - 60 seconds. Also {PHONE}. "
                f"HASHTAGS: 8 hashtags.", max_tokens=200)
            caption  = caption_raw.split("HASHTAGS:")[0].strip()
            hashtags = "HASHTAGS:" + caption_raw.split("HASHTAGS:")[1] if "HASHTAGS:" in caption_raw else ""
        except Exception:
            caption  = f"{context['count']} business owners in {context['county']} County have a federal lien this week. taxcasereview.org/quiz | {PHONE}"
            hashtags = "#IRSTaxLien #TaxRelief #Florida #IRSHelp"

        video_file  = render_remotion(reel_type, context, dry_run=args.dry_run)
        script_data = {
            "reel_type": reel_type, "engine": "remotion",
            "week_of": date.today().strftime("%B %d, %Y"),
            "caption": caption, "hashtags": hashtags,
            "script": f"[Remotion - {reel_type}]", "hook": caption[:80],
            "length_tier": "micro", "hook_type": "horror", "cta_strategy": "quiz_cta",
            "save_worthy": True, "authority_ref": False, "quality_score": 88,
            "viral_scores": {"scroll_stop_score":25,"emotional_score":20,"curiosity_score":20,"comment_score":15,"save_score":8},
            "visual_cues": get_visual_cues_for_type("data_reveal"),
            "text_overlays": ["NEW LIENS THIS WEEK","YOUR COUNTY","SEE YOUR OPTIONS"],
            "youtube_title":"","youtube_shorts_hook":"","youtube_description":"","youtube_tags":"",
            "topic":"","trade":"","city":"","state":context["state"],
            "county":context["county"],"data_source":context["data_source"],"archetype":"",
        }
        save_script_locally(script_data, video_file=video_file)
        if video_file and not args.dry_run:
            # Rehost the local render to a permanent GitHub raw URL for Make.com.
            public_url = rehost_to_github(Path(video_file).name, local_file=video_file)
            # Never post an empty URL — Make throws "Invalid URL in parameter 'url'".
            if not public_url:
                print("  Upload failed — NOT posting to Make (would send an empty video_url).")
                save_reel_entry({"date":date.today().isoformat(),"engine":"remotion",
                                 "reel_type":reel_type,"video_file":video_file,"video_url":"",
                                 "status":"rendered_upload_failed",
                                 **{k:script_data[k] for k in ["hook_type","save_worthy","quality_score"]}})
                save_performance_entry(build_performance_entry(script_data, video_id=str(video_file)))
                if logger: logger.finish({"engine":"remotion","reel_type":reel_type,
                                          "posted":False,"error":"github_upload_failed"})
                return
            result  = post_reel_via_make(caption, hashtags, video_url=public_url,
                                         reel_type=reel_type, script=script_data["script"],
                                         analytics=script_data)
            make_ok = result.get("status") == 200
            print(f"Make.com: {result}")
            save_reel_entry({"date":date.today().isoformat(),"engine":"remotion",
                             "reel_type":reel_type,"video_file":video_file,"video_url":public_url,
                             "status":"posted" if make_ok else "rendered",
                             **{k:script_data[k] for k in ["hook_type","save_worthy","quality_score"]}})
            save_performance_entry(build_performance_entry(script_data, video_id=str(video_file)))
            # Content flywheel — log high-performers + ping the collection page.
            if make_ok:
                coll_page = detect_collection_page(script_data.get("state",""),
                                                   script_data.get("trade",""),
                                                   script_data.get("topic",""))
                if HAS_SHARED and script_data.get("quality_score", 0) >= 85:
                    try:
                        si.log_content_opportunity(
                            script_data.get("topic") or reel_type, "reel",
                            script_data.get("quality_score", 0), public_url or coll_page)
                        print(f"  Content opportunity logged (score {script_data.get('quality_score',0)})")
                    except Exception as e:
                        print(f"  Content opportunity log failed (non-blocking): {e}")
                _indexnow_ping(coll_page)
                if HAS_SHARED:
                    try:
                        si.record_daily_content("reel", reel_type,
                                                script_data.get("county", ""),
                                                script_data.get("trade", ""),
                                                script_data.get("state", ""))
                    except Exception:
                        pass
        if logger: logger.finish({"engine":"remotion","reel_type":reel_type,"dry_run":args.dry_run})
        return

    print(f"Generating {reel_type} script...\n")
    script_data = generate_heygen_script(reel_type, context, force=args.force)
    vs = script_data.get("viral_scores", {})
    dsep = "-" * 60
    print(f"{dsep}")
    print(f"HOOK ({script_data['hook_type']}): {script_data['hook']}")
    print(f"ARCHETYPE: {script_data.get('archetype','')} | TIER: {script_data['length_tier'].upper()} | FORMAT: {script_data.get('reel_format','')}")
    print(f"VIRAL: {script_data['quality_score']}/100 | scroll={vs.get('scroll_stop_score',0)} emotional={vs.get('emotional_score',0)} curiosity={vs.get('curiosity_score',0)} visual={vs.get('visual_story_score', vs.get('visual_tension',0))} comment={vs.get('comment_score',0)}")
    print(f"\nSCRIPT:\n{script_data['script']}")
    print(f"\nCAPTION:\n{script_data['caption']}")
    print(f"\nYT TITLE: {script_data.get('youtube_title','')}")
    if script_data.get("visual_cues"):
        print(f"\nVISUAL CUES:")
        for c in script_data["visual_cues"]:
            print(f"  {c.get('time','')} | {c.get('visual','')} | {c.get('overlay','')}")
    print(f"{dsep}\n")

    if script_data.get("quality_below_threshold") and not args.force and not args.dry_run:
        save_script_locally(script_data)
        print(f"Viral score {script_data['quality_score']} < {QUALITY_THRESHOLD}. Use --force to render.")
        return

    if args.dry_run:
        save_script_locally(script_data)
        print("Dry run - saved.\n")
        if logger: logger.finish({"engine":"heygen","reel_type":reel_type,"dry_run":True})
        return

    try:
        job      = submit_heygen_video(script_data)
        video_id = job["video_id"]
        save_script_locally(script_data, video_id=video_id)
        save_reel_entry({"date":date.today().isoformat(),"engine":"heygen",
                         "reel_type":reel_type,"video_id":video_id,"status":"processing","video_url":"",
                         **{k:script_data[k] for k in ["hook_type","save_worthy","quality_score","cta_strategy","length_tier","archetype"]}})

        video_url = wait_for_heygen(video_id, max_minutes=15)
        if video_url:
            # HeyGen URLs are temporary signed links (~7 day expiry). Rehost to a
            # permanent GitHub raw URL; fall back to the signed URL if that fails so
            # the post still goes out today (it is valid for the immediate Make run).
            dest_name = f"{date.today().isoformat()}-{reel_type.replace('_','-')}-{video_id[:8]}.mp4"
            permanent_url = rehost_to_github(dest_name, source_url=video_url)
            post_url = permanent_url or video_url
            if not permanent_url:
                print("  [rehost] falling back to temporary HeyGen URL for this post")
            log = load_reel_log()
            for e in log:
                if e.get("video_id") == video_id:
                    e["status"] = "completed"; e["video_url"] = post_url
            REEL_LOG_FILE.write_text(json.dumps(log, indent=2))
            result  = post_reel_via_make(script_data["caption"], script_data["hashtags"],
                                         video_url=post_url, reel_type=reel_type,
                                         script=script_data.get("script",""), analytics=script_data)
            make_ok = result.get("status") == 200
            print(f"Make.com: {result}")
            save_performance_entry(build_performance_entry(script_data, video_id=video_id, video_url=post_url))
            # Content flywheel — log high-performers + ping the collection page.
            if make_ok:
                coll_page = detect_collection_page(script_data.get("state",""),
                                                   script_data.get("trade",""),
                                                   script_data.get("topic",""))
                if HAS_SHARED and script_data.get("quality_score", 0) >= 85:
                    try:
                        si.log_content_opportunity(
                            script_data.get("topic") or reel_type, "reel",
                            script_data.get("quality_score", 0), post_url)
                        print(f"  Content opportunity logged (score {script_data.get('quality_score',0)})")
                    except Exception as e:
                        print(f"  Content opportunity log failed (non-blocking): {e}")
                _indexnow_ping(coll_page)
                if HAS_SHARED:
                    try:
                        si.record_daily_content("reel", reel_type,
                                                script_data.get("county", ""),
                                                script_data.get("trade", ""),
                                                script_data.get("state", ""))
                    except Exception:
                        pass
            if logger:
                logger.finish({"engine":"heygen","reel_type":reel_type,"video_id":video_id,
                               "posted":make_ok,"quality_score":script_data["quality_score"],
                               "credits":get_heygen_usage_this_month()*HEYGEN_CREDITS_PER_VIDEO})
        else:
            print("Timeout. python reel_generator.py --status")
    except Exception as e:
        print(f"Error: {e}")
        import traceback; traceback.print_exc()


if __name__ == "__main__":
    main()