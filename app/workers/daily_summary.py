"""
daily_summary.py (v4.2 — Optimization Intelligence Digest)
=========================================================
TaxCase Review daily pipeline + behavior/conversion intelligence digest.

Purpose:
  This summary is designed to be uploaded to ChatGPT/Claude and immediately show:
  - what is working
  - what is broken
  - where the lead engine is clogged
  - where traffic/conversion is leaking
  - what to optimize next

Key upgrades:
  - Daily / 7-day / 30-day / lifetime sends by sequence step
  - Opens/clicks by sequence step and time window
  - Ready-to-send counts for all 7 touches
  - Subject variant performance
  - State and county lead intelligence:
      liens vs matched contractors vs email-ready leads vs high-confidence matches
  - GA4 funnel + traffic summary
  - Clarity UX summary
  - Priority action logic

Usage:
  python -m app.workers.daily_summary --dry-run
  python -m app.workers.daily_summary
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
load_dotenv()

from app.core.db import get_connection, release_connection

try:
    from app.analytics.ga4_metrics import get_daily_ga4_summary
except Exception:
    get_daily_ga4_summary = None

try:
    from app.analytics.clarity_metrics import fetch_clarity_metrics
except Exception:
    fetch_clarity_metrics = None

try:
    from app.analytics.conversion_metrics import build_conversion_funnel
except Exception:
    build_conversion_funnel = None

try:
    from app.analytics.ux_intelligence import analyze_ux
except Exception:
    analyze_ux = None


BASE_DIR = Path(__file__).resolve().parents[2]

SUMMARY_SENDER = os.getenv("GMAIL_SUMMARY_SENDER", os.getenv("GMAIL_SENDER", "romy@taxcasereview.org"))
SUMMARY_PASSWORD = os.getenv("GMAIL_SUMMARY_PASSWORD", os.getenv("GMAIL_APP_PASSWORD", "")).replace(" ", "")
SENDER_NAME = os.getenv("GMAIL_SUMMARY_NAME", "TaxCase Review")
RECIPIENTS = [
    r.strip()
    for r in os.getenv("DAILY_SUMMARY_TO", "info@taxcasereview.org,romy@taxcasereview.org").split(",")
    if r.strip()
]

CAMPAIGN_ID = os.getenv("CAMPAIGN_ID", "lien_outreach_2026")
GOAL_CONVERSIONS = int(os.getenv("GOAL_CONVERSIONS", "500"))
PRICE_PER = int(os.getenv("PRICE_PER_CASE_REVIEW", "399"))
GOAL_REVENUE = GOAL_CONVERSIONS * PRICE_PER


# ─────────────────────────────────────────────────────────────────────────────
# Safe DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def safe_query(fn, default):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            result = fn(cur)
        conn.commit()
        return result
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        name = getattr(fn, "__name__", "query")
        print(f"  ⚠ Query warning ({name}): {e}")
        return default
    finally:
        # Return the connection to the pool (NOT conn.close(), which leaks the
        # pool slot and exhausts the pool after a handful of safe_query calls).
        release_connection(conn)


def _one(cur, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row:
        return 0
    return row[0] or 0


def _pct(n: float, d: float) -> float:
    return round((float(n) / max(float(d), 1.0)) * 100, 1)


def _money(v: float) -> str:
    return f"${float(v or 0):,.0f}"


# ─────────────────────────────────────────────────────────────────────────────
# Query-cache + index helpers (keep the summary fast and crash-proof)
# ─────────────────────────────────────────────────────────────────────────────

CACHE_FILE = BASE_DIR / "daily_summary_cache.json"


def _load_summary_cache() -> dict:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_summary_cache(key: str, data) -> None:
    cache = _load_summary_cache()
    cache[key] = data
    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=2, default=str),
                              encoding="utf-8")
    except Exception:
        pass


def ensure_email_indexes(cur) -> None:
    """Idempotently ensure the indexes the breakdown queries rely on exist
    (no-op where they already do)."""
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_email_sends_email
            ON email_sends(to_email);
        CREATE INDEX IF NOT EXISTS idx_email_sends_sent_at
            ON email_sends(sent_at);
        CREATE INDEX IF NOT EXISTS idx_email_sends_campaign
            ON email_sends(campaign_id);
    """)


# ─────────────────────────────────────────────────────────────────────────────
# Revenue / conversion
# ─────────────────────────────────────────────────────────────────────────────

def _get_conversion_stats(cur):
    try:
        cur.execute("SELECT COUNT(*), COALESCE(SUM(revenue), 0) FROM conversions")
        total = cur.fetchone()
        cur.execute("""
            SELECT COUNT(*), COALESCE(SUM(revenue), 0)
            FROM conversions
            WHERE converted_at >= NOW() - INTERVAL '24 hours'
        """)
        today = cur.fetchone()
        cur.execute("""
            SELECT COUNT(*), COALESCE(SUM(revenue), 0)
            FROM conversions
            WHERE converted_at >= NOW() - INTERVAL '7 days'
        """)
        week = cur.fetchone()
        cur.execute("""
            SELECT COUNT(*), COALESCE(SUM(revenue), 0)
            FROM conversions
            WHERE converted_at >= NOW() - INTERVAL '30 days'
        """)
        month = cur.fetchone()
        return {
            "total": total[0] or 0,
            "revenue": float(total[1] or 0),
            "today": today[0] or 0,
            "revenue_today": float(today[1] or 0),
            "week": week[0] or 0,
            "revenue_week": float(week[1] or 0),
            "month": month[0] or 0,
            "revenue_month": float(month[1] or 0),
        }
    except Exception:
        return {
            "total": 0, "revenue": 0.0,
            "today": 0, "revenue_today": 0.0,
            "week": 0, "revenue_week": 0.0,
            "month": 0, "revenue_month": 0.0,
        }




# ─────────────────────────────────────────────────────────────────────────────
# Booking / retargeting intelligence
# ─────────────────────────────────────────────────────────────────────────────

