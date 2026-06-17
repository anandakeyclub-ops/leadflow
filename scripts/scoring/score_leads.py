"""
score_leads.py
==============
Lead scoring for the email outreach pool (lien_dbpr_contacts — the table the
7-touch sequence in app/workers/send_email_sequence.py actually selects from).

Score is 0-100 = sum of weight * factor, where each factor is a 0..1 tier and
WEIGHTS sets the point budget:
  Lien age           (35)  — days since nl.filed_date (best urgency proxy we have)
  Email engagement   (25)  — opens/clicks/step from email_sends
  State match        (20)  — counties.state
  Contact confidence (20)  — ldc.confidence
  Lien amount        ( 0)  — nl.amount; structurally dead for FL/AZ/TX (no source).
                            Logic kept intact — set WEIGHTS['amount']>0 to
                            reactivate once amounts are backfilled.

Writes lead_score + last_scored_at onto every email-ready row. The step-1
selector then orders by lead_score DESC so the best leads go out first.

Usage:
  python scripts/scoring/score_leads.py            # score + show distribution + top 10
  python scripts/scoring/score_leads.py --dry-run  # compute + show, don't write
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

LEADFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LEADFLOW_DIR))

from app.core.db import get_connection  # noqa: E402

CAMPAIGN_ID = os.getenv("CAMPAIGN_ID", "lien_outreach_2026")

# Email-ready pool definition — must match the step-1 selector in
# send_email_sequence.py so we score exactly the candidates that can be sent.
POOL_WHERE = (
    "ldc.email IS NOT NULL AND ldc.email != '' "
    "AND ldc.email NOT LIKE '%%@example.com'"
)


# ── Weights (point budget per factor; active weights sum to 100) ─────────────────
# Amount is 0 because no structured lien-amount source exists for FL/AZ/TX. The
# amount tier logic below is kept intact — bump WEIGHTS["amount"] back to 25 (and
# trim the others) to reactivate it once amounts are backfilled.
WEIGHTS = {
    "age":        35,
    "engagement": 25,
    "state":      20,
    "confidence": 20,
    "amount":      0,
}


# ── Factor scorers — each returns a 0..1 tier; total = sum(weight * factor) ───────

def amount_factor(amount) -> float:
    """NULL/0 -> 0.32 (the original 8/25 "unknown" baseline)."""
    if amount is None or amount <= 0:
        return 0.32
    if amount >= 100_000:
        return 1.00
    if amount >= 50_000:
        return 0.80
    if amount >= 25_000:
        return 0.60
    if amount >= 10_000:
        return 0.40
    return 0.20


def age_factor(filed_date, today: date) -> float:
    """Sweet spot 6-18 months (motivated, not yet resolved). Missing date and
    <3 months use the 0.40 baseline (too fresh / still in shock)."""
    if not filed_date:
        return 0.40
    months = (today - filed_date).days / 30.44
    if months < 3:
        return 0.40          # too fresh
    if months < 6:
        return 0.60          # 3-6 months — still in shock
    if months < 18:
        return 1.00          # 6-18 months — sweet spot
    if months < 36:
        return 0.80          # 18-36 months
    if months <= 60:
        return 0.40          # 36-60 months
    return 0.20              # >60 months — likely resolved or given up


def engagement_factor(eng) -> float:
    """eng = (opened, clicked, max_step), or None if never emailed."""
    if eng is None:
        return 0.40          # no sends yet — step-1 candidate
    opened, clicked, max_step = eng
    if opened and clicked:
        return 1.00
    if opened:
        return 0.60
    if (max_step or 0) <= 1:
        return 0.40          # no opens, only step 1 sent
    return 0.15              # no opens, already on step 2+


def state_factor(state) -> float:
    """Pipeline/match-rate quality by state."""
    return {"FL": 1.00, "TX": 0.667, "AZ": 0.533,
            "GA": 0.333, "NY": 0.333, "IL": 0.333}.get((state or "").upper(), 0.333)


def confidence_factor(conf) -> float:
    """Enrichment contact confidence."""
    return {"high": 1.00, "medium": 0.533, "low": 0.20}.get((conf or "").lower(), 0.20)


def amount_range_label(amount) -> str:
    if amount is None or amount <= 0:
        return "unknown"
    if amount >= 100_000:
        return ">=$100K"
    if amount >= 50_000:
        return "$50K-$100K"
    if amount >= 25_000:
        return "$25K-$50K"
    if amount >= 10_000:
        return "$10K-$25K"
    return "<$10K"


# ── Core ────────────────────────────────────────────────────────────────────────

def ensure_columns(cur):
    cur.execute("ALTER TABLE lien_dbpr_contacts ADD COLUMN IF NOT EXISTS lead_score INTEGER")
    cur.execute("ALTER TABLE lien_dbpr_contacts ADD COLUMN IF NOT EXISTS last_scored_at TIMESTAMPTZ")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ldc_lead_score ON lien_dbpr_contacts(lead_score DESC)")


def _engagement_map(cur) -> dict:
    """email(lower) -> (opened, clicked, max_step) across the campaign."""
    cur.execute("""
        SELECT LOWER(to_email),
               bool_or(opened_at IS NOT NULL),
               bool_or(clicked_at IS NOT NULL),
               MAX(sequence_step)
        FROM email_sends
        WHERE campaign_id = %s
        GROUP BY LOWER(to_email)
    """, (CAMPAIGN_ID,))
    return {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}


def score_all_contacts(conn=None, dry_run: bool = False) -> dict:
    """Score every email-ready contact in lien_dbpr_contacts. Returns stats."""
    own = conn is None
    if own:
        conn = get_connection()
    try:
        with conn.cursor() as cur:
            if not dry_run:
                ensure_columns(cur)
                conn.commit()

            eng = _engagement_map(cur)
            today = date.today()

            cur.execute(f"""
                SELECT ldc.id, ldc.email, nl.amount, nl.filed_date,
                       c.state, ldc.confidence
                FROM lien_dbpr_contacts ldc
                JOIN normalized_liens nl ON ldc.lien_id = nl.id
                JOIN counties c ON ldc.county_id = c.id
                WHERE {POOL_WHERE}
            """)
            rows = cur.fetchall()

            updates = []
            scores = []
            for (cid, email, amount, filed_date, state, conf) in rows:
                total = round(
                    WEIGHTS["amount"]     * amount_factor(amount)
                    + WEIGHTS["age"]        * age_factor(filed_date, today)
                    + WEIGHTS["engagement"] * engagement_factor(eng.get((email or "").lower()))
                    + WEIGHTS["state"]      * state_factor(state)
                    + WEIGHTS["confidence"] * confidence_factor(conf)
                )
                total = max(0, min(100, total))
                updates.append((cid, total))
                scores.append(total)

            if not dry_run and updates:
                from psycopg2.extras import execute_values
                with conn.cursor() as ucur:
                    execute_values(ucur, """
                        UPDATE lien_dbpr_contacts AS t
                        SET lead_score = v.score, last_scored_at = NOW()
                        FROM (VALUES %s) AS v(id, score)
                        WHERE t.id = v.id
                    """, updates, page_size=1000)
                conn.commit()

        tiers = {"80-100": 0, "60-79": 0, "40-59": 0, "<40": 0}
        for s in scores:
            if s >= 80:
                tiers["80-100"] += 1
            elif s >= 60:
                tiers["60-79"] += 1
            elif s >= 40:
                tiers["40-59"] += 1
            else:
                tiers["<40"] += 1

        return {
            "scored": len(updates),
            "rows_in_pool": len(rows),
            "avg": round(sum(scores) / len(scores), 1) if scores else 0,
            "tiers": tiers,
            "dry_run": dry_run,
        }
    finally:
        if own:
            conn.close()


def show_top(n: int = 10):
    """Print top N scored contacts — no PII (score/state/county/amount/confidence)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (LOWER(ldc.email))
                       ldc.lead_score, c.state, c.county_name, nl.amount, ldc.confidence
                FROM lien_dbpr_contacts ldc
                JOIN normalized_liens nl ON ldc.lien_id = nl.id
                JOIN counties c ON ldc.county_id = c.id
                WHERE ldc.lead_score IS NOT NULL
                ORDER BY LOWER(ldc.email), ldc.lead_score DESC NULLS LAST
            """)
            rows = cur.fetchall()
        rows.sort(key=lambda r: (r[0] or 0), reverse=True)
        print(f"\n  Top {n} scored contacts (no PII):")
        print(f"  {'score':>5}  {'state':<5} {'county':<16} {'amount':<11} confidence")
        print(f"  {'-'*5}  {'-'*5} {'-'*16} {'-'*11} {'-'*10}")
        for sc, state, county, amount, conf in rows[:n]:
            print(f"  {sc:>5}  {state or '?':<5} {(county or '?')[:16]:<16} "
                  f"{amount_range_label(amount):<11} {conf or '?'}")
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description="Lead scoring for the email pool")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute and show, but don't write to the DB")
    ap.add_argument("--top", type=int, default=10, help="How many top leads to show")
    args = ap.parse_args()

    stats = score_all_contacts(dry_run=args.dry_run)
    print(f"\n{'='*60}")
    print(f"  Lead scoring {'(DRY RUN)' if args.dry_run else 'complete'}")
    print(f"  Pool (email-ready rows): {stats['rows_in_pool']:,}")
    print(f"  Scored: {stats['scored']:,}   Average score: {stats['avg']}")
    print(f"{'='*60}")
    print(f"  Distribution:")
    for tier, label in [("80-100", "hot   "), ("60-79", "warm  "),
                        ("40-59", "cool  "), ("<40", "cold  ")]:
        c = stats["tiers"][tier]
        pct = (c / stats["rows_in_pool"] * 100) if stats["rows_in_pool"] else 0
        print(f"    {label} {tier:>7}: {c:>6,}  ({pct:4.1f}%)")

    if not args.dry_run:
        show_top(args.top)


if __name__ == "__main__":
    main()
