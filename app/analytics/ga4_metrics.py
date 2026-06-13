"""
ga4_metrics.py
=============
LeadFlow / TaxCaseReview GA4 metrics helper.

Purpose:
  Pull GA4 traffic, funnel, landing page, source/medium, device, state/page-type,
  report engagement, and conversion metrics for the daily summary and future
  reporting automation.

Expected location in project:
  app/analytics/ga4_metrics.py

Required .env:
  GA4_PROPERTY_ID=123456789
  GA4_CLIENT_SECRET_PATH=data/credentials/ga4-oauth.json
  GA4_TOKEN_PATH=data/credentials/ga4-token.pickle

Install:
  pip install google-analytics-data google-auth-oauthlib google-auth python-dotenv

Quick test:
  cd C:/Users/Dana/Desktop/leadflow
  python -m app.analytics.ga4_metrics

Daily summary compatibility:
  daily_summary.py can safely import:
    from app.analytics.ga4_metrics import fetch_ga4_metrics
"""

from __future__ import annotations

import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow


BASE_DIR = Path(__file__).resolve().parents[2]

GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "").strip()
GA4_CLIENT_SECRET_PATH = Path(os.getenv("GA4_CLIENT_SECRET_PATH", "data/credentials/ga4-oauth.json"))
GA4_TOKEN_PATH = Path(os.getenv("GA4_TOKEN_PATH", "data/credentials/ga4-token.pickle"))
CASE_REVIEW_VALUE = int(os.getenv("CASE_REVIEW_VALUE", "399"))

SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]

STATE_SLUGS = {
    "fl": "Florida",
    "florida": "Florida",
    "tx": "Texas",
    "texas": "Texas",
    "ga": "Georgia",
    "georgia": "Georgia",
    "az": "Arizona",
    "arizona": "Arizona",
    "ca": "California",
    "california": "California",
    "ny": "New York",
    "new-york": "New York",
    "nc": "North Carolina",
    "north-carolina": "North Carolina",
}


@dataclass
class GA4MetricResult:
    ok: bool
    data: Dict[str, Any]
    error: Optional[str] = None


def _resolve_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return BASE_DIR / path


def get_credentials():
    client_secret_path = _resolve_path(GA4_CLIENT_SECRET_PATH)
    token_path = _resolve_path(GA4_TOKEN_PATH)

    if not client_secret_path.exists():
        raise FileNotFoundError(f"GA4 OAuth client file not found: {client_secret_path}")

    creds = None

    if token_path.exists():
        with open(token_path, "rb") as token_file:
            creds = pickle.load(token_file)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
            creds = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(token_path, "wb") as token_file:
            pickle.dump(creds, token_file)

    return creds


def get_client() -> BetaAnalyticsDataClient:
    if not GA4_PROPERTY_ID:
        raise ValueError("Missing GA4_PROPERTY_ID in .env")

    return BetaAnalyticsDataClient(credentials=get_credentials())


def _run_report(
    client: BetaAnalyticsDataClient,
    *,
    dimensions: List[str],
    metrics: List[str],
    start_date: str = "yesterday",
    end_date: str = "today",
    limit: int = 25,
) -> Any:
    request = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        dimensions=[Dimension(name=d) for d in dimensions],
        metrics=[Metric(name=m) for m in metrics],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        limit=limit,
    )
    return client.run_report(request)