def _get_booking_stats(cur) -> dict:
    """Pull booking funnel stats from the bookings table."""
    try:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'pending')   AS pending,
                COUNT(*) FILTER (WHERE status = 'paid')      AS paid,
                COUNT(*) FILTER (WHERE status = 'abandoned') AS abandoned,
                COUNT(*) FILTER (WHERE status = 'canceled')  AS canceled,
                COUNT(*) FILTER (WHERE status = 'no_show')   AS no_show,
                COUNT(*)                                      AS total
            FROM bookings
        """)
        row = cur.fetchone()
        pending   = row[0] or 0
        paid      = row[1] or 0
        abandoned = row[2] or 0
        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE status='pending') AS pt,
                   COUNT(*) FILTER (WHERE status='paid')    AS pp
            FROM bookings WHERE calendly_booked_at >= NOW() - INTERVAL '24 hours'
        """)
        today = cur.fetchone()
        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE retarget_email_1_sent=TRUE),
                   COUNT(*) FILTER (WHERE retarget_email_2_sent=TRUE),
                   COUNT(*) FILTER (WHERE feedback_sent=TRUE),
                   COUNT(*) FILTER (WHERE retarget_email_1_sent=TRUE AND status='paid'),
                   COUNT(*) FILTER (WHERE retarget_email_2_sent=TRUE AND status='paid'),
                   COUNT(*) FILTER (WHERE feedback_sent=TRUE AND feedback_response IS NOT NULL)
            FROM bookings
        """)
        rt = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM bookings WHERE status='pending' AND retarget_email_1_sent=FALSE AND calendly_booked_at < NOW() - INTERVAL '23 hours'")
        needs_r1 = (cur.fetchone() or [0])[0]
        cur.execute("SELECT COUNT(*) FROM bookings WHERE status='pending' AND retarget_email_1_sent=TRUE AND retarget_email_2_sent=FALSE AND calendly_booked_at < NOW() - INTERVAL '71 hours'")
        needs_r2 = (cur.fetchone() or [0])[0]
        cur.execute("SELECT email, name, lien_county, lien_amount, paid_at, traffic_source, email_step FROM bookings WHERE status='paid' AND paid_at >= NOW() - INTERVAL '7 days' ORDER BY paid_at DESC LIMIT 5")
        recent_paid = cur.fetchall()
        conv_rate = round(paid / max(paid + abandoned, 1) * 100, 1)
        return {
            "total": row[5] or 0, "pending": pending, "paid": paid,
            "abandoned": abandoned, "canceled": row[3] or 0, "no_show": row[4] or 0,
            "pending_today": today[0] or 0, "paid_today": today[1] or 0,
            "r1_sent": rt[0] or 0, "r2_sent": rt[1] or 0, "r3_sent": rt[2] or 0,
            "r1_converted": rt[3] or 0, "r2_converted": rt[4] or 0,
            "feedback_responses": rt[5] or 0, "needs_r1_today": needs_r1,
            "needs_r2_today": needs_r2, "conversion_rate": conv_rate,
            "recent_paid": recent_paid or [],
        }
    except Exception as e:
        return {"total":0,"pending":0,"paid":0,"abandoned":0,"canceled":0,"no_show":0,
                "pending_today":0,"paid_today":0,"r1_sent":0,"r2_sent":0,"r3_sent":0,
                "r1_converted":0,"r2_converted":0,"feedback_responses":0,
                "needs_r1_today":0,"needs_r2_today":0,"conversion_rate":0.0,
                "recent_paid":[],"_error":str(e)}

# ─────────────────────────────────────────────────────────────────────────────
# Lead database intelligence
# ─────────────────────────────────────────────────────────────────────────────

def _get_lead_intelligence(cur):
    cur.execute("""
        SELECT
            COUNT(*) AS liens_total,
            COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours') AS liens_24h,
            COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS liens_7d,
            COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS liens_30d
        FROM normalized_liens
    """)
    lien_row = cur.fetchone()

    cur.execute("""
        SELECT
            COUNT(*) AS matched_total,
            COUNT(DISTINCT lien_id) AS matched_liens,
            COUNT(DISTINCT email) FILTER (
                WHERE email IS NOT NULL AND email != '' AND email NOT LIKE '%%@example.com'
            ) AS email_ready,
            COUNT(*) FILTER (
                WHERE confidence = 'high'
                  AND email IS NOT NULL AND email != '' AND email NOT LIKE '%%@example.com'
            ) AS high_confidence,
            COUNT(*) FILTER (
                WHERE confidence = 'medium'
                  AND email IS NOT NULL AND email != '' AND email NOT LIKE '%%@example.com'
            ) AS medium_confidence
        FROM lien_dbpr_contacts
    """)
    match_row = cur.fetchone()

    liens_total = lien_row[0] or 0
    matched_total = match_row[0] or 0
    matched_liens = match_row[1] or 0
    email_ready = match_row[2] or 0
    high = match_row[3] or 0
    medium = match_row[4] or 0

    return {
        "liens_total": liens_total,
        "liens_24h": lien_row[1] or 0,
        "liens_7d": lien_row[2] or 0,
        "liens_30d": lien_row[3] or 0,
        "matched_total": matched_total,
        "matched_liens": matched_liens,
        "email_ready": email_ready,
        "high_confidence": high,
        "medium_confidence": medium,
        "match_rate": _pct(matched_liens, liens_total),
        "email_coverage_rate": _pct(email_ready, liens_total),
        "high_confidence_rate": _pct(high, email_ready),
    }


def _get_state_breakdown(cur):
    """Per-state lead funnel.

    Rewritten for speed: the old query LEFT JOINed normalized_liens AND
    lien_dbpr_contacts off counties (a per-county lien x contact cartesian) and
    then self-joined email_sends four times on LOWER(to_email) — which timed out.
    Now each metric is aggregated ONCE in its own CTE and joined on the small
    per-state keys; the email join is on raw to_email (uses idx_email_sends_email,
    emails are already stored lowercased on both sides). Caps at 10 states, runs
    under a 30s statement timeout, and falls back to the last cached result if
    cancelled."""
    cached = _load_summary_cache().get("state_breakdown")
    try:
        cur.execute("SET LOCAL statement_timeout = '30s'")
        cur.execute("""
            WITH lien_counts AS (
                SELECT COALESCE(c.state,'Unknown') AS state, COUNT(*) AS liens,
                    COUNT(*) FILTER (WHERE nl.created_at >= NOW() - INTERVAL '24 hours') AS new_24h
                FROM normalized_liens nl JOIN counties c ON nl.county_id = c.id
                GROUP BY 1
            ),
            contact_counts AS (
                SELECT COALESCE(c.state,'Unknown') AS state,
                    COUNT(DISTINCT ldc.lien_id) AS matched_liens,
                    COUNT(DISTINCT ldc.email) FILTER (
                        WHERE ldc.email IS NOT NULL AND ldc.email <> ''
                          AND ldc.email NOT LIKE '%%@example.com') AS email_ready,
                    COUNT(DISTINCT ldc.email) FILTER (
                        WHERE ldc.confidence='high' AND ldc.email IS NOT NULL
                          AND ldc.email <> '' AND ldc.email NOT LIKE '%%@example.com') AS high_confidence
                FROM lien_dbpr_contacts ldc JOIN counties c ON ldc.county_id = c.id
                GROUP BY 1
            ),
            sent_counts AS (
                SELECT COALESCE(c.state,'Unknown') AS state,
                    COUNT(DISTINCT es.to_email) FILTER (WHERE es.sequence_step=1) AS email1_sent,
                    COUNT(DISTINCT es.to_email) FILTER (WHERE es.sequence_step=2) AS email2_sent,
                    COUNT(DISTINCT es.to_email) FILTER (WHERE es.sequence_step=3) AS email3_sent,
                    COUNT(DISTINCT es.to_email) FILTER (WHERE COALESCE(es.reply_received,FALSE)=TRUE) AS replied
                FROM email_sends es
                JOIN lien_dbpr_contacts ldc ON ldc.email = es.to_email
                JOIN counties c ON ldc.county_id = c.id
                WHERE es.campaign_id = %s AND es.status='sent'
                GROUP BY 1
            ),
            all_states AS (
                SELECT state FROM lien_counts UNION SELECT state FROM contact_counts
            )
            SELECT a.state, COALESCE(l.liens,0), COALESCE(cc.matched_liens,0),
                   COALESCE(cc.email_ready,0), COALESCE(cc.high_confidence,0),
                   COALESCE(sc.email1_sent,0), COALESCE(sc.email2_sent,0),
                   COALESCE(sc.email3_sent,0), COALESCE(sc.replied,0),
                   COALESCE(l.new_24h,0)
            FROM all_states a
            LEFT JOIN lien_counts l   ON l.state  = a.state
            LEFT JOIN contact_counts cc ON cc.state = a.state
            LEFT JOIN sent_counts sc  ON sc.state = a.state
            WHERE COALESCE(l.liens,0) > 0 OR COALESCE(cc.email_ready,0) > 0
            ORDER BY COALESCE(l.liens,0) DESC, COALESCE(cc.email_ready,0) DESC
            LIMIT 10
        """, (CAMPAIGN_ID,))
        rows = []
        for r in cur.fetchall():
            rows.append({
                "state": r[0],
                "liens": r[1] or 0,
                "matched_liens": r[2] or 0,
                "email_ready": r[3] or 0,
                "high_confidence": r[4] or 0,
                "email1_sent": r[5] or 0,
                "email2_sent": r[6] or 0,
                "email3_sent": r[7] or 0,
                "replied": r[8] or 0,
                "new_24h": (r[9] if len(r) > 9 else 0) or 0,
                "match_rate": _pct(r[2] or 0, r[1] or 0),
                "email_coverage_rate": _pct(r[3] or 0, r[1] or 0),
            })
        _save_summary_cache("state_breakdown", rows)
        return rows
    except Exception as e:
        print(f"  ⚠ state breakdown slow/failed ({e}); using cached results")
        try:
            cur.connection.rollback()
        except Exception:
            pass
        return cached or []


def _get_county_breakdown(cur):
    """Per-county lead funnel — same cartesian/self-join fix as the state
    breakdown, aggregated per county id, 30s timeout + cache fallback, top 35."""
    cached = _load_summary_cache().get("county_breakdown")
    try:
        cur.execute("SET LOCAL statement_timeout = '30s'")
        cur.execute("""
            WITH lc AS (
                SELECT c.id AS cid, COUNT(*) AS liens
                FROM normalized_liens nl JOIN counties c ON nl.county_id = c.id
                GROUP BY c.id
            ),
            cc AS (
                SELECT c.id AS cid,
                    COUNT(DISTINCT ldc.lien_id) AS matched_liens,
                    COUNT(DISTINCT ldc.email) FILTER (
                        WHERE ldc.email IS NOT NULL AND ldc.email <> ''
                          AND ldc.email NOT LIKE '%%@example.com') AS email_ready,
                    COUNT(DISTINCT ldc.email) FILTER (
                        WHERE ldc.confidence='high' AND ldc.email IS NOT NULL
                          AND ldc.email <> '' AND ldc.email NOT LIKE '%%@example.com') AS high_confidence
                FROM lien_dbpr_contacts ldc JOIN counties c ON ldc.county_id = c.id
                GROUP BY c.id
            ),
            sc AS (
                SELECT c.id AS cid,
                    COUNT(DISTINCT es.to_email) FILTER (WHERE es.sequence_step=1) AS email1_sent,
                    COUNT(DISTINCT es.to_email) FILTER (WHERE es.sequence_step=2) AS email2_sent,
                    COUNT(DISTINCT es.to_email) FILTER (WHERE es.sequence_step=3) AS email3_sent,
                    COUNT(DISTINCT es.to_email) FILTER (WHERE COALESCE(es.reply_received,FALSE)=TRUE) AS replied
                FROM email_sends es
                JOIN lien_dbpr_contacts ldc ON ldc.email = es.to_email
                JOIN counties c ON ldc.county_id = c.id
                WHERE es.campaign_id = %s AND es.status='sent'
                GROUP BY c.id
            ),
            ids AS (SELECT cid FROM lc UNION SELECT cid FROM cc)
            SELECT co.state, co.county_name, COALESCE(lc.liens,0),
                   COALESCE(cc.matched_liens,0), COALESCE(cc.email_ready,0),
                   COALESCE(cc.high_confidence,0), COALESCE(sc.email1_sent,0),
                   COALESCE(sc.email2_sent,0), COALESCE(sc.email3_sent,0),
                   COALESCE(sc.replied,0)
            FROM ids i JOIN counties co ON co.id = i.cid
            LEFT JOIN lc ON lc.cid = i.cid
            LEFT JOIN cc ON cc.cid = i.cid
            LEFT JOIN sc ON sc.cid = i.cid
            WHERE COALESCE(lc.liens,0) > 0 OR COALESCE(cc.email_ready,0) > 0
            ORDER BY COALESCE(lc.liens,0) DESC, COALESCE(cc.email_ready,0) DESC
            LIMIT 35
        """, (CAMPAIGN_ID,))
        rows = []
        for r in cur.fetchall():
            rows.append({
                "state": r[0] or "",
                "county_name": r[1] or "",
                "liens": r[2] or 0,
                "matched_liens": r[3] or 0,
                "email_ready": r[4] or 0,
                "high_confidence": r[5] or 0,
                "email1_sent": r[6] or 0,
                "email2_sent": r[7] or 0,
                "email3_sent": r[8] or 0,
                "replied": r[9] or 0,
                "match_rate": _pct(r[3] or 0, r[2] or 0),
                "email_coverage_rate": _pct(r[4] or 0, r[2] or 0),
            })
        _save_summary_cache("county_breakdown", rows)
        return rows
    except Exception as e:
        print(f"  ⚠ county breakdown slow/failed ({e}); using cached results")
        try:
            cur.connection.rollback()
        except Exception:
            pass
        return cached or []


# ─────────────────────────────────────────────────────────────────────────────
# Email sequence intelligence
# ─────────────────────────────────────────────────────────────────────────────

PERIODS = {
    "24h": "24 hours",
    "7d": "7 days",
    "30d": "30 days",
    "lifetime": None,
}

STEP_LABELS = {
    1: "Public record awareness",
    2: "Common misunderstanding",
    3: "Lien vs levy / options",
    4: "What happens if ignored",
    5: "Former IRS officer insight",
    6: "IRS collection timeline",
    7: "Final follow-up",
}

STEP_DELAYS = {2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 10}


def _period_filter(alias: str, period: str) -> str:
    interval = PERIODS[period]
    if not interval:
        return ""
    return f"AND {alias}.sent_at >= NOW() - INTERVAL '{interval}'"


def _get_email_sequence_stats(cur):
    total_contacts = _one(cur, """
        SELECT COUNT(DISTINCT email)
        FROM lien_dbpr_contacts
        WHERE email IS NOT NULL AND email != '' AND email NOT LIKE '%%@example.com'
    """)

    step_periods: dict[str, dict[int, dict[str, Any]]] = {}
    for period in PERIODS:
        step_periods[period] = {}
        date_filter = _period_filter("es", period)
        for step in range(1, 8):
            cur.execute(f"""
                SELECT
                    COUNT(DISTINCT es.to_email) AS sent,
                    COUNT(DISTINCT eo.tracking_id) AS opens,
                    COUNT(DISTINCT ec.tracking_id) AS clicks,
                    COUNT(DISTINCT es.to_email) FILTER (WHERE COALESCE(es.reply_received, FALSE)=TRUE) AS replies
                FROM email_sends es
                LEFT JOIN email_opens eo ON eo.tracking_id = es.tracking_id
                LEFT JOIN email_clicks ec ON ec.tracking_id = es.tracking_id
                WHERE es.campaign_id=%s
                  AND es.sequence_step=%s
                  AND es.status='sent'
                  {date_filter}
            """, (CAMPAIGN_ID, step))
            r = cur.fetchone()
            sent = r[0] or 0
            opens = r[1] or 0
            clicks = r[2] or 0
            replies = r[3] or 0
            step_periods[period][step] = {
                "sent": sent,
                "opens": opens,
                "clicks": clicks,
                "replies": replies,
                "open_rate": _pct(opens, sent),
                "click_rate": _pct(clicks, sent),
                "reply_rate": _pct(replies, sent),
            }

    steps_lifetime = {step: step_periods["lifetime"][step]["sent"] for step in range(1, 8)}

    status_counts = {}
    cur.execute("""
        SELECT status, COUNT(*)
        FROM email_sends
        WHERE campaign_id=%s
        GROUP BY status
    """, (CAMPAIGN_ID,))
    for status, count in cur.fetchall():
        status_counts[status or "unknown"] = count or 0

    ready = {}
    for step in range(2, 8):
        prev = step - 1
        delay_days = STEP_DELAYS[step]
        ready[step] = _one(cur, """
            SELECT COUNT(DISTINCT es_prev.to_email)
            FROM email_sends es_prev
            WHERE es_prev.campaign_id=%s
              AND es_prev.sequence_step=%s
              AND es_prev.status='sent'
              AND es_prev.sent_at <= NOW() - (%s || ' days')::interval
              AND COALESCE(es_prev.reply_received, FALSE)=FALSE
              AND COALESCE(es_prev.unsubscribed, FALSE)=FALSE
              AND NOT EXISTS (
                  SELECT 1
                  FROM email_sends es_next
                  WHERE LOWER(es_next.to_email)=LOWER(es_prev.to_email)
                    AND es_next.campaign_id=%s
                    AND es_next.sequence_step=%s
                    AND (
                        es_next.status='sent'
                        OR es_next.status='spam_trap'
                        OR COALESCE(es_next.unsubscribed, FALSE)=TRUE
                        OR COALESCE(es_next.reply_received, FALSE)=TRUE
                        OR (es_next.status='queued' AND es_next.sent_at > NOW() - INTERVAL '6 hours')
                    )
              )
        """, (CAMPAIGN_ID, prev, delay_days, CAMPAIGN_ID, step))

    opens = sum(step_periods["lifetime"][s]["opens"] for s in range(1, 8))
    clicks = sum(step_periods["lifetime"][s]["clicks"] for s in range(1, 8))
    sent_total = sum(step_periods["lifetime"][s]["sent"] for s in range(1, 8))
    replied = _one(cur, """
        SELECT COUNT(DISTINCT to_email)
        FROM email_sends
        WHERE campaign_id=%s AND COALESCE(reply_received, FALSE)=TRUE
    """, (CAMPAIGN_ID,))
    unsubscribed = _one(cur, """
        SELECT COUNT(DISTINCT to_email)
        FROM email_sends
        WHERE campaign_id=%s AND COALESCE(unsubscribed, FALSE)=TRUE
    """, (CAMPAIGN_ID,))

    # Subject variants — last 30 days and lifetime.
    variants = []
    try:
        cur.execute("""
            SELECT
                COALESCE(subject_variant,'legacy') AS variant,
                MIN(subject) AS example_subject,
                COUNT(DISTINCT es.to_email) AS sent,
                COUNT(DISTINCT eo.tracking_id) AS opens,
                COUNT(DISTINCT ec.tracking_id) AS clicks,
                ROUND(COUNT(DISTINCT eo.tracking_id)::numeric / NULLIF(COUNT(DISTINCT es.to_email),0) * 100, 1) AS open_rate,
                ROUND(COUNT(DISTINCT ec.tracking_id)::numeric / NULLIF(COUNT(DISTINCT es.to_email),0) * 100, 1) AS click_rate
            FROM email_sends es
            LEFT JOIN email_opens eo ON eo.tracking_id = es.tracking_id
            LEFT JOIN email_clicks ec ON ec.tracking_id = es.tracking_id
            WHERE es.campaign_id=%s
              AND es.status='sent'
              AND es.sent_at >= NOW() - INTERVAL '30 days'
            GROUP BY COALESCE(subject_variant,'legacy')
            HAVING COUNT(DISTINCT es.to_email) >= 5
            ORDER BY open_rate DESC NULLS LAST, click_rate DESC NULLS LAST, sent DESC
            LIMIT 12
        """, (CAMPAIGN_ID,))
        variants = [{
            "variant": r[0] or "legacy",
            "subject": r[1] or "",
            "sent": r[2] or 0,
            "opens": r[3] or 0,
            "clicks": r[4] or 0,
            "open_rate": float(r[5] or 0),
            "click_rate": float(r[6] or 0),
        } for r in cur.fetchall()]
    except Exception:
        variants = []

    # ── Lead scoring (lead_score lives on lien_dbpr_contacts) ──────────────────
    avg_score_today = None
    top_unsent: list = []
    try:
        cur.execute("""
            SELECT ROUND(AVG(score)::numeric, 1) FROM (
                SELECT DISTINCT ON (LOWER(es.to_email)) ldc.lead_score AS score
                FROM email_sends es
                JOIN lien_dbpr_contacts ldc ON LOWER(ldc.email) = LOWER(es.to_email)
                WHERE es.campaign_id = %s AND es.status = 'sent'
                  AND es.sent_at::date = CURRENT_DATE
                ORDER BY LOWER(es.to_email), ldc.lead_score DESC NULLS LAST
            ) x WHERE score IS NOT NULL
        """, (CAMPAIGN_ID,))
        r = cur.fetchone()
        avg_score_today = float(r[0]) if r and r[0] is not None else None

        cur.execute("""
            SELECT DISTINCT ON (LOWER(ldc.email))
                   ldc.lead_score, c.state, c.county_name, ldc.confidence
            FROM lien_dbpr_contacts ldc
            JOIN normalized_liens nl ON ldc.lien_id = nl.id
            JOIN counties c ON ldc.county_id = c.id
            WHERE ldc.email IS NOT NULL AND ldc.email != ''
              AND ldc.email NOT LIKE '%%@example.com'
              AND ldc.lead_score IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM email_sends es
                  WHERE LOWER(es.to_email) = LOWER(ldc.email)
                    AND es.campaign_id = %s
              )
            ORDER BY LOWER(ldc.email), ldc.lead_score DESC NULLS LAST
        """, (CAMPAIGN_ID,))
        scored_unsent = cur.fetchall()
        scored_unsent.sort(key=lambda x: (x[0] or 0), reverse=True)
        top_unsent = scored_unsent[:5]
    except Exception:
        pass

    # Subject-line optimizer — active variants under management (incl. AI ones).
    sl_optimizer = []
    try:
        cur.execute("""
            SELECT variant_id, source, sends, opens, open_rate, click_rate
            FROM subject_line_performance
            WHERE active = TRUE
            ORDER BY open_rate DESC NULLS LAST, sends DESC
        """)
        sl_optimizer = [dict(zip(
            ["variant_id", "source", "sends", "opens", "open_rate", "click_rate"], r))
            for r in cur.fetchall()]
    except Exception:
        sl_optimizer = []

    return {
        "avg_score_sent_today": avg_score_today,
        "top_unsent": top_unsent,
        "sl_optimizer": sl_optimizer,
        "total_contacts": total_contacts,
        "waiting": max(total_contacts - steps_lifetime.get(1, 0) - unsubscribed, 0),
        "steps": steps_lifetime,
        "periods": step_periods,
        "ready": ready,
        "status_counts": status_counts,
        "sent_24h": sum(step_periods["24h"][s]["sent"] for s in range(1, 8)),
        "sent_7d": sum(step_periods["7d"][s]["sent"] for s in range(1, 8)),
        "sent_30d": sum(step_periods["30d"][s]["sent"] for s in range(1, 8)),
        "sent_total": sent_total,
        "opens": opens,
        "clicks": clicks,
        "replied": replied,
        "unsubscribed": unsubscribed,
        "failed": status_counts.get("failed", 0),
        "throttled": status_counts.get("throttled", 0),
        "spam_trap": status_counts.get("spam_trap", 0),
        "stale_queued": status_counts.get("stale_queued", 0),
        "recent_queued": _one(cur, """
            SELECT COUNT(*)
            FROM email_sends
            WHERE campaign_id=%s
              AND status='queued'
              AND sent_at > NOW() - INTERVAL '6 hours'
        """, (CAMPAIGN_ID,)),
        "open_rate": _pct(opens, sent_total),
        "click_rate": _pct(clicks, sent_total),
        "reply_rate": _pct(replied, sent_total),
        "variants": variants,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GA4 / Clarity wrappers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_ga4():
    if not get_daily_ga4_summary:
        return {}
    try:
        result = get_daily_ga4_summary()
        if hasattr(result, "data"):
            raw = result.data or {}
        elif isinstance(result, dict):
            raw = result
        else:
            raw = {}

        traffic = raw.get("traffic", {}) or {}
        funnel = raw.get("funnel", {}) or {}
        sources = raw.get("sources", []) or []
        top_src = sources[0] if sources else {}
        top_pages = raw.get("top_pages", []) or []
        top_page = top_pages[0] if top_pages else {}

        source_medium = top_src.get("sessionSourceMedium", "") or ""
        if " / " in source_medium:
            top_source, top_medium = source_medium.split(" / ", 1)
        else:
            top_source, top_medium = source_medium, ""

        users = traffic.get("active_users", 0) or raw.get("users", 0) or 0
        sessions = traffic.get("sessions", 0) or raw.get("sessions", 0) or 0
        page_views = traffic.get("page_views", 0) or raw.get("page_views", 0) or 0

        return {
            "users": users,
            "sessions": sessions,
            "page_views": page_views,
            "pages_per_session": round(page_views / max(sessions, 1), 2),
            "engagement_rate": round(traffic.get("engagement_rate", 0) or raw.get("engagement_rate", 0) or 0, 1),
            "top_source": top_source or "(unknown)",
            "top_medium": top_medium or "",
            "top_landing_page": top_page.get("pagePath", "—") or "—",
            "top_landing_page_views": top_page.get("screenPageViews", 0) or 0,
            "questionnaire_start": funnel.get("questionnaire_start", 0) or 0,
            "questionnaire_complete": funnel.get("questionnaire_complete", 0) or 0,
            "calendly_booking": funnel.get("calendly_booking", 0) or 0,
            "stripe_checkout_started": funnel.get("stripe_checkout_started", 0) or 0,
            "stripe_payment_success": funnel.get("stripe_payment_success", 0) or 0,
            "report_views": (raw.get("reports") or {}).get("views", 0) or 0,
            "newsletter_signups": funnel.get("newsletter_signup", 0) or 0,
        }
    except Exception as e:
        print(f"  ⚠ GA4 warning: {e}")
        return {}


def _fetch_clarity():
    if not fetch_clarity_metrics:
        return {}
    try:
        clarity = fetch_clarity_metrics() or {}
        if hasattr(clarity, "data"):
            clarity = clarity.data or {}
        return clarity if isinstance(clarity, dict) else {}
    except Exception as e:
        print(f"  ⚠ Clarity warning: {e}")
        return {}


def _fetch_ux(clarity: dict):
    if not analyze_ux:
        return {"score": 0, "primary_issue": "UX analyzer unavailable"}
    try:
        ux = analyze_ux(clarity) or {}
        return ux if isinstance(ux, dict) else {"score": 0, "primary_issue": "UX analyzer returned no data"}
    except Exception as e:
        return {"score": 0, "primary_issue": f"UX analyzer warning: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
# HTML helpers
# ─────────────────────────────────────────────────────────────────────────────

def h(value: Any) -> str:
    import html
    return html.escape(str(value if value is not None else ""))


def badge(text: str, bg: str = "#eef2ff", color: str = "#0f1b2d") -> str:
    return f"<span style='display:inline-block;padding:3px 8px;border-radius:999px;background:{bg};color:{color};font-size:12px;font-weight:700'>{h(text)}</span>"


def card(title: str, value: str, note: str = "") -> str:
    return f"""
    <td style="width:25%;padding:8px">
      <div style="border:1px solid #e5e7eb;border-radius:12px;padding:14px;background:#ffffff">
        <div style="font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:.04em">{h(title)}</div>
        <div style="font-size:24px;font-weight:800;color:#0f1b2d;margin-top:6px">{value}</div>
        <div style="font-size:12px;color:#64748b;margin-top:4px">{h(note)}</div>
      </div>
    </td>
    """


def sec(title: str, body: str, note: str = "") -> str:
    note_html = f"<p style='margin:0 0 10px;color:#64748b;font-size:13px'>{h(note)}</p>" if note else ""
    return f"<h3 style='color:#0f1b2d;margin:28px 0 6px'>{title}</h3>{note_html}{body}"


def tr(label: str, value: str, note: str = "") -> str:
    return f"""
    <tr>
      <td style="padding:8px 10px;border-bottom:1px solid #eef2f7">{h(label)}</td>
      <td style="padding:8px 10px;border-bottom:1px solid #eef2f7;text-align:right;font-weight:700">{value}</td>
      <td style="padding:8px 10px;border-bottom:1px solid #eef2f7;color:#64748b">{h(note)}</td>
    </tr>
    """


def table(rows: str, headers=("Metric", "Value", "Note")) -> str:
    return f"""
    <table style="width:100%;border-collapse:collapse;margin:8px 0 18px;background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden">
      <tr style="background:#f8fafc">
        <th style="padding:10px;text-align:left;color:#334155">{h(headers[0])}</th>
        <th style="padding:10px;text-align:right;color:#334155">{h(headers[1])}</th>
        <th style="padding:10px;text-align:left;color:#334155">{h(headers[2])}</th>
      </tr>
      {rows}
    </table>
    """


def simple_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(
        f"<th style='padding:10px;text-align:{'right' if i>1 else 'left'};color:#334155'>{h(x)}</th>"
        for i, x in enumerate(headers)
    )
    body = ""
    for row in rows:
        body += "<tr>"
        for i, cell in enumerate(row):
            align = "right" if i > 1 else "left"
            body += f"<td style='padding:8px 10px;border-bottom:1px solid #eef2f7;text-align:{align}'>{cell}</td>"
        body += "</tr>"
    return f"""
    <table style="width:100%;border-collapse:collapse;margin:8px 0 18px;background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden">
      <tr style="background:#f8fafc">{head}</tr>{body}
    </table>
    """


# ─────────────────────────────────────────────────────────────────────────────
# Sections
# ─────────────────────────────────────────────────────────────────────────────

def build_email_sequence_section(seq: dict, sender: dict | None = None) -> str:
    summary_rows = ""
    if sender:
        kind = sender.get("sender_kind", "")
        label = ("⚠️ Gmail cold account" if kind == "gmail_cold"
                 else "Workspace (legacy)" if kind == "workspace_legacy"
                 else "—")
        acct = sender.get("sender_account") or sender.get("sender_login") or "—"
        summary_rows += tr("Cold sending account", h(acct),
                           f"{label} · replies → {sender.get('reply_to','—')}")
    summary_rows += tr("Total email-ready contacts", f"{seq.get('total_contacts',0):,}", f"{seq.get('waiting',0):,} not contacted")
    summary_rows += tr("Sends last 24h / 7d / 30d", f"{seq.get('sent_24h',0):,} / {seq.get('sent_7d',0):,} / {seq.get('sent_30d',0):,}", "all sequence steps")
    summary_rows += tr("Lifetime sends", f"{seq.get('sent_total',0):,}", "all 7 touches")
    summary_rows += tr("Open / click / reply rate", f"{seq.get('open_rate',0)}% / {seq.get('click_rate',0)}% / {seq.get('reply_rate',0)}%", "lifetime tracked")
    summary_rows += tr("Failed / throttled / stale queued", f"{seq.get('failed',0):,} / {seq.get('throttled',0):,} / {seq.get('stale_queued',0):,}", "health checks")
    _avg = seq.get("avg_score_sent_today")
    summary_rows += tr("Avg lead score (sent today)", f"{_avg}" if _avg is not None else "—", "0-100; higher = better lead")

    top_unsent = seq.get("top_unsent", [])
    unsent_rows = [[f"{(s if s is not None else '—')}", state or "?", h((county or "?")[:20]), conf or "?"]
                   for (s, state, county, conf) in top_unsent]
    if not unsent_rows:
        unsent_rows = [["—", "—", "no scored unsent contacts", "—"]]

    # Subject-line optimizer — active variants (flag AI-generated challengers).
    sl_rows = []
    for v in seq.get("sl_optimizer", []):
        flag = " 🤖 NEW (AI)" if v.get("source") == "ai" else ""
        sl_rows.append([
            h(v["variant_id"]) + flag,
            f"{v.get('sends',0):,}",
            f"{v.get('opens',0):,}",
            f"{float(v.get('open_rate',0) or 0)}%",
            f"{float(v.get('click_rate',0) or 0)}%",
        ])
    if not sl_rows:
        sl_rows = [["—", "—", "—", "no optimizer data yet", "—"]]

    step_rows = []
    for step in range(1, 8):
        p24 = seq["periods"]["24h"][step]
        p7 = seq["periods"]["7d"][step]
        p30 = seq["periods"]["30d"][step]
        life = seq["periods"]["lifetime"][step]
        ready = seq.get("ready", {}).get(step, "—") if step > 1 else "new pool"
        step_rows.append([
            f"Email {step}",
            h(STEP_LABELS.get(step, "")),
            f"{p24['sent']:,}",
            f"{p7['sent']:,}",
            f"{p30['sent']:,}",
            f"{life['sent']:,}",
            f"{life['open_rate']}%",
            f"{life['click_rate']}%",
            f"{ready:,}" if isinstance(ready, int) else ready,
        ])

    variants = seq.get("variants", [])
    variant_rows = []
    for v in variants[:10]:
        variant_rows.append([
            h(v["variant"]),
            h(v.get("subject", ""))[:80],
            f"{v.get('sent',0):,}",
            f"{v.get('open_rate',0)}%",
            f"{v.get('click_rate',0)}%",
        ])
    if not variant_rows:
        variant_rows = [["—", "No variant data yet", "—", "—", "—"]]

    return (
        sec("📧 Email Engine — Executive View", table(summary_rows),
            "This is the primary cold outreach health section. Watch opens, clicks, replies, throttles, and ready queues.")
        + sec("📬 Sends by Sequence Step", simple_table(
            ["Step", "Purpose", "24h", "7d", "30d", "Lifetime", "Open", "Click", "Ready"],
            step_rows
        ))
        + sec("🧪 Subject Line Testing", simple_table(
            ["Variant", "Example subject", "Sent", "Open", "Click"],
            variant_rows
        ))
        + sec("🎯 Top Scored Unsent Leads", simple_table(
            ["Score", "State", "County", "Confidence"],
            unsent_rows
        ), "Highest-scored email-ready contacts not yet contacted — these go out next.")
        + sec("🔬 Subject Line Optimizer (bandit)", simple_table(
            ["Variant", "Sends", "Opens", "Open %", "Click %"],
            sl_rows
        ), "Active variants the ε-greedy bandit picks from. 🤖 = new AI-generated challenger under test.")
    )


def _outreach_csv(name: str) -> list[dict]:
    p = BASE_DIR / "data" / "outreach" / name
    if not p.exists():
        return []
    try:
        import csv as _csv
        with p.open(encoding="utf-8") as f:
            return list(_csv.DictReader(f))
    except Exception:
        return []


def build_outreach_section(runs: list[dict]) -> str:
    """Consolidated link-building / PR outreach panel. Today's activity comes from
    the pipeline log (haro_monitor / guest_post_outreach / press_release /
    broken_links run records); cumulative stats come from the data/outreach
    tracker CSVs the scripts write. (There is no backlink_outreach DB table — the
    trackers are CSV-based — so cumulative figures are read from those.)"""
    now = datetime.now()
    is_sunday = now.weekday() == 6
    month_prefix = now.strftime("%Y-%m")

    def latest(rt: str):
        matches = [r for r in runs if r.get("run_type") == rt]
        return max(matches, key=lambda r: r.get("started", ""), default=None)

    def metric(run, key, default=0):
        return (run or {}).get("metrics", {}).get(key, default)

    haro   = latest("haro_monitor")
    guest  = latest("guest_post_outreach")
    press  = latest("press_release")
    broken = latest("broken_links")

    # Cumulative stats from the backlink_outreach DB table.
    try:
        from scripts.outreach.outreach_db import get_counts
        cum = get_counts() or {}
    except Exception as e:
        print(f"  ⚠ outreach counts unavailable: {e}")
        cum = {}
    gp_total  = cum.get("guest_post_total", 0)
    gp_resp   = cum.get("guest_post_responses", 0)
    pr_month  = cum.get("press_release_month", 0)
    bl_total  = cum.get("broken_link_total", 0)
    dir_sub   = cum.get("directories_submitted", 0)
    backlinks = cum.get("backlinks_confirmed", 0)

    def status(ran: bool, scheduled: bool) -> str:
        if not scheduled:
            return badge("➖ n/a", "#f1f5f9", "#64748b")
        return (badge("✅ ran", "#dcfce7", "#15803d") if ran
                else badge("❌ missed", "#fee2e2", "#b91c1c"))

    rows = [
        ["HARO queries reviewed today", f"{metric(haro, 'reviewed', 0):,}",
         status(haro is not None, True)],
        ["HARO high-priority drafts sent to Romy", f"{metric(haro, 'drafts_sent', 0):,}",
         status(haro is not None, True)],
        ["Guest post pitches sent (today / total)",
         f"{metric(guest, 'pitches_sent', 0)} / {gp_total}",
         status(guest is not None, guest is not None)],
        ["Guest post responses received", f"{gp_resp}", "—"],
        ["Press releases generated this month", f"{pr_month}",
         status(press is not None, is_sunday)],
        ["Broken-link opportunities (today / total)",
         f"{metric(broken, 'opportunities', 0)} / {bl_total}",
         status(broken is not None, is_sunday)],
        ["Directories submitted (total / 30)", f"{dir_sub} / 30", "—"],
        ["Confirmed backlinks earned", f"{backlinks}", "—"],
    ]
    note = ("Today's activity from the pipeline log; cumulative from the "
            "backlink_outreach table. Status: ✅ ran today · ❌ scheduled but missing · ➖ not scheduled today.")
    return sec("🔗 Outreach Engine",
               simple_table(["Metric", "Value", "Status"], rows), note)


def build_lead_database_section(lead: dict, states: list[dict], counties: list[dict]) -> str:
    rows = ""
    rows += tr("Liens total", f"{lead.get('liens_total',0):,}", f"+{lead.get('liens_24h',0):,} 24h · +{lead.get('liens_7d',0):,} 7d · +{lead.get('liens_30d',0):,} 30d")
    rows += tr("Matched liens", f"{lead.get('matched_liens',0):,}", f"{lead.get('match_rate',0)}% of liens matched to contractor/contact data")
    rows += tr("Email-ready leads", f"{lead.get('email_ready',0):,}", f"{lead.get('email_coverage_rate',0)}% lien-to-email coverage")
    rows += tr("High confidence contacts", f"{lead.get('high_confidence',0):,}", f"{lead.get('high_confidence_rate',0)}% of email-ready leads")
    rows += tr("Medium confidence contacts", f"{lead.get('medium_confidence',0):,}", "review quality before scaling")

    state_rows = []
    for s in states:
        new24 = s.get("new_24h", 0)
        state_rows.append([
            h(s["state"]),
            f"{s['liens']:,}",
            (f"<b style='color:#15803d'>+{new24:,}</b>" if new24 else "—"),
            f"{s['matched_liens']:,}",
            f"{s['email_ready']:,}",
            f"{s['high_confidence']:,}",
            f"{s['match_rate']}%",
            f"{s['email_coverage_rate']}%",
            f"{s['email1_sent']:,}",
            f"{s['replied']:,}",
        ])

    county_rows = []
    for c in counties:
        county_rows.append([
            h(c["state"]),
            h(c["county_name"]),
            f"{c['liens']:,}",
            f"{c['matched_liens']:,}",
            f"{c['email_ready']:,}",
            f"{c['high_confidence']:,}",
            f"{c['match_rate']}%",
            f"{c['email_coverage_rate']}%",
            f"{c['email1_sent']:,}",
            f"{c['replied']:,}",
        ])

    return (
        sec("🏦 Lead Engine — Lien → Contractor → Email Pipeline", table(rows),
            "This shows whether scraping and enrichment are producing usable leads, not just raw liens.")
        + sec("🗺️ State Breakdown", simple_table(
            ["State", "Liens", "New 24h", "Matched", "Email ready", "High conf", "Match", "Coverage", "Email 1", "Replied"],
            state_rows
        ))
        + sec("📍 County Breakdown", simple_table(
            ["State", "County", "Liens", "Matched", "Email ready", "High conf", "Match", "Coverage", "Email 1", "Replied"],
            county_rows
        ))
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline-log–driven sections (collection runs + content automation)
# ─────────────────────────────────────────────────────────────────────────────

def _read_pipeline_today(d=None) -> list[dict]:
    """All pipeline-log records for a given day (logs/pipeline/YYYY-MM-DD.jsonl);
    defaults to today. Every worker that uses PipelineLogger appends one record."""
    from datetime import date
    d = d or date.today()
    f = BASE_DIR / "logs" / "pipeline" / f"{d.isoformat()}.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").strip().splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _latest_run(runs: list[dict], predicate) -> dict | None:
    """Most recent record (by start time) whose run_type matches predicate."""
    matches = [r for r in runs if predicate(r.get("run_type", ""))]
    return max(matches, key=lambda r: r.get("started", ""), default=None)


def _status_chip(ok: bool) -> str:
    return (badge("✅ ran", "#dcfce7", "#15803d") if ok
            else badge("❌ missing", "#fee2e2", "#b91c1c"))


def _blog_published_today() -> tuple[bool, str]:
    """Blog tracks its own publish history (data/blog_publish_history.json:
    {slug: 'YYYY-MM-DD'}) instead of PipelineLogger."""
    from datetime import date
    f = BASE_DIR / "data" / "blog_publish_history.json"
    if not f.exists():
        return False, ""
    try:
        hist = json.loads(f.read_text())
    except Exception:
        return False, ""
    today = date.today().isoformat()
    todays = [slug for slug, d in hist.items() if d == today]
    return (bool(todays), todays[0] if todays else "")


def build_collection_status_section(runs: list[dict]) -> str:
    """Task 2 — did the data-collection pipeline run today, per state: how many
    new liens added and any failures. Sourced from data_collection_<state>
    pipeline-log records (scripts/data_engine/run_daily.py)."""
    coll = [r for r in runs if r.get("run_type", "").startswith("data_collection_")]
    if not coll:
        return sec("📥 Collection Runs Today (per state)",
                   "<p style='color:#94a3b8;font-size:13px'>No data-collection runs "
                   "logged today — scripts/data_engine/run_daily.py has not run yet.</p>",
                   "Did today's scrape/enrichment run per state, and what did it add.")
    rows = []
    for r in sorted(coll, key=lambda x: x.get("started", "")):
        state = r["run_type"].replace("data_collection_", "").upper()
        m = r.get("metrics", {})
        ok = r.get("status") == "ok" and not m.get("error")
        status = (badge("✅ ok", "#dcfce7", "#15803d") if ok
                  else badge("❌ failed", "#fee2e2", "#b91c1c"))
        liens = m.get("liens", 0)
        detail = (f"lic +{m.get('licenses',0)} · match +{m.get('matched',0)} · "
                  f"synced {m.get('synced',0)}") if ok else h(m.get("error", "see logs"))[:90]
        rows.append([
            h(state),
            status,
            (f"<b style='color:#15803d'>+{liens:,}</b>" if liens else "0"),
            r.get("started", "")[11:19],
            detail,
        ])
    return sec("📥 Collection Runs Today (per state)", simple_table(
        ["State", "Status", "New liens", "Started", "Detail"], rows),
        "Did today's scrape/enrichment run per state, and what did it add.")


def _email_sends_by_day(cur, since) -> dict:
    """{ 'YYYY-MM-DD': sent_count } for sends on/after `since`."""
    cur.execute("""
        SELECT DATE(sent_at) AS d, COUNT(*) FROM email_sends
        WHERE status = 'sent' AND sent_at >= %s
        GROUP BY 1
    """, (since,))
    return {r[0].isoformat(): r[1] for r in cur.fetchall()}


def _blog_publish_dates() -> set:
    """Set of 'YYYY-MM-DD' dates a blog was published (data/blog_publish_history.json)."""
    f = BASE_DIR / "data" / "blog_publish_history.json"
    if not f.exists():
        return set()
    try:
        return set(json.loads(f.read_text()).values())
    except Exception:
        return set()


def build_weekly_calendar_section() -> str:
    """Full-week scheduled-vs-actual grid (rows = automation, cols = Mon→Sun).
    ✅ ran+ok · ❌ scheduled but missing/failed · ⬜ scheduled (upcoming) · ➖ not scheduled.

    Scheduling comes from scripts/schedule_config.SCHEDULE (the single source of
    truth) — NOT inferred from log history. A task's SCHEDULE key is also its
    PipelineLogger run_type, so "did it run" is a key-for-key log lookup; only
    email sends and blog posts use their own dedicated signals (DB / publish
    history) instead of a pipeline-log record."""
    from datetime import date, timedelta
    from scripts.schedule_config import SCHEDULE, is_scheduled_on
    today  = date.today()
    monday = today - timedelta(days=today.weekday())
    days   = [monday + timedelta(days=i) for i in range(7)]          # Mon..Sun
    WD     = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    runs_by_day  = {d: _read_pipeline_today(d) for d in days}
    email_counts = safe_query(lambda cur: _email_sends_by_day(cur, monday), {})
    blog_dates   = _blog_publish_dates()

    def ran_ok(d, task_key: str) -> bool:
        return any(r.get("status") == "ok" and r.get("run_type", "") == task_key
                   for r in runs_by_day[d])

    # Display label + how to tell whether the task actually ran+succeeded on a
    # day. Every key here must exist in SCHEDULE; scheduling is read from there.
    # Default detection = a successful pipeline-log record under the same key.
    def _default_didrun(task_key):
        return lambda d: ran_ok(d, task_key)

    automations = [
        ("📧 Email sends",         "email_sends",
         lambda d: email_counts.get(d.isoformat(), 0) > 0),
        ("📱 Social post",         "social_post",         _default_didrun("social_post")),
        ("🎬 Reel (HeyGen)",       "reel_heygen",         _default_didrun("reel_heygen")),
        ("🎬 Reel (Remotion)",     "reel_remotion",       _default_didrun("reel_remotion")),
        ("📝 Blog post",           "blog_post",
         lambda d: d.isoformat() in blog_dates),
        ("📥 Data collection FL",  "data_collection_fl",  _default_didrun("data_collection_fl")),
        ("📥 Data collection TX",  "data_collection_tx",  _default_didrun("data_collection_tx")),
        ("📥 Data collection GA",  "data_collection_ga",  _default_didrun("data_collection_ga")),
        ("📥 Data collection IL",  "data_collection_il",  _default_didrun("data_collection_il")),
        ("📥 Data collection AZ",  "data_collection_az",  _default_didrun("data_collection_az")),
        ("🎯 Lead scoring",        "lead_scoring",        _default_didrun("lead_scoring")),
        ("📄 Collection pages",    "collection_pages",    _default_didrun("collection_pages")),
        ("✉️ Email enrichment",    "email_enrichment",    _default_didrun("email_enrichment")),
        ("🆓 Free email enrich",   "free_email_enrichment", _default_didrun("free_email_enrichment")),
        ("📊 Daily summary",       "daily_summary",       _default_didrun("daily_summary")),
        ("🛰️ Weekly intel",        "weekly_intel",        _default_didrun("weekly_intel")),
        ("🏛️ County lien intel",   "county_lien_intel",   _default_didrun("county_lien_intel")),
        ("🗓️ Monthly report",      "monthly_report",      _default_didrun("monthly_report")),
        ("🔗 Guest post outreach", "guest_post_outreach", _default_didrun("guest_post_outreach")),
    ]

    def cell(scheduled: bool, ok: bool, d) -> str:
        if not scheduled:        return "<span style='color:#cbd5e1'>➖</span>"      # not scheduled today
        if ok:                   return "✅"                                         # scheduled + ran ok
        if d > today:            return "<span style='color:#94a3b8'>⬜</span>"      # scheduled, upcoming
        return "<span style='color:#b91c1c'>❌</span>"                              # scheduled, past/today, missing/failed

    rows = []
    for label, task_key, didrun in automations:
        cells = []
        for d in days:
            scheduled = is_scheduled_on(task_key, d)
            cells.append(cell(scheduled, scheduled and didrun(d), d))
        rows.append([label] + cells)

    headers = ["Automation"] + [f"{WD[d.weekday()]} {d.month}/{d.day}" for d in days]
    return sec("🗓️ Weekly Automation Calendar",
               simple_table(headers, rows),
               "Scheduled vs actual, this week (Mon→Sun), per scripts/schedule_config.SCHEDULE. "
               "✅ scheduled + ran ok · ❌ scheduled but missing/failed · ⬜ scheduled (upcoming) · ➖ not scheduled.")


def build_content_automation_section(runs: list[dict]) -> str:
    """Task 3 — today's status for every content automation. ❌ means the worker
    did not run or failed today, so a missing automation is immediately visible."""
    rows = []

    blog_ok, blog_slug = _blog_published_today()
    rows.append(["Blog post", _status_chip(blog_ok),
                 (h(blog_slug) if blog_ok else "no post published today")])

    social = _latest_run(runs, lambda t: t == "social_post")
    if social:
        m = social.get("metrics", {})
        sent = bool(m.get("sent"))
        st = social.get("status")
        if st == "quality_rejected":
            chip = badge(f"⚠️ quality rejected ({m.get('quality','?')}/100)",
                         "#fef9c3", "#a16207")
            rows.append(["Social media post", chip,
                         (f"{m.get('post_type','?')} scored below the "
                          f"{m.get('threshold','?')}/100 bar twice — not posted")])
        else:
            rows.append(["Social media post",
                         _status_chip(st == "ok" and sent),
                         (f"{m.get('platform','?')} · {m.get('post_type','?')} · "
                          f"score {m.get('quality','?')}" if sent
                          else f"generated but not sent ({m.get('post_type','?')})")])
    else:
        rows.append(["Social media post", _status_chip(False), "no run logged today"])

    reel = _latest_run(runs, lambda t: t.startswith("reel_"))
    if reel:
        m = reel.get("metrics", {})
        posted = bool(m.get("posted")) and not m.get("dry_run")
        rows.append(["Reel",
                     _status_chip(reel.get("status") == "ok" and posted),
                     (f"{m.get('engine','?')} · {m.get('reel_type','?')} · "
                      f"score {m.get('quality_score','?')}"
                      + (" · posted" if posted else " · not posted"))])
    else:
        rows.append(["Reel", _status_chip(False), "no run logged today"])

    cp = _latest_run(runs, lambda t: t == "collection_pages")
    if cp:
        m = cp.get("metrics", {})
        detail = f"{m.get('updated', 0)} updated · {m.get('counties', 0)} counties tracked"
        if m.get("new_drafts"):
            detail += f" · {m.get('new_drafts')} new-page drafts"
        if m.get("published"):
            detail += " · published"
        rows.append(["Collection pages", _status_chip(cp.get("status") == "ok"), detail])
    else:
        rows.append(["Collection pages", _status_chip(False), "no run logged today"])

    ds = _latest_run(runs, lambda t: t == "daily_summary")
    if ds:
        t = ds.get("started", "")
        try:
            when = datetime.fromisoformat(t).strftime("%I:%M %p")
        except Exception:
            when = t[11:19]
        rows.append(["Daily summary", _status_chip(ds.get("status") == "ok"),
                     f"last sent {when} → {ds.get('metrics',{}).get('to','')}"])
    else:
        rows.append(["Daily summary", badge("▶ running", "#fef9c3", "#854d0e"),
                     "this run (logged after send)"])

    return sec("🤖 Content Automation — Today", simple_table(
        ["Automation", "Status", "Detail"], rows),
        "Pulled from logs/pipeline/. ❌ = the worker did not run or failed today.")


def build_data_collection_section(cur) -> str:
    """Per-state data-engine summary: liens, licenses, matches, emails, pipeline.

    Reads the data engine's normalized_contacts staging table. Safe if the
    table does not exist yet (caller wraps this in safe_query)."""
    cur.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'normalized_contacts'
        )
    """)
    if not cur.fetchone()[0]:
        return sec("🛰️ Data Engine — Multi-State Collection",
                   "<p style='color:#94a3b8;font-size:13px'>normalized_contacts "
                   "not built yet — run scripts/data_engine/run_daily.py.</p>")

    cur.execute("""
        SELECT state,
               COUNT(*) AS licenses,
               COUNT(*) FILTER (WHERE has_lien_match) AS matched,
               COUNT(*) FILTER (WHERE email IS NOT NULL AND email <> '') AS emails
        FROM normalized_contacts
        GROUP BY state
    """)
    contacts = {r[0]: {"licenses": r[1], "matched": r[2], "emails": r[3]}
                for r in cur.fetchall()}

    cur.execute("SELECT COALESCE(state,'?'), COUNT(*) FROM normalized_liens GROUP BY state")
    liens = {r[0]: r[1] for r in cur.fetchall()}

    cur.execute("""
        SELECT nc.state, COUNT(DISTINCT LOWER(nc.email))
        FROM normalized_contacts nc
        JOIN lien_dbpr_contacts ldc ON LOWER(ldc.email) = LOWER(nc.email)
        WHERE nc.email IS NOT NULL AND nc.email <> ''
        GROUP BY nc.state
    """)
    in_pipe = {r[0]: r[1] for r in cur.fetchall()}

    states = sorted(s for s in set(list(contacts) + list(liens)) if s)
    rows = []
    for st in states:
        c = contacts.get(st, {})
        lic = c.get("licenses", 0)
        matched = c.get("matched", 0)
        emails = c.get("emails", 0)
        match_pct = _pct(matched, lic)
        rows.append([
            h(st),
            f"{liens.get(st, 0):,}",
            f"{lic:,}",
            f"{matched:,}",
            f"{emails:,}",
            f"{in_pipe.get(st, 0):,}",
            f"{match_pct}%",
        ])
    if not rows:
        rows = [["—", "0", "0", "0", "0", "0", "0%"]]

    return sec("🛰️ Data Engine — Multi-State Collection", simple_table(
        ["State", "Liens", "Licenses", "Matched", "Emails", "In pipeline", "Match%"],
        rows
    ), "Centralized engine: liens scraped, license universe, lien↔license matches, "
       "enriched emails, and rows synced into the email pipeline.")


