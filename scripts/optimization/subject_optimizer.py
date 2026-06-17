"""
subject_optimizer.py
====================
Automatic subject-line optimizer for step-1 of the email sequence.

Three parts:
  1. Performance tracker  — recompute sends/opens/open_rate/click_rate per
     step-1 subject variant from email_sends ⋈ email_opens ⋈ email_clicks and
     upsert into subject_line_performance.
  2. AI generator         — when there are no challengers still under test, or
     the winner's 7-day open rate drops below 20%, generate 3 new variants via
     the Claude API and store them as active='ai' variants.
  3. Epsilon-greedy bandit (ε=0.1) — 90% exploit the best valid variant, 10%
     explore a random untested/low-send variant. Never sends a variant with
     >50 sends and <5% open rate.

send_email_sequence.choose_subject() calls select_variant() for step 1.

Usage:
  python scripts/optimization/subject_optimizer.py --track       # recompute standings
  python scripts/optimization/subject_optimizer.py --generate    # force-generate 3 variants
  python scripts/optimization/subject_optimizer.py --simulate 100 # bandit distribution check
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import date
from pathlib import Path

import requests

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(LEADFLOW_DIR / ".env")

from app.core.db import get_connection  # noqa: E402

CAMPAIGN_ID       = os.getenv("CAMPAIGN_ID", "lien_outreach_2026")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = "claude-sonnet-4-5"   # matches the rest of the codebase

# Bandit / validity knobs
EPSILON           = 0.1
MIN_VALID_SENDS   = 20      # below this a variant is "still testing"
KILL_SENDS        = 50      # >this sends AND
KILL_RATE         = 5.0     # <this open-rate% -> never send again
WINNER_FLOOR_RATE = 20.0    # winner 7-day open-rate% below this -> regenerate
MAX_ACTIVE_CHALLENGERS = 3  # don't pile up more than this many untested variants

# The proven seed winner (kept hardcoded as the safe fallback).
SEED_VARIANT_ID = "s1_v1"
SEED_TEMPLATE   = "Quick question about your {county} County filing"

# Historical step-1 variants that were retired — tracked but never selected.
RETIRED = {f"s1_v{i}" for i in range(2, 13)}

_CACHE = None   # process-level cache of active variants for the send run


# ── Table ────────────────────────────────────────────────────────────────────

def ensure_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS subject_line_performance (
            variant_id       TEXT PRIMARY KEY,
            subject_template TEXT,
            sends            INTEGER DEFAULT 0,
            opens            INTEGER DEFAULT 0,
            open_rate        NUMERIC DEFAULT 0,
            click_rate       NUMERIC DEFAULT 0,
            active           BOOLEAN DEFAULT TRUE,
            source           TEXT DEFAULT 'seed',
            created_at       TIMESTAMPTZ DEFAULT NOW(),
            last_updated     TIMESTAMPTZ DEFAULT NOW()
        )
    """)


def _is_active_id(vid: str) -> bool:
    return vid == SEED_VARIANT_ID or vid.startswith("s1_ai")


# ── 1. Performance tracker ────────────────────────────────────────────────────

