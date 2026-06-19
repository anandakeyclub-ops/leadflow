"""
lien_search.py
==============
Public lien search endpoint.

GET /api/lien-search?q={query}&state={state_abbr}

Returns ONLY aggregate county/state coverage for a search term — never an
individual name, address, email, or phone. This is deliberately low-resolution
so the endpoint can be exposed publicly (e.g. a "is my county covered?" widget)
without disclosing personal data from the lien dataset.

The underlying table (lien_dbpr_contacts) does not literally have the
business_name / owner_name / first_name / last_name columns named in the spec —
it stores debtor_name / full_name, and the county lives in `counties` via
county_id. So the search columns and county source are resolved at runtime from
information_schema and the query adapts to whatever name columns exist.
"""
from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.db import get_connection, release_connection

router = APIRouter()

DISCLAIMER = "Results from public county records. Data updated weekly."
MAX_COUNTIES = 5          # cap returned counties to avoid over-disclosure
MIN_QUERY_LEN = 3

# ── Rate limiting (simple in-memory sliding window, no Redis) ───────────────────
RATE_LIMIT = 10           # requests
RATE_WINDOW = 60.0        # seconds, per IP
_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    """Resolve the caller IP, honoring the proxy header Render/Vercel set."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.time()
    with _hits_lock:
        window = [t for t in _hits.get(ip, []) if now - t < RATE_WINDOW]
        if len(window) >= RATE_LIMIT:
            _hits[ip] = window
            return True
        window.append(now)
        _hits[ip] = window
        # Opportunistic cleanup so the dict can't grow unbounded.
        if len(_hits) > 10_000:
            for k in [k for k, v in _hits.items()
                      if not v or now - v[-1] > RATE_WINDOW]:
                _hits.pop(k, None)
        return False


# ── State normalization (data mixes 'FL' and 'florida') ─────────────────────────
_ABBR_TO_NAME = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut", "DE": "delaware",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia", "PR": "puerto rico",
}
_NAME_TO_ABBR = {v: k for k, v in _ABBR_TO_NAME.items()}


def _normalize_state_abbr(raw: str) -> str:
    """Turn whatever is stored ('FL', 'florida') into a 2-letter abbr for output."""
    if not raw:
        return ""
    s = raw.strip()
    if len(s) == 2:
        return s.upper()
    return _NAME_TO_ABBR.get(s.lower(), s.upper())


def _table_columns(cur, table: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return {r[0] for r in cur.fetchall()}


@router.get("/api/lien-search")
def lien_search(request: Request, q: str = "", state: str = ""):
    q = (q or "").strip()
    state = (state or "").strip()

    if len(q) < MIN_QUERY_LEN:
        return {"found": False, "count": 0, "error": "Search term too short"}

    ip = _client_ip(request)
    if _rate_limited(ip):
        return JSONResponse(
            status_code=429,
            content={"found": False, "count": 0,
                     "error": "Rate limit exceeded. Try again in a minute."},
        )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cols = _table_columns(cur, "lien_dbpr_contacts")
            if not cols:
                return {"found": False, "count": 0, "counties": [], "states": [],
                        "disclaimer": DISCLAIMER}

            # ── Build the name-search predicate from whatever columns exist ──
            pattern = f"%{q}%"
            name_clauses: list[str] = []
            params: list = []
            for col in ("business_name", "owner_name", "debtor_name", "full_name"):
                if col in cols:
                    name_clauses.append(f"d.{col} ILIKE %s")
                    params.append(pattern)
            if "first_name" in cols and "last_name" in cols:
                name_clauses.append(
                    "(COALESCE(d.first_name,'') || ' ' || COALESCE(d.last_name,'')) ILIKE %s"
                )
                params.append(pattern)
            if not name_clauses:
                # No searchable name column — nothing we can match on.
                return {"found": False, "count": 0, "counties": [], "states": [],
                        "disclaimer": DISCLAIMER}
            where = "(" + " OR ".join(name_clauses) + ")"

            # ── County source: a county_name column, else join `counties` ──
            counties_cols = _table_columns(cur, "counties")
            has_counties_join = (
                "county_id" in cols
                and {"id", "county_name"} <= counties_cols
            )
            if "county_name" in cols:
                base_from = "lien_dbpr_contacts d"
                county_expr = "d.county_name"
            elif has_counties_join:
                base_from = "lien_dbpr_contacts d JOIN counties c ON c.id = d.county_id"
                county_expr = "c.county_name"
            else:
                base_from = "lien_dbpr_contacts d"
                county_expr = None

            # ── Optional state filter (handles 'FL' and 'florida' storage) ──
            if state and "state" in cols:
                abbr = _normalize_state_abbr(state)
                full = _ABBR_TO_NAME.get(abbr, "")
                state_vals = [abbr.lower()]
                if full:
                    state_vals.append(full)
                placeholders = ", ".join(["%s"] * len(state_vals))
                where += f" AND LOWER(d.state) IN ({placeholders})"
                params.extend(state_vals)

            # ── Total match count ──
            cur.execute(f"SELECT COUNT(*) FROM {base_from} WHERE {where}", params)
            count = cur.fetchone()[0]

            if count == 0:
                return {"found": False, "count": 0, "counties": [], "states": [],
                        "disclaimer": DISCLAIMER}

            # ── Counties (capped) ──
            counties: list[str] = []
            if county_expr:
                cur.execute(
                    f"SELECT {county_expr} AS cn, COUNT(*) AS n FROM {base_from} "
                    f"WHERE {where} AND {county_expr} IS NOT NULL "
                    f"GROUP BY {county_expr} ORDER BY n DESC LIMIT %s",
                    params + [MAX_COUNTIES],
                )
                counties = [r[0] for r in cur.fetchall()]

            # ── States (normalized to abbreviations, deduped) ──
            states_out: list[str] = []
            if "state" in cols:
                cur.execute(
                    f"SELECT DISTINCT d.state FROM {base_from} "
                    f"WHERE {where} AND d.state IS NOT NULL",
                    params,
                )
                seen = set()
                for (raw_state,) in cur.fetchall():
                    abbr = _normalize_state_abbr(raw_state)
                    if abbr and abbr not in seen:
                        seen.add(abbr)
                        states_out.append(abbr)
                states_out.sort()

            return {
                "found": True,
                "count": count,
                "counties": counties,
                "states": states_out,
                "disclaimer": DISCLAIMER,
            }
    finally:
        release_connection(conn)