def build_traffic_section(ga4: dict, clarity: dict, ux: dict) -> str:
    questionnaire_start = ga4.get("questionnaire_start", 0)
    users = ga4.get("users", 0)
    sessions = ga4.get("sessions", 0)
    page_views = ga4.get("page_views", 0)

    rows = ""
    rows += tr("Users / sessions / views", f"{users:,} / {sessions:,} / {page_views:,}", f"{ga4.get('pages_per_session',0)} pages/session")
    rows += tr("Engagement rate", f"{ga4.get('engagement_rate',0)}%", "GA4")
    rows += tr("Top source", f"{h(ga4.get('top_source','—'))} / {h(ga4.get('top_medium','—'))}", "GA4")
    rows += tr("Top landing page", h(ga4.get("top_landing_page", "—")), f"{ga4.get('top_landing_page_views',0):,} views")
    rows += tr("Quiz starts", f"{questionnaire_start:,}", f"{_pct(questionnaire_start, users)}% of users")
    rows += tr("Quiz completed", f"{ga4.get('questionnaire_complete',0):,}", f"{_pct(ga4.get('questionnaire_complete',0), max(questionnaire_start,1))}% of starts")
    rows += tr("Bookings / checkouts / payments", f"{ga4.get('calendly_booking',0):,} / {ga4.get('stripe_checkout_started',0):,} / {ga4.get('stripe_payment_success',0):,}", "booking before payment")

    clarity_rows = ""
    clarity_rows += tr("Clarity sessions", f"{clarity.get('sessions', clarity.get('total_sessions', 0)):,}", f"{clarity.get('bot_sessions', 0):,} bot/test")
    clarity_rows += tr("Average scroll depth", f"{clarity.get('avg_scroll_depth', clarity.get('scroll_depth', 0))}%", "watch if below 45%")
    clarity_rows += tr("Rage / dead / quickback clicks", f"{clarity.get('rage_clicks',0):,} / {clarity.get('dead_clicks',0):,} / {clarity.get('quick_backs',0):,}", "UX friction signals")
    clarity_rows += tr("Script errors", f"{clarity.get('script_errors',0):,}", "technical friction")
    clarity_rows += tr("UX health score", f"{ux.get('score', 0)}/100", ux.get("primary_issue", "—"))

    return (
        sec("👀 Website Traffic + GA4 Funnel", table(rows),
            "If users are low, traffic is the bottleneck. If users are decent and quiz starts are zero, landing/CTA is the bottleneck.")
        + sec("🖱️ Clarity UX Intelligence", table(clarity_rows),
              "This separates UX problems from traffic/offer problems.")
    )


