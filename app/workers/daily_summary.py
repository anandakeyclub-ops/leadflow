"""
daily_summary.py (v6 — Lead Intelligence Dashboard)
=========================================================
TaxCase Review daily pipeline intelligence digest.

v5 upgrades over v4.2:
  DESIGN:
    - Dark branded header band with engine health scorecards (A/B/C/D/F per engine)
    - Color-coded KPI cards (green/amber/red based on thresholds)
    - Visual goal progress bar for revenue target
    - Severity-coded action items (CRITICAL / WARNING / OPPORTUNITY)
    - Consistent section cards — unified styling across all sections
    - Alternating row colors on all tables
    - Trend arrows on key metrics (↑↓→ vs prior 7d)

  CONTENT:
    - SMS engine section — sends, delivery rate, link clicks, opt-outs, cost per send
    - Engine health scorecard block at top — single A–F grade per engine for instant triage
    - "What broke today" (CRITICAL) separated from "action items" (WARNING/OPPORTUNITY)
    - Content engine section — blog, social, reel in one block
    - Week-over-week deltas on open rate, click rate, sends
    - Lead pipeline velocity — new email-ready leads per day (7d trend)
    - AI export block at bottom — structured plain text for uploading to Claude

  TECHNICAL:
    - All sections use unified sec2() card helper — no more inline div inconsistency
    - Booking revenue uses PRICE_PER constant (not hardcoded 399)
    - Smart subject line includes key alert ("⚠️ 0 sends" or "🔥 reply received")
    - --save-html flag saves HTML on live runs too
    - --date flag for historical regeneration
    - Plain text fallback includes key metrics inline

Usage:
  python -m app.workers.daily_summary --dry-run
  python -m app.workers.daily_summary
  python -m app.workers.daily_summary --save-html
  python -m app.workers.daily_summary --date 2026-06-20
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
from datetime import datetime, date, timedelta
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

SUMMARY_SENDER   = os.getenv("GMAIL_SUMMARY_SENDER",   os.getenv("GMAIL_SENDER", "romy@taxcasereview.org"))
SUMMARY_PASSWORD = os.getenv("GMAIL_SUMMARY_PASSWORD", os.getenv("GMAIL_APP_PASSWORD", "")).replace(" ", "")
SENDER_NAME      = os.getenv("GMAIL_SUMMARY_NAME", "TaxCase Review")
RECIPIENTS       = [r.strip() for r in os.getenv("DAILY_SUMMARY_TO", "info@taxcasereview.org,romy@taxcasereview.org").split(",") if r.strip()]

CAMPAIGN_ID      = os.getenv("CAMPAIGN_ID", "lien_outreach_2026")
GOAL_CONVERSIONS = int(os.getenv("GOAL_CONVERSIONS", "500"))
PRICE_PER        = int(os.getenv("PRICE_PER_CASE_REVIEW", "399"))
GOAL_REVENUE     = GOAL_CONVERSIONS * PRICE_PER
SITE_URL         = "https://taxcasereview.org"

# ── Brand colors ──────────────────────────────────────────────────────────────
C_NAVY   = "#0f1b2d"
C_BLUE   = "#1e40af"
C_GREEN  = "#15803d"
C_AMBER  = "#b45309"
C_RED    = "#b91c1c"
C_SLATE  = "#64748b"
C_BG     = "#f1f5f9"
C_WHITE  = "#ffffff"
C_BORDER = "#e2e8f0"

BG_GREEN = "#dcfce7"
BG_AMBER = "#fef9c3"
BG_RED   = "#fee2e2"
BG_BLUE  = "#dbeafe"


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
# Cache
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
        CACHE_FILE.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def ensure_email_indexes(cur) -> None:
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_email_sends_email    ON email_sends(to_email);
        CREATE INDEX IF NOT EXISTS idx_email_sends_sent_at  ON email_sends(sent_at);
        CREATE INDEX IF NOT EXISTS idx_email_sends_campaign ON email_sends(campaign_id);
    """)


# ─────────────────────────────────────────────────────────────────────────────
# HTML primitives — unified v5 style
# ─────────────────────────────────────────────────────────────────────────────

def h(value: Any) -> str:
    import html
    return html.escape(str(value if value is not None else ""))


def badge(text: str, bg: str = BG_BLUE, color: str = C_NAVY) -> str:
    return (f"<span style='display:inline-block;padding:3px 10px;border-radius:999px;"
            f"background:{bg};color:{color};font-size:11px;font-weight:700;"
            f"letter-spacing:.03em'>{h(text)}</span>")


def grade_badge(grade: str) -> str:
    """A–F engine health grade badge."""
    colors = {
        "A": (BG_GREEN, C_GREEN),
        "B": ("#d1fae5", "#065f46"),
        "C": (BG_AMBER, C_AMBER),
        "D": (BG_RED, C_RED),
        "F": ("#fecaca", "#7f1d1d"),
        "?": ("#f1f5f9", C_SLATE),
    }
    bg, fg = colors.get(grade, colors["?"])
    return (f"<span style='display:inline-flex;align-items:center;justify-content:center;"
            f"width:28px;height:28px;border-radius:6px;background:{bg};color:{fg};"
            f"font-size:14px;font-weight:900;font-family:monospace'>{h(grade)}</span>")


def trend_arrow(current: float, previous: float, higher_is_better: bool = True) -> str:
    """↑ green / ↓ red / → gray trend indicator."""
    if previous == 0:
        return "<span style='color:#94a3b8'>→</span>"
    delta = current - previous
    if abs(delta) < 0.5:
        return "<span style='color:#94a3b8'>→</span>"
    if (delta > 0) == higher_is_better:
        return f"<span style='color:{C_GREEN}'>↑</span>"
    return f"<span style='color:{C_RED}'>↓</span>"


def progress_bar(value: float, total: float, color: str = C_BLUE, height: int = 10) -> str:
    """Horizontal fill bar — value/total rendered as colored fill."""
    pct = min(100.0, round(value / max(total, 1) * 100, 1))
    bar_color = C_GREEN if pct >= 50 else (C_AMBER if pct >= 20 else C_RED)
    return (
        f"<div style='background:#e2e8f0;border-radius:999px;height:{height}px;margin:6px 0'>"
        f"<div style='width:{pct}%;background:{bar_color};height:{height}px;"
        f"border-radius:999px;min-width:2px'></div></div>"
        f"<div style='font-size:11px;color:{C_SLATE}'>{value:,.0f} / {total:,.0f} "
        f"({pct}%)</div>"
    )


def kpi_card(title: str, value: str, note: str = "",
             color: str = C_NAVY, bg: str = C_WHITE,
             border_color: str = C_BORDER) -> str:
    return (
        f"<td style='width:25%;padding:8px;vertical-align:top'>"
        f"<div style='border:2px solid {border_color};border-radius:12px;padding:16px;"
        f"background:{bg};height:100%'>"
        f"<div style='font-size:11px;color:{C_SLATE};text-transform:uppercase;"
        f"letter-spacing:.06em;font-weight:600'>{h(title)}</div>"
        f"<div style='font-size:26px;font-weight:900;color:{color};margin:8px 0 4px;"
        f"line-height:1.1'>{value}</div>"
        f"<div style='font-size:12px;color:{C_SLATE}'>{h(note)}</div>"
        f"</div></td>"
    )


def sec2(icon: str, title: str, body: str, note: str = "") -> str:
    """Unified section card — used for ALL sections in v5."""
    note_html = (f"<p style='margin:0 0 12px;color:{C_SLATE};font-size:12px;"
                 f"background:#f8fafc;border-radius:6px;padding:8px 12px;"
                 f"border-left:3px solid #cbd5e1'>{h(note)}</p>") if note else ""
    return (
        f"<div style='background:{C_WHITE};border:1px solid {C_BORDER};"
        f"border-radius:14px;padding:20px 24px;margin-bottom:16px'>"
        f"<h2 style='margin:0 0 14px;color:{C_NAVY};font-size:16px;font-weight:800;"
        f"display:flex;align-items:center;gap:8px'>{icon} {h(title)}</h2>"
        f"{note_html}{body}</div>"
    )


def tbl(headers: list[str], rows: list[list], center_cols: set[int] | None = None) -> str:
    """Unified table with alternating rows and configurable alignment."""
    center_cols = center_cols or set()
    head = "".join(
        f"<th style='padding:9px 12px;text-align:{'center' if i in center_cols else ('right' if i >= 2 else 'left')};"
        f"color:{C_SLATE};font-size:11px;text-transform:uppercase;letter-spacing:.05em;"
        f"white-space:nowrap'>{h(x)}</th>"
        for i, x in enumerate(headers)
    )
    body = ""
    for ri, row in enumerate(rows):
        bg = "#f8fafc" if ri % 2 == 0 else C_WHITE
        body += f"<tr style='background:{bg}'>"
        for ci, cell in enumerate(row):
            align = "center" if ci in center_cols else ("right" if ci >= 2 else "left")
            body += (f"<td style='padding:8px 12px;border-bottom:1px solid #f1f5f9;"
                     f"text-align:{align};font-size:13px'>{cell}</td>")
        body += "</tr>"
    return (
        f"<table style='width:100%;border-collapse:collapse;border-radius:8px;"
        f"overflow:hidden;border:1px solid {C_BORDER}'>"
        f"<tr style='background:#f1f5f9'>{head}</tr>{body}</table>"
    )


def metric_row(label: str, value: str, note: str = "", severity: str = "") -> str:
    """Single metric row for use inside a tbl-style layout without a full table."""
    sev_color = {
        "ok": C_GREEN, "warn": C_AMBER, "crit": C_RED, "": C_SLATE
    }.get(severity, C_SLATE)
    return (
        f"<tr>"
        f"<td style='padding:8px 12px;border-bottom:1px solid #f1f5f9;"
        f"font-size:13px;color:{C_NAVY}'>{h(label)}</td>"
        f"<td style='padding:8px 12px;border-bottom:1px solid #f1f5f9;"
        f"text-align:right;font-weight:700;font-size:13px;color:{sev_color}'>{value}</td>"
        f"<td style='padding:8px 12px;border-bottom:1px solid #f1f5f9;"
        f"color:{C_SLATE};font-size:12px'>{h(note)}</td>"
        f"</tr>"
    )


def metric_table(rows_html: str) -> str:
    return (
        f"<table style='width:100%;border-collapse:collapse;border-radius:8px;"
        f"overflow:hidden;border:1px solid {C_BORDER}'>"
        f"<tr style='background:#f1f5f9'>"
        f"<th style='padding:9px 12px;text-align:left;color:{C_SLATE};font-size:11px;"
        f"text-transform:uppercase;letter-spacing:.05em'>Metric</th>"
        f"<th style='padding:9px 12px;text-align:right;color:{C_SLATE};font-size:11px;"
        f"text-transform:uppercase;letter-spacing:.05em'>Value</th>"
        f"<th style='padding:9px 12px;text-align:left;color:{C_SLATE};font-size:11px;"
        f"text-transform:uppercase;letter-spacing:.05em'>Note</th>"
        f"</tr>{rows_html}</table>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# DB queries — preserved from v4.2 with SMS added
# ─────────────────────────────────────────────────────────────────────────────

def _get_conversion_stats(cur):
    try:
        cur.execute("SELECT COUNT(*), COALESCE(SUM(revenue), 0) FROM conversions")
        total = cur.fetchone()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(revenue), 0) FROM conversions WHERE converted_at >= NOW() - INTERVAL '24 hours'")
        today = cur.fetchone()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(revenue), 0) FROM conversions WHERE converted_at >= NOW() - INTERVAL '7 days'")
        week = cur.fetchone()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(revenue), 0) FROM conversions WHERE converted_at >= NOW() - INTERVAL '30 days'")
        month = cur.fetchone()
        # 7d prior for trend
        cur.execute("SELECT COUNT(*), COALESCE(SUM(revenue), 0) FROM conversions WHERE converted_at >= NOW() - INTERVAL '14 days' AND converted_at < NOW() - INTERVAL '7 days'")
        prev_week = cur.fetchone()
        return {
            "total": total[0] or 0, "revenue": float(total[1] or 0),
            "today": today[0] or 0, "revenue_today": float(today[1] or 0),
            "week": week[0] or 0, "revenue_week": float(week[1] or 0),
            "month": month[0] or 0, "revenue_month": float(month[1] or 0),
            "prev_week": prev_week[0] or 0, "revenue_prev_week": float(prev_week[1] or 0),
        }
    except Exception:
        return {"total":0,"revenue":0.0,"today":0,"revenue_today":0.0,
                "week":0,"revenue_week":0.0,"month":0,"revenue_month":0.0,
                "prev_week":0,"revenue_prev_week":0.0}


