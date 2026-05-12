"""
app/services/tracking.py
========================
Helpers for recording email tracking events.
Called by the tracking server (or a simple Flask endpoint you expose).

Open tracking:  embed a 1x1 pixel → GET /track/open/{tracking_id}.gif
Click tracking: wrap links  → GET /track/click/{tracking_id}?url=...

For now, tracking is recorded directly to DB.
In production, expose these via a lightweight Flask app or FastAPI route.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.db import get_connection


def record_open(tracking_id: str, ip: str = None, user_agent: str = None) -> None:
    """Record an email open event."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO email_opens (tracking_id, opened_at, ip_address, user_agent)
                    VALUES (%s, NOW(), %s, %s)
                """, (tracking_id, ip, user_agent))
    finally:
        conn.close()


def record_click(tracking_id: str, url: str, ip: str = None, user_agent: str = None) -> None:
    """Record a link click event."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO email_clicks (tracking_id, clicked_at, url, ip_address, user_agent)
                    VALUES (%s, NOW(), %s, %s, %s)
                """, (tracking_id, url, ip, user_agent))
    finally:
        conn.close()


def record_bounce(tracking_id: str, error: str = None) -> None:
    """Mark an email as bounced."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE email_sends
                    SET status = 'bounced', error_message = %s
                    WHERE tracking_id = %s::uuid
                """, (error, tracking_id))
    finally:
        conn.close()


def record_conversion(lead_id: int, tracking_id: str = None, revenue: float = 399.00,
                      notes: str = None) -> None:
    """Record a paid conversion ($399 tax review)."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO conversions (lead_id, tracking_id, revenue, notes, converted_at)
                    VALUES (%s, %s::uuid, %s, %s, NOW())
                    ON CONFLICT DO NOTHING
                """, (lead_id, tracking_id, revenue, notes))
                cur.execute("""
                    UPDATE matched_leads SET lead_status = 'converted', updated_at = NOW()
                    WHERE id = %s
                """, (lead_id,))
    finally:
        conn.close()


def get_metrics_summary() -> dict:
    """Pull current campaign metrics for the daily summary."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Overall totals
            cur.execute("""
                SELECT
                    COUNT(*)                                                    AS total_sent,
                    COUNT(DISTINCT eo.tracking_id)                              AS total_opens,
                    COUNT(DISTINCT ec.tracking_id)                              AS total_clicks,
                    COUNT(DISTINCT CASE WHEN es.status='bounced' THEN es.id END) AS total_bounces,
                    ROUND(100.0 * COUNT(DISTINCT eo.tracking_id) /
                        NULLIF(COUNT(*),0), 1)                                  AS open_rate,
                    ROUND(100.0 * COUNT(DISTINCT ec.tracking_id) /
                        NULLIF(COUNT(*),0), 1)                                  AS click_rate
                FROM email_sends es
                LEFT JOIN email_opens  eo ON eo.tracking_id = es.tracking_id
                LEFT JOIN email_clicks ec ON ec.tracking_id = es.tracking_id
                WHERE es.status IN ('sent','bounced')
            """)
            overall = cur.fetchone()

            # Last 24h
            cur.execute("""
                SELECT COUNT(*) FROM email_sends
                WHERE sent_at >= NOW() - INTERVAL '24 hours' AND status = 'sent'
            """)
            sent_24h = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*) FROM email_opens
                WHERE opened_at >= NOW() - INTERVAL '24 hours'
            """)
            opens_24h = cur.fetchone()[0]

            # Conversions
            cur.execute("""
                SELECT COUNT(*), COALESCE(SUM(revenue), 0) FROM conversions
            """)
            conv_row = cur.fetchone()

            # Per-county leads
            cur.execute("""
                SELECT c.county_name, COUNT(*) as leads,
                       COUNT(CASE WHEN ml.enrichment_status LIKE 'matched_dbpr%' THEN 1 END) as enriched,
                       COUNT(CASE WHEN ml.lead_status = 'contacted' THEN 1 END) as contacted
                FROM matched_leads ml
                JOIN counties c ON ml.county_id = c.id
                GROUP BY c.county_name ORDER BY leads DESC
            """)
            county_rows = cur.fetchall()

            # Pipeline health
            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM normalized_permits) AS total_permits,
                    (SELECT COUNT(*) FROM normalized_liens)   AS total_liens,
                    (SELECT COUNT(*) FROM matched_leads)      AS total_leads,
                    (SELECT COUNT(*) FROM contacts WHERE email NOT LIKE '%leadflow.invalid') AS real_emails
            """)
            pipeline = cur.fetchone()

        return {
            "overall": {
                "sent":        overall[0] or 0,
                "opens":       overall[1] or 0,
                "clicks":      overall[2] or 0,
                "bounces":     overall[3] or 0,
                "open_rate":   float(overall[4] or 0),
                "click_rate":  float(overall[5] or 0),
            },
            "last_24h": {
                "sent":   sent_24h,
                "opens":  opens_24h,
            },
            "conversions": {
                "count":   conv_row[0],
                "revenue": float(conv_row[1]),
                "goal":    500,
                "goal_revenue": 199500.0,  # 500 × $399
            },
            "counties": [
                {"name": r[0], "leads": r[1], "enriched": r[2], "contacted": r[3]}
                for r in county_rows
            ],
            "pipeline": {
                "permits":     pipeline[0],
                "liens":       pipeline[1],
                "leads":       pipeline[2],
                "real_emails": pipeline[3],
            },
        }
    finally:
        conn.close()