def build_revenue_section(conv: dict) -> str:
    pct_goal = round(conv.get("total", 0) / max(GOAL_CONVERSIONS, 1) * 100, 1)
    rows = ""
    rows += tr("Paid case reviews", f"{conv.get('total',0):,}", f"+{conv.get('today',0):,} today · +{conv.get('week',0):,} 7d · +{conv.get('month',0):,} 30d")
    rows += tr("Revenue", _money(conv.get("revenue", 0)), f"{_money(conv.get('revenue_today',0))} today · {_money(conv.get('revenue_week',0))} 7d · {_money(conv.get('revenue_month',0))} 30d")
    rows += tr("Goal progress", f"{pct_goal}%", f"{GOAL_CONVERSIONS:,} reviews × ${PRICE_PER:,}")
    rows += tr("Remaining reviews", f"{max(0, GOAL_CONVERSIONS - conv.get('total',0)):,}", f"remaining revenue target: {_money(max(0, GOAL_REVENUE - conv.get('revenue',0)))}")
    return sec("💰 Revenue Snapshot", table(rows))


def build_action_items(lead: dict, seq: dict, ga4: dict, clarity: dict, ux: dict, conv: dict) -> str:
    actions = []

    users = ga4.get("users", 0)
    starts = ga4.get("questionnaire_start", 0)
    if users < 50:
        actions.append("🚦 Traffic is still too low for reliable CRO conclusions. Push distribution, email clicks, social, and indexing.")
    elif starts == 0:
        actions.append("🔎 Funnel leak: users are landing but nobody is starting the assessment. Test stronger hero/CTA immediately.")
    elif _pct(starts, users) < 5:
        actions.append(f"🔎 Funnel leak: quiz start rate is only {_pct(starts, users)}%. CTA curiosity is likely weak.")

    if seq.get("sent_24h", 0) == 0 and any(seq.get("ready", {}).get(s, 0) for s in range(2, 8)):
        actions.append("📨 There are contacts ready for follow-up but no sends in the last 24h. Check scheduler or Gmail sender.")
    if seq.get("recent_queued", 0):
        actions.append(f"⏳ {seq['recent_queued']:,} recent queued rows exist. Make sure they clear within 6 hours.")
    if seq.get("failed", 0):
        actions.append(f"⚠️ {seq['failed']:,} failed sends need review.")
    if seq.get("throttled", 0):
        actions.append(f"🚦 Gmail throttling recorded. Keep daily ramp conservative.")
    if seq.get("open_rate", 0) < 15 and seq.get("sent_total", 0) > 100:
        actions.append(f"📬 Open rate is {seq.get('open_rate',0)}%. Subject lines or deliverability need work.")
    if seq.get("click_rate", 0) < 3 and seq.get("sent_total", 0) > 100:
        actions.append(f"🖱️ Click rate is {seq.get('click_rate',0)}%. CTA/offer angle needs stronger curiosity.")
    if seq.get("replied", 0):
        actions.append(f"🔥 {seq['replied']:,} replied contacts need manual follow-up.")

    if lead.get("match_rate", 0) < 10:
        actions.append(f"🧩 Match rate is only {lead.get('match_rate',0)}%. Enrichment is the lead-engine bottleneck.")
    if lead.get("email_coverage_rate", 0) < 5:
        actions.append(f"📇 Email coverage is only {lead.get('email_coverage_rate',0)}%. Scraping is finding liens faster than enrichment is finding contacts.")

    if clarity.get("avg_scroll_depth", clarity.get("scroll_depth", 100)) and float(clarity.get("avg_scroll_depth", clarity.get("scroll_depth", 100)) or 0) < 40:
        actions.append("📉 Average scroll depth is weak. Above-the-fold message may not be compelling enough.")

    actions.append(f"🎯 Need {max(0, GOAL_CONVERSIONS - conv.get('total',0)):,} more paid reviews to hit annual goal.")

    return sec("✅ Priority Action Items", "<ul style='margin-top:8px'>" + "".join(f"<li style='margin-bottom:7px'>{a}</li>" for a in actions) + "</ul>")