def _get_booking_stats(cur) -> dict:
    try:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status='pending')   AS pending,
                COUNT(*) FILTER (WHERE status='paid')      AS paid,
                COUNT(*) FILTER (WHERE status='abandoned') AS abandoned,
                COUNT(*) FILTER (WHERE status='canceled')  AS canceled,
                COUNT(*) FILTER (WHERE status='no_show')   AS no_show,
                COUNT(*)                                    AS total
            FROM bookings
        """)
        row = cur.fetchone()
        pending = row[0] or 0; paid = row[1] or 0; abandoned = row[2] or 0
        cur.execute("SELECT COUNT(*) FILTER (WHERE status='pending'), COUNT(*) FILTER (WHERE status='paid') FROM bookings WHERE calendly_booked_at >= NOW() - INTERVAL '24 hours'")
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


def _get_lead_intelligence(cur):
    cur.execute("""
        SELECT COUNT(*), COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),
               COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'),
               COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days')
        FROM normalized_liens
    """)
    lien_row = cur.fetchone()
    cur.execute("""
        SELECT COUNT(*), COUNT(DISTINCT lien_id),
               COUNT(DISTINCT email) FILTER (WHERE email IS NOT NULL AND email != '' AND email NOT LIKE '%%@example.com'),
               COUNT(*) FILTER (WHERE confidence='high' AND email IS NOT NULL AND email != '' AND email NOT LIKE '%%@example.com'),
               COUNT(*) FILTER (WHERE confidence='medium' AND email IS NOT NULL AND email != '' AND email NOT LIKE '%%@example.com')
        FROM lien_dbpr_contacts
    """)
    match_row = cur.fetchone()

    # 7d prior email-ready for velocity trend
    try:
        cur.execute("""
            SELECT COUNT(DISTINCT email) FROM lien_dbpr_contacts
            WHERE email IS NOT NULL AND email != '' AND email NOT LIKE '%%@example.com'
              AND created_at < NOW() - INTERVAL '7 days'
        """)
        email_ready_prior = (cur.fetchone() or [0])[0] or 0
    except Exception:
        email_ready_prior = 0

    liens_total   = lien_row[0] or 0
    email_ready   = match_row[2] or 0
    high          = match_row[3] or 0
    medium        = match_row[4] or 0
    matched_liens = match_row[1] or 0

    # New email-ready per day over 7d (velocity)
    velocity_7d = round((email_ready - email_ready_prior) / 7, 1) if email_ready > email_ready_prior else 0

    return {
        "liens_total": liens_total, "liens_24h": lien_row[1] or 0,
        "liens_7d": lien_row[2] or 0, "liens_30d": lien_row[3] or 0,
        "matched_total": match_row[0] or 0, "matched_liens": matched_liens,
        "email_ready": email_ready, "high_confidence": high, "medium_confidence": medium,
        "match_rate": _pct(matched_liens, liens_total),
        "email_coverage_rate": _pct(email_ready, liens_total),
        "high_confidence_rate": _pct(high, email_ready),
        "velocity_7d": velocity_7d,
    }


def _get_state_breakdown(cur):
    cached = _load_summary_cache().get("state_breakdown")
    try:
        cur.execute("SET LOCAL statement_timeout = '30s'")
        cur.execute("""
            WITH lien_counts AS (
                SELECT COALESCE(c.state,'Unknown') AS state, COUNT(*) AS liens,
                    COUNT(*) FILTER (WHERE nl.created_at >= NOW() - INTERVAL '24 hours') AS new_24h
                FROM normalized_liens nl JOIN counties c ON nl.county_id = c.id GROUP BY 1
            ),
            contact_counts AS (
                SELECT COALESCE(c.state,'Unknown') AS state,
                    COUNT(DISTINCT ldc.lien_id) AS matched_liens,
                    COUNT(DISTINCT ldc.email) FILTER (WHERE ldc.email IS NOT NULL AND ldc.email <> '' AND ldc.email NOT LIKE '%%@example.com') AS email_ready,
                    COUNT(DISTINCT ldc.email) FILTER (WHERE ldc.confidence='high' AND ldc.email IS NOT NULL AND ldc.email <> '' AND ldc.email NOT LIKE '%%@example.com') AS high_confidence
                FROM lien_dbpr_contacts ldc JOIN counties c ON ldc.county_id = c.id GROUP BY 1
            ),
            sent_counts AS (
                SELECT COALESCE(c.state,'Unknown') AS state,
                    COUNT(DISTINCT es.to_email) FILTER (WHERE es.sequence_step=1) AS email1_sent,
                    COUNT(DISTINCT es.to_email) FILTER (WHERE es.sequence_step=2) AS email2_sent,
                    COUNT(DISTINCT es.to_email) FILTER (WHERE es.sequence_step=3) AS email3_sent,
                    COUNT(DISTINCT es.to_email) FILTER (WHERE COALESCE(es.reply_received,FALSE)=TRUE) AS replied
                FROM email_sends es JOIN lien_dbpr_contacts ldc ON ldc.email = es.to_email
                JOIN counties c ON ldc.county_id = c.id
                WHERE es.campaign_id=%s AND es.status='sent' GROUP BY 1
            ),
            all_states AS (SELECT state FROM lien_counts UNION SELECT state FROM contact_counts)
            SELECT a.state, COALESCE(l.liens,0), COALESCE(cc.matched_liens,0),
                   COALESCE(cc.email_ready,0), COALESCE(cc.high_confidence,0),
                   COALESCE(sc.email1_sent,0), COALESCE(sc.email2_sent,0),
                   COALESCE(sc.email3_sent,0), COALESCE(sc.replied,0), COALESCE(l.new_24h,0)
            FROM all_states a
            LEFT JOIN lien_counts l   ON l.state  = a.state
            LEFT JOIN contact_counts cc ON cc.state = a.state
            LEFT JOIN sent_counts sc  ON sc.state = a.state
            WHERE COALESCE(l.liens,0) > 0 OR COALESCE(cc.email_ready,0) > 0
            ORDER BY COALESCE(l.liens,0) DESC LIMIT 10
        """, (CAMPAIGN_ID,))
        rows = []
        for r in cur.fetchall():
            rows.append({
                "state": r[0], "liens": r[1] or 0, "matched_liens": r[2] or 0,
                "email_ready": r[3] or 0, "high_confidence": r[4] or 0,
                "email1_sent": r[5] or 0, "email2_sent": r[6] or 0,
                "email3_sent": r[7] or 0, "replied": r[8] or 0, "new_24h": r[9] or 0,
                "match_rate": _pct(r[2] or 0, r[1] or 0),
                "email_coverage_rate": _pct(r[3] or 0, r[1] or 0),
            })
        _save_summary_cache("state_breakdown", rows)
        return rows
    except Exception as e:
        print(f"  ⚠ state breakdown slow/failed ({e}); using cached results")
        try: cur.connection.rollback()
        except Exception: pass
        return cached or []


def _get_county_breakdown(cur):
    cached = _load_summary_cache().get("county_breakdown")
    try:
        cur.execute("SET LOCAL statement_timeout = '30s'")
        cur.execute("""
            WITH lc AS (SELECT c.id AS cid, COUNT(*) AS liens FROM normalized_liens nl JOIN counties c ON nl.county_id = c.id GROUP BY c.id),
            cc AS (SELECT c.id AS cid, COUNT(DISTINCT ldc.lien_id) AS matched_liens,
                COUNT(DISTINCT ldc.email) FILTER (WHERE ldc.email IS NOT NULL AND ldc.email <> '' AND ldc.email NOT LIKE '%%@example.com') AS email_ready,
                COUNT(DISTINCT ldc.email) FILTER (WHERE ldc.confidence='high' AND ldc.email IS NOT NULL AND ldc.email <> '' AND ldc.email NOT LIKE '%%@example.com') AS high_confidence
                FROM lien_dbpr_contacts ldc JOIN counties c ON ldc.county_id = c.id GROUP BY c.id),
            sc AS (SELECT c.id AS cid,
                COUNT(DISTINCT es.to_email) FILTER (WHERE es.sequence_step=1) AS email1_sent,
                COUNT(DISTINCT es.to_email) FILTER (WHERE es.sequence_step=2) AS email2_sent,
                COUNT(DISTINCT es.to_email) FILTER (WHERE es.sequence_step=3) AS email3_sent,
                COUNT(DISTINCT es.to_email) FILTER (WHERE COALESCE(es.reply_received,FALSE)=TRUE) AS replied
                FROM email_sends es JOIN lien_dbpr_contacts ldc ON ldc.email = es.to_email
                JOIN counties c ON ldc.county_id = c.id WHERE es.campaign_id=%s AND es.status='sent' GROUP BY c.id),
            ids AS (SELECT cid FROM lc UNION SELECT cid FROM cc)
            SELECT co.state, co.county_name, COALESCE(lc.liens,0), COALESCE(cc.matched_liens,0),
                COALESCE(cc.email_ready,0), COALESCE(cc.high_confidence,0),
                COALESCE(sc.email1_sent,0), COALESCE(sc.email2_sent,0),
                COALESCE(sc.email3_sent,0), COALESCE(sc.replied,0)
            FROM ids i JOIN counties co ON co.id = i.cid
            LEFT JOIN lc ON lc.cid = i.cid LEFT JOIN cc ON cc.cid = i.cid LEFT JOIN sc ON sc.cid = i.cid
            WHERE COALESCE(lc.liens,0) > 0 OR COALESCE(cc.email_ready,0) > 0
            ORDER BY COALESCE(lc.liens,0) DESC LIMIT 35
        """, (CAMPAIGN_ID,))
        rows = []
        for r in cur.fetchall():
            rows.append({
                "state": r[0] or "", "county_name": r[1] or "",
                "liens": r[2] or 0, "matched_liens": r[3] or 0,
                "email_ready": r[4] or 0, "high_confidence": r[5] or 0,
                "email1_sent": r[6] or 0, "email2_sent": r[7] or 0,
                "email3_sent": r[8] or 0, "replied": r[9] or 0,
                "match_rate": _pct(r[3] or 0, r[2] or 0),
                "email_coverage_rate": _pct(r[4] or 0, r[2] or 0),
            })
        _save_summary_cache("county_breakdown", rows)
        return rows
    except Exception as e:
        print(f"  ⚠ county breakdown slow/failed ({e}); using cached results")
        try: cur.connection.rollback()
        except Exception: pass
        return cached or []


PERIODS = {"24h": "24 hours", "7d": "7 days", "30d": "30 days", "lifetime": None}
STEP_LABELS = {1:"Public record awareness", 2:"Common misunderstanding", 3:"Lien vs levy / options",
               4:"What happens if ignored", 5:"Enrolled Agent insight",
               6:"IRS collection timeline", 7:"Final follow-up"}
STEP_DELAYS = {2:3, 3:4, 4:5, 5:6, 6:7, 7:10}


def _period_filter(alias: str, period: str) -> str:
    interval = PERIODS[period]
    return f"AND {alias}.sent_at >= NOW() - INTERVAL '{interval}'" if interval else ""


def _get_email_sequence_stats(cur):
    total_contacts = _one(cur, """
        SELECT COUNT(DISTINCT email) FROM lien_dbpr_contacts
        WHERE email IS NOT NULL AND email != '' AND email NOT LIKE '%%@example.com'
    """)

    step_periods: dict = {}
    for period in PERIODS:
        step_periods[period] = {}
        date_filter = _period_filter("es", period)
        for step in range(1, 8):
            cur.execute(f"""
                SELECT COUNT(DISTINCT es.to_email), COUNT(DISTINCT eo.tracking_id),
                       COUNT(DISTINCT ec.tracking_id),
                       COUNT(DISTINCT es.to_email) FILTER (WHERE COALESCE(es.reply_received,FALSE)=TRUE)
                FROM email_sends es
                LEFT JOIN email_opens eo  ON eo.tracking_id = es.tracking_id
                LEFT JOIN email_clicks ec ON ec.tracking_id = es.tracking_id
                WHERE es.campaign_id=%s AND es.sequence_step=%s AND es.status='sent' {date_filter}
            """, (CAMPAIGN_ID, step))
            r = cur.fetchone()
            sent=r[0] or 0; opens=r[1] or 0; clicks=r[2] or 0; replies=r[3] or 0
            step_periods[period][step] = {
                "sent": sent, "opens": opens, "clicks": clicks, "replies": replies,
                "open_rate": _pct(opens, sent), "click_rate": _pct(clicks, sent),
                "reply_rate": _pct(replies, sent),
            }

    steps_lifetime = {step: step_periods["lifetime"][step]["sent"] for step in range(1, 8)}
    sent_total = sum(steps_lifetime.values())
    opens_total = sum(step_periods["lifetime"][s]["opens"] for s in range(1, 8))
    clicks_total = sum(step_periods["lifetime"][s]["clicks"] for s in range(1, 8))

    status_counts = {}
    cur.execute("SELECT status, COUNT(*) FROM email_sends WHERE campaign_id=%s GROUP BY status", (CAMPAIGN_ID,))
    for status, count in cur.fetchall():
        status_counts[status or "unknown"] = count or 0

    ready = {}
    for step in range(2, 8):
        prev = step - 1
        delay_days = STEP_DELAYS[step]
        ready[step] = _one(cur, """
            SELECT COUNT(DISTINCT es_prev.to_email) FROM email_sends es_prev
            WHERE es_prev.campaign_id=%s AND es_prev.sequence_step=%s AND es_prev.status='sent'
              AND es_prev.sent_at <= NOW() - INTERVAL %s
              AND NOT EXISTS (
                  SELECT 1 FROM email_sends es_next
                  WHERE es_next.campaign_id=%s AND es_next.sequence_step=%s
                    AND es_next.to_email=es_prev.to_email AND es_next.status='sent')
        """, (CAMPAIGN_ID, prev, f"{delay_days} days", CAMPAIGN_ID, step))

    replied = _one(cur, "SELECT COUNT(DISTINCT to_email) FROM email_sends WHERE campaign_id=%s AND COALESCE(reply_received,FALSE)=TRUE", (CAMPAIGN_ID,))
    unsubscribed = _one(cur, "SELECT COUNT(DISTINCT to_email) FROM email_sends WHERE campaign_id=%s AND COALESCE(unsubscribed,FALSE)=TRUE", (CAMPAIGN_ID,))

    # Prior 7d for trend comparison
    prev_open_rate = 0.0
    prev_click_rate = 0.0
    try:
        cur.execute("""
            SELECT COUNT(DISTINCT eo.tracking_id), COUNT(DISTINCT es.to_email)
            FROM email_sends es LEFT JOIN email_opens eo ON eo.tracking_id = es.tracking_id
            WHERE es.campaign_id=%s AND es.status='sent'
              AND es.sent_at >= NOW() - INTERVAL '14 days' AND es.sent_at < NOW() - INTERVAL '7 days'
        """, (CAMPAIGN_ID,))
        prev = cur.fetchone()
        if prev and prev[1]:
            prev_open_rate = _pct(prev[0] or 0, prev[1] or 1)
            prev_click_rate = _pct(0, prev[1] or 1)
    except Exception:
        pass

    # Subject variants
    variants = []
    try:
        cur.execute("""
            SELECT COALESCE(subject_variant,'legacy'), MIN(subject), COUNT(DISTINCT es.to_email),
                   COUNT(DISTINCT eo.tracking_id), COUNT(DISTINCT ec.tracking_id),
                   ROUND(COUNT(DISTINCT eo.tracking_id)::numeric / NULLIF(COUNT(DISTINCT es.to_email),0)*100,1),
                   ROUND(COUNT(DISTINCT ec.tracking_id)::numeric / NULLIF(COUNT(DISTINCT es.to_email),0)*100,1)
            FROM email_sends es
            LEFT JOIN email_opens eo  ON eo.tracking_id = es.tracking_id
            LEFT JOIN email_clicks ec ON ec.tracking_id = es.tracking_id
            WHERE es.campaign_id=%s AND es.status='sent' AND es.sent_at >= NOW() - INTERVAL '30 days'
            GROUP BY COALESCE(subject_variant,'legacy') HAVING COUNT(DISTINCT es.to_email) >= 5
            ORDER BY 6 DESC NULLS LAST, 7 DESC NULLS LAST, 3 DESC LIMIT 12
        """, (CAMPAIGN_ID,))
        variants = [{"variant":r[0],"subject":r[1] or "","sent":r[2] or 0,"opens":r[3] or 0,
                     "clicks":r[4] or 0,"open_rate":float(r[5] or 0),"click_rate":float(r[6] or 0)}
                    for r in cur.fetchall()]
    except Exception:
        variants = []

    # Top unsent high-score leads
    avg_score_today = None
    top_unsent: list = []
    try:
        cur.execute("""
            SELECT ROUND(AVG(score)::numeric,1) FROM (
                SELECT DISTINCT ON (LOWER(es.to_email)) ldc.lead_score AS score
                FROM email_sends es JOIN lien_dbpr_contacts ldc ON LOWER(ldc.email)=LOWER(es.to_email)
                WHERE es.campaign_id=%s AND es.status='sent' AND es.sent_at::date=CURRENT_DATE
                ORDER BY LOWER(es.to_email), ldc.lead_score DESC NULLS LAST
            ) x WHERE score IS NOT NULL
        """, (CAMPAIGN_ID,))
        r = cur.fetchone()
        avg_score_today = float(r[0]) if r and r[0] is not None else None
        cur.execute("""
            SELECT DISTINCT ON (LOWER(ldc.email)) ldc.lead_score, c.state, c.county_name, ldc.confidence
            FROM lien_dbpr_contacts ldc JOIN normalized_liens nl ON ldc.lien_id=nl.id
            JOIN counties c ON ldc.county_id=c.id
            WHERE ldc.email IS NOT NULL AND ldc.email != '' AND ldc.email NOT LIKE '%%@example.com'
              AND ldc.lead_score IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM email_sends es WHERE LOWER(es.to_email)=LOWER(ldc.email) AND es.campaign_id=%s)
            ORDER BY LOWER(ldc.email), ldc.lead_score DESC NULLS LAST
        """, (CAMPAIGN_ID,))
        scored_unsent = cur.fetchall()
        scored_unsent.sort(key=lambda x: (x[0] or 0), reverse=True)
        top_unsent = scored_unsent[:5]
    except Exception:
        pass

    return {
        "avg_score_sent_today": avg_score_today, "top_unsent": top_unsent,
        "total_contacts": total_contacts,
        "waiting": max(total_contacts - steps_lifetime.get(1, 0) - unsubscribed, 0),
        "steps": steps_lifetime, "periods": step_periods, "ready": ready,
        "status_counts": status_counts,
        "sent_24h": sum(step_periods["24h"][s]["sent"] for s in range(1,8)),
        "sent_7d":  sum(step_periods["7d"][s]["sent"]  for s in range(1,8)),
        "sent_30d": sum(step_periods["30d"][s]["sent"] for s in range(1,8)),
        "sent_total": sent_total, "opens": opens_total, "clicks": clicks_total,
        "replied": replied, "unsubscribed": unsubscribed,
        "failed": status_counts.get("failed", 0), "throttled": status_counts.get("throttled", 0),
        "spam_trap": status_counts.get("spam_trap", 0), "stale_queued": status_counts.get("stale_queued", 0),
        "recent_queued": _one(cur, "SELECT COUNT(*) FROM email_sends WHERE campaign_id=%s AND status='queued' AND sent_at > NOW() - INTERVAL '6 hours'", (CAMPAIGN_ID,)),
        "open_rate": _pct(opens_total, sent_total), "click_rate": _pct(clicks_total, sent_total),
        "reply_rate": _pct(replied, sent_total), "variants": variants,
        "prev_open_rate": prev_open_rate, "prev_click_rate": prev_click_rate,
    }


# ── NEW: SMS stats ─────────────────────────────────────────────────────────────

def _get_sms_stats(cur) -> dict:
    """Pull stats from sms_campaign_log. Graceful if table doesn't exist."""
    try:
        # Check table exists
        cur.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='sms_campaign_log')")
        if not cur.fetchone()[0]:
            return {"_no_table": True}

        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status='sent')      AS sent_total,
                COUNT(*) FILTER (WHERE status='delivered') AS delivered,
                COUNT(*) FILTER (WHERE status='failed')    AS failed,
                COUNT(*) FILTER (WHERE status='undelivered') AS undelivered,
                COUNT(*) FILTER (WHERE DATE(sent_at)=CURRENT_DATE) AS sent_today,
                COUNT(*) FILTER (WHERE sent_at >= NOW() - INTERVAL '7 days') AS sent_7d,
                COUNT(*) FILTER (WHERE sent_at >= NOW() - INTERVAL '30 days') AS sent_30d,
                MIN(sent_at), MAX(sent_at)
            FROM sms_campaign_log
        """)
        r = cur.fetchone()
        sent_total  = r[0] or 0
        delivered   = r[1] or 0
        failed      = r[2] or 0
        undelivered = r[3] or 0
        sent_today  = r[4] or 0
        sent_7d     = r[5] or 0
        sent_30d    = r[6] or 0

        # Link clicks — check if link_clicked column exists
        link_clicks = 0
        opt_outs    = 0
        states_sent: list = []
        try:
            cur.execute("SELECT COUNT(*) FROM sms_campaign_log WHERE link_clicked=TRUE")
            link_clicks = (cur.fetchone() or [0])[0] or 0
        except Exception:
            pass
        try:
            cur.execute("SELECT COUNT(*) FROM sms_campaign_log WHERE opt_out=TRUE OR status='opt_out'")
            opt_outs = (cur.fetchone() or [0])[0] or 0
        except Exception:
            pass
        try:
            cur.execute("""
                SELECT state, COUNT(*) AS cnt, COUNT(*) FILTER (WHERE status='delivered') AS del
                FROM sms_campaign_log WHERE sent_at >= NOW() - INTERVAL '30 days'
                GROUP BY state ORDER BY cnt DESC LIMIT 5
            """)
            states_sent = [(r[0] or "?", r[1] or 0, r[2] or 0) for r in cur.fetchall()]
        except Exception:
            pass

        # Try to get link_url and page destination
        destination_url = ""
        try:
            cur.execute("SELECT DISTINCT link_url FROM sms_campaign_log WHERE link_url IS NOT NULL LIMIT 1")
            row = cur.fetchone()
            if row:
                destination_url = row[0] or ""
        except Exception:
            pass

        # Cost estimate (Twilio SMS ~$0.0079/msg)
        cost_estimate = round(sent_total * 0.0079, 2)
        ctr = _pct(link_clicks, delivered) if delivered else 0

        return {
            "sent_total": sent_total, "delivered": delivered, "failed": failed,
            "undelivered": undelivered, "sent_today": sent_today,
            "sent_7d": sent_7d, "sent_30d": sent_30d,
            "link_clicks": link_clicks, "opt_outs": opt_outs,
            "delivery_rate": _pct(delivered, sent_total),
            "ctr": ctr,
            "opt_out_rate": _pct(opt_outs, sent_total),
            "cost_estimate": cost_estimate,
            "states_sent": states_sent,
            "destination_url": destination_url,
            "_no_table": False,
        }
    except Exception as e:
        return {"_no_table": False, "_error": str(e), "sent_total": 0}


# ─────────────────────────────────────────────────────────────────────────────
# GA4 / Clarity / UX — preserved from v4.2
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_ga4():
    if not get_daily_ga4_summary: return {}
    try:
        result = get_daily_ga4_summary()
        raw = result.data if hasattr(result, "data") else (result if isinstance(result, dict) else {})
        traffic = raw.get("traffic", {}) or {}
        funnel  = raw.get("funnel",  {}) or {}
        sources = raw.get("sources", []) or []
        top_src  = sources[0] if sources else {}
        top_pages = raw.get("top_pages", []) or []
        top_page  = top_pages[0] if top_pages else {}
        source_medium = top_src.get("sessionSourceMedium", "") or ""
        top_source, top_medium = (source_medium.split(" / ", 1) if " / " in source_medium else (source_medium, ""))
        users     = traffic.get("active_users", 0) or raw.get("users", 0) or 0
        sessions  = traffic.get("sessions", 0) or raw.get("sessions", 0) or 0
        page_views = traffic.get("page_views", 0) or raw.get("page_views", 0) or 0
        return {
            "users": users, "sessions": sessions, "page_views": page_views,
            "pages_per_session": round(page_views / max(sessions, 1), 2),
            "engagement_rate": round(traffic.get("engagement_rate", 0) or raw.get("engagement_rate", 0) or 0, 1),
            "top_source": top_source or "(unknown)", "top_medium": top_medium or "",
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
    if not fetch_clarity_metrics: return {}
    try:
        clarity = fetch_clarity_metrics() or {}
        if hasattr(clarity, "data"): clarity = clarity.data or {}
        return clarity if isinstance(clarity, dict) else {}
    except Exception as e:
        print(f"  ⚠ Clarity warning: {e}")
        return {}


def _fetch_ux(clarity: dict):
    if not analyze_ux: return {"score": 0, "primary_issue": "UX analyzer unavailable"}
    try:
        ux = analyze_ux(clarity) or {}
        return ux if isinstance(ux, dict) else {"score": 0, "primary_issue": "UX analyzer returned no data"}
    except Exception as e:
        return {"score": 0, "primary_issue": f"UX analyzer warning: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
# Engine health grading
# ─────────────────────────────────────────────────────────────────────────────

def _grade_email_engine(seq: dict) -> tuple[str, str]:
    """Returns (grade, reason)"""
    if seq.get("sent_24h", 0) == 0 and seq.get("sent_total", 0) > 0:
        return "D", "No sends in last 24h"
    if seq.get("failed", 0) > 10:
        return "D", f"{seq['failed']} failed sends"
    if seq.get("throttled", 0) > 0:
        return "C", "Gmail throttling detected"
    open_rate = seq.get("open_rate", 0)
    if open_rate >= 20: return "A", f"{open_rate}% open rate"
    if open_rate >= 14: return "B", f"{open_rate}% open rate"
    if open_rate >= 8:  return "C", f"{open_rate}% open rate"
    if open_rate > 0:   return "D", f"{open_rate}% open rate — needs work"
    return "?", "Insufficient data"


def _grade_lead_engine(lead: dict) -> tuple[str, str]:
    er = lead.get("email_coverage_rate", 0)
    mr = lead.get("match_rate", 0)
    if er >= 15 and mr >= 20: return "A", f"{er}% email coverage"
    if er >= 8  and mr >= 10: return "B", f"{er}% email coverage"
    if er >= 4:               return "C", f"{er}% email coverage"
    if er > 0:                return "D", f"Only {er}% of liens have emails"
    return "F", "No email-ready leads"


def _grade_traffic_engine(ga4: dict) -> tuple[str, str]:
    users  = ga4.get("users", 0)
    starts = ga4.get("questionnaire_start", 0)
    if users >= 200 and starts >= 10: return "A", f"{users} users, {starts} quiz starts"
    if users >= 100 and starts >= 3:  return "B", f"{users} users, {starts} quiz starts"
    if users >= 50:                   return "C", f"{users} users, low conversions"
    if users > 0:                     return "D", f"Only {users} users"
    return "F", "No GA4 data"


def _grade_revenue_engine(conv: dict) -> tuple[str, str]:
    total = conv.get("total", 0)
    week  = conv.get("week", 0)
    if total > 0 and week > 0: return "A", f"{total} paid, {week} this week"
    if total > 0:              return "B", f"{total} paid, none this week"
    return "F", "$0 revenue — no conversions yet"


def _grade_sms_engine(sms: dict) -> tuple[str, str]:
    if sms.get("_no_table") or sms.get("_error"):
        return "?", "SMS table not found"
    sent = sms.get("sent_total", 0)
    dr   = sms.get("delivery_rate", 0)
    ctr  = sms.get("ctr", 0)
    if sent == 0: return "?", "No SMS sent yet"
    if dr >= 90 and ctr >= 5: return "A", f"{dr}% delivery, {ctr}% CTR"
    if dr >= 85:              return "B", f"{dr}% delivery rate"
    if dr >= 70:              return "C", f"{dr}% delivery rate"
    return "D", f"Low delivery rate: {dr}%"


# ─────────────────────────────────────────────────────────────────────────────
# Action item intelligence
# ─────────────────────────────────────────────────────────────────────────────

def _build_action_items(lead, seq, ga4, clarity, ux, conv, sms) -> tuple[list, list, list]:
    """Returns (criticals, warnings, opportunities) — each is list of str."""
    criticals, warnings, opps = [], [], []

    # Revenue
    if conv.get("total", 0) == 0:
        criticals.append("$0 revenue — no paid case reviews yet. Every other metric is secondary to closing the first conversion.")

    # Email engine
    if seq.get("sent_24h", 0) == 0 and any(seq.get("ready", {}).get(s, 0) for s in range(2, 8)):
        criticals.append(f"Contacts are ready for follow-up but no sends fired in 24h — check Windows Task Scheduler and Gmail sender.")
    if seq.get("failed", 0) > 10:
        criticals.append(f"{seq['failed']:,} failed email sends — check Gmail authentication and Render API health.")
    if seq.get("replied", 0) > 0:
        criticals.append(f"🔥 {seq['replied']:,} reply received — needs manual follow-up NOW. This is a warm lead.")
    if seq.get("throttled", 0) > 0:
        warnings.append(f"Gmail throttling recorded ({seq['throttled']:,}). Keep daily ramp under 150/day.")
    if seq.get("spam_trap", 0) > 0:
        warnings.append(f"{seq['spam_trap']:,} spam trap hits — pause enrichment from that source.")
    if seq.get("open_rate", 0) < 12 and seq.get("sent_total", 0) > 200:
        warnings.append(f"Open rate {seq.get('open_rate',0)}% — below 12% threshold. Test subject line variants.")
    if seq.get("click_rate", 0) < 2 and seq.get("sent_total", 0) > 200:
        warnings.append(f"Click rate {seq.get('click_rate',0)}% — CTA or offer angle needs stronger curiosity hook.")
    if seq.get("stale_queued", 0) > 50:
        warnings.append(f"{seq['stale_queued']:,} stale queued rows — may be blocking fresh sends.")

    # Lead engine
    if lead.get("match_rate", 0) < 10:
        warnings.append(f"Match rate only {lead.get('match_rate',0)}% — enrichment is the lead bottleneck.")
    if lead.get("email_coverage_rate", 0) < 5:
        warnings.append(f"Email coverage only {lead.get('email_coverage_rate',0)}% — scraping outpacing enrichment.")

    # Traffic
    users  = ga4.get("users", 0)
    starts = ga4.get("questionnaire_start", 0)
    if users < 20:
        warnings.append(f"Only {users} users today — insufficient traffic for CRO conclusions. Push email clicks, social, indexing.")
    elif starts == 0 and users > 30:
        warnings.append("Users are landing but nobody starts the quiz — test hero/CTA. Quiz start rate should be 5%+.")
    elif starts > 0 and _pct(starts, users) < 3:
        warnings.append(f"Quiz start rate {_pct(starts, users)}% — below 3%. CTA positioning or copy needs work.")

    # UX
    scroll = float(clarity.get("avg_scroll_depth", clarity.get("scroll_depth", 100)) or 100)
    if scroll < 35:
        warnings.append(f"Average scroll depth {scroll}% — above-the-fold message isn't holding attention.")
    if clarity.get("rage_clicks", 0) > 20:
        warnings.append(f"{clarity.get('rage_clicks',0)} rage clicks — UI element is broken or misleading.")

    # Booking
    # (bk passed in separately, shown in its own section)

    # SMS
    if not sms.get("_no_table") and sms.get("sent_total", 0) > 0:
        if sms.get("delivery_rate", 100) < 80:
            warnings.append(f"SMS delivery rate {sms.get('delivery_rate',0)}% — check Twilio number health.")
        if sms.get("opt_out_rate", 0) > 5:
            warnings.append(f"SMS opt-out rate {sms.get('opt_out_rate',0)}% — message angle may feel spammy.")

    # Opportunities
    top_unsent = seq.get("top_unsent", [])
    if top_unsent:
        top = top_unsent[0]
        opps.append(f"Top unsent lead: score {top[0] or '?'} — {top[1] or '?'}, {top[2] or '?'} county ({top[3] or '?'} confidence).")
    total_ready = sum(seq.get("ready", {}).get(s, 0) for s in range(2, 8))
    if total_ready > 0:
        opps.append(f"{total_ready:,} contacts are sequence-ready across steps 2–7. Run the scheduler to clear the queue.")
    if conv.get("total", 0) > 0 and conv.get("week", 0) == 0:
        opps.append("Past conversions exist but none this week — retarget your warm booking list.")
    pct_goal = _pct(conv.get("total", 0), GOAL_CONVERSIONS)
    opps.append(f"Goal: {max(0, GOAL_CONVERSIONS - conv.get('total',0)):,} more paid reviews to hit {GOAL_CONVERSIONS:,} × ${PRICE_PER:,} target ({pct_goal}% complete).")

    return criticals, warnings, opps


# ─────────────────────────────────────────────────────────────────────────────
# Section builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_engine_scorecards(seq, lead, ga4, conv, sms) -> str:
    grades = [
        ("📧 Email Engine",  *_grade_email_engine(seq)),
        ("🏦 Lead Engine",   *_grade_lead_engine(lead)),
        ("👀 Traffic",       *_grade_traffic_engine(ga4)),
        ("💰 Revenue",       *_grade_revenue_engine(conv)),
        ("📱 SMS Engine",    *_grade_sms_engine(sms)),
    ]
    cards_html = ""
    for engine, grade, reason in grades:
        bg = {
            "A": BG_GREEN, "B": "#d1fae5", "C": BG_AMBER,
            "D": BG_RED, "F": "#fecaca", "?": "#f1f5f9"
        }.get(grade, "#f1f5f9")
        border = {
            "A": C_GREEN, "B": C_GREEN, "C": C_AMBER,
            "D": C_RED, "F": C_RED, "?": C_BORDER
        }.get(grade, C_BORDER)
        cards_html += (
            f"<td style='width:20%;padding:6px;vertical-align:top'>"
            f"<div style='border:2px solid {border};border-radius:10px;padding:14px;"
            f"background:{bg};text-align:center'>"
            f"<div style='font-size:11px;color:{C_SLATE};font-weight:600;"
            f"text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px'>{h(engine)}</div>"
            f"{grade_badge(grade)}"
            f"<div style='font-size:11px;color:{C_NAVY};margin-top:8px;"
            f"line-height:1.4'>{h(reason)}</div>"
            f"</div></td>"
        )
    return (
        f"<table style='width:100%;border-collapse:collapse;margin-bottom:16px'>"
        f"<tr>{cards_html}</tr></table>"
    )


def build_action_section(lead, seq, ga4, clarity, ux, conv, sms) -> str:
    criticals, warnings, opps = _build_action_items(lead, seq, ga4, clarity, ux, conv, sms)

    def item_list(items: list[str], color: str, bg: str, icon: str) -> str:
        if not items: return ""
        li = "".join(
            f"<li style='margin-bottom:8px;padding:8px 12px;background:{bg};"
            f"border-radius:6px;border-left:3px solid {color};"
            f"font-size:13px;color:{C_NAVY}'>{icon} {h(a)}</li>"
            for a in items
        )
        return f"<ul style='list-style:none;margin:0 0 12px;padding:0'>{li}</ul>"

    body = ""
    if criticals:
        body += f"<div style='font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:{C_RED};margin-bottom:6px'>🔴 Critical — act now</div>"
        body += item_list(criticals, C_RED, BG_RED, "⚠️")
    if warnings:
        body += f"<div style='font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:{C_AMBER};margin-bottom:6px'>🟡 Warnings</div>"
        body += item_list(warnings, C_AMBER, BG_AMBER, "⚡")
    if opps:
        body += f"<div style='font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:{C_BLUE};margin-bottom:6px'>🔵 Opportunities</div>"
        body += item_list(opps, C_BLUE, BG_BLUE, "→")

    return sec2("🎯", "Priority Intelligence", body,
                "Critical = fix before anything else. Warning = monitor. Opportunity = next growth lever.")


def build_revenue_section(conv: dict) -> str:
    pct_goal = _pct(conv.get("total", 0), GOAL_CONVERSIONS)
    rev_pct  = _pct(conv.get("revenue", 0), GOAL_REVENUE)
    week_arrow = trend_arrow(conv.get("week", 0), conv.get("prev_week", 0))

    rows = (
        metric_row("Paid case reviews", f"{conv.get('total',0):,}", f"+{conv.get('today',0)} today · +{conv.get('week',0)} 7d {week_arrow} · +{conv.get('month',0)} 30d", severity="ok" if conv.get("total",0)>0 else "crit")
        + metric_row("Revenue", _money(conv.get("revenue",0)), f"{_money(conv.get('revenue_today',0))} today · {_money(conv.get('revenue_week',0))} 7d · {_money(conv.get('revenue_month',0))} 30d")
        + metric_row("Goal progress", f"{pct_goal}%", f"{GOAL_CONVERSIONS:,} reviews × ${PRICE_PER:,} = {_money(GOAL_REVENUE)} target")
        + metric_row("Remaining", f"{max(0, GOAL_CONVERSIONS-conv.get('total',0)):,} reviews", _money(max(0, GOAL_REVENUE-conv.get('revenue',0))) + " remaining")
    )
    bar = progress_bar(conv.get("total", 0), GOAL_CONVERSIONS, height=12)
    return sec2("💰", "Revenue", metric_table(rows) + f"<div style='margin-top:12px'>{bar}</div>")


def build_sms_section(sms: dict) -> str:
    if sms.get("_no_table"):
        body = (f"<p style='color:{C_SLATE};font-size:13px;padding:12px;background:#f8fafc;"
                f"border-radius:8px'>SMS campaign log table not found. Once "
                f"<code>twilio_sms_campaign.py</code> runs, metrics will appear here.</p>")
        return sec2("📱", "SMS Engine", body)

    if sms.get("_error"):
        body = f"<p style='color:{C_RED};font-size:13px'>Error reading SMS stats: {h(sms['_error'])}</p>"
        return sec2("📱", "SMS Engine", body)

    dest = sms.get("destination_url", "")
    dest_note = f"Links to: {h(dest)}" if dest else "Link destination: not recorded in log"
    dr_sev = "ok" if sms.get("delivery_rate", 0) >= 85 else ("warn" if sms.get("delivery_rate", 0) >= 70 else "crit")
    rows = (
        metric_row("Sent today / 7d / 30d / lifetime",
                   f"{sms.get('sent_today',0):,} / {sms.get('sent_7d',0):,} / {sms.get('sent_30d',0):,} / {sms.get('sent_total',0):,}", "Twilio sends")
        + metric_row("Delivery rate", f"{sms.get('delivery_rate',0)}%",
                     f"{sms.get('delivered',0):,} delivered · {sms.get('failed',0)+sms.get('undelivered',0):,} failed", severity=dr_sev)
        + metric_row("Link click-through rate", f"{sms.get('ctr',0)}%",
                     f"{sms.get('link_clicks',0):,} clicks on {sms.get('delivered',0):,} delivered",
                     severity="ok" if sms.get("ctr",0) >= 3 else "warn")
        + metric_row("Opt-outs", f"{sms.get('opt_outs',0):,}",
                     f"{sms.get('opt_out_rate',0)}% opt-out rate",
                     severity="ok" if sms.get("opt_out_rate",0) < 3 else "warn")
        + metric_row("Estimated cost (Twilio)", f"${sms.get('cost_estimate',0):.2f}",
                     "~$0.0079/msg estimate")
    )

    states_html = ""
    if sms.get("states_sent"):
        state_rows = [[h(s[0]), f"{s[1]:,}", f"{s[2]:,}", f"{_pct(s[2],s[1])}%"]
                      for s in sms["states_sent"]]
        states_html = (f"<div style='margin-top:12px'>"
                       f"{tbl(['State','Sent (30d)','Delivered','Delivery %'], state_rows)}</div>")

    return sec2("📱", "SMS Engine", metric_table(rows) + states_html, dest_note)


def build_email_section(seq: dict, sender: dict | None = None) -> str:
    open_arrow  = trend_arrow(seq.get("open_rate",0), seq.get("prev_open_rate",0))
    click_arrow = trend_arrow(seq.get("click_rate",0), seq.get("prev_click_rate",0))

    # Summary metrics
    rows = ""
    if sender:
        kind  = sender.get("sender_kind", "")
        label = ("⚠️ Gmail cold (risky)" if kind == "gmail_cold" else "Workspace (legacy)" if kind == "workspace_legacy" else "—")
        acct  = sender.get("sender_account") or sender.get("sender_login") or "—"
        rows += metric_row("Cold sending account", h(acct), f"{label} · reply-to: {sender.get('reply_to','—')}")
    rows += metric_row("Email-ready contacts", f"{seq.get('total_contacts',0):,}", f"{seq.get('waiting',0):,} not yet contacted")
    rows += metric_row("Sends 24h / 7d / 30d / lifetime", f"{seq.get('sent_24h',0):,} / {seq.get('sent_7d',0):,} / {seq.get('sent_30d',0):,} / {seq.get('sent_total',0):,}", "all sequence steps", severity="ok" if seq.get("sent_24h",0) > 0 else "warn")
    rows += metric_row("Open rate (lifetime)", f"{seq.get('open_rate',0)}% {open_arrow}", "↑↓ vs prior 7d", severity="ok" if seq.get("open_rate",0) >= 14 else "warn")
    rows += metric_row("Click rate (lifetime)", f"{seq.get('click_rate',0)}% {click_arrow}", "↑↓ vs prior 7d", severity="ok" if seq.get("click_rate",0) >= 2 else "warn")
    rows += metric_row("Reply rate", f"{seq.get('reply_rate',0)}%", f"{seq.get('replied',0):,} total replies — each is a warm lead", severity="ok" if seq.get("replied",0) > 0 else "")
    rows += metric_row("Unsubscribed", f"{seq.get('unsubscribed',0):,}", "monitor trend")
    rows += metric_row("Failed / throttled / stale queued", f"{seq.get('failed',0):,} / {seq.get('throttled',0):,} / {seq.get('stale_queued',0):,}", "health signals", severity="warn" if seq.get("failed",0) > 5 else "")
    avg = seq.get("avg_score_sent_today")
    rows += metric_row("Avg lead score (sent today)", f"{avg}" if avg is not None else "—", "0–100; >80 is high-priority tier")

    # Sequence step table
    step_rows = []
    for step in range(1, 8):
        p24  = seq["periods"]["24h"][step]
        life = seq["periods"]["lifetime"][step]
        rdy  = seq.get("ready", {}).get(step, "new pool") if step > 1 else "new pool"
        ready_str = f"{rdy:,}" if isinstance(rdy, int) else rdy
        open_sev  = "✅" if life["open_rate"] >= 14 else ("⚠️" if life["open_rate"] >= 8 else "❌")
        step_rows.append([
            f"<b>Step {step}</b>",
            h(STEP_LABELS.get(step, "")),
            f"{p24['sent']:,}",
            f"{seq['periods']['7d'][step]['sent']:,}",
            f"{life['sent']:,}",
            f"{open_sev} {life['open_rate']}%",
            f"{life['click_rate']}%",
            f"{life['reply_rate']}%",
            ready_str,
        ])

    # Top unsent
    unsent_rows = [[f"<b>{s or '—'}</b>", st or "?", h((co or "?")[:22]), cf or "?"]
                   for s, st, co, cf in seq.get("top_unsent", [])]
    if not unsent_rows:
        unsent_rows = [["—", "—", "No scored unsent contacts", "—"]]

    # Subject variants
    var_rows = []
    for v in seq.get("variants", [])[:8]:
        top = badge("⭐ TOP", BG_GREEN, C_GREEN) if v.get("open_rate",0) == max((x.get("open_rate",0) for x in seq.get("variants",[])), default=0) else ""
        var_rows.append([
            h(v["variant"]) + (" " + top if top else ""),
            h(v.get("subject",""))[:70],
            f"{v.get('sent',0):,}",
            f"{v.get('open_rate',0)}%",
            f"{v.get('click_rate',0)}%",
        ])
    if not var_rows:
        var_rows = [["—", "No subject variant data yet", "—", "—", "—"]]

    body = (
        metric_table(rows)
        + f"<h3 style='color:{C_NAVY};font-size:13px;font-weight:700;margin:18px 0 8px'>Sends by Sequence Step</h3>"
        + tbl(["Step","Label","24h","7d","Lifetime","Open%","Click%","Reply%","Ready"], step_rows)
        + f"<h3 style='color:{C_NAVY};font-size:13px;font-weight:700;margin:18px 0 8px'>Top Unsent High-Score Leads</h3>"
        + tbl(["Score","State","County","Confidence"], unsent_rows)
        + f"<h3 style='color:{C_NAVY};font-size:13px;font-weight:700;margin:18px 0 8px'>Subject Line Performance (30d)</h3>"
        + tbl(["Variant","Subject Line","Sent","Open%","Click%"], var_rows)
    )
    return sec2("📧", "Email Engine", body,
                "Replies are highest priority — each is a warm lead. Watch open rate vs prior 7d trend.")


def build_lead_section(lead: dict, states: list, counties: list) -> str:
    velocity = lead.get("velocity_7d", 0)
    vel_color = C_GREEN if velocity > 5 else (C_AMBER if velocity > 0 else C_RED)

    rows = (
        metric_row("Liens total", f"{lead.get('liens_total',0):,}",
                   f"+{lead.get('liens_24h',0):,} today · +{lead.get('liens_7d',0):,} 7d · +{lead.get('liens_30d',0):,} 30d")
        + metric_row("Matched liens", f"{lead.get('matched_liens',0):,}", f"{lead.get('match_rate',0)}% match rate", severity="ok" if lead.get("match_rate",0)>=10 else "warn")
        + metric_row("Email-ready leads", f"{lead.get('email_ready',0):,}", f"{lead.get('email_coverage_rate',0)}% coverage rate", severity="ok" if lead.get("email_coverage_rate",0)>=5 else "warn")
        + metric_row("High confidence", f"{lead.get('high_confidence',0):,}", f"{lead.get('high_confidence_rate',0)}% of email-ready")
        + metric_row("Medium confidence", f"{lead.get('medium_confidence',0):,}", "review quality before scaling")
        + metric_row("Lead velocity (7d avg)", f"<span style='color:{vel_color}'>{velocity:+.1f}/day</span>", "new email-ready leads per day")
    )

    state_rows = [[
        h(s["state"]), f"{s['liens']:,}",
        f"<b style='color:{C_GREEN}'>+{s['new_24h']:,}</b>" if s.get("new_24h") else "—",
        f"{s['email_ready']:,}", f"{s['high_confidence']:,}",
        f"{s['match_rate']}%", f"{s['email_coverage_rate']}%",
        f"{s['email1_sent']:,}", f"{s['replied']:,}",
    ] for s in states]

    county_rows = [[
        h(c["state"]), h(c["county_name"]), f"{c['liens']:,}",
        f"{c['email_ready']:,}", f"{c['high_confidence']:,}",
        f"{c['match_rate']}%", f"{c['email_coverage_rate']}%",
        f"{c['email1_sent']:,}", f"{c['replied']:,}",
    ] for c in counties]

    body = (
        metric_table(rows)
        + f"<h3 style='color:{C_NAVY};font-size:13px;font-weight:700;margin:18px 0 8px'>State Breakdown</h3>"
        + tbl(["State","Liens","New 24h","Email Ready","High Conf","Match%","Coverage%","Email 1","Replies"], state_rows)
        + f"<h3 style='color:{C_NAVY};font-size:13px;font-weight:700;margin:18px 0 8px'>County Breakdown (Top 35)</h3>"
        + tbl(["State","County","Liens","Email Ready","High Conf","Match%","Coverage%","Email 1","Replies"], county_rows)
    )
    return sec2("🏦", "Lead Engine — Lien → Contact → Email Pipeline", body,
                "Match rate = enrichment quality. Coverage rate = how much of the lien universe has contactable emails.")


def build_traffic_section(ga4: dict, clarity: dict, ux: dict) -> str:
    users   = ga4.get("users", 0)
    sessions = ga4.get("sessions", 0)
    starts  = ga4.get("questionnaire_start", 0)
    start_pct = _pct(starts, users)

    funnel_rows = [
        [f"{users:,} users", f"{sessions:,} sessions", f"{ga4.get('page_views',0):,} views", f"{ga4.get('pages_per_session',0)} pages/session"],
    ]

    rows = (
        metric_row("Users / sessions / views", f"{users:,} / {sessions:,} / {ga4.get('page_views',0):,}", f"{ga4.get('pages_per_session',0)} pages/session")
        + metric_row("Engagement rate", f"{ga4.get('engagement_rate',0)}%", "GA4")
        + metric_row("Top traffic source", f"{h(ga4.get('top_source','—'))} / {h(ga4.get('top_medium','—'))}", "GA4")
        + metric_row("Top landing page", h(ga4.get("top_landing_page","—")), f"{ga4.get('top_landing_page_views',0):,} views")
        + metric_row("Quiz starts", f"{starts:,}", f"{start_pct}% of users", severity="ok" if start_pct>=5 else ("warn" if start_pct>0 else "crit"))
        + metric_row("Quiz completed", f"{ga4.get('questionnaire_complete',0):,}", f"{_pct(ga4.get('questionnaire_complete',0), max(starts,1))}% completion")
        + metric_row("Calendly bookings", f"{ga4.get('calendly_booking',0):,}", "via GA4 event")
        + metric_row("Stripe checkout / payments", f"{ga4.get('stripe_checkout_started',0):,} / {ga4.get('stripe_payment_success',0):,}", "checkout → payment funnel")
    )

    clarity_rows = (
        metric_row("Clarity sessions", f"{clarity.get('sessions', clarity.get('total_sessions',0)):,}", f"{clarity.get('bot_sessions',0):,} bot/test filtered")
        + metric_row("Avg scroll depth", f"{clarity.get('avg_scroll_depth', clarity.get('scroll_depth',0))}%", "Target: 45%+", severity="ok" if float(clarity.get("avg_scroll_depth",clarity.get("scroll_depth",100)) or 100) >= 45 else "warn")
        + metric_row("Rage / dead / quickback clicks", f"{clarity.get('rage_clicks',0):,} / {clarity.get('dead_clicks',0):,} / {clarity.get('quick_backs',0):,}", "UX friction signals", severity="warn" if clarity.get("rage_clicks",0) > 10 else "")
        + metric_row("Script errors", f"{clarity.get('script_errors',0):,}", "technical friction")
        + metric_row("UX health score", f"{ux.get('score',0)}/100", ux.get("primary_issue","—"))
    )

    return sec2("👀", "Website Traffic + Funnel",
                metric_table(rows) + f"<h3 style='color:{C_NAVY};font-size:13px;font-weight:700;margin:18px 0 8px'>Clarity UX Intelligence</h3>" + metric_table(clarity_rows),
                "Traffic low → fix distribution. Traffic decent but no quiz starts → fix hero/CTA. Starts but no bookings → fix offer.")


def build_booking_section(bk: dict) -> str:
    if not bk or bk.get("total", 0) == 0:
        body = (f"<p style='color:{C_SLATE};font-size:13px;padding:12px;background:#f8fafc;"
                f"border-radius:8px'>No bookings yet — Calendly webhook is active, "
                f"waiting for first booking.</p>")
        return sec2("📅", "Booking Funnel", body)

    paid      = bk.get("paid", 0)
    pending   = bk.get("pending", 0)
    abandoned = bk.get("abandoned", 0)
    conv_rate = bk.get("conversion_rate", 0)
    r1_needs  = bk.get("needs_r1_today", 0)
    r2_needs  = bk.get("needs_r2_today", 0)

    rows = (
        metric_row("New bookings today", f"{bk.get('pending_today',0):,}", "")
        + metric_row("Payments today", f"{bk.get('paid_today',0):,}", f"{_money(bk.get('paid_today',0) * PRICE_PER)} revenue")
        + metric_row("All-time paid", f"{paid:,}", f"{_money(paid * PRICE_PER)} total revenue")
        + metric_row("Pending (booked, not paid)", f"{pending:,}", f"{_money(pending * PRICE_PER)} pipeline opportunity")
        + metric_row("Abandoned (5+ days)", f"{abandoned:,}", "no payment, no retarget response")
        + metric_row("Booking → payment rate", f"{conv_rate}%", "50%+ is healthy", severity="ok" if conv_rate>=50 else "warn")
        + metric_row("Need retarget 1 today", f"{r1_needs:,}", "⚠️ run retarget script NOW" if r1_needs > 0 else "✅ up to date", severity="warn" if r1_needs > 0 else "ok")
        + metric_row("Need retarget 2 today", f"{r2_needs:,}", "")
        + metric_row("Retarget sent (1/2/3)", f"{bk.get('r1_sent',0)} / {bk.get('r2_sent',0)} / {bk.get('r3_sent',0)}", "")
        + metric_row("Retarget conversions (1/2)", f"{bk.get('r1_converted',0)} / {bk.get('r2_converted',0)}", "paid after retarget")
        + metric_row("Feedback responses", f"{bk.get('feedback_responses',0):,}", "from abandoned booking survey")
    )

    recent_rows = []
    for r in bk.get("recent_paid", []):
        recent_rows.append([r[1] or r[0], r[2] or "—", _money(float(r[3] or 0)), str(r[4])[:10] if r[4] else "—", r[5] or "—"])

    body = metric_table(rows)
    if recent_rows:
        body += (f"<h3 style='color:{C_NAVY};font-size:13px;font-weight:700;margin:18px 0 8px'>"
                 f"Recent Paid (7 days)</h3>"
                 + tbl(["Name","County","Lien $","Paid","Source"], recent_rows))
    return sec2("📅", "Booking Funnel + Retargeting", body)


def build_content_section(runs: list[dict]) -> str:
    """Unified content engine: blog + social + reel in one block."""
    from datetime import date as _date

    def _blog_today():
        f = BASE_DIR / "data" / "blog_publish_history.json"
        if not f.exists(): return False, ""
        try:
            hist = json.loads(f.read_text())
            today = _date.today().isoformat()
            slugs = [s for s, d in hist.items() if d == today]
            return bool(slugs), slugs[0] if slugs else ""
        except Exception:
            return False, ""

    def _latest(predicate):
        matches = [r for r in runs if predicate(r.get("run_type",""))]
        return max(matches, key=lambda r: r.get("started",""), default=None)

    def _chip(ok: bool, label_ok="✅ ran", label_fail="❌ missing"):
        return badge(label_ok, BG_GREEN, C_GREEN) if ok else badge(label_fail, BG_RED, C_RED)

    blog_ok, blog_slug = _blog_today()
    social = _latest(lambda t: t == "social_post")
    reel   = _latest(lambda t: t.startswith("reel_"))

    rows = [[
        "Blog post", _chip(blog_ok),
        (h(blog_slug) + f" <a href='{SITE_URL}/blog/md/{h(blog_slug)}' style='font-size:11px;color:{C_BLUE}'>→ view</a>"
         if blog_ok else "No post published today")
    ]]

    if social:
        m  = social.get("metrics", {})
        ok = social.get("status") == "ok" and bool(m.get("sent"))
        if social.get("status") == "quality_rejected":
            rows.append(["Social post",
                          badge(f"⚠️ quality rejected ({m.get('quality','?')}/100)", BG_AMBER, C_AMBER),
                          f"{m.get('post_type','?')} scored below threshold"])
        else:
            detail = (f"{m.get('platform','?')} · {m.get('post_type','?')} · score {m.get('quality','?')}/100"
                      if ok else f"generated but not sent ({m.get('post_type','?')})")
            rows.append(["Social post", _chip(ok), detail])
    else:
        rows.append(["Social post", _chip(False), "no run logged today"])

    if reel:
        m  = reel.get("metrics", {})
        ok = reel.get("status") == "ok" and bool(m.get("posted")) and not m.get("dry_run")
        rows.append(["Reel", _chip(ok), f"{m.get('engine','?')} · {m.get('reel_type','?')} · score {m.get('quality_score','?')} · {'posted' if ok else 'not posted'}"])
    else:
        rows.append(["Reel", _chip(False), "no run logged today"])

    return sec2("🎬", "Content Engine", tbl(["Content","Status","Detail"], rows, center_cols={1}),
                "All three content engines should fire daily. Red = no log entry = Task Scheduler missed or failed.")


def build_pipeline_calendar_section(runs: list[dict]) -> str:
    """Weekly automation calendar — preserved from v4.2."""
    try:
        from datetime import date as _date, timedelta
        from scripts.schedule_config import SCHEDULE, is_scheduled_on
    except Exception as e:
        return sec2("🗓️", "Weekly Automation Calendar",
                    f"<p style='color:{C_SLATE};font-size:13px'>schedule_config unavailable: {h(str(e))}</p>")

    today  = date.today()
    monday = today - timedelta(days=today.weekday())
    days   = [monday + timedelta(days=i) for i in range(7)]
    WD     = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]

    runs_by_day = {d: _read_pipeline_today(d) for d in days}
    email_counts = safe_query(lambda cur: _email_sends_by_day(cur, monday), {})
    blog_dates   = _blog_publish_dates()

    def ran_ok(d, key): return any(r.get("status")=="ok" and r.get("run_type","")==key for r in runs_by_day[d])

    automations = [
        ("📧 Email sends",        "email_sends",       lambda d: email_counts.get(d.isoformat(),0)>0),
        ("📱 Social post",        "social_post",       lambda d: ran_ok(d,"social_post")),
        ("🎬 Reel (HeyGen)",      "reel_heygen",       lambda d: ran_ok(d,"reel_heygen")),
        ("🎬 Reel (Remotion)",    "reel_remotion",     lambda d: ran_ok(d,"reel_remotion")),
        ("📝 Blog post",          "blog_post",         lambda d: d.isoformat() in blog_dates),
        ("📥 Data FL",            "data_collection_fl",lambda d: ran_ok(d,"data_collection_fl")),
        ("📥 Data TX",            "data_collection_tx",lambda d: ran_ok(d,"data_collection_tx")),
        ("📥 Data GA/AZ/IL",      "data_collection_ga",lambda d: ran_ok(d,"data_collection_ga")),
        ("🎯 Lead scoring",       "lead_scoring",      lambda d: ran_ok(d,"lead_scoring")),
        ("✉️ Email enrichment",   "email_enrichment",  lambda d: ran_ok(d,"email_enrichment")),
        ("📊 Daily summary",      "daily_summary",     lambda d: ran_ok(d,"daily_summary")),
        ("📱 SMS campaign",       "sms_campaign",      lambda d: ran_ok(d,"sms_campaign")),
        ("🗂️ Collection pages",   "collection_pages",  lambda d: ran_ok(d,"collection_pages")),
        ("📰 Weekly report",      "weekly_intel",      lambda d: ran_ok(d,"weekly_intel")),
    ]

    def cell(scheduled, ok, d):
        if not scheduled: return f"<span style='color:#cbd5e1'>➖</span>"
        if ok:            return "✅"
        if d > today:     return f"<span style='color:#94a3b8'>⬜</span>"
        return f"<span style='color:{C_RED}'>❌</span>"

    rows = []
    for label, key, didrun in automations:
        cells = []
        for d in days:
            scheduled = is_scheduled_on(key, d)
            cells.append(cell(scheduled, scheduled and didrun(d), d))
        rows.append([label] + cells)

    # NOTE: tbl() HTML-escapes headers, so embedded markup renders as literal text.
    # Keep headers plain ("Mon 6/29") so they display correctly.
    headers = ["Automation"] + [f"{WD[d.weekday()]} {d.month}/{d.day}" for d in days]
    return sec2("🗓️", "Weekly Automation Calendar", tbl(headers, rows, center_cols=set(range(1,8))),
                "✅ ran · ❌ scheduled but missing · ⬜ upcoming · ➖ not scheduled today")


def build_data_collection_section(cur) -> str:
    try:
        cur.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='normalized_contacts')")
        if not cur.fetchone()[0]:
            return sec2("🛰️", "Data Engine — Multi-State Collection",
                        f"<p style='color:{C_SLATE};font-size:13px'>normalized_contacts not built yet — "
                        f"run <code>scripts/data_engine/run_daily.py</code> to populate.</p>")

        cur.execute("""
            SELECT state, COUNT(*) AS licenses,
                   COUNT(*) FILTER (WHERE has_lien_match) AS matched,
                   COUNT(*) FILTER (WHERE email IS NOT NULL AND email <> '') AS emails
            FROM normalized_contacts GROUP BY state
        """)
        contacts = {r[0]: {"licenses":r[1],"matched":r[2],"emails":r[3]} for r in cur.fetchall()}
        cur.execute("SELECT COALESCE(state,'?'), COUNT(*) FROM normalized_liens GROUP BY state")
        liens = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("""
            SELECT nc.state, COUNT(DISTINCT LOWER(nc.email)) FROM normalized_contacts nc
            JOIN lien_dbpr_contacts ldc ON LOWER(ldc.email)=LOWER(nc.email)
            WHERE nc.email IS NOT NULL AND nc.email <> '' GROUP BY nc.state
        """)
        in_pipe = {r[0]: r[1] for r in cur.fetchall()}

        states = sorted(s for s in set(list(contacts)+list(liens)) if s)
        rows = []
        for st in states:
            c = contacts.get(st, {})
            lic = c.get("licenses",0); matched = c.get("matched",0); emails = c.get("emails",0)
            rows.append([h(st), f"{liens.get(st,0):,}", f"{lic:,}", f"{matched:,}", f"{emails:,}", f"{in_pipe.get(st,0):,}", f"{_pct(matched,lic)}%"])
        if not rows:
            rows = [["—","0","0","0","0","0","0%"]]

        return sec2("🛰️", "Data Engine — Multi-State Collection",
                    tbl(["State","Liens","Licenses","Matched","Emails","In Pipeline","Match%"], rows),
                    "Liens scraped → license universe → lien↔license matches → enriched emails → pipeline.")
    except Exception as e:
        return sec2("🛰️", "Data Engine", f"<p style='color:{C_RED};font-size:13px'>Error: {h(str(e))}</p>")


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline log helpers — preserved from v4.2
# ─────────────────────────────────────────────────────────────────────────────

def _read_pipeline_today(d=None) -> list[dict]:
    from datetime import date as _date
    d = d or _date.today()
    f = BASE_DIR / "logs" / "pipeline" / f"{d.isoformat()}.jsonl"
    if not f.exists(): return []
    out = []
    for line in f.read_text(encoding="utf-8").strip().splitlines():
        try: out.append(json.loads(line))
        except Exception: pass
    return out


def _email_sends_by_day(cur, since) -> dict:
    cur.execute("SELECT DATE(sent_at), COUNT(*) FROM email_sends WHERE status='sent' AND sent_at >= %s GROUP BY 1", (since,))
    return {r[0].isoformat(): r[1] for r in cur.fetchall()}


def _blog_publish_dates() -> set:
    f = BASE_DIR / "data" / "blog_publish_history.json"
    if not f.exists(): return set()
    try: return set(json.loads(f.read_text()).values())
    except Exception: return set()


# ─────────────────────────────────────────────────────────────────────────────
# AI export block — structured plain text for uploading to Claude
# ─────────────────────────────────────────────────────────────────────────────

def build_ai_export_block(lead, seq, ga4, conv, sms, bk, today_str) -> str:
    """Structured plain-text digest optimized for AI analysis.
    Upload this section to Claude for engine optimization advice."""
    lines = [
        f"=== TAXCASE REVIEW ENGINE SNAPSHOT — {today_str} ===",
        "",
        "## REVENUE",
        f"Paid reviews: {conv.get('total',0)} | Revenue: {_money(conv.get('revenue',0))}",
        f"Goal: {GOAL_CONVERSIONS} × ${PRICE_PER} = {_money(GOAL_REVENUE)} | Progress: {_pct(conv.get('total',0), GOAL_CONVERSIONS)}%",
        f"This week: {conv.get('week',0)} reviews {_money(conv.get('revenue_week',0))} | This month: {conv.get('month',0)} {_money(conv.get('revenue_month',0))}",
        "",
        "## EMAIL ENGINE",
        f"Total email-ready contacts: {seq.get('total_contacts',0):,} | Not yet contacted: {seq.get('waiting',0):,}",
        f"Sends 24h/7d/30d/lifetime: {seq.get('sent_24h',0)}/{seq.get('sent_7d',0)}/{seq.get('sent_30d',0)}/{seq.get('sent_total',0)}",
        f"Open rate: {seq.get('open_rate',0)}% | Click rate: {seq.get('click_rate',0)}% | Reply rate: {seq.get('reply_rate',0)}%",
        f"Replied: {seq.get('replied',0)} | Unsubscribed: {seq.get('unsubscribed',0)} | Failed: {seq.get('failed',0)} | Throttled: {seq.get('throttled',0)}",
        f"Ready for follow-up: {sum(seq.get('ready',{}).get(s,0) for s in range(2,8))} across steps 2-7",
        "",
        "## LEAD DATABASE",
        f"Liens total: {lead.get('liens_total',0):,} | +24h: {lead.get('liens_24h',0):,} | +7d: {lead.get('liens_7d',0):,}",
        f"Email-ready leads: {lead.get('email_ready',0):,} | Coverage: {lead.get('email_coverage_rate',0)}%",
        f"High confidence: {lead.get('high_confidence',0):,} | Medium: {lead.get('medium_confidence',0):,}",
        f"Match rate: {lead.get('match_rate',0)}% | Lead velocity: {lead.get('velocity_7d',0):+.1f} new leads/day (7d avg)",
        "",
        "## TRAFFIC + FUNNEL (GA4 24h)",
        f"Users: {ga4.get('users',0)} | Sessions: {ga4.get('sessions',0)} | Pages/session: {ga4.get('pages_per_session',0)}",
        f"Quiz starts: {ga4.get('questionnaire_start',0)} ({_pct(ga4.get('questionnaire_start',0), ga4.get('users',1))}% of users)",
        f"Quiz completed: {ga4.get('questionnaire_complete',0)} | Calendly bookings: {ga4.get('calendly_booking',0)}",
        f"Stripe checkout: {ga4.get('stripe_checkout_started',0)} | Payments: {ga4.get('stripe_payment_success',0)}",
        "",
        "## SMS ENGINE (Twilio)",
    ]
    if sms.get("_no_table"):
        lines.append("SMS table not yet created")
    else:
        lines += [
            f"Sent lifetime: {sms.get('sent_total',0)} | Today: {sms.get('sent_today',0)} | 7d: {sms.get('sent_7d',0)}",
            f"Delivery rate: {sms.get('delivery_rate',0)}% | Link CTR: {sms.get('ctr',0)}% | Opt-outs: {sms.get('opt_outs',0)} ({sms.get('opt_out_rate',0)}%)",
            f"Est. cost: ${sms.get('cost_estimate',0):.2f} | Destination: {sms.get('destination_url','not recorded')}",
        ]
    lines += [
        "",
        "## BOOKING FUNNEL",
        f"Total bookings: {bk.get('total',0)} | Paid: {bk.get('paid',0)} | Pending: {bk.get('pending',0)} | Abandoned: {bk.get('abandoned',0)}",
        f"Booking→payment rate: {bk.get('conversion_rate',0)}% | Needs retarget 1: {bk.get('needs_r1_today',0)} | Retarget 2: {bk.get('needs_r2_today',0)}",
        "",
        "## TOP SUBJECT LINE (30d)",
    ]
    variants = seq.get("variants", [])
    if variants:
        top_v = max(variants, key=lambda v: v.get("open_rate",0))
        lines.append(f"Best: '{top_v.get('subject','')}' — {top_v.get('open_rate',0)}% open · {top_v.get('click_rate',0)}% click · {top_v.get('sent',0)} sent")
    else:
        lines.append("No variant data yet")
    lines += [
        "",
        "=== END SNAPSHOT ===",
        "Upload this block to Claude to ask: What should I optimize today?",
    ]

    plain_text = "\n".join(lines)
    # Render as styled pre block inside a section card
    body = (
        f"<div style='background:#0f1b2d;border-radius:8px;padding:16px;overflow-x:auto'>"
        f"<pre style='color:#e2e8f0;font-size:11px;font-family:monospace;margin:0;"
        f"white-space:pre-wrap;line-height:1.6'>{h(plain_text)}</pre></div>"
        f"<p style='font-size:11px;color:{C_SLATE};margin-top:8px'>"
        f"Copy the text above and paste it into Claude with your question, e.g. "
        f"\"What should I optimize first?\" or \"Where is the biggest bottleneck?\"</p>"
    )
    return sec2("🤖", "AI Analysis Export — Upload to Claude", body,
                "Structured machine-readable snapshot of all engine metrics. Paste into Claude for instant optimization analysis.")


# ─────────────────────────────────────────────────────────────────────────────
# Automation Command Center (sections A–H) — appended after existing content.
# Self-contained module; never raises into build_html.
# ─────────────────────────────────────────────────────────────────────────────

def _automation_sections(today_str, lead, seq, conv, sms, pipeline_runs) -> str:
    try:
        from app.workers.automation_command_center import build_automation_sections
        ctx = {
            "today_str": today_str,
            "base_dir": BASE_DIR,
            "sec2": sec2, "h": h, "tbl": tbl, "badge": badge,
            "colors": {
                "C_NAVY": C_NAVY, "C_GREEN": C_GREEN, "C_AMBER": C_AMBER, "C_RED": C_RED,
                "C_BLUE": C_BLUE, "C_SLATE": C_SLATE, "BG_GREEN": BG_GREEN,
                "BG_AMBER": BG_AMBER, "BG_RED": BG_RED, "BG_BLUE": BG_BLUE,
            },
            "pipeline_runs": pipeline_runs,
            "safe_query": safe_query,
            "seq": seq, "sms": sms, "conv": conv, "lead": lead,
        }
        return build_automation_sections(ctx)
    except Exception:
        return ""




# ─────────────────────────────────────────────────────────────────────────────
# Lead Intelligence Dashboard — Complete lead database view
# ─────────────────────────────────────────────────────────────────────────────

def _get_lead_intelligence(cur) -> dict:
    """Master lead database query — every state, every touchpoint, every gap."""
    result = {"_error": None, "states": [], "enrichment": {}, "sequence": {}, "sms": {}, "gaps": []}

    try:
        # ── 1. State-level pipeline overview ──────────────────────────────────
        cur.execute("""
            SELECT
                c.state,
                COUNT(DISTINCT nl.id)                                           AS total_liens,
                COUNT(DISTINCT ldc.id)                                          AS matched_contacts,
                COUNT(DISTINCT CASE WHEN ldc.email IS NOT NULL
                               AND ldc.email != '' THEN ldc.id END)             AS email_contacts,
                COUNT(DISTINCT CASE WHEN ldc.phone IS NOT NULL
                               AND ldc.phone != '' THEN ldc.id END)             AS phone_contacts,
                COUNT(DISTINCT CASE WHEN es.status = 'sent'
                               THEN es.to_email END)                            AS emailed,
                COUNT(DISTINCT CASE WHEN es.status = 'sent'
                               AND es.sequence_step = 1
                               THEN es.to_email END)                            AS email_step1,
                COUNT(DISTINCT CASE WHEN es.status = 'sent'
                               AND es.sequence_step >= 2
                               THEN es.to_email END)                            AS email_followup,
                COUNT(DISTINCT CASE WHEN es.reply_received = TRUE
                               THEN es.to_email END)                            AS replied,
                COUNT(DISTINCT CASE WHEN es.unsubscribed = TRUE
                               THEN es.to_email END)                            AS unsubscribed,
                COUNT(DISTINCT CASE WHEN sms.status IN ('queued','sent','delivered')
                               THEN sms.to_number END)                          AS sms_sent,
                COUNT(DISTINCT CASE WHEN sms.opt_out = TRUE
                               THEN sms.to_number END)                          AS sms_optout,
                ROUND(100.0 * COUNT(DISTINCT CASE WHEN ldc.email IS NOT NULL
                               AND ldc.email != '' THEN ldc.id END)
                    / NULLIF(COUNT(DISTINCT nl.id), 0), 1)                      AS email_match_pct,
                -- Gap: liens with no email contact at all
                COUNT(DISTINCT nl.id)
                    - COUNT(DISTINCT CASE WHEN ldc.email IS NOT NULL
                               AND ldc.email != '' THEN ldc.id END)             AS email_gap,
                -- Pipeline gap: have email but never contacted
                COUNT(DISTINCT CASE WHEN ldc.email IS NOT NULL
                               AND ldc.email != '' THEN ldc.id END)
                    - COUNT(DISTINCT CASE WHEN es.status = 'sent'
                               THEN es.to_email END)                            AS contact_gap
            FROM normalized_liens nl
            JOIN counties c ON c.id = nl.county_id
            LEFT JOIN lien_dbpr_contacts ldc ON ldc.lien_id = nl.id
            LEFT JOIN email_sends es
                ON LOWER(es.to_email) = LOWER(ldc.email)
                AND es.campaign_id = 'lien_outreach_2026'
            LEFT JOIN sms_campaign_log sms
                ON sms.lien_id = nl.id
            GROUP BY c.state
            ORDER BY COUNT(DISTINCT nl.id) DESC
        """)
        cols = [d[0] for d in cur.description]
        result["states"] = [dict(zip(cols, row)) for row in cur.fetchall()]

    except Exception as e:
        result["_error"] = str(e)
        return result

    try:
        # ── 2. Enrichment source breakdown ────────────────────────────────────
        cur.execute("""
            SELECT
                source,
                COUNT(*)                                                         AS contacts,
                COUNT(CASE WHEN email IS NOT NULL AND email != '' THEN 1 END)    AS with_email,
                COUNT(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 END)    AS with_phone,
                ROUND(100.0 * COUNT(CASE WHEN email IS NOT NULL
                              AND email != '' THEN 1 END)
                    / NULLIF(COUNT(*), 0), 1)                                    AS email_pct
            FROM lien_dbpr_contacts
            WHERE source IS NOT NULL
            GROUP BY source
            ORDER BY contacts DESC
        """)
        result["enrichment"] = [
            {"source": r[0], "contacts": r[1], "emails": r[2],
             "phones": r[3], "email_pct": float(r[4] or 0)}
            for r in cur.fetchall()
        ]
    except Exception:
        pass

    try:
        # ── 3. Email sequence funnel ───────────────────────────────────────────
        cur.execute("""
            SELECT
                sequence_step,
                COUNT(DISTINCT to_email)                                         AS sent,
                COUNT(DISTINCT CASE WHEN reply_received = TRUE
                               THEN to_email END)                                AS replied,
                COUNT(DISTINCT CASE WHEN unsubscribed = TRUE
                               THEN to_email END)                                AS unsub,
                COUNT(DISTINCT eo.tracking_id)                                   AS opens,
                COUNT(DISTINCT ec.tracking_id)                                   AS clicks
            FROM email_sends es
            LEFT JOIN email_opens eo ON eo.tracking_id = es.tracking_id
            LEFT JOIN email_clicks ec ON ec.tracking_id = es.tracking_id
            WHERE es.campaign_id = 'lien_outreach_2026'
              AND es.status = 'sent'
            GROUP BY sequence_step
            ORDER BY sequence_step
        """)
        result["sequence"] = [
            {"step": r[0], "sent": r[1], "replied": r[2],
             "unsub": r[3], "opens": r[4], "clicks": r[5]}
            for r in cur.fetchall()
        ]
    except Exception:
        pass

    try:
        # ── 4. SMS A/B template performance ────────────────────────────────────
        cur.execute("""
            SELECT
                template_key,
                COUNT(*)                                                         AS sent,
                COUNT(CASE WHEN status IN ('delivered','sent') THEN 1 END)       AS delivered,
                COUNT(CASE WHEN link_clicked = TRUE THEN 1 END)                  AS clicked,
                COUNT(CASE WHEN opt_out = TRUE THEN 1 END)                       AS optouts,
                ROUND(100.0 * COUNT(CASE WHEN link_clicked = TRUE THEN 1 END)
                    / NULLIF(COUNT(*), 0), 1)                                    AS ctr,
                batch_label
            FROM sms_campaign_log
            WHERE sent_at >= NOW() - INTERVAL '30 days'
              AND template_key IS NOT NULL
            GROUP BY template_key, batch_label
            ORDER BY ctr DESC NULLS LAST, sent DESC
        """)
        result["sms_ab"] = [
            {"template": r[0], "sent": r[1], "delivered": r[2],
             "clicked": r[3], "optouts": r[4], "ctr": float(r[5] or 0),
             "batch": r[6]}
            for r in cur.fetchall()
        ]
    except Exception:
        result["sms_ab"] = []

    try:
        # ── 5. Top enrichment gaps by county ──────────────────────────────────
        cur.execute("""
            SELECT
                c.state,
                c.county_name,
                COUNT(DISTINCT nl.id)                                            AS liens,
                COUNT(DISTINCT CASE WHEN ldc.email IS NOT NULL
                               AND ldc.email != '' THEN ldc.id END)             AS emails,
                COUNT(DISTINCT nl.id)
                    - COUNT(DISTINCT CASE WHEN ldc.email IS NOT NULL
                               AND ldc.email != '' THEN ldc.id END)             AS gap
            FROM normalized_liens nl
            JOIN counties c ON c.id = nl.county_id
            LEFT JOIN lien_dbpr_contacts ldc ON ldc.lien_id = nl.id
            GROUP BY c.state, c.county_name
            HAVING COUNT(DISTINCT nl.id) >= 50
            ORDER BY gap DESC
            LIMIT 8
        """)
        result["gaps"] = [
            {"state": r[0], "county": r[1], "liens": r[2],
             "emails": r[3], "gap": r[4]}
            for r in cur.fetchall()
        ]
    except Exception:
        pass

    return result


def build_lead_intelligence_section(intel: dict) -> str:
    """Lead Intelligence Dashboard — full pipeline visibility across all states."""

    if intel.get("_error"):
        return sec2("🧠", "Lead Intelligence Dashboard",
                    f"<p style='color:{C_RED};font-size:13px'>Query error: {h(intel['_error'])}</p>")

    parts = []

    # ── State pipeline table ───────────────────────────────────────────────────
    state_rows = []
    for s in intel.get("states", []):
        st = s.get("state") or "?"
        liens = s.get("total_liens", 0)
        emails = s.get("email_contacts", 0)
        phones = s.get("phone_contacts", 0)
        emailed = s.get("emailed", 0)
        replied = s.get("replied", 0)
        unsub = s.get("unsubscribed", 0)
        sms = s.get("sms_sent", 0)
        email_gap = s.get("email_gap", 0)
        contact_gap = s.get("contact_gap", 0)
        pct = s.get("email_match_pct", 0)

        # Color code the match rate
        pct_color = C_GREEN if float(pct or 0) >= 30 else (C_AMBER if float(pct or 0) >= 10 else C_RED)
        pct_cell = f"<span style='color:{pct_color};font-weight:700'>{pct}%</span>"

        # Color code gaps
        gap_cell = f"<span style='color:{C_RED if email_gap > 1000 else C_AMBER}'>{email_gap:,}</span>" if email_gap > 0 else "—"
        cpgap_cell = f"<span style='color:{C_AMBER}'>{contact_gap:,}</span>" if contact_gap > 0 else "—"

        state_rows.append([
            f"<b>{h(st)}</b>",
            f"{liens:,}",
            f"{emails:,}",
            f"{phones:,}",
            pct_cell,
            f"{emailed:,}",
            f"{replied:,}",
            f"<span style='color:{C_SLATE}'>{unsub:,}</span>",
            f"{sms:,}",
            gap_cell,
            cpgap_cell,
        ])

    if state_rows:
        parts.append(tbl(
            ["State", "Liens", "Emails", "Phones", "Email%",
             "Emailed", "Replied", "Unsub", "SMS Sent",
             "Email Gap", "Contact Gap"],
            state_rows,
            note="Email Gap = liens with no email match. Contact Gap = have email but never contacted."
        ))

    # ── Email sequence funnel ─────────────────────────────────────────────────
    seq_rows = []
    prev_sent = None
    for step in intel.get("sequence", []):
        sent = step.get("sent", 0)
        opens = step.get("opens", 0)
        clicks = step.get("clicks", 0)
        replied = step.get("replied", 0)
        unsub = step.get("unsub", 0)
        drop = ""
        if prev_sent and sent < prev_sent:
            drop_pct = round(100 - (sent / max(prev_sent, 1) * 100), 1)
            drop = f"<span style='color:{C_RED}'>▼{drop_pct}%</span>"
        open_rate = round(opens / max(sent, 1) * 100, 1)
        click_rate = round(clicks / max(sent, 1) * 100, 1)
        seq_rows.append([
            f"Step {step.get('step', '?')}",
            f"{sent:,}",
            drop or "—",
            f"{opens:,} ({open_rate}%)",
            f"{clicks:,} ({click_rate}%)",
            f"<span style='color:{C_GREEN}'>{replied:,}</span>",
            f"<span style='color:{C_SLATE}'>{unsub:,}</span>",
        ])
        prev_sent = sent

    if seq_rows:
        parts.append(
            f"<h4 style='margin:20px 0 8px;font-size:13px;color:{C_SLATE};text-transform:uppercase;letter-spacing:.05em'>Email Sequence Funnel</h4>"
            + tbl(["Step", "Sent", "Drop", "Opens", "Clicks", "Replied", "Unsub"], seq_rows)
        )

    # ── SMS A/B template results ───────────────────────────────────────────────
    sms_ab = intel.get("sms_ab", [])
    if sms_ab:
        ab_rows = []
        for t in sms_ab:
            ctr = t.get("ctr", 0)
            ctr_color = C_GREEN if ctr >= 5 else (C_AMBER if ctr >= 2 else C_SLATE)
            ab_rows.append([
                f"<b>{h(t.get('template','?'))}</b>",
                h(t.get("batch") or "—"),
                f"{t.get('sent',0):,}",
                f"{t.get('delivered',0):,}",
                f"{t.get('clicked',0):,}",
                f"<span style='color:{ctr_color};font-weight:700'>{ctr}%</span>",
                f"<span style='color:{C_RED}'>{t.get('optouts',0):,}</span>",
            ])
        parts.append(
            f"<h4 style='margin:20px 0 8px;font-size:13px;color:{C_SLATE};text-transform:uppercase;letter-spacing:.05em'>SMS A/B Template Performance (30d)</h4>"
            + tbl(["Template", "Batch", "Sent", "Delivered", "Clicked", "CTR", "Opt-Outs"], ab_rows,
                  note="CTR = link clicks ÷ sent. Best performers get more weight next week.")
        )

    # ── Enrichment source breakdown ────────────────────────────────────────────
    enr = intel.get("enrichment", [])
    if enr:
        enr_rows = []
        for e in enr:
            ep = e.get("email_pct", 0)
            ep_color = C_GREEN if ep >= 50 else (C_AMBER if ep >= 20 else C_RED)
            enr_rows.append([
                f"<b>{h(e.get('source','?'))}</b>",
                f"{e.get('contacts',0):,}",
                f"{e.get('emails',0):,}",
                f"{e.get('phones',0):,}",
                f"<span style='color:{ep_color};font-weight:700'>{ep}%</span>",
            ])
        parts.append(
            f"<h4 style='margin:20px 0 8px;font-size:13px;color:{C_SLATE};text-transform:uppercase;letter-spacing:.05em'>Enrichment Sources</h4>"
            + tbl(["Source", "Contacts", "Emails", "Phones", "Email %"], enr_rows,
                  note="Source = how each contact was found. DBPR=FL licensed contractors, ROC=AZ, TDLR=TX.")
        )

    # ── Top enrichment gaps ────────────────────────────────────────────────────
    gaps = intel.get("gaps", [])
    if gaps:
        gap_rows = []
        for g in gaps:
            gap = g.get("gap", 0)
            gap_color = C_RED if gap > 1000 else C_AMBER
            gap_rows.append([
                f"<b>{h(g.get('state','?'))}</b>",
                h(g.get("county","?")),
                f"{g.get('liens',0):,}",
                f"{g.get('emails',0):,}",
                f"<span style='color:{gap_color};font-weight:700'>{gap:,}</span>",
            ])
        parts.append(
            f"<h4 style='margin:20px 0 8px;font-size:13px;color:{C_SLATE};text-transform:uppercase;letter-spacing:.05em'>Top Enrichment Gaps (Counties)</h4>"
            + tbl(["State", "County", "Liens", "Emails", "Gap"],
                  gap_rows,
                  note="Gap = liens with no email match. These are your next BatchData or scraper targets.")
        )

    if not parts:
        parts.append(f"<p style='color:{C_SLATE};font-size:13px'>No lead intelligence data yet. Run scrapers and enrichment to populate.</p>")

    return sec2("🧠", "Lead Intelligence Dashboard",
                "".join(parts),
                "Complete pipeline view: every state, every lien, every touchpoint. "
                "Email Gap = need enrichment. Contact Gap = have email, just need to send.")

# ─────────────────────────────────────────────────────────────────────────────
# Smart subject line
# ─────────────────────────────────────────────────────────────────────────────

def _smart_subject(seq: dict, conv: dict, bk: dict, today_str: str) -> str:
    alerts = []
    if conv.get("today", 0) > 0:
        alerts.append(f"💰 {conv['today']} paid today")
    if seq.get("replied", 0) > 0:
        alerts.append(f"🔥 {seq['replied']} reply")
    if seq.get("sent_24h", 0) == 0:
        alerts.append("⚠️ 0 sends")
    if seq.get("failed", 0) > 10:
        alerts.append(f"❌ {seq['failed']} failures")
    if bk.get("needs_r1_today", 0) + bk.get("needs_r2_today", 0) > 0:
        alerts.append("⏰ retarget needed")
    alert_str = " · ".join(alerts)
    base = f"📊 TaxCase Review Engine — {today_str}"
    return f"{base} · {alert_str}" if alert_str else base


# ─────────────────────────────────────────────────────────────────────────────
# HTML assembly
# ─────────────────────────────────────────────────────────────────────────────

def build_html(lead, states, counties, seq, conv, ga4, clarity, ux, today_str,
               bk=None, sms=None, data_section="", intel=None):
    bk  = bk  or {}
    sms = sms or {"_no_table": True}

    pipeline_runs = _read_pipeline_today()
    email_run     = next((r for r in pipeline_runs if r.get("run_type")=="email_sends"), None)
    sender        = (email_run or {}).get("metrics", {}) if email_run else None

    subject = _smart_subject(seq, conv, bk, today_str)

    # KPI card colors
    def _kpi_color(v, thresh_green, thresh_amber):
        if v >= thresh_green: return C_GREEN, BG_GREEN, C_GREEN
        if v >= thresh_amber: return C_AMBER, BG_AMBER, C_AMBER
        return C_RED, BG_RED, C_RED

    rev_c, rev_bg, rev_border = _kpi_color(conv.get("revenue",0), 1000, 1)
    send_c, send_bg, send_border = _kpi_color(seq.get("sent_24h",0), 50, 1)
    or_c, or_bg, or_border = _kpi_color(seq.get("open_rate",0), 14, 8)
    er_c, er_bg, er_border = _kpi_color(lead.get("email_ready",0), 1000, 100)

    kpi_row = (
        f"<table style='width:100%;border-collapse:collapse;margin:0 0 16px'><tr>"
        + kpi_card("Revenue", _money(conv.get("revenue",0)), f"{conv.get('total',0)} paid reviews", rev_c, rev_bg, rev_border)
        + kpi_card("Sends 24h", f"{seq.get('sent_24h',0):,}", f"{seq.get('sent_7d',0):,} last 7d", send_c, send_bg, send_border)
        + kpi_card("Open Rate", f"{seq.get('open_rate',0)}%", f"{seq.get('sent_total',0):,} lifetime sends", or_c, or_bg, or_border)
        + kpi_card("Email-Ready", f"{lead.get('email_ready',0):,}", f"{lead.get('email_coverage_rate',0)}% coverage", er_c, er_bg, er_border)
        + "</tr></table>"
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>{h(subject)}</title>
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:{C_BG};margin:0;padding:16px;color:{C_NAVY}">
<div style="max-width:1120px;margin:0 auto">

  <!-- HEADER -->
  <div style="background:{C_NAVY};border-radius:14px 14px 0 0;padding:24px 28px;margin-bottom:0">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#64748b;margin-bottom:6px">TaxCase Review</div>
    <h1 style="margin:0;color:{C_WHITE};font-size:22px;font-weight:900">Engine Intelligence Digest</h1>
    <p style="margin:6px 0 0;color:#94a3b8;font-size:13px">{h(today_str)} · Generated {datetime.now().strftime('%I:%M %p')} · Campaign: {h(CAMPAIGN_ID)}</p>
  </div>

  <!-- ENGINE SCORECARDS -->
  <div style="background:#1e293b;padding:16px 28px;border-radius:0;margin-bottom:0">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#64748b;margin-bottom:10px">Engine Health At-a-Glance</div>
    {_build_engine_scorecards(seq, lead, ga4, conv, sms)}
  </div>

  <!-- KPI ROW -->
  <div style="background:{C_WHITE};padding:16px 28px;border-radius:0 0 14px 14px;margin-bottom:16px;border-bottom:1px solid {C_BORDER}">
    {kpi_row}
  </div>

  <!-- SECTIONS -->
  {build_action_section(lead, seq, ga4, clarity, ux, conv, sms)}
  {build_revenue_section(conv)}
  {build_email_section(seq, sender)}
  {build_sms_section(sms)}
  {build_lead_section(lead, states, counties)}
  {build_booking_section(bk)}
  {build_traffic_section(ga4, clarity, ux)}
  {build_content_section(pipeline_runs)}
  {build_pipeline_calendar_section(pipeline_runs)}
  {build_lead_intelligence_section(intel or {})}
  {data_section}
  {build_ai_export_block(lead, seq, ga4, conv, sms, bk, today_str)}
  {_automation_sections(today_str, lead, seq, conv, sms, pipeline_runs)}

  <p style="margin-top:20px;color:{C_SLATE};font-size:11px;border-top:1px solid {C_BORDER};padding-top:12px">
    TaxCase Review · LeadFlow Pipeline v5 · {datetime.now().strftime('%Y-%m-%d %H:%M')}
  </p>
</div>
</body>
</html>"""
    return subject, html


