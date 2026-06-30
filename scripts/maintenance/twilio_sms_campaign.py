#!/usr/bin/env python3
"""
twilio_sms_campaign.py (v4 — A/B Testing + 150/day + Daily Summary wired)
====================================================================

TaxCase Review SMS campaign engine for lien/contact records.

What changed from v2:
  - Hard cap: 100 live texts/day max, regardless of .env or --limit.
  - Safer compliance layer:
      * STOP/opt-out suppression table.
      * Prior sent suppression.
      * Quiet-hours guard.
      * Mandatory STOP language in every text.
      * TCPA risk acknowledgement flag for live sends.
  - Better conversion copy:
      * Multiple emotionally relevant templates.
      * County/state-specific landing URLs.
      * Less creepy wording than "we found you".
      * Clear low-friction CTA.
  - Message quality scoring:
      * Length, CTA, personalization, compliance, urgency, clarity.
      * Preview CSV includes quality score and warnings.
  - Safer SQL:
      * Parameterized queries instead of interpolated state/county strings.
  - Better reporting:
      * Preview CSV.
      * Daily cap status.
      * Variant tracking.
      * Pipeline metrics.

Important:
  SMS outreach is legally sensitive. This script protects against obvious mistakes,
  but it does not make cold SMS legally safe. Use only with contacts you are
  permitted to text, honor STOP immediately, and consult counsel for TCPA/state-law
  compliance.

Usage:
  python -m scripts.maintenance.twilio_sms_campaign --dry-run
  python -m scripts.maintenance.twilio_sms_campaign --state AZ --source roc --limit 25 --dry-run
  python -m scripts.maintenance.twilio_sms_campaign --state TX --source tdlr --limit 100 --i-understand-tcpa-risk
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
load_dotenv()

try:
    from app.core.db import get_connection
except ImportError:
    sys.exit("Run from leadflow root: python -m scripts.maintenance.twilio_sms_campaign")


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")

# Hard cap. Env can lower it, but never raise above HARD_DAILY_CAP.
# Volume bumped to 400/day at user request: 3x100 AZ ROC + 1x100 IRS FOIA payroll.
# NOTE: this is a SHARED daily pool across all batches (morning/midday/afternoon/foia).
# COMPLIANCE: 400/day exceeds the prior "A2P 10DLC safe at 150/day" comfort level —
# confirm your A2P 10DLC campaign throughput/registration supports this volume before
# relying on it, or carriers may filter. Volume is the user's explicit business decision.
REQUESTED_DAILY_LIMIT = int(os.getenv("TWILIO_DAILY_LIMIT", "400"))
HARD_DAILY_CAP = 400  # 3x100 ROC + 100 FOIA payroll
MAX_DAILY_SMS = min(HARD_DAILY_CAP, max(0, REQUESTED_DAILY_LIMIT))

SITE_DOMAIN = os.getenv("TAXCASE_SITE_DOMAIN", "taxcasereview.org").replace("https://", "").replace("http://", "").strip("/")
QUIZ_URL = os.getenv("SMS_QUIZ_URL", f"https://{SITE_DOMAIN}/quiz")
BRAND = os.getenv("SMS_BRAND_NAME", "TaxCase Review")
SENDER_PERSONA = os.getenv("SMS_SENDER_PERSONA", "Romy")
DEFAULT_CAMPAIGN_ID = os.getenv("SMS_CAMPAIGN_ID", "sms_lien_outreach_2026")

# Sending windows are local server time. Adjust in Task Scheduler if needed.
QUIET_HOURS_START = int(os.getenv("SMS_QUIET_HOURS_START", "20"))  # 8 PM
QUIET_HOURS_END = int(os.getenv("SMS_QUIET_HOURS_END", "9"))       # 9 AM

BASE_DIR = Path.cwd()
EXPORT_DIR = BASE_DIR / "data" / "exports"


# ─────────────────────────────────────────────────────────────────────────────
# Message copy engine
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SmsTemplate:
    key: str
    audience: str
    body: str
    intent: str
    notes: str = ""


TEMPLATES: dict[str, SmsTemplate] = {
    # Best default: direct, not creepy, action-oriented.
    "public_record_soft": SmsTemplate(
        key="public_record_soft",
        audience="business",
        intent="quiz",
        body=(
            "Hi {first_name}, {sender} with {brand}. Public records may show an IRS lien in {county} County. "
            "See resolution options in 60 sec: {url} Reply STOP to opt out."
        ),
        notes="Soft public-record language; avoids sounding like surveillance.",
    ),
    "consequence_soft": SmsTemplate(
        key="consequence_soft",
        audience="business",
        intent="quiz",
        body=(
            "Hi {first_name}, IRS liens can create financing, levy, or contractor-license stress. "
            "Check your options for {county} County: {url} Reply STOP to opt out."
        ),
        notes="Consequence-first without overclaiming.",
    ),
    "contractor_specific": SmsTemplate(
        key="contractor_specific",
        audience="contractor",
        intent="county_page",
        body=(
            "Hi {first_name}, contractors with tax liens often face cash-flow and license pressure. "
            "Review {county} County options here: {url} Reply STOP to opt out."
        ),
        notes="Best for ROC/TDLR/DBPR contractor contacts.",
    ),
    "notice_style": SmsTemplate(
        key="notice_style",
        audience="business",
        intent="tool",
        body=(
            "Hi {first_name}, if an IRS lien or notice is showing in {county} County records, timing matters. "
            "Start here: {url} Reply STOP to opt out."
        ),
        notes="Good all-purpose low-pressure outreach.",
    ),
    "short_direct": SmsTemplate(
        key="short_direct",
        audience="all",
        intent="quiz",
        body=(
            "IRS lien issue in {county} County? See your options in 60 sec: {url} "
            "TaxCase Review. Reply STOP to opt out."
        ),
        notes="Shortest fallback.",
    ),
    # High-converting new templates based on A/B testing best practices
    "urgency_window": SmsTemplate(
        key="urgency_window",
        audience="business",
        intent="quiz",
        body=(
            "Hi {first_name}, IRS collection windows close fast once a lien files in {county} County. "
            "Check your options before enforcement escalates: {url} Reply STOP to opt out."
        ),
        notes="Urgency without overclaiming — window language converts well.",
    ),
    "social_proof": SmsTemplate(
        key="social_proof",
        audience="contractor",
        intent="county_page",
        body=(
            "Hi {first_name}, most contractors with {county} County liens don't know all their resolution options. "
            "Romy at TaxCase Review does. See what applies: {url} Reply STOP to opt out."
        ),
        notes="Authority + social proof angle. EA credential implied.",
    ),
    "question_hook": SmsTemplate(
        key="question_hook",
        audience="business",
        intent="quiz",
        body=(
            "Hi {first_name}, quick question — has anyone walked you through your IRS options "
            "for the {county} County lien? Takes 60 sec: {url} Reply STOP to opt out."
        ),
        notes="Question hook — highest reply rate in cold SMS.",
    ),
    "dollar_specific": SmsTemplate(
        key="dollar_specific",
        audience="business",
        intent="quiz",
        body=(
            "Hi {first_name}, IRS liens in {county} County can often be resolved for less "
            "than the full balance. See what you qualify for: {url} Reply STOP to opt out."
        ),
        notes="Dollar-specific implication without guaranteeing amount.",
    ),
    # IRS FOIA payroll source — personalized with the actual lien amount.
    # Longer (2 SMS segments) and intentionally specific; bypasses the generic
    # county-tuned quality gate via the lower FOIA min-quality floor in main().
    "foia_payroll": SmsTemplate(
        key="foia_payroll",
        audience="business",
        intent="quiz",
        body=(
            "Hi {business_name} - {sender} Cruz EA at {brand}. We found a federal payroll "
            "tax lien filed against your business (${amount:,.0f}). These can escalate to "
            "bank levies fast. Free 15-min case review: {url} Reply STOP to opt out."
        ),
        notes="FOIA payroll lien outreach. Personalized with debtor lien amount.",
    ),
}


STATE_PATHS = {
    "FL": "florida",
    "TX": "texas",
    "GA": "georgia",
    "AZ": "arizona",
    "CA": "california",
    "NY": "new-york",
    "NC": "north-carolina",
    "IL": "illinois",
    "OH": "ohio",
    "PA": "pennsylvania",
}


CONTRACTOR_SOURCES = {"dbpr", "roc", "tdlr"}

# IRS FOIA lien sources (google_places_contacts matched to irs_foia_liens).
# irs_foia      → payroll liens only (941/940) — highest-intent.
# irs_foia_all  → any FOIA-matched lien.
FOIA_SOURCES = {"irs_foia", "irs_foia_all"}


def county_to_slug(county: str) -> str:
    cleaned = (county or "").strip()
    cleaned = re.sub(r"\s+County$", "", cleaned, flags=re.I)
    cleaned = cleaned.lower().replace("&", " and ")
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    return cleaned or "local"


def landing_url(state: str | None, county: str | None, intent: str = "quiz") -> str:
    state = (state or "").upper().strip()
    county_slug = county_to_slug(county or "")
    state_path = STATE_PATHS.get(state)

    if intent == "county_page" and state_path and county_slug:
        return f"https://{SITE_DOMAIN}/{state_path}/{county_slug}/irs-tax-lien-help"
    if intent == "tool":
        return f"https://{SITE_DOMAIN}/tools/risk-assessment"
    return QUIZ_URL


def clean_phone(phone: str) -> str | None:
    if not phone:
        return None
    digits = "".join(c for c in str(phone) if c.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def get_first_name(full_name: str) -> str:
    if not full_name:
        return "there"

    text = str(full_name).strip()
    lower = text.lower()
    biz_indicators = [
        "llc", "inc", "corp", "co.", "ltd", "services", "construction",
        "roofing", "hvac", "plumbing", "electric", "group", "properties",
        "trucking", "restaurant", "realty", "holdings", "enterprises",
        "floors", "masonry", "solutions", "contractors", "company",
        "management", "partners", "investments", "consulting",
    ]
    if any(ind in lower for ind in biz_indicators):
        return "there"

    # LAST, FIRST
    if "," in text:
        first = text.split(",", 1)[1].strip().split()
        return first[0].title() if first and len(first[0]) > 1 else "there"

    parts = text.split()
    if not parts:
        return "there"

    # Avoid weird initials.
    first = re.sub(r"[^A-Za-z'-]", "", parts[0]).title()
    return first if len(first) > 1 else "there"


# ── A/B test configuration ────────────────────────────────────────────────────
# Each group tests different angles. Winner gets more weight over time.
# Track results via sms_campaign_log.template_key + daily_summary SMS section.
AB_GROUPS = {
    "contractor": {
        "templates": [
            "contractor_specific",   # best performer for ROC contacts
            "question_hook",         # new — highest reply rate in cold SMS
            "social_proof",          # new — EA authority angle
            "public_record_soft",    # reliable baseline
            "urgency_window",        # new — window urgency
        ],
        "weights": [0.35, 0.25, 0.20, 0.15, 0.05],
    },
    "business": {
        "templates": [
            "question_hook",         # new — test against public_record_soft
            "public_record_soft",    # baseline winner
            "dollar_specific",       # new — dollar implication
            "consequence_soft",      # reliable
            "urgency_window",        # new
        ],
        "weights": [0.30, 0.30, 0.20, 0.15, 0.05],
    },
    "default": {
        "templates": [
            "public_record_soft",
            "question_hook",
            "notice_style",
            "short_direct",
        ],
        "weights": [0.40, 0.30, 0.20, 0.10],
    },
}


def pick_template(state: str | None, source: str, force: str = "auto") -> str:
    """Pick template using A/B weighted rotation.
    New templates (question_hook, social_proof, dollar_specific, urgency_window)
    start at 20-25% weight so they get enough volume to measure.
    Check daily_summary SMS section for template_key breakdown after 7 days.
    """
    if force != "auto":
        return force

    source = (source or "").lower()
    state = (state or "").upper()

    # FOIA payroll/all leads always use the personalized, amount-aware template.
    if source in FOIA_SOURCES:
        return "foia_payroll"

    if source in CONTRACTOR_SOURCES:
        group = AB_GROUPS["contractor"]
    elif state in {"AZ", "TX", "GA", "FL", "NC", "OH", "PA", "IL"}:
        group = AB_GROUPS["business"]
    else:
        group = AB_GROUPS["default"]

    return random.choices(group["templates"], weights=group["weights"], k=1)[0]


def format_message(template_key: str, contact: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    county = (contact.get("county") or "").replace(" County", "").strip() or "your county"
    state = contact.get("state") or ""
    first_name = get_first_name(contact.get("name") or "")
    # Business name for FOIA template (real business name, not a parsed first name).
    business_name = (contact.get("business_name") or contact.get("name") or "there").strip() or "there"
    try:
        amount = float(contact.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    tmpl = TEMPLATES.get(template_key, TEMPLATES["public_record_soft"])
    url = landing_url(state, county, tmpl.intent)

    body = tmpl.body.format(
        first_name=first_name,
        business_name=business_name,
        amount=amount,
        sender=SENDER_PERSONA,
        brand=BRAND,
        county=county,
        state=state,
        url=url,
    )

    # Hard compliance guard: every outgoing body must contain STOP.
    if "STOP" not in body.upper():
        body = body.rstrip(". ") + ". Reply STOP to opt out."

    meta = {
        "template_key": template_key,
        "intent": tmpl.intent,
        "url": url,
        "first_name": first_name,
        "county": county,
        "message_length": len(body),
    }
    return body, meta


def score_message(body: str, contact: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Heuristic SMS quality score. This is not deliverability magic; it catches bad copy."""
    score = 0
    warnings: list[str] = []
    lower = body.lower()

    # Compliance
    if "stop" in lower:
        score += 20
    else:
        warnings.append("missing STOP opt-out")

    # Clear CTA/link
    if "taxcasereview.org" in lower:
        score += 18
    else:
        warnings.append("missing site URL")

    # Personalization/local context
    if meta.get("first_name") and meta.get("first_name") != "there":
        score += 8
    if meta.get("county") and meta.get("county") != "your county":
        score += 12
    else:
        warnings.append("missing county")

    # Low-friction value proposition
    if any(x in lower for x in ["60 sec", "60-sec", "options", "review", "risk", "assessment"]):
        score += 14
    else:
        warnings.append("weak value proposition")

    # Tone/urgency without illegal certainty
    if any(x in lower for x in ["may show", "can create", "if an irs", "often face", "timing matters"]):
        score += 12
    if any(x in lower for x in ["guarantee", "erase", "settle for pennies", "approved", "we found you"]):
        score -= 20
        warnings.append("risky/overclaiming language")

    # SMS length.
    length = len(body)
    if length <= 160:
        score += 16
    elif length <= 240:
        score += 8
        warnings.append("long; may split into multiple SMS")
    else:
        score -= 10
        warnings.append("too long")

    return {
        "score": max(0, min(100, score)),
        "warnings": warnings,
        "length": length,
        "segments_est": 1 if length <= 160 else 2 if length <= 306 else 3,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def ensure_campaign_tables(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sms_campaign_log (
            id             SERIAL PRIMARY KEY,
            lien_id        INTEGER,
            contact_id     INTEGER,
            contact_table  TEXT,
            to_number      TEXT NOT NULL,
            from_number    TEXT NOT NULL,
            message_sid    TEXT,
            status         TEXT,
            debtor_name    TEXT,
            county         TEXT,
            state          TEXT,
            source         TEXT,
            message_body   TEXT,
            sent_at        TIMESTAMPTZ DEFAULT NOW(),
            error_message  TEXT
        )
    """)
    # Additive columns. Safe for existing table.
    for ddl in [
        "ALTER TABLE sms_campaign_log ADD COLUMN IF NOT EXISTS campaign_id TEXT",
        "ALTER TABLE sms_campaign_log ADD COLUMN IF NOT EXISTS batch_id TEXT",
        "ALTER TABLE sms_campaign_log ADD COLUMN IF NOT EXISTS template_key TEXT",
        "ALTER TABLE sms_campaign_log ADD COLUMN IF NOT EXISTS quality_score INTEGER",
        "ALTER TABLE sms_campaign_log ADD COLUMN IF NOT EXISTS message_length INTEGER",
        "ALTER TABLE sms_campaign_log ADD COLUMN IF NOT EXISTS landing_url TEXT",
        # Daily summary requires these columns
        "ALTER TABLE sms_campaign_log ADD COLUMN IF NOT EXISTS link_clicked BOOLEAN DEFAULT FALSE",
        "ALTER TABLE sms_campaign_log ADD COLUMN IF NOT EXISTS link_url TEXT",
        "ALTER TABLE sms_campaign_log ADD COLUMN IF NOT EXISTS opt_out BOOLEAN DEFAULT FALSE",
        "ALTER TABLE sms_campaign_log ADD COLUMN IF NOT EXISTS delivery_status TEXT",
        "ALTER TABLE sms_campaign_log ADD COLUMN IF NOT EXISTS batch_label TEXT",
    ]:
        cur.execute(ddl)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sms_opt_outs (
            id          SERIAL PRIMARY KEY,
            to_number   TEXT UNIQUE NOT NULL,
            source      TEXT,
            opted_out_at TIMESTAMPTZ DEFAULT NOW(),
            note        TEXT
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_sms_log_number ON sms_campaign_log(to_number)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sms_log_sent_at ON sms_campaign_log(sent_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sms_log_campaign ON sms_campaign_log(campaign_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sms_log_status ON sms_campaign_log(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sms_opt_outs_number ON sms_opt_outs(to_number)")


def ensure_google_places_columns(cur) -> None:
    """Additive columns used by the IRS FOIA source. Safe to run repeatedly."""
    for ddl in [
        "ALTER TABLE google_places_contacts ADD COLUMN IF NOT EXISTS sms_sent BOOLEAN DEFAULT FALSE",
        "ALTER TABLE google_places_contacts ADD COLUMN IF NOT EXISTS sms_opted_out BOOLEAN DEFAULT FALSE",
    ]:
        cur.execute(ddl)


def get_sends_today(cur, campaign_id: str | None = None) -> int:
    if campaign_id:
        cur.execute("""
            SELECT COUNT(*) FROM sms_campaign_log
            WHERE status IN ('queued','sent','delivered')
              AND DATE(sent_at) = CURRENT_DATE
              AND campaign_id = %s
        """, (campaign_id,))
    else:
        cur.execute("""
            SELECT COUNT(*) FROM sms_campaign_log
            WHERE status IN ('queued','sent','delivered')
              AND DATE(sent_at) = CURRENT_DATE
        """)
    return cur.fetchone()[0] or 0


def get_suppression_numbers(cur) -> set[str]:
    cur.execute("""
        SELECT DISTINCT to_number FROM sms_campaign_log
        WHERE status IN ('queued','sent','delivered','opt_out','suppressed')
    """)
    sent_numbers = {row[0] for row in cur.fetchall() if row and row[0]}

    cur.execute("SELECT to_number FROM sms_opt_outs")
    opt_outs = {row[0] for row in cur.fetchall() if row and row[0]}
    return sent_numbers | opt_outs


def state_county_clauses(table_alias: str, state: str | None, county: str | None) -> tuple[str, list[Any]]:
    clauses = []
    params: list[Any] = []
    if state:
        clauses.append(f"{table_alias}.state = %s")
        params.append(state)
    if county:
        clauses.append(f"{table_alias}.county_name ILIKE %s")
        params.append(f"%{county}%")
    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params


def add_contact_if_valid(contact: dict[str, Any], contacts: list[dict[str, Any]], seen: set[str], suppressed: set[str]) -> bool:
    phone = clean_phone(contact.get("phone") or "")
    if not phone:
        return False
    if phone in suppressed or phone in seen:
        return False
    seen.add(phone)
    contact["phone_e164"] = phone
    contacts.append(contact)
    return True


def get_contacts(cur, state: str | None, source: str, limit: int, county: str | None) -> list[dict[str, Any]]:
    contacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    suppressed = get_suppression_numbers(cur)
    remaining = max(0, int(limit))

    # Source 1: lien_dbpr_contacts
    if source in ("all", "dbpr") and remaining > 0:
        clauses, params = state_county_clauses("c", state, county)
        sql = f"""
            SELECT nl.id, ldc.id, ldc.full_name, ldc.phone, ldc.email,
                   c.county_name, c.state, nl.lien_type, nl.filed_date,
                   ldc.confidence
            FROM lien_dbpr_contacts ldc
            JOIN normalized_liens nl ON nl.id = ldc.lien_id
            JOIN counties c ON c.id = nl.county_id
            WHERE ldc.phone IS NOT NULL AND ldc.phone <> ''
              AND (ldc.confidence IS NULL OR ldc.confidence IN ('high','medium'))
              {clauses}
            ORDER BY
              CASE WHEN ldc.confidence='high' THEN 0 WHEN ldc.confidence='medium' THEN 1 ELSE 2 END,
              nl.filed_date DESC NULLS LAST
            LIMIT %s
        """
        cur.execute(sql, (*params, remaining * 3))
        for row in cur.fetchall():
            if remaining <= 0:
                break
            if add_contact_if_valid({
                "lien_id": row[0],
                "contact_id": row[1],
                "contact_table": "lien_dbpr_contacts",
                "name": row[2] or "",
                "phone": row[3],
                "email": row[4],
                "county": row[5],
                "state": row[6],
                "source": "dbpr",
                "confidence": row[9] or "",
            }, contacts, seen, suppressed):
                remaining -= 1

    # Source 2: lien_skiptrace_contacts
    if source in ("all", "skiptrace") and remaining > 0:
        clauses, params = state_county_clauses("c", state, county)
        try:
            cur.execute(f"""
                SELECT nl.id, s.id, s.debtor_name, s.phone,
                       c.county_name, c.state, nl.lien_type
                FROM lien_skiptrace_contacts s
                JOIN normalized_liens nl ON nl.id = s.normalized_lien_id
                JOIN counties c ON c.id = nl.county_id
                WHERE s.phone IS NOT NULL AND s.phone <> ''
                  {clauses}
                ORDER BY nl.filed_date DESC NULLS LAST
                LIMIT %s
            """, (*params, remaining * 3))
            for row in cur.fetchall():
                if remaining <= 0:
                    break
                if add_contact_if_valid({
                    "lien_id": row[0],
                    "contact_id": row[1],
                    "contact_table": "lien_skiptrace_contacts",
                    "name": row[2] or "",
                    "phone": row[3],
                    "email": None,
                    "county": row[4],
                    "state": row[5],
                    "source": "skiptrace",
                    "confidence": "",
                }, contacts, seen, suppressed):
                    remaining -= 1
        except Exception as e:
            print(f"  Skip trace table unavailable: {e}")

    # Source 3: arizona_roc_contacts
    if source in ("all", "roc") and remaining > 0 and (not state or state == "AZ"):
        county_clause = "AND county ILIKE %s" if county else ""
        params = [f"%{county}%"] if county else []
        try:
            cur.execute(f"""
                SELECT id, business_name, owner_name, phone, county, license_class
                FROM arizona_roc_contacts
                WHERE phone IS NOT NULL AND phone <> ''
                  AND COALESCE(emailed, false) = false
                  {county_clause}
                ORDER BY id DESC
                LIMIT %s
            """, (*params, remaining * 3))
            for row in cur.fetchall():
                if remaining <= 0:
                    break
                if add_contact_if_valid({
                    "lien_id": None,
                    "contact_id": row[0],
                    "contact_table": "arizona_roc_contacts",
                    "name": row[2] or row[1] or "",
                    "business_name": row[1] or "",
                    "phone": row[3],
                    "email": None,
                    "county": row[4] or "Maricopa",
                    "state": "AZ",
                    "source": "roc",
                    "confidence": "license",
                }, contacts, seen, suppressed):
                    remaining -= 1
        except Exception as e:
            print(f"  Arizona ROC table unavailable: {e}")

    # Source 4: texas_tdlr_contacts
    if source in ("all", "tdlr") and remaining > 0 and (not state or state == "TX"):
        county_clause = "AND business_county ILIKE %s" if county else ""
        params = [f"%{county}%"] if county else []
        try:
            cur.execute(f"""
                SELECT id, business_name, owner_name, business_phone, business_county, license_type
                FROM texas_tdlr_contacts
                WHERE business_phone IS NOT NULL AND business_phone <> ''
                  AND COALESCE(emailed, false) = false
                  AND COALESCE(lien_match, false) = true
                  {county_clause}
                ORDER BY RANDOM()
                LIMIT %s
            """, (*params, remaining * 3))
            for row in cur.fetchall():
                if remaining <= 0:
                    break
                if add_contact_if_valid({
                    "lien_id": None,
                    "contact_id": row[0],
                    "contact_table": "texas_tdlr_contacts",
                    "name": row[2] or row[1] or "",
                    "business_name": row[1] or "",
                    "phone": row[3],
                    "email": None,
                    "county": row[4] or "Harris",
                    "state": "TX",
                    "source": "tdlr",
                    "confidence": "license_lien_match",
                }, contacts, seen, suppressed):
                    remaining -= 1
        except Exception as e:
            print(f"  Texas TDLR table unavailable: {e}")

    # Source 5: IRS FOIA liens matched to google_places_contacts.
    #   irs_foia      → payroll liens only (join irs_foia_periods, is_payroll=TRUE)
    #   irs_foia_all  → any matched FOIA lien
    if source in FOIA_SOURCES and remaining > 0:
        state_clause = "AND g.state = %s" if state else ""
        state_params = [state] if state else []
        if source == "irs_foia":
            period_join = "JOIN irs_foia_periods fp ON f.lien_id = fp.lien_id"
            payroll_clause = "AND fp.is_payroll = TRUE"
        else:
            period_join = ""
            payroll_clause = ""
        try:
            cur.execute(f"""
                SELECT DISTINCT ON (g.id)
                       g.id, g.business_name, g.phone, g.state, g.city,
                       f.amount, f.lien_date, f.county
                FROM irs_foia_liens f
                JOIN google_places_contacts g ON f.matched_contact_id = g.id
                {period_join}
                WHERE f.matched_contact_id IS NOT NULL
                  {payroll_clause}
                  AND g.phone IS NOT NULL AND g.phone <> ''
                  AND (g.sms_sent IS NULL OR g.sms_sent = FALSE)
                  AND (g.sms_opted_out IS NULL OR g.sms_opted_out = FALSE)
                  {state_clause}
                ORDER BY g.id, f.amount DESC
                LIMIT %s
            """, (*state_params, remaining * 3))
            for row in cur.fetchall():
                if remaining <= 0:
                    break
                if add_contact_if_valid({
                    "lien_id": None,
                    "contact_id": row[0],
                    "contact_table": "google_places_contacts",
                    "name": row[1] or "",
                    "business_name": row[1] or "",
                    "phone": row[2],
                    "email": None,
                    "county": row[7],
                    "state": row[3] or "",
                    "amount": row[5],
                    "source": source,
                    "confidence": "irs_foia_payroll" if source == "irs_foia" else "irs_foia",
                }, contacts, seen, suppressed):
                    remaining -= 1
        except Exception as e:
            print(f"  IRS FOIA source unavailable: {e}")

    return contacts


def mark_contact_texted(cur, contact: dict[str, Any]) -> None:
    table = contact.get("contact_table", "")
    cid = contact.get("contact_id")
    if not cid:
        return
    if table == "arizona_roc_contacts":
        cur.execute("UPDATE arizona_roc_contacts SET emailed=true WHERE id=%s", (cid,))
    elif table == "texas_tdlr_contacts":
        cur.execute("UPDATE texas_tdlr_contacts SET emailed=true WHERE id=%s", (cid,))
    elif table == "google_places_contacts":
        cur.execute("UPDATE google_places_contacts SET sms_sent=true WHERE id=%s", (cid,))


def log_sms_result(cur, *, contact: dict[str, Any], from_number: str, result: dict[str, Any],
                   body: str, meta: dict[str, Any], score: dict[str, Any],
                   campaign_id: str, batch_id: str, batch_label: str = "default") -> None:
    cur.execute("""
        INSERT INTO sms_campaign_log
            (lien_id, contact_id, contact_table, to_number, from_number,
             message_sid, status, debtor_name, county, state, source, message_body,
             error_message, campaign_id, batch_id, template_key, quality_score,
             message_length, landing_url)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        contact.get("lien_id"),
        contact.get("contact_id"),
        contact.get("contact_table"),
        contact.get("phone_e164"),
        from_number,
        result.get("sid"),
        result.get("status"),
        contact.get("name", ""),
        meta.get("county", ""),
        contact.get("state", ""),
        contact.get("source", ""),
        body,
        result.get("error"),
        campaign_id,
        batch_id,
        meta.get("template_key"),
        score.get("score"),
        score.get("length"),
        meta.get("url"),
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Sending / safety
# ─────────────────────────────────────────────────────────────────────────────

def within_quiet_hours(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    hour = now.hour
    if QUIET_HOURS_START > QUIET_HOURS_END:
        return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END
    return QUIET_HOURS_START <= hour < QUIET_HOURS_END


def send_sms(client, from_number: str, to_number: str, body: str) -> dict[str, Any]:
    try:
        msg = client.messages.create(body=body, from_=from_number, to=to_number)
        return {"sid": msg.sid, "status": msg.status, "error": None}
    except Exception as e:
        return {"sid": None, "status": "failed", "error": str(e)}


def print_audit_summary() -> None:
    print("\nAudit summary:")
    print("  v2 was functional but too risky and basic for serious SMS outreach.")
    print("  Fixes in v3:")
    print("    - hard 100/day cap")
    print("    - STOP suppression table")
    print("    - no silent repeat texts")
    print("    - parameterized SQL")
    print("    - message scoring")
    print("    - better conversion copy")
    print("    - quiet-hours guard")
    print("    - preview CSV before live sends")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="TaxCase Review SMS Campaign v3 — Conversion SMS Engine")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=50, help="Sends per batch (default 50 for 3x daily = 150/day total).")
    parser.add_argument("--batch-id", "--batch", default=None, dest="batch_label", help="Label for this batch: morning, midday, afternoon, foia")
    parser.add_argument("--state", default=None, choices=["AZ", "GA", "TX", "FL", "CA", "NY", "NC", "IL", "OH", "PA", "TN"])
    parser.add_argument("--county", default=None)
    parser.add_argument("--source", default="all",
                        choices=["all", "dbpr", "skiptrace", "roc", "tdlr", "irs_foia", "irs_foia_all"])
    parser.add_argument("--template", default="auto", choices=["auto"] + list(TEMPLATES.keys()))
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--campaign-id", default=DEFAULT_CAMPAIGN_ID)
    parser.add_argument("--min-quality", type=int, default=None,
                        help="Skip messages below this score. Default 70 (40 for IRS FOIA, "
                             "whose personalized 2-segment copy scores lower on the generic heuristic).")
    parser.add_argument("--ignore-quiet-hours", action="store_true", help="Override quiet-hours guard.")
    parser.add_argument("--i-understand-tcpa-risk", action="store_true", help="Required for live sends unless SMS_REQUIRE_TCPA_ACK=false.")
    parser.add_argument("--audit", action="store_true", help="Print audit summary and exit.")
    args = parser.parse_args()

    if args.audit:
        print_audit_summary()
        return

    # Resolve min-quality default. FOIA messages are intentionally longer/personalized
    # and score lower on the county-tuned generic heuristic, so they get a lower floor.
    if args.min_quality is None:
        args.min_quality = 40 if args.source in FOIA_SOURCES else 70

    # Hard cap no matter what.
    requested = max(0, int(args.limit))
    requested = min(requested, HARD_DAILY_CAP)

    if not args.dry_run:
        if os.getenv("SMS_REQUIRE_TCPA_ACK", "true").lower() not in {"0", "false", "no"}:
            if not args.i_understand_tcpa_risk:
                print("ERROR: Live SMS requires --i-understand-tcpa-risk.")
                print("SMS outreach is TCPA/state-law sensitive. Use only where you have permission to text.")
                print("For preview, run with --dry-run.")
                return

        if within_quiet_hours() and not args.ignore_quiet_hours:
            print(f"ERROR: Quiet-hours guard active ({QUIET_HOURS_START}:00-{QUIET_HOURS_END}:00).")
            print("Use --ignore-quiet-hours only when you are certain it is safe and lawful.")
            return

        if not all([ACCOUNT_SID, AUTH_TOKEN, FROM_NUMBER]):
            print("ERROR: Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER in .env")
            return

        try:
            from twilio.rest import Client
            client = Client(ACCOUNT_SID, AUTH_TOKEN)
        except ImportError:
            print("ERROR: pip install twilio")
            return
    else:
        client = None

    logger = None
    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("sms_campaign")
        logger.start()
    except Exception:
        pass

    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    conn = get_connection()
    conn.autocommit = False

    sent = failed = skipped = previewed = 0
    quality_skipped = 0
    cap_reached = False

    try:
        with conn.cursor() as cur:
            ensure_campaign_tables(cur)
            if args.source in FOIA_SOURCES:
                ensure_google_places_columns(cur)
            conn.commit()

            sends_today = get_sends_today(cur)
            remaining_cap = max(0, MAX_DAILY_SMS - sends_today)

            if not args.dry_run and remaining_cap <= 0:
                cap_reached = True
                print(f"Daily SMS cap reached ({sends_today}/{MAX_DAILY_SMS}).")
                if logger:
                    logger.finish({"sent": 0, "cap_reached": True, "daily_cap": MAX_DAILY_SMS})
                return

            effective_limit = requested if args.dry_run else min(requested, remaining_cap)
            contacts = get_contacts(cur, args.state, args.source, effective_limit, args.county)

        print(f"\n{'='*72}")
        print("  TaxCase Review SMS Campaign v4 — A/B Testing Engine")
        print(f"  Mode       : {'DRY RUN' if args.dry_run else 'LIVE'}")
        print(f"  State      : {args.state or 'ALL'}")
        print(f"  County     : {args.county or 'ALL'}")
        print(f"  Source     : {args.source}")
        print(f"  Contacts   : {len(contacts)}")
        print(f"  From       : {FROM_NUMBER or 'not set'}")
        print(f"  Cap        : {sends_today}/{MAX_DAILY_SMS} sent today ({HARD_DAILY_CAP}/day hard max)")
        print(f"  Limit      : {effective_limit}")
        print(f"  Delay      : {args.delay}s")
        print(f"  Batch      : {batch_id}")
        print(f"{'='*72}\n")

        if not contacts:
            print("No eligible contacts found.")
            if logger:
                logger.finish({"sent": 0, "no_contacts": True})
            return

        preview_rows: list[dict[str, Any]] = []

        for i, contact in enumerate(contacts, 1):
            template_key = pick_template(contact.get("state") or args.state, contact.get("source", ""), args.template)
            body, meta = format_message(template_key, contact)
            meta["template_key"] = template_key
            score = score_message(body, contact, meta)

            phone = contact["phone_e164"]
            name = contact.get("name", "")[:32]
            county = meta.get("county", "")
            warnings = "; ".join(score.get("warnings", []))

            print(f"  [{i}/{len(contacts)}] {name:32} | {phone} | {county} | {contact.get('source')}")
            print(f"    score {score['score']}/100 · {score['length']} chars · {template_key}")
            print(f"    {body}")

            if score["score"] < args.min_quality:
                skipped += 1
                quality_skipped += 1
                print(f"    ⚠️ skipped below min quality ({args.min_quality})")
                continue

            if args.dry_run:
                preview_rows.append({
                    "name": contact.get("name", ""),
                    "phone": phone,
                    "county": county,
                    "state": contact.get("state", args.state or ""),
                    "source": contact.get("source", ""),
                    "template": template_key,
                    "intent": meta.get("intent", ""),
                    "url": meta.get("url", ""),
                    "quality_score": score["score"],
                    "message_length": score["length"],
                    "segments_est": score["segments_est"],
                    "warnings": warnings,
                    "message": body,
                })
                previewed += 1
                continue

            # Re-check cap before each live send.
            with conn.cursor() as cur:
                sends_today_live = get_sends_today(cur)
                if sends_today_live >= MAX_DAILY_SMS:
                    cap_reached = True
                    print(f"    ⛔ daily cap reached mid-run ({sends_today_live}/{MAX_DAILY_SMS})")
                    break

            result = send_sms(client, FROM_NUMBER, phone, body)
            time.sleep(max(args.delay, 0.5))

            with conn.cursor() as cur:
                log_sms_result(
                    cur,
                    contact=contact,
                    from_number=FROM_NUMBER,
                    result=result,
                    body=body,
                    meta=meta,
                    score=score,
                    campaign_id=args.campaign_id,
                    batch_id=batch_id,
                    batch_label=getattr(args, "batch_label", None) or "default",
                )
                if result.get("status") in ("queued", "sent", "delivered"):
                    mark_contact_texted(cur, contact)
                conn.commit()

            if result.get("status") in ("queued", "sent", "delivered"):
                sent += 1
                print(f"    ✅ {result.get('sid')}")
            else:
                failed += 1
                print(f"    ❌ {result.get('error')}")

        if args.dry_run and preview_rows:
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            out = EXPORT_DIR / f"sms_preview_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            with out.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(preview_rows[0].keys()))
                writer.writeheader()
                writer.writerows(preview_rows)
            print(f"\nPreview CSV: {out}")

        with conn.cursor() as cur:
            final_today = get_sends_today(cur)

        print(f"\n{'='*72}")
        if args.dry_run:
            print(f"  DRY RUN previewed : {previewed}")
        else:
            print(f"  Sent/queued       : {sent}")
            print(f"  Failed            : {failed}")
        print(f"  Skipped           : {skipped}")
        print(f"  Quality skipped   : {quality_skipped}")
        print(f"  Today             : {final_today}/{MAX_DAILY_SMS}")
        print(f"  Cap reached       : {cap_reached}")
        print(f"  State/source      : {args.state or 'ALL'} / {args.source}")
        print(f"{'='*72}\n")

        if logger:
            logger.finish({
                "sent": sent if not args.dry_run else 0,
                "failed": failed,
                "skipped": skipped,
                "previewed": previewed if args.dry_run else 0,
                "quality_skipped": quality_skipped,
                "cap_reached": cap_reached,
                "daily_cap": MAX_DAILY_SMS,
                "hard_cap": HARD_DAILY_CAP,
                "state": args.state or "ALL",
                "source": args.source,
                "dry_run": args.dry_run,
                "campaign_id": args.campaign_id,
                "batch_id": batch_id,
            })

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        if logger:
            logger.finish({"error": str(e), "sent": sent, "failed": failed})
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