def build_booking_section(bk: dict) -> str:
    """Build the booking funnel + retargeting HTML section."""
    if not bk or bk.get("total", 0) == 0:
        return """<div style="background:#fff;border-radius:8px;padding:20px;margin-bottom:20px;border:1px solid #e2e8f0">
  <h2 style="color:#1a1a2e;font-size:16px;margin:0 0 8px 0">📅 Booking Funnel</h2>
  <p style="color:#94a3b8;font-size:13px">No bookings yet — bookings table created, waiting for first Calendly booking.</p>
</div>"""

    paid     = bk.get("paid", 0)
    pending  = bk.get("pending", 0)
    abandoned = bk.get("abandoned", 0)
    conv_rate = bk.get("conversion_rate", 0)
    paid_color = "#27ae60" if paid > 0 else "#e74c3c"
    conv_color = "#27ae60" if conv_rate >= 50 else "#e74c3c"
    r1_needs   = bk.get("needs_r1_today", 0)
    r2_needs   = bk.get("needs_r2_today", 0)
    retarget_flag = "⚠️ run retarget script NOW" if (r1_needs + r2_needs) > 0 else "✅ retargeting up to date"

    rows = ""
    rows += tr("New bookings today",        str(bk.get("pending_today", 0)), "")
    rows += tr("Payments today",            str(bk.get("paid_today", 0)), f"${bk.get('paid_today',0)*399:,} revenue")
    rows += tr("All-time paid",             str(paid), f"${paid*399:,} total revenue")
    rows += tr("Pending (booked, not paid)", str(pending), f"${pending*399:,} opportunity")
    rows += tr("Abandoned (5+ days)",       str(abandoned), "closed without paying")
    rows += tr("Booking → payment rate",    f"{conv_rate}%", "50%+ is healthy")
    rows += tr("Need retarget email 1 today", str(r1_needs), retarget_flag if r1_needs > 0 else "✅")
    rows += tr("Need retarget email 2 today", str(r2_needs), "")
    rows += tr("Retarget emails sent (1/2/3)", f"{bk.get('r1_sent',0)} / {bk.get('r2_sent',0)} / {bk.get('r3_sent',0)}", "")
    rows += tr("Retarget conversions (1/2)", f"{bk.get('r1_converted',0)} / {bk.get('r2_converted',0)}", "bookings that paid after retarget")
    rows += tr("Feedback responses",        str(bk.get("feedback_responses", 0)), "from abandoned booking survey")

    paid_rows_html = ""
    for r in bk.get("recent_paid", []):
        paid_rows_html += f"<tr><td>{r[1] or r[0]}</td><td>{r[2] or '—'}</td><td>${float(r[3] or 0):,.0f}</td><td>{str(r[4])[:10] if r[4] else '—'}</td><td>{r[5] or '—'}</td></tr>"

    recent_table = f"""<table width="100%" cellpadding="5" cellspacing="0" style="font-size:12px;margin-top:12px">
    <tr style="background:#f8f9fa;font-weight:bold"><td>Name</td><td>County</td><td>Lien $</td><td>Paid</td><td>Source</td></tr>
    {paid_rows_html}
    </table>""" if paid_rows_html else ""

    return f"""<div style="background:#fff;border-radius:8px;padding:20px;margin-bottom:20px;border:1px solid #e2e8f0">
  <h2 style="color:#1a1a2e;font-size:16px;margin:0 0 16px 0">📅 Booking Funnel & Retargeting</h2>
  <table width="100%" cellpadding="6" cellspacing="0">{rows}</table>
  {"<h3 style='font-size:13px;margin:16px 0 8px 0'>💰 Recent Paid Bookings (7 days)</h3>" + recent_table if recent_table else ""}
</div>"""

