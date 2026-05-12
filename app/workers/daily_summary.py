"""
daily_summary.py
================
Sends a morning pipeline + campaign digest via Gmail OAuth.
Schedule with Windows Task Scheduler at 7:00 AM daily.

Usage:
  python -m app.workers.daily_summary
  python -m app.workers.daily_summary --to you@email.com

Task Scheduler setup:
  Action: python -m app.workers.daily_summary
  Start in: C:\\Users\\Dana\\Desktop\\leadflow
  Trigger: Daily at 7:00 AM
"""
from __future__ import annotations

import argparse
import base64
import os
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from app.core.db import get_connection
from app.services.tracking import get_metrics_summary

BASE_DIR    = Path(__file__).resolve().parents[2]
TOKEN_PATH  = BASE_DIR / "data" / "credentials" / "gmail_token.json"
CREDS_PATH  = BASE_DIR / "data" / "credentials" / "gmail_credentials.json"
SENDER      = os.getenv("GMAIL_SENDER", "")
SUMMARY_TO  = os.getenv("DAILY_SUMMARY_TO", SENDER)  # defaults to self


GOAL_CONVERSIONS = 500
GOAL_REVENUE     = 500 * 399


# ---------------------------------------------------------------------------
# Gmail (reuse token from send_email_campaign)
# ---------------------------------------------------------------------------

def get_gmail_service():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# Pull additional stats
# ---------------------------------------------------------------------------

def get_pipeline_stats(cur) -> dict:
    """New permits/liens added in last 24h and last 7 days."""
    cur.execute("""
        SELECT
            COUNT(CASE WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 1 END) AS permits_24h,
            COUNT(CASE WHEN created_at >= NOW() - INTERVAL '7 days'   THEN 1 END) AS permits_7d,
            COUNT(*) AS permits_total
        FROM normalized_permits
    """)
    p = cur.fetchone()

    cur.execute("""
        SELECT
            COUNT(CASE WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 1 END) AS liens_24h,
            COUNT(CASE WHEN created_at >= NOW() - INTERVAL '7 days'   THEN 1 END) AS liens_7d,
            COUNT(*) AS liens_total
        FROM normalized_liens
    """)
    l = cur.fetchone()

    cur.execute("""
        SELECT
            COUNT(CASE WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 1 END) AS leads_24h,
            COUNT(CASE WHEN created_at >= NOW() - INTERVAL '7 days'   THEN 1 END) AS leads_7d,
            COUNT(*) AS leads_total
        FROM matched_leads
    """)
    m = cur.fetchone()

    cur.execute("""
        SELECT
            COUNT(CASE WHEN sent_at >= NOW() - INTERVAL '24 hours' THEN 1 END)  AS sent_24h,
            COUNT(CASE WHEN sent_at >= NOW() - INTERVAL '7 days'   THEN 1 END)  AS sent_7d
        FROM email_sends WHERE status = 'sent'
    """)
    e = cur.fetchone()

    cur.execute("""
        SELECT county_name, permits, liens, leads FROM (
            SELECT
                c.county_name,
                COUNT(DISTINCT np.id) AS permits,
                COUNT(DISTINCT nl.id) AS liens,
                COUNT(DISTINCT ml.id) AS leads
            FROM counties c
            LEFT JOIN normalized_permits np ON np.county_id = c.id
            LEFT JOIN normalized_liens   nl ON nl.county_id = c.id
            LEFT JOIN matched_leads      ml ON ml.county_id = c.id
            GROUP BY c.county_name
        ) sub ORDER BY leads DESC
    """)
    counties = cur.fetchall()

    return {
        "permits": {"24h": p[0], "7d": p[1], "total": p[2]},
        "liens":   {"24h": l[0], "7d": l[1], "total": l[2]},
        "leads":   {"24h": m[0], "7d": m[1], "total": m[2]},
        "emails":  {"24h": e[0], "7d": e[1]},
        "counties": [{"name": r[0], "permits": r[1], "liens": r[2], "leads": r[3]} for r in counties],
    }