def _rows_to_dicts(response: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    dimension_headers = [h.name for h in response.dimension_headers]
    metric_headers = [h.name for h in response.metric_headers]

    for row in response.rows:
        item: Dict[str, Any] = {}

        for i, header in enumerate(dimension_headers):
            item[header] = row.dimension_values[i].value

        for i, header in enumerate(metric_headers):
            raw = row.metric_values[i].value
            try:
                item[header] = float(raw) if "." in raw else int(raw)
            except Exception:
                item[header] = raw

        out.append(item)

    return out


def infer_state_from_path(path: str) -> str:
    clean = (path or "").strip("/").lower()
    if not clean:
        return "Homepage"
    first = clean.split("/")[0]
    return STATE_SLUGS.get(first, "Other")


def infer_page_type(path: str) -> str:
    clean = (path or "").strip("/").lower()

    if not clean:
        return "homepage"
    if clean.startswith("research"):
        return "research"
    if clean.startswith("reports"):
        return "reports"
    if clean.startswith("data-center"):
        return "data_center"
    if clean.startswith("dashboards"):
        return "dashboard"
    if clean.startswith("comparison"):
        return "comparison"
    if clean.startswith("methodology"):
        return "methodology"
    if clean.startswith("glossary"):
        return "glossary"
    if "/trends/" in clean or clean.endswith("trends"):
        return "trend"
    if clean.count("/") >= 2:
        return "service_or_county_detail"
    if clean.split("/")[0] in STATE_SLUGS:
        return "state_hub"

    return "other"


def get_traffic_summary(
    client: Optional[BetaAnalyticsDataClient] = None,
    start_date: str = "yesterday",
    end_date: str = "today",
) -> Dict[str, Any]:
    client = client or get_client()

    response = _run_report(
        client,
        dimensions=[],
        metrics=["activeUsers", "sessions", "screenPageViews", "engagedSessions", "averageSessionDuration"],
        start_date=start_date,
        end_date=end_date,
        limit=1,
    )

    rows = _rows_to_dicts(response)

    if not rows:
        return {
            "active_users": 0,
            "sessions": 0,
            "page_views": 0,
            "engaged_sessions": 0,
            "average_session_duration": 0,
            "engagement_rate": 0,
        }

    r = rows[0]
    sessions = int(r.get("sessions", 0) or 0)
    engaged = int(r.get("engagedSessions", 0) or 0)

    return {
        "active_users": int(r.get("activeUsers", 0) or 0),
        "sessions": sessions,
        "page_views": int(r.get("screenPageViews", 0) or 0),
        "engaged_sessions": engaged,
        "average_session_duration": round(float(r.get("averageSessionDuration", 0) or 0), 1),
        "engagement_rate": round((engaged / max(sessions, 1)) * 100, 1),
    }


def get_event_counts(
    client: Optional[BetaAnalyticsDataClient] = None,
    start_date: str = "yesterday",
    end_date: str = "today",
) -> Dict[str, int]:
    client = client or get_client()

    response = _run_report(
        client,
        dimensions=["eventName"],
        metrics=["eventCount"],
        start_date=start_date,
        end_date=end_date,
        limit=250,
    )

    rows = _rows_to_dicts(response)
    return {r["eventName"]: int(r.get("eventCount", 0) or 0) for r in rows}


def get_funnel_summary(
    client: Optional[BetaAnalyticsDataClient] = None,
    start_date: str = "yesterday",
    end_date: str = "today",
) -> Dict[str, Any]:
    client = client or get_client()
    events = get_event_counts(client, start_date, end_date)

    page_views = events.get("page_view", 0)
    q_start = events.get("questionnaire_start", 0)
    q_complete = events.get("questionnaire_complete", 0)
    booking = events.get("calendly_booking", 0)
    checkout = events.get("stripe_checkout_started", 0)
    payment = events.get("stripe_payment_success", 0)

    def rate(n: int, d: int) -> float:
        return round((n / max(d, 1)) * 100, 1)

    return {
        "steps": [
            {"step": "Page views", "event": "page_view", "count": page_views, "drop_from_previous_pct": None},
            {"step": "Questionnaire started", "event": "questionnaire_start", "count": q_start, "drop_from_previous_pct": round(100 - rate(q_start, page_views), 1) if page_views else None},
            {"step": "Questionnaire completed", "event": "questionnaire_complete", "count": q_complete, "drop_from_previous_pct": round(100 - rate(q_complete, q_start), 1) if q_start else None},
            {"step": "Calendly booked", "event": "calendly_booking", "count": booking, "drop_from_previous_pct": round(100 - rate(booking, q_complete), 1) if q_complete else None},
            {"step": "Stripe checkout started", "event": "stripe_checkout_started", "count": checkout, "drop_from_previous_pct": round(100 - rate(checkout, booking), 1) if booking else None},
            {"step": "Payment success", "event": "stripe_payment_success", "count": payment, "drop_from_previous_pct": round(100 - rate(payment, checkout), 1) if checkout else None},
        ],
        "page_views": page_views,
        "questionnaire_start": q_start,
        "questionnaire_complete": q_complete,
        "calendly_booking": booking,
        "stripe_checkout_started": checkout,
        "stripe_payment_success": payment,
        "landing_to_questionnaire_rate": rate(q_start, page_views),
        "questionnaire_completion_rate": rate(q_complete, q_start),
        "booking_to_payment_rate": rate(payment, booking),
        "checkout_conversion_rate": rate(payment, checkout),
        "estimated_revenue": payment * CASE_REVIEW_VALUE,
    }


def get_top_pages(
    client: Optional[BetaAnalyticsDataClient] = None,
    start_date: str = "yesterday",
    end_date: str = "today",
    limit: int = 10,
) -> List[Dict[str, Any]]:
    client = client or get_client()

    response = _run_report(
        client,
        dimensions=["pagePath", "pageTitle"],
        metrics=["screenPageViews", "activeUsers", "averageSessionDuration"],
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )

    rows = _rows_to_dicts(response)

    for r in rows:
        r["state"] = infer_state_from_path(r.get("pagePath", ""))
        r["page_type"] = infer_page_type(r.get("pagePath", ""))
        r["averageSessionDuration"] = round(float(r.get("averageSessionDuration", 0) or 0), 1)

    return rows


def get_traffic_sources(
    client: Optional[BetaAnalyticsDataClient] = None,
    start_date: str = "yesterday",
    end_date: str = "today",
    limit: int = 10,
) -> List[Dict[str, Any]]:
    client = client or get_client()

    response = _run_report(
        client,
        dimensions=["sessionSourceMedium"],
        metrics=["sessions", "activeUsers", "engagedSessions"],
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )

    rows = _rows_to_dicts(response)

    for r in rows:
        sessions = int(r.get("sessions", 0) or 0)
        engaged = int(r.get("engagedSessions", 0) or 0)
        r["engagement_rate"] = round((engaged / max(sessions, 1)) * 100, 1)

    return rows


def get_device_breakdown(
    client: Optional[BetaAnalyticsDataClient] = None,
    start_date: str = "yesterday",
    end_date: str = "today",
) -> List[Dict[str, Any]]:
    client = client or get_client()

    response = _run_report(
        client,
        dimensions=["deviceCategory"],
        metrics=["sessions", "activeUsers", "engagedSessions"],
        start_date=start_date,
        end_date=end_date,
        limit=10,
    )

    rows = _rows_to_dicts(response)

    for r in rows:
        sessions = int(r.get("sessions", 0) or 0)
        engaged = int(r.get("engagedSessions", 0) or 0)
        r["engagement_rate"] = round((engaged / max(sessions, 1)) * 100, 1)

    return rows


def get_state_performance(
    client: Optional[BetaAnalyticsDataClient] = None,
    start_date: str = "yesterday",
    end_date: str = "today",
) -> List[Dict[str, Any]]:
    client = client or get_client()

    response = _run_report(
        client,
        dimensions=["pagePath"],
        metrics=["screenPageViews", "activeUsers", "eventCount"],
        start_date=start_date,
        end_date=end_date,
        limit=500,
    )

    rows = _rows_to_dicts(response)
    state_map: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        state = infer_state_from_path(r.get("pagePath", ""))

        if state not in state_map:
            state_map[state] = {
                "state": state,
                "page_views": 0,
                "active_users": 0,
                "event_count": 0,
            }

        state_map[state]["page_views"] += int(r.get("screenPageViews", 0) or 0)
        state_map[state]["active_users"] += int(r.get("activeUsers", 0) or 0)
        state_map[state]["event_count"] += int(r.get("eventCount", 0) or 0)

    return sorted(state_map.values(), key=lambda x: x["page_views"], reverse=True)


def get_report_engagement(
    client: Optional[BetaAnalyticsDataClient] = None,
    start_date: str = "yesterday",
    end_date: str = "today",
    limit: int = 10,
) -> Dict[str, Any]:
    client = client or get_client()

    report_paths = ["/research", "/reports", "/data-center", "/dashboards", "/comparison"]

    pages = get_top_pages(client, start_date, end_date, limit=50)

    report_pages = [
        p for p in pages
        if any((p.get("pagePath") or "").startswith(prefix) for prefix in report_paths)
    ][:limit]

    events = get_event_counts(client, start_date, end_date)

    return {
        "top_research_pages": report_pages,
        "report_view": events.get("report_view", 0),
        "report_download": events.get("report_download", 0),
        "dashboard_interaction": events.get("dashboard_interaction", 0),
        "newsletter_signup": events.get("newsletter_signup", 0),
    }


def get_daily_ga4_summary(
    start_date: str = "yesterday",
    end_date: str = "today",
) -> GA4MetricResult:
    try:
        client = get_client()

        traffic = get_traffic_summary(client, start_date, end_date)
        funnel = get_funnel_summary(client, start_date, end_date)
        top_pages = get_top_pages(client, start_date, end_date)
        sources = get_traffic_sources(client, start_date, end_date)
        devices = get_device_breakdown(client, start_date, end_date)
        states = get_state_performance(client, start_date, end_date)
        reports = get_report_engagement(client, start_date, end_date)

        return GA4MetricResult(
            ok=True,
            data={
                "date_range": {"start_date": start_date, "end_date": end_date},
                "traffic": traffic,
                "funnel": funnel,
                "top_pages": top_pages,
                "sources": sources,
                "devices": devices,
                "states": states,
                "reports": reports,
            },
        )

    except Exception as e:
        return GA4MetricResult(
            ok=False,
            data={
                "traffic": {},
                "funnel": {},
                "top_pages": [],
                "sources": [],
                "devices": [],
                "states": [],
                "reports": {},
            },
            error=str(e),
        )


def fetch_ga4_metrics(
    start_date: str = "yesterday",
    end_date: str = "today",
) -> dict:
    """
    Compatibility wrapper for app.workers.daily_summary.

    daily_summary.py expects a flat dictionary and uses ga4.get(...).
    get_daily_ga4_summary() returns a GA4MetricResult object.
    This wrapper flattens the full result so the summary does not crash.
    """
    result = get_daily_ga4_summary(start_date=start_date, end_date=end_date)

    if not result.ok:
        return {
            "users": 0,
            "sessions": 0,
            "page_views": 0,
            "engagement_rate": 0,
            "top_source": "",
            "top_medium": "",
            "top_landing_page": "GA4 error",
            "top_landing_page_views": 0,
            "questionnaire_start": 0,
            "questionnaire_complete": 0,
            "calendly_booking": 0,
            "stripe_checkout_started": 0,
            "stripe_payment_success": 0,
            "estimated_revenue": 0,
            "error": result.error,
        }

    data = result.data
    traffic = data.get("traffic", {})
    funnel = data.get("funnel", {})
    sources = data.get("sources", [])
    top_pages = data.get("top_pages", [])
    devices = data.get("devices", [])
    states = data.get("states", [])
    reports = data.get("reports", {})

    top_source = ""
    top_medium = ""

    if sources:
        src = str(sources[0].get("sessionSourceMedium", "") or "")
        if " / " in src:
            top_source, top_medium = src.split(" / ", 1)
        else:
            top_source = src

    top_landing_page = "—"
    top_landing_page_views = 0

    if top_pages:
        top_landing_page = top_pages[0].get("pagePath", "—") or "—"
        top_landing_page_views = int(top_pages[0].get("screenPageViews", 0) or 0)

    top_device = devices[0].get("deviceCategory", "") if devices else ""
    top_state = states[0].get("state", "") if states else ""

    return {
        "users": traffic.get("active_users", 0),
        "sessions": traffic.get("sessions", 0),
        "page_views": traffic.get("page_views", 0),
        "engaged_sessions": traffic.get("engaged_sessions", 0),
        "avg_session_duration": traffic.get("average_session_duration", 0),
        "engagement_rate": traffic.get("engagement_rate", 0),

        "top_source": top_source,
        "top_medium": top_medium,
        "top_landing_page": top_landing_page,
        "top_landing_page_views": top_landing_page_views,
        "top_device": top_device,
        "top_state": top_state,

        "questionnaire_start": funnel.get("questionnaire_start", 0),
        "questionnaire_complete": funnel.get("questionnaire_complete", 0),
        "calendly_booking": funnel.get("calendly_booking", 0),
        "stripe_checkout_started": funnel.get("stripe_checkout_started", 0),
        "stripe_payment_success": funnel.get("stripe_payment_success", 0),
        "landing_to_questionnaire_rate": funnel.get("landing_to_questionnaire_rate", 0),
        "questionnaire_completion_rate": funnel.get("questionnaire_completion_rate", 0),
        "booking_to_payment_rate": funnel.get("booking_to_payment_rate", 0),
        "checkout_conversion_rate": funnel.get("checkout_conversion_rate", 0),
        "estimated_revenue": funnel.get("estimated_revenue", 0),

        "report_view": reports.get("report_view", 0),
        "report_download": reports.get("report_download", 0),
        "dashboard_interaction": reports.get("dashboard_interaction", 0),
        "newsletter_signup": reports.get("newsletter_signup", 0),

        "top_pages": top_pages,
        "sources": sources,
        "devices": devices,
        "states": states,
        "reports": reports,
        "funnel_steps": funnel.get("steps", []),
        "error": None,
    }


def print_summary(summary: GA4MetricResult) -> None:
    print("\nGA4 Daily Summary")
    print("=" * 60)

    if not summary.ok:
        print("ERROR:", summary.error)
        return

    data = summary.data
    traffic = data["traffic"]
    funnel = data["funnel"]

    print(f"Users       : {traffic.get('active_users', 0)}")
    print(f"Sessions    : {traffic.get('sessions', 0)}")
    print(f"Page views  : {traffic.get('page_views', 0)}")
    print(f"Engagement  : {traffic.get('engagement_rate', 0)}%")
    print(f"Payments    : {funnel.get('stripe_payment_success', 0)}")
    print(f"Revenue est.: ${funnel.get('estimated_revenue', 0):,.0f}")

    print("\nFunnel")
    for step in funnel.get("steps", []):
        drop = step.get("drop_from_previous_pct")
        drop_txt = "—" if drop is None else f"{drop}% drop"
        print(f"  {step['step']:<28} {step['count']:>5}   {drop_txt}")

    print("\nTop Pages")
    for p in data.get("top_pages", [])[:5]:
        print(f"  {p.get('screenPageViews', 0):>5}  {p.get('pagePath', '')}")

    print("\nTop Sources")
    for s in data.get("sources", [])[:5]:
        print(f"  {s.get('sessions', 0):>5}  {s.get('sessionSourceMedium', '')}")


if __name__ == "__main__":
    result = get_daily_ga4_summary()
    print_summary(result)