def build_html(lead: dict, states: list[dict], counties: list[dict], seq: dict, conv: dict, ga4: dict, clarity: dict, ux: dict, today: str, bk: dict | None = None, data_section: str = ""):
    subject = f"📊 TaxCase Review Optimization Intelligence — {today}"
    _pipeline_runs = _read_pipeline_today()
    # Surface which account did today's cold sends (logged by send_email_sequence).
    _email_run = _latest_run(_pipeline_runs, lambda t: t == "email_sends")
    _sender = (_email_run or {}).get("metrics", {}) if _email_run else None

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>{h(subject)}</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif;background:#f1f5f9;margin:0;padding:20px;color:#0f172a">
  <div style="max-width:1120px;margin:0 auto;background:#ffffff;border-radius:16px;padding:28px;border:1px solid #e2e8f0">
    <h1 style="margin:0;color:#0f1b2d;font-size:28px">TaxCase Review Optimization Intelligence</h1>
    <p style="margin:6px 0 18px;color:#64748b">{h(today)} · Generated {datetime.now().strftime('%I:%M %p')}</p>

    <table style="width:100%;border-collapse:collapse;margin:10px 0 20px"><tr>
      {card("Users", f"{ga4.get('users',0):,}", "GA4 24h")}
      {card("Quiz starts", f"{ga4.get('questionnaire_start',0):,}", f"{_pct(ga4.get('questionnaire_start',0), ga4.get('users',0))}% of users")}
      {card("Email sent 24h", f"{seq.get('sent_24h',0):,}", f"{seq.get('sent_7d',0):,} last 7d")}
      {card("Email-ready leads", f"{lead.get('email_ready',0):,}", f"{lead.get('email_coverage_rate',0)}% lien coverage")}
    </tr></table>

    {build_action_items(lead, seq, ga4, clarity, ux, conv)}
    {build_lead_database_section(lead, states, counties)}
    {build_collection_status_section(_pipeline_runs)}
    {build_weekly_calendar_section()}
    {build_content_automation_section(_pipeline_runs)}
    {build_revenue_section(conv)}
    {build_traffic_section(ga4, clarity, ux)}
    {build_booking_section(bk or {})}
    {build_email_sequence_section(seq, _sender)}
    {build_outreach_section(_pipeline_runs)}
    {data_section}

    <p style="margin-top:28px;color:#64748b;font-size:12px;border-top:1px solid #e2e8f0;padding-top:14px">
      TaxCase Review · LeadFlow Pipeline · {datetime.now().strftime('%Y-%m-%d %H:%M')} · Campaign: {h(CAMPAIGN_ID)}
    </p>
  </div>
