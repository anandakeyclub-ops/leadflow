"""
shared_intelligence.py
======================
Shared intelligence + coordination layer for the TaxCase Review content engine.

Imported by BOTH social_media_poster.py and reel_generator.py so the two
content systems learn from each other and stop stepping on each other.

It does three jobs:
  1. Unified performance — read reel + social history into one shape, then
     surface the winning hook categories, emotional drivers, and CTA styles.
  2. Content opportunities — a backlog of high-performing topics worth turning
     into more content (content_opportunities.json).
  3. Cross-script coordination — a shared daily log so the reel script and the
     social script don't both post the same county/trade angle on the same day
     (daily_content_log.json).

All functions are defensive: missing files, partial schemas, and bad JSON never
raise — they degrade to empty results so neither poster ever crashes on import
or call.

Data sources (all in the leadflow root):
  reel_log.json          — reel render/post log (sparse)
  reel_performance.json  — richer reel metrics (hook, emotion, cta, engagement)
  post_analytics.json    — social post analytics (quality_total, hook_category)
  content_opportunities.json  — written here
  daily_content_log.json      — written here
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

# Resolve data files relative to this module so it works regardless of cwd.
BASE = Path(__file__).resolve().parent

REEL_LOG_FILE        = BASE / "reel_log.json"
REEL_PERFORMANCE_FILE = BASE / "reel_performance.json"
SOCIAL_ANALYTICS_FILE = BASE / "post_analytics.json"
CONTENT_OPPORTUNITIES_FILE = BASE / "content_opportunities.json"
DAILY_CONTENT_LOG_FILE     = BASE / "daily_content_log.json"

HIGH_PRIORITY = 85  # opportunities at/above this priority are "high priority"


# ── JSON helpers (never raise) ──────────────────────────────────────────────
def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json(path: Path, data) -> bool:
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def _num(value):
    """Coerce to float, or None if not a usable number."""
    if value is None:
        return None
    try:
        f = float(value)
        return f
    except (TypeError, ValueError):
        return None


def _sum_present(record: dict, fields: list) -> float | None:
    """Sum numeric fields that are present and non-null. None if none present."""
    total = 0.0
    found = False
    for f in fields:
        v = _num(record.get(f))
        if v is not None:
            total += v
            found = True
    return total if found else None


# ── Unified performance ─────────────────────────────────────────────────────
def _reel_record(r: dict) -> dict:
    return {
        "source":     "reel",
        "type":       r.get("reel_type", ""),
        "quality":    _num(r.get("quality_score")) or 0.0,
        "hook":       (r.get("hook_type") or "").strip(),
        "emotion":    (r.get("emotional_driver") or "").strip(),
        "cta":        (r.get("cta_strategy") or "").strip(),
        "format":     (r.get("reel_format") or "").strip(),
        "trade":      (r.get("trade") or "").strip(),
        "county":     (r.get("county") or "").strip(),
        "state":      (r.get("state") or "").strip(),
        "engagement": _sum_present(r, ["views", "likes", "comments", "shares", "saves"]),
        "conversion": _sum_present(r, ["clicks", "quiz_starts"]),
        "date":       r.get("date", ""),
        "video_id":   r.get("video_id", ""),
    }


def _social_record(r: dict) -> dict:
    return {
        "source":     "social",
        "type":       r.get("post_type", ""),
        "quality":    _num(r.get("quality_total")) or 0.0,
        "hook":       (r.get("hook_category") or "").strip(),
        "emotion":    "",  # social analytics has no explicit emotional driver
        "cta":        "",
        "format":     (r.get("platform") or "").strip(),
        "trade":      "",
        "county":     (r.get("county") or "").strip(),
        "state":      (r.get("state") or "").strip(),
        "engagement": _sum_present(r, ["reactions", "comments", "shares", "clicks"]),
        "conversion": _sum_present(r, ["clicks"]),
        "date":       r.get("date", ""),
        "video_id":   "",
    }


def load_shared_performance() -> dict:
    """
    Read reel_log.json + the social analytics file (plus reel_performance.json
    for richer reel signals) and return one unified performance structure:
        {"records": [...], "reel_count": int, "social_count": int}
    Reel rows are de-duplicated by video_id (performance file wins over log).
    """
    reel_perf = _load_json(REEL_PERFORMANCE_FILE, []) or []
    reel_log  = _load_json(REEL_LOG_FILE, []) or []
    social    = _load_json(SOCIAL_ANALYTICS_FILE, []) or []

    records: list[dict] = []
    seen_ids: set[str] = set()

    # Richer reel performance file first.
    for r in reel_perf:
        if not isinstance(r, dict):
            continue
        rec = _reel_record(r)
        records.append(rec)
        if rec["video_id"]:
            seen_ids.add(rec["video_id"])

    # Then any reel_log rows not already represented.
    for r in reel_log:
        if not isinstance(r, dict):
            continue
        vid = r.get("video_id", "")
        if vid and vid in seen_ids:
            continue
        records.append(_reel_record(r))

    for r in social:
        if isinstance(r, dict):
            records.append(_social_record(r))

    reel_count   = sum(1 for r in records if r["source"] == "reel")
    social_count = sum(1 for r in records if r["source"] == "social")
    return {"records": records, "reel_count": reel_count,
            "social_count": social_count}


def _quality_score(records: list) -> float:
    qs = [r["quality"] for r in records if r["quality"]]
    avg_q = sum(qs) / len(qs) if qs else 0.0
    eng = [r["engagement"] for r in records if r["engagement"]]
    avg_eng = sum(eng) / len(eng) if eng else 0.0
    # Quality dominates; engagement is a capped bonus (real metrics are sparse).
    return avg_q + min(avg_eng, 50.0)


def _conversion_score(records: list) -> float:
    conv = [r["conversion"] for r in records if r["conversion"]]
    avg_conv = sum(conv) / len(conv) if conv else 0.0
    qs = [r["quality"] for r in records if r["quality"]]
    avg_q = sum(qs) / len(qs) if qs else 0.0
    # Conversion dominates ranking; quality breaks ties.
    return avg_conv * 10.0 + avg_q


def _rank_by(records: list, key: str, scorer, top_n: int) -> list[str]:
    groups: dict[str, list] = {}
    for r in records:
        val = (r.get(key) or "").strip()
        if val:
            groups.setdefault(val, []).append(r)
    ranked = sorted(groups.items(), key=lambda kv: scorer(kv[1]), reverse=True)
    return [name for name, _ in ranked[:top_n]]


def _filter_records(records: list, trade=None, county=None) -> list:
    out = records
    if trade:
        t = str(trade).lower()
        out = [r for r in out if t in r.get("trade", "").lower()] or out
    if county:
        c = str(county).lower()
        filtered = [r for r in out if c in r.get("county", "").lower()]
        out = filtered or out  # fall back to unfiltered if nothing local yet
    return out


def get_winning_hooks(trade=None, county=None, top_n: int = 5) -> list[str]:
    """Top-performing hook categories from combined reel + social data.
    Optionally narrowed to a trade and/or county (falls back to global if the
    narrowed slice has no data yet)."""
    data = load_shared_performance()["records"]
    data = _filter_records(data, trade=trade, county=county)
    return _rank_by(data, "hook", _quality_score, top_n)


def get_winning_emotions(top_n: int = 5) -> list[str]:
    """Top-performing emotional drivers from combined data (reel-sourced)."""
    data = load_shared_performance()["records"]
    return _rank_by(data, "emotion", _quality_score, top_n)


def get_winning_cta_styles(top_n: int = 5) -> list[str]:
    """Top CTA styles ranked by conversion (clicks/quiz starts), quality tie-break."""
    data = load_shared_performance()["records"]
    return _rank_by(data, "cta", _conversion_score, top_n)


# ── Content opportunities ───────────────────────────────────────────────────
def log_content_opportunity(topic: str, source: str, engagement, url: str = "") -> bool:
    """
    Append a high-performing piece to content_opportunities.json so it can be
    turned into more content. `engagement` is a numeric signal (e.g. a viral
    quality score or real engagement count) and becomes the opportunity's
    priority. Exact duplicates (same topic + url, still unactioned) are merged
    rather than re-appended, keeping the backlog clean across daily runs.
    """
    opps = _load_json(CONTENT_OPPORTUNITIES_FILE, [])
    if not isinstance(opps, list):
        opps = []
    priority = _num(engagement) or 0.0
    topic = (topic or "").strip()
    url = (url or "").strip()

    for o in opps:
        if (isinstance(o, dict) and not o.get("actioned")
                and o.get("topic") == topic and o.get("url") == url):
            # Already queued — keep the strongest signal.
            o["priority"] = max(_num(o.get("priority")) or 0.0, priority)
            o["engagement"] = max(_num(o.get("engagement")) or 0.0, priority)
            o["date"] = date.today().isoformat()
            _write_json(CONTENT_OPPORTUNITIES_FILE, opps[-500:])
            return True

    opps.append({
        "topic":      topic,
        "source":     source,
        "engagement": priority,
        "url":        url,
        "priority":   priority,
        "actioned":   False,
        "date":       date.today().isoformat(),
    })
    return _write_json(CONTENT_OPPORTUNITIES_FILE, opps[-500:])


def load_content_opportunities() -> list[dict]:
    """Return un-actioned opportunities sorted by priority (highest first)."""
    opps = _load_json(CONTENT_OPPORTUNITIES_FILE, [])
    if not isinstance(opps, list):
        return []
    pending = [o for o in opps if isinstance(o, dict) and not o.get("actioned")]
    pending.sort(
        key=lambda o: (_num(o.get("priority")) or 0.0, o.get("date", "")),
        reverse=True,
    )
    return pending


# ── Cross-script daily coordination ─────────────────────────────────────────
def record_daily_content(script: str, topic: str, county: str = "",
                         trade: str = "", state: str = "") -> bool:
    """Record that `script` (\"reel\" or \"social\") posted this topic/county/
    trade today, so the other script can avoid duplicating the same angle."""
    log = _load_json(DAILY_CONTENT_LOG_FILE, [])
    if not isinstance(log, list):
        log = []
    log.append({
        "date":   date.today().isoformat(),
        "script": script,
        "topic":  (topic or "").strip(),
        "county": (county or "").strip(),
        "trade":  (trade or "").strip(),
        "state":  (state or "").strip(),
    })
    return _write_json(DAILY_CONTENT_LOG_FILE, log[-200:])


def get_today_posts(exclude_script: str | None = None) -> list[dict]:
    """Today's daily-content entries, optionally excluding one script's own."""
    log = _load_json(DAILY_CONTENT_LOG_FILE, [])
    if not isinstance(log, list):
        return []
    today = date.today().isoformat()
    return [e for e in log if isinstance(e, dict) and e.get("date") == today
            and (exclude_script is None or e.get("script") != exclude_script)]


def is_duplicate_today(this_script: str, county: str = "",
                       trade: str = "", topic: str = "") -> bool:
    """
    True if the OTHER script already posted this county/trade combo today.
    County must match; trade must match when provided on both sides.
    """
    county = (county or "").strip().lower()
    trade = (trade or "").strip().lower()
    if not county and not trade:
        return False
    for e in get_today_posts(exclude_script=this_script):
        same_county = bool(county) and e.get("county", "").strip().lower() == county
        if not same_county:
            continue
        if trade:
            if e.get("trade", "").strip().lower() == trade:
                return True
        else:
            return True
    return False