# ─────────────────────────────────────────────────────────────────────────────
# Email send
# ─────────────────────────────────────────────────────────────────────────────

def _build_plain_text(seq, conv, lead, sms, today_str) -> str:
    """Minimal text fallback with key metrics inline."""
    lines = [
        f"TaxCase Review Engine Digest — {today_str}",
        "",
        f"Revenue: {_money(conv.get('revenue',0))} ({conv.get('total',0)} paid reviews)",
        f"Email: {seq.get('sent_24h',0)} sent today | {seq.get('open_rate',0)}% open | {seq.get('click_rate',0)}% click | {seq.get('replied',0)} replies",
        f"Leads: {lead.get('email_ready',0):,} email-ready | {lead.get('liens_total',0):,} total liens",
    ]
    if not sms.get("_no_table") and sms.get("sent_total", 0) > 0:
        lines.append(f"SMS: {sms.get('sent_total',0)} sent | {sms.get('delivery_rate',0)}% delivery | {sms.get('ctr',0)}% CTR")
    lines.append("")
    lines.append("Open the HTML version for full engine analysis and AI export block.")
    return "\n".join(lines)


def send_summary(subject: str, html: str, plain: str, recipients: list[str]):
    if not SUMMARY_PASSWORD:
        raise RuntimeError("GMAIL_SUMMARY_PASSWORD or GMAIL_APP_PASSWORD not set in .env")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{SENDER_NAME} <{SUMMARY_SENDER}>"
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html,  "html"))
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
        server.login(SUMMARY_SENDER, SUMMARY_PASSWORD)
        server.sendmail(SUMMARY_SENDER, recipients, msg.as_string())


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TaxCase Review Daily Summary v5")
    parser.add_argument("--dry-run",   action="store_true", help="Generate HTML but don't send")
    parser.add_argument("--save-html", action="store_true", help="Save HTML to data/ on live runs too")
    parser.add_argument("--to",        default=None, help="Override recipient list (comma-separated)")
    parser.add_argument("--date",      default=None, help="Generate for a past date YYYY-MM-DD")
    args = parser.parse_args()

    target_date = args.date or date.today().isoformat()
    today_str   = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")

    print(f"\n[TaxCase Review Engine Digest v5] {today_str}")
    print(f"  Sender : {SUMMARY_SENDER}")
    print(f"  Campaign: {CAMPAIGN_ID}")

    safe_query(lambda cur: ensure_email_indexes(cur), None)

    lead = safe_query(_get_lead_intelligence, {
        "liens_total":0,"liens_24h":0,"liens_7d":0,"liens_30d":0,
        "matched_total":0,"matched_liens":0,"email_ready":0,
        "high_confidence":0,"medium_confidence":0,
        "match_rate":0,"email_coverage_rate":0,"high_confidence_rate":0,"velocity_7d":0,
    })
    states   = safe_query(_get_state_breakdown,  [])
    counties = safe_query(_get_county_breakdown, [])
    seq = safe_query(_get_email_sequence_stats, {
        "total_contacts":0,"waiting":0,
        "steps":{i:0 for i in range(1,8)},
        "periods":{p:{i:{"sent":0,"opens":0,"clicks":0,"replies":0,"open_rate":0,"click_rate":0,"reply_rate":0} for i in range(1,8)} for p in PERIODS},
        "ready":{},"status_counts":{},"sent_24h":0,"sent_7d":0,"sent_30d":0,"sent_total":0,
        "opens":0,"clicks":0,"replied":0,"unsubscribed":0,"failed":0,"throttled":0,
        "spam_trap":0,"stale_queued":0,"recent_queued":0,"open_rate":0,"click_rate":0,
        "reply_rate":0,"variants":[],"prev_open_rate":0,"prev_click_rate":0,"avg_score_sent_today":None,"top_unsent":[],
    })
    conv = safe_query(_get_conversion_stats, {
        "total":0,"revenue":0.0,"today":0,"revenue_today":0.0,
        "week":0,"revenue_week":0.0,"month":0,"revenue_month":0.0,"prev_week":0,"revenue_prev_week":0.0,
    })
    bk = safe_query(_get_booking_stats, {
        "total":0,"pending":0,"paid":0,"abandoned":0,"canceled":0,"no_show":0,
        "pending_today":0,"paid_today":0,"r1_sent":0,"r2_sent":0,"r3_sent":0,
        "r1_converted":0,"r2_converted":0,"feedback_responses":0,
        "needs_r1_today":0,"needs_r2_today":0,"conversion_rate":0.0,"recent_paid":[],
    })
    sms = safe_query(_get_sms_stats, {"_no_table": True})

    ga4     = _fetch_ga4()
    clarity = _fetch_clarity()
    ux      = _fetch_ux(clarity)

    data_section = safe_query(build_data_collection_section, "")

    subject, html = build_html(lead, states, counties, seq, conv, ga4, clarity, ux,
                               today_str, bk=bk, sms=sms, data_section=data_section)
    plain = _build_plain_text(seq, conv, lead, sms, today_str)

    # Save HTML
    if args.dry_run or args.save_html:
        out = BASE_DIR / "data" / "daily_summary_preview.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print(f"\n  Preview saved: {out}")

    if args.dry_run:
        print(f"\n  Subject: {subject}")
        print(f"  Sections: engine scorecards, action items, revenue, email, SMS, lead, booking, traffic, content, calendar, AI export")
        return

    recipients = [r.strip() for r in args.to.split(",")] if args.to else RECIPIENTS

    from pipeline_log import PipelineLogger
    logger = PipelineLogger("daily_summary")
    logger.start()
    logger.step_start("send_summary")
    try:
        send_summary(subject, html, plain, recipients)
        logger.step_done("send_summary", ok=True, detail=f"sent to {len(recipients)} recipient(s)")
        print(f"  ✅ Sent to: {', '.join(recipients)}")
        logger.finish({
            "recipients": len(recipients), "to": ",".join(recipients),
            "subject": subject, "sent_24h": seq.get("sent_24h",0),
            "email_ready": lead.get("email_ready",0), "liens_total": lead.get("liens_total",0),
            "paid_reviews": conv.get("total",0), "sms_total": sms.get("sent_total",0),
        })
    except Exception as e:
        logger.step_done("send_summary", ok=False, error=str(e))
        logger.finish({"error": str(e)})
        raise


if __name__ == "__main__":
    main()