</body>
</html>"""
    return subject, html


# ─────────────────────────────────────────────────────────────────────────────
# Email send
# ─────────────────────────────────────────────────────────────────────────────

def send_summary(subject: str, html: str, recipients: list[str]):
    if not SUMMARY_PASSWORD:
        raise RuntimeError("GMAIL_SUMMARY_PASSWORD or GMAIL_APP_PASSWORD is not set in .env")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SENDER_NAME} <{SUMMARY_SENDER}>"
    msg["To"] = ", ".join(recipients)

    plain = "TaxCase Review Optimization Intelligence summary. Open HTML email for full tables."
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
        server.login(SUMMARY_SENDER, SUMMARY_PASSWORD)
        server.sendmail(SUMMARY_SENDER, recipients, msg.as_string())


def main():
    parser = argparse.ArgumentParser(description="TaxCase Review Daily Summary v4.2")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--to", default=None)
    args = parser.parse_args()

    today = datetime.now().strftime("%B %d, %Y")
    print(f"\n[TaxCase Review Optimization Intelligence v4.2] {today}")
    print(f"  Sending from : {SUMMARY_SENDER}")
    print(f"  Campaign     : {CAMPAIGN_ID}")

    # Ensure the indexes the breakdown queries rely on exist (idempotent no-op).
    safe_query(lambda cur: ensure_email_indexes(cur), None)

    lead = safe_query(_get_lead_intelligence, {
        "liens_total": 0, "liens_24h": 0, "liens_7d": 0, "liens_30d": 0,
        "matched_total": 0, "matched_liens": 0, "email_ready": 0,
        "high_confidence": 0, "medium_confidence": 0,
        "match_rate": 0, "email_coverage_rate": 0, "high_confidence_rate": 0,
    })
    states = safe_query(_get_state_breakdown, [])
    counties = safe_query(_get_county_breakdown, [])
    seq = safe_query(_get_email_sequence_stats, {
        "total_contacts": 0, "waiting": 0,
        "steps": {i: 0 for i in range(1, 8)},
        "periods": {p: {i: {"sent": 0, "opens": 0, "clicks": 0, "replies": 0, "open_rate": 0, "click_rate": 0, "reply_rate": 0} for i in range(1, 8)} for p in PERIODS},
        "ready": {}, "status_counts": {}, "sent_24h": 0, "sent_7d": 0, "sent_30d": 0, "sent_total": 0,
        "opens": 0, "clicks": 0, "replied": 0, "unsubscribed": 0, "failed": 0, "throttled": 0,
        "spam_trap": 0, "stale_queued": 0, "recent_queued": 0, "open_rate": 0, "click_rate": 0,
        "reply_rate": 0, "variants": [],
    })
    conv = safe_query(_get_conversion_stats, {
        "total": 0, "revenue": 0.0, "today": 0, "revenue_today": 0.0,
        "week": 0, "revenue_week": 0.0, "month": 0, "revenue_month": 0.0,
    })
    bk = safe_query(_get_booking_stats, {
        "total":0,"pending":0,"paid":0,"abandoned":0,"canceled":0,"no_show":0,
        "pending_today":0,"paid_today":0,"r1_sent":0,"r2_sent":0,"r3_sent":0,
        "r1_converted":0,"r2_converted":0,"feedback_responses":0,
        "needs_r1_today":0,"needs_r2_today":0,"conversion_rate":0.0,"recent_paid":[],
    })

    ga4 = _fetch_ga4()
    clarity = _fetch_clarity()
    ux = _fetch_ux(clarity)

    data_section = safe_query(build_data_collection_section, "")

    subject, html = build_html(lead, states, counties, seq, conv, ga4, clarity, ux, today, bk, data_section)

    if args.dry_run:
        print("\n" + subject)
        print(html[:5000])
        out = BASE_DIR / "data" / "daily_summary_preview.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print(f"\n  Preview saved: {out}")
        return

    recipients = [r.strip() for r in args.to.split(",")] if args.to else RECIPIENTS

    # Pipeline log so "did the summary send today?" is answerable from
    # logs/pipeline/ (python pipeline_log.py --today) without opening Task
    # Scheduler. Same pattern as send_email_sequence / weekly_scrape.
    from pipeline_log import PipelineLogger
    logger = PipelineLogger("daily_summary")
    logger.start()
    logger.step_start("send_summary")
    try:
        send_summary(subject, html, recipients)
        logger.step_done("send_summary", ok=True,
                         detail=f"sent to {len(recipients)} recipient(s)")
        print(f"  ✅ Sent to: {', '.join(recipients)}")
        logger.finish({
            "recipients":  len(recipients),
            "to":          ",".join(recipients),
            "subject":     subject,
            "sent_24h":    seq.get("sent_24h", 0),
            "email_ready": lead.get("email_ready", 0),
            "liens_total": lead.get("liens_total", 0),
            "paid_reviews": conv.get("total", 0),
        })
    except Exception as e:
        logger.step_done("send_summary", ok=False, error=str(e))
        logger.finish({"error": str(e)})
        raise


if __name__ == "__main__":
    main()