def track_performance(conn=None) -> list[dict]:
    own = conn is None
    if own:
        conn = get_connection()
    try:
        with conn.cursor() as cur:
            ensure_table(cur)
            # Make sure the seed winner always exists and stays active.
            cur.execute("""
                INSERT INTO subject_line_performance
                    (variant_id, subject_template, source, active)
                VALUES (%s, %s, 'seed', TRUE)
                ON CONFLICT (variant_id) DO UPDATE SET subject_template = EXCLUDED.subject_template
            """, (SEED_VARIANT_ID, SEED_TEMPLATE))

            cur.execute("""
                SELECT COALESCE(es.subject_variant, 'legacy')      AS variant,
                       MIN(es.subject)                             AS example,
                       COUNT(DISTINCT es.to_email)                 AS sends,
                       COUNT(DISTINCT eo.tracking_id)              AS opens,
                       COUNT(DISTINCT ec.tracking_id)              AS clicks
                FROM email_sends es
                LEFT JOIN email_opens  eo ON eo.tracking_id = es.tracking_id
                LEFT JOIN email_clicks ec ON ec.tracking_id = es.tracking_id
                WHERE es.campaign_id = %s
                  AND es.sequence_step = 1
                  AND es.status = 'sent'
                GROUP BY 1
            """, (CAMPAIGN_ID,))
            rows = cur.fetchall()

            for vid, example, sends, opens, clicks in rows:
                sends = sends or 0
                open_rate = round((opens or 0) / sends * 100, 1) if sends else 0
                click_rate = round((clicks or 0) / sends * 100, 1) if sends else 0
                active = _is_active_id(vid)
                # Insert with computed active/template; on conflict update stats
                # only so we never clobber a generator-set template/active/source.
                cur.execute("""
                    INSERT INTO subject_line_performance
                        (variant_id, subject_template, sends, opens, open_rate,
                         click_rate, active, source, last_updated)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (variant_id) DO UPDATE SET
                        sends        = EXCLUDED.sends,
                        opens        = EXCLUDED.opens,
                        open_rate    = EXCLUDED.open_rate,
                        click_rate   = EXCLUDED.click_rate,
                        last_updated = NOW()
                """, (vid, example or SEED_TEMPLATE, sends, opens or 0, open_rate,
                      click_rate, active, "seed" if vid == SEED_VARIANT_ID else
                      ("ai" if vid.startswith("s1_ai") else "retired")))
            conn.commit()

            cur.execute("""
                SELECT variant_id, subject_template, sends, opens, open_rate,
                       click_rate, active, source
                FROM subject_line_performance
                ORDER BY active DESC, open_rate DESC NULLS LAST, sends DESC
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        if own:
            conn.close()


def _winner_7d_rate(cur) -> tuple[str, float, int]:
    """Returns (winner_variant_id, 7-day open rate %, 7-day sends) for the
    current best active variant."""
    cur.execute("""
        SELECT variant_id FROM subject_line_performance
        WHERE active = TRUE AND sends >= %s
        ORDER BY open_rate DESC NULLS LAST LIMIT 1
    """, (MIN_VALID_SENDS,))
    row = cur.fetchone()
    winner = row[0] if row else SEED_VARIANT_ID
    cur.execute("""
        SELECT COUNT(DISTINCT es.to_email),
               COUNT(DISTINCT eo.tracking_id)
        FROM email_sends es
        LEFT JOIN email_opens eo ON eo.tracking_id = es.tracking_id
        WHERE es.campaign_id = %s AND es.sequence_step = 1 AND es.status = 'sent'
          AND es.subject_variant = %s
          AND es.sent_at >= NOW() - INTERVAL '7 days'
    """, (CAMPAIGN_ID, winner))
    sends, opens = cur.fetchone()
    sends = sends or 0
    rate = round((opens or 0) / sends * 100, 1) if sends else 0.0
    return winner, rate, sends


# ── 2. AI generator ───────────────────────────────────────────────────────────

def generate_variants(winner_template: str, winner_rate: float, n: int = 3) -> list[str]:
    """Generate n new subject-line templates via the Claude API. Returns a list
    of template strings, each containing the literal {county} token."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    prompt = (
        f"Winner to beat: \"{winner_template}\" at {winner_rate}% open rate\n"
        f"Audience: contractors and small business owners with active IRS tax liens\n"
        f"Goal: curiosity + personal relevance, under 50 characters\n"
        f"Must include: the literal token {{county}} for county personalization\n"
        f"Generate {n} cold-email subject line variants designed to beat the winner.\n"
        f"Return ONLY a JSON array of {n} strings, nothing else. "
        f'Example: ["...", "...", "..."]'
    )
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": ANTHROPIC_MODEL, "max_tokens": 400,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=60,
    )
    r.raise_for_status()
    text = r.json()["content"][0]["text"].strip()
    # Tolerate code fences / surrounding prose — extract the JSON array.
    if "[" in text and "]" in text:
        text = text[text.index("["): text.rindex("]") + 1]
    variants = json.loads(text)
    out = []
    for v in variants:
        v = str(v).strip()
        if "{county}" not in v and "{state}" not in v:
            continue  # must be personalizable
        if len(v) <= 70:
            out.append(v)
    return out[:n]


def insert_ai_variants(conn, templates: list[str]) -> list[str]:
    ids = []
    with conn.cursor() as cur:
        ensure_table(cur)
        cur.execute("SELECT COUNT(*) FROM subject_line_performance WHERE variant_id LIKE 's1_ai%%'")
        start = cur.fetchone()[0]
        for i, tmpl in enumerate(templates):
            vid = f"s1_ai_{date.today().strftime('%Y%m%d')}_{start + i + 1}"
            cur.execute("""
                INSERT INTO subject_line_performance
                    (variant_id, subject_template, source, active, sends, opens)
                VALUES (%s, %s, 'ai', TRUE, 0, 0)
                ON CONFLICT (variant_id) DO NOTHING
            """, (vid, tmpl))
            ids.append(vid)
        conn.commit()
    return ids


def maybe_generate(conn=None) -> list[str]:
    """Generate challengers if there are none under test, or the winner has
    decayed below the 7-day floor. Returns new variant ids (or [])."""
    own = conn is None
    if own:
        conn = get_connection()
    try:
        with conn.cursor() as cur:
            ensure_table(cur)
            cur.execute("""
                SELECT variant_id, subject_template, sends, open_rate
                FROM subject_line_performance WHERE active = TRUE
            """)
            active = [dict(zip(["variant_id", "template", "sends", "open_rate"], r))
                      for r in cur.fetchall()]
            untested = [v for v in active if (v["sends"] or 0) < MIN_VALID_SENDS]
            winner, w7_rate, w7_sends = _winner_7d_rate(cur)

            winner_decayed = w7_sends >= MIN_VALID_SENDS and w7_rate < WINNER_FLOOR_RATE
            no_challengers = len(untested) == 0

            if len(untested) >= MAX_ACTIVE_CHALLENGERS:
                return []   # already enough under test
            if not (no_challengers or winner_decayed):
                return []

        # Pick the template/rate to beat (best valid active variant).
        valid = [v for v in active if (v["sends"] or 0) >= MIN_VALID_SENDS]
        best = max(valid, key=lambda v: v["open_rate"] or 0) if valid else \
            {"template": SEED_TEMPLATE, "open_rate": 0}
        templates = generate_variants(best["template"], best.get("open_rate") or 0)
        if not templates:
            return []
        return insert_ai_variants(conn, templates)
    finally:
        if own:
            conn.close()