def save_daily_snapshot(cur, metrics: dict, pipeline: dict) -> None:
    """Save today's snapshot for trend tracking."""
    for county in pipeline["counties"]:
        # Find county email stats
        cur.execute("""
            SELECT
                COUNT(DISTINCT es.id)                                              AS sent,
                COUNT(DISTINCT eo.tracking_id)                                     AS opens,
                COUNT(DISTINCT ec.tracking_id)                                     AS clicks,
                COUNT(DISTINCT CASE WHEN es.status='bounced' THEN es.id END)       AS bounces
            FROM email_sends es
            LEFT JOIN email_opens  eo ON eo.tracking_id = es.tracking_id
            LEFT JOIN email_clicks ec ON ec.tracking_id = es.tracking_id
            WHERE es.county_name = %s AND es.status IN ('sent','bounced')
        """, (county["name"],))
        em = cur.fetchone()

        cur.execute("""
            INSERT INTO daily_snapshots (
                snapshot_date, county_name,
                total_permits, total_liens, total_leads,
                new_leads_24h,
                emails_sent, emails_opened, emails_clicked, emails_bounced,
                conversions, revenue
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (snapshot_date, county_name) DO UPDATE SET
                total_permits  = EXCLUDED.total_permits,
                total_liens    = EXCLUDED.total_liens,
                total_leads    = EXCLUDED.total_leads,
                new_leads_24h  = EXCLUDED.new_leads_24h,
                emails_sent    = EXCLUDED.emails_sent,
                emails_opened  = EXCLUDED.emails_opened,
                emails_clicked = EXCLUDED.emails_clicked,
                emails_bounced = EXCLUDED.emails_bounced
        """, (
            date.today(), county["name"],
            county["permits"], county["liens"], county["leads"],
            pipeline["leads"]["24h"],
            em[0], em[1], em[2], em[3],
            metrics["conversions"]["count"],
            metrics["conversions"]["revenue"],
        ))


# ---------------------------------------------------------------------------
# HTML summary email
# ---------------------------------------------------------------------------

def bar(value: int, total: int, width: int = 20) -> str:
    """Simple ASCII progress bar."""
    filled = int(width * min(value, total) / max(total, 1))
    return "█" * filled + "░" * (width - filled)


