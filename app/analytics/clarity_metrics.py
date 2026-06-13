"""Microsoft Clarity metrics helper for LeadFlow daily summary."""
from __future__ import annotations
import os
from dataclasses import dataclass, asdict
from typing import Any
import requests
from dotenv import load_dotenv
load_dotenv()

CLARITY_API_URL = "https://www.clarity.ms/export-data/api/v1/project-live-insights"

@dataclass
class ClarityMetrics:
    sessions: int = 0
    bot_sessions: int = 0
    distinct_users: int = 0
    pages_per_session: float = 0.0
    rage_clicks: int = 0
    dead_clicks: int = 0
    excessive_scroll: int = 0
    quick_backs: int = 0
    script_errors: int = 0
    error_clicks: int = 0
    average_scroll_depth: float = 0.0
    total_time_seconds: int = 0
    active_time_seconds: int = 0
    top_page_title: str = ""
    top_referrer: str = ""
    top_page_url: str = ""
    device: str = ""
    browser: str = ""
    country: str = ""
    status: str = "ok"
    error: str = ""

def _to_int(v: Any, default: int = 0) -> int:
    try: return int(float(v)) if v not in (None, "") else default
    except Exception: return default

def _to_float(v: Any, default: float = 0.0) -> float:
    try: return float(v) if v not in (None, "") else default
    except Exception: return default

def _first(payload: list[dict], name: str) -> dict:
    for item in payload:
        if item.get("metricName") == name:
            info = item.get("information") or []
            return info[0] if info else {}
    return {}

def fetch_clarity_metrics() -> dict:
    project_id = os.getenv("CLARITY_PROJECT_ID", "").strip()
    token = os.getenv("CLARITY_API_TOKEN", "").strip()
    if not project_id or not token:
        return asdict(ClarityMetrics(status="missing_credentials", error="Missing CLARITY_PROJECT_ID or CLARITY_API_TOKEN"))
    try:
        r = requests.get(CLARITY_API_URL, params={"projectId": project_id}, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=20)
        if r.status_code != 200:
            return asdict(ClarityMetrics(status="error", error=f"Clarity API {r.status_code}: {r.text[:300]}"))
        payload = r.json()
        traffic = _first(payload, "Traffic")
        scroll = _first(payload, "ScrollDepth")
        engagement = _first(payload, "EngagementTime")
        return asdict(ClarityMetrics(
            sessions=_to_int(traffic.get("totalSessionCount")),
            bot_sessions=_to_int(traffic.get("totalBotSessionCount")),
            distinct_users=_to_int(traffic.get("distinctUserCount")),
            pages_per_session=_to_float(traffic.get("pagesPerSessionPercentage")),
            rage_clicks=_to_int(_first(payload, "RageClickCount").get("subTotal")),
            dead_clicks=_to_int(_first(payload, "DeadClickCount").get("subTotal")),
            excessive_scroll=_to_int(_first(payload, "ExcessiveScroll").get("subTotal")),
            quick_backs=_to_int(_first(payload, "QuickbackClick").get("subTotal")),
            script_errors=_to_int(_first(payload, "ScriptErrorCount").get("subTotal")),
            error_clicks=_to_int(_first(payload, "ErrorClickCount").get("subTotal")),
            average_scroll_depth=_to_float(scroll.get("averageScrollDepth")),
            total_time_seconds=_to_int(engagement.get("totalTime")),
            active_time_seconds=_to_int(engagement.get("activeTime")),
            top_page_title=str(_first(payload, "PageTitle").get("name") or ""),
            top_referrer=str(_first(payload, "ReferrerUrl").get("name") or ""),
            top_page_url=str(_first(payload, "PopularPages").get("url") or ""),
            device=str(_first(payload, "Device").get("name") or ""),
            browser=str(_first(payload, "Browser").get("name") or ""),
            country=str(_first(payload, "Country").get("name") or ""),
        ))
    except Exception as e:
        return asdict(ClarityMetrics(status="error", error=str(e)))

if __name__ == "__main__":
    import json
    print(json.dumps(fetch_clarity_metrics(), indent=2))