# ── 3. Epsilon-greedy bandit ──────────────────────────────────────────────────

def load_active_variants(conn=None, force=False) -> list[dict]:
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    own = conn is None
    if own:
        conn = get_connection()
    try:
        with conn.cursor() as cur:
            ensure_table(cur)
            cur.execute("""
                SELECT variant_id, subject_template, COALESCE(sends,0),
                       COALESCE(open_rate,0)
                FROM subject_line_performance WHERE active = TRUE
            """)
            _CACHE = [{"variant_id": r[0], "template": r[1],
                       "sends": r[2], "open_rate": float(r[3])}
                      for r in cur.fetchall()]
    finally:
        if own:
            conn.close()
    return _CACHE


def select_variant(candidates=None, rng=None) -> tuple[str, str]:
    """Epsilon-greedy pick. Returns (variant_id, subject_template)."""
    rng = rng or random
    cands = candidates if candidates is not None else load_active_variants()
    # Safety rule: never send a proven loser.
    cands = [c for c in cands
             if not (c["sends"] > KILL_SENDS and c["open_rate"] < KILL_RATE)]
    if not cands:
        return SEED_VARIANT_ID, SEED_TEMPLATE

    valid    = [c for c in cands if c["sends"] >= MIN_VALID_SENDS]
    untested = [c for c in cands if c["sends"] < MIN_VALID_SENDS]

    if rng.random() < EPSILON and untested:        # explore
        pick = rng.choice(untested)
    elif valid:                                     # exploit
        pick = max(valid, key=lambda c: c["open_rate"])
    elif untested:
        pick = rng.choice(untested)
    else:
        pick = cands[0]
    return pick["variant_id"], pick["template"]


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_standings(rows: list[dict]):
    print(f"\n  {'variant':<22} {'src':<7} {'act':<4} {'sends':>6} {'opens':>6} {'open%':>6} {'click%':>7}")
    print(f"  {'-'*22} {'-'*7} {'-'*4} {'-'*6} {'-'*6} {'-'*6} {'-'*7}")
    for r in rows:
        print(f"  {r['variant_id'][:22]:<22} {r['source']:<7} "
              f"{'Y' if r['active'] else 'n':<4} {r['sends']:>6} {r['opens']:>6} "
              f"{float(r['open_rate'] or 0):>6.1f} {float(r['click_rate'] or 0):>7.1f}")


def main():
    ap = argparse.ArgumentParser(description="Subject line optimizer")
    ap.add_argument("--track", action="store_true", help="Recompute standings")
    ap.add_argument("--generate", action="store_true", help="Force-generate 3 AI variants")
    ap.add_argument("--simulate", type=int, metavar="N", help="Run an N-iteration bandit sim")
    args = ap.parse_args()

    if args.track or not (args.generate or args.simulate):
        rows = track_performance()
        print(f"\n{'='*70}\n  Subject Line Performance — standings\n{'='*70}")
        _print_standings(rows)

    if args.generate:
        from collections import Counter
        ids = maybe_generate()
        if not ids:
            # Force a generation for demonstration even if conditions not met.
            conn = get_connection()
            try:
                best = track_performance(conn)
                active_valid = [r for r in best if r["active"] and (r["sends"] or 0) >= MIN_VALID_SENDS]
                seed = active_valid[0] if active_valid else {"subject_template": SEED_TEMPLATE, "open_rate": 0}
                tmpls = generate_variants(seed["subject_template"], float(seed.get("open_rate") or 0))
                ids = insert_ai_variants(conn, tmpls)
            finally:
                conn.close()
        print(f"\n  Generated {len(ids)} AI variants:")
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                for vid in ids:
                    cur.execute("SELECT subject_template FROM subject_line_performance WHERE variant_id=%s", (vid,))
                    t = cur.fetchone()
                    print(f"    {vid}: {t[0] if t else '?'}")
        finally:
            conn.close()

    if args.simulate:
        from collections import Counter
        cands = load_active_variants(force=True)
        print(f"\n  Active candidates ({len(cands)}):")
        for c in cands:
            print(f"    {c['variant_id']:<26} sends={c['sends']:<4} open%={c['open_rate']}")
        counts = Counter()
        rng = random.Random(42)
        for _ in range(args.simulate):
            vid, _t = select_variant(cands, rng=rng)
            counts[vid] += 1
        print(f"\n  {args.simulate}-iteration bandit distribution:")
        for vid, n in counts.most_common():
            print(f"    {vid:<26} {n:>4}  ({n/args.simulate*100:.1f}%)")


if __name__ == "__main__":
    main()