def build_summary_html(metrics: dict, pipeline: dict, today: str) -> tuple[str, str]:
    m   = metrics
    ov  = m["overall"]
    cv  = m["conversions"]
    pct = round(100 * cv["count"] / GOAL_CONVERSIONS, 1)

    # County table rows
    county_rows = ""
    for c in pipeline["counties"]:
        em_data = next((x for x in m["counties"] if x["name"] == c["name"]), {})
        county_rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;font-weight:500">{c['name']}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">{c['permits']:,}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">{c['liens']:,}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right;font-weight:600;color:#1a56db">{c['leads']:,}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">{em_data.get('enriched',0):,}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">{em_data.get('contacted',0):,}</td>
        </tr>"""

    subject = f"📊 Leadflow Daily Summary — {today}"

    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#222;max-width:700px;margin:0 auto;padding:20px;background:#f8f9fa">

<div style="background:#1a56db;color:#fff;padding:20px 24px;border-radius:8px 8px 0 0">
  <h2 style="margin:0;font-size:20px">📊 Leadflow Daily Summary</h2>
  <p style="margin:4px 0 0;opacity:0.85">{today}</p>
</div>

<div style="background:#fff;padding:24px;border-radius:0 0 8px 8px;box-shadow:0 2px 8px rgba(0,0,0,0.08)">

  <!-- GOAL PROGRESS -->
  <h3 style="color:#1a56db;margin-top:0">🎯 Annual Goal: 500 Conversions × $399</h3>
  <div style="background:#f0f4ff;padding:16px;border-radius:8px;margin-bottom:24px">
    <div style="display:flex;justify-content:space-between;margin-bottom:8px">
      <span><strong>{cv['count']}</strong> conversions</span>
      <span style="color:#666">{pct}% of goal</span>
    </div>
    <div style="background:#dde3f0;border-radius:4px;height:12px">
      <div style="background:#1a56db;width:{min(pct,100)}%;height:12px;border-radius:4px"></div>
    </div>
    <div style="display:flex;justify-content:space-between;margin-top:8px;font-size:13px;color:#555">
      <span>Revenue: <strong style="color:#1a56db">${cv['revenue']:,.0f}</strong></span>
      <span>Goal: <strong>${GOAL_REVENUE:,}</strong></span>
    </div>
  </div>

  <!-- EMAIL METRICS -->
  <h3 style="color:#1a56db">📧 Email Campaign Metrics</h3>
  <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
    <tr style="background:#f0f4ff">
      <th style="padding:10px 12px;text-align:left">Metric</th>
      <th style="padding:10px 12px;text-align:right">Last 24h</th>
      <th style="padding:10px 12px;text-align:right">All Time</th>
    </tr>
    <tr>
      <td style="padding:8px 12px;border-bottom:1px solid #eee">Emails Sent</td>
      <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">{m['last_24h']['sent']:,}</td>
      <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right;font-weight:600">{ov['sent']:,}</td>
    </tr>
    <tr>
      <td style="padding:8px 12px;border-bottom:1px solid #eee">Opens</td>
      <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">{m['last_24h']['opens']:,}</td>
      <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">{ov['opens']:,} <span style="color:#666;font-size:12px">({ov['open_rate']}%)</span></td>
    </tr>
    <tr>
      <td style="padding:8px 12px;border-bottom:1px solid #eee">Clicks</td>
      <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">—</td>
      <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">{ov['clicks']:,} <span style="color:#666;font-size:12px">({ov['click_rate']}%)</span></td>
    </tr>
    <tr>
      <td style="padding:8px 12px;border-bottom:1px solid #eee">Bounces</td>
      <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">—</td>
      <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right;color:#e03">{ov['bounces']:,}</td>
    </tr>
    <tr>
      <td style="padding:8px 12px">Conversions</td>
      <td style="padding:8px 12px;text-align:right">—</td>
      <td style="padding:8px 12px;text-align:right;font-weight:600;color:#1a56db">{cv['count']:,}</td>
    </tr>
  </table>

  <!-- PIPELINE -->
  <h3 style="color:#1a56db">🔧 Pipeline Health</h3>
  <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
    <tr style="background:#f0f4ff">
      <th style="padding:10px 12px;text-align:left">Metric</th>
      <th style="padding:10px 12px;text-align:right">+24h</th>
      <th style="padding:10px 12px;text-align:right">+7 Days</th>
      <th style="padding:10px 12px;text-align:right">Total</th>
    </tr>
    <tr>
      <td style="padding:8px 12px;border-bottom:1px solid #eee">Permits</td>
      <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">+{pipeline['permits']['24h']}</td>
      <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">+{pipeline['permits']['7d']}</td>
      <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right;font-weight:600">{pipeline['permits']['total']:,}</td>
    </tr>
    <tr>
      <td style="padding:8px 12px;border-bottom:1px solid #eee">Liens</td>
      <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">+{pipeline['liens']['24h']}</td>
      <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">+{pipeline['liens']['7d']}</td>
      <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right;font-weight:600">{pipeline['liens']['total']:,}</td>
    </tr>
    <tr>
      <td style="padding:8px 12px">Matched Leads</td>
      <td style="padding:8px 12px;text-align:right">+{pipeline['leads']['24h']}</td>
      <td style="padding:8px 12px;text-align:right">+{pipeline['leads']['7d']}</td>
      <td style="padding:8px 12px;text-align:right;font-weight:600;color:#1a56db">{pipeline['leads']['total']:,}</td>
    </tr>
  </table>

  <!-- COUNTY BREAKDOWN -->
  <h3 style="color:#1a56db">📍 County Breakdown</h3>
  <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
    <tr style="background:#f0f4ff">
      <th style="padding:10px 12px;text-align:left">County</th>
      <th style="padding:10px 12px;text-align:right">Permits</th>
      <th style="padding:10px 12px;text-align:right">Liens</th>
      <th style="padding:10px 12px;text-align:right">Leads</th>
      <th style="padding:10px 12px;text-align:right">Enriched</th>
      <th style="padding:10px 12px;text-align:right">Contacted</th>
    </tr>
    {county_rows}
  </table>

  <!-- NEXT ACTIONS -->
  <h3 style="color:#1a56db">✅ Suggested Actions Today</h3>
  <ul style="line-height:1.8;color:#444">
    {"<li>🔴 Run match_and_score — new permits/liens added</li>" if pipeline['permits']['24h'] > 0 or pipeline['liens']['24h'] > 0 else ""}
    {"<li>🟡 Run enrich_dbpr — leads without email found</li>" if m['pipeline']['leads'] > m['pipeline']['real_emails'] else ""}
    {"<li>🟢 Send email campaign — leads ready to contact</li>" if m['pipeline']['real_emails'] > ov['sent'] else ""}
    <li>📈 Goal pace: need <strong>{max(0, GOAL_CONVERSIONS - cv['count'])}</strong> more conversions at $399 each</li>
  </ul>

  <p style="color:#999;font-size:12px;margin-top:24px;border-top:1px solid #eee;padding-top:12px">
    Generated by Leadflow at {datetime.now().strftime('%Y-%m-%d %H:%M')} · 
    <a href="http://localhost:3000" style="color:#999">Open Dashboard</a>
  </p>

</div>
</body>
</html>
"""
    return subject, html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", default=SUMMARY_TO)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    today = datetime.now().strftime("%B %d, %Y")
    print(f"[Daily Summary] {today}")

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                metrics  = get_metrics_summary()
                pipeline = get_pipeline_stats(cur)
                save_daily_snapshot(cur, metrics, pipeline)
    finally:
        conn.close()

    subject, html = build_summary_html(metrics, pipeline, today)

    if args.dry_run:
        print(subject)
        print(f"  Leads total   : {pipeline['leads']['total']}")
        print(f"  Emails sent   : {metrics['overall']['sent']}")
        print(f"  Open rate     : {metrics['overall']['open_rate']}%")
        print(f"  Conversions   : {metrics['conversions']['count']} / {GOAL_CONVERSIONS}")
        print(f"  Revenue       : ${metrics['conversions']['revenue']:,.0f}")
        print("  [DRY RUN — email not sent]")
        return

    service = get_gmail_service()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SENDER
    msg["To"]      = args.to

    msg.attach(MIMEText(html, "html"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"  ✓ Summary sent to {args.to}")


if __name__ == "__main__":
    main()
