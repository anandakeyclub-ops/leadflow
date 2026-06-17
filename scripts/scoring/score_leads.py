"""
score_leads.py
==============
Lead scoring for the email outreach pool (lien_dbpr_contacts — the table the
7-touch sequence in app/workers/send_email_sequence.py actually selects from).

Score is 0-100, summed from five factors:
  Lien amount        (25)  — nl.amount
  Lien age           (25)  — days since nl.filed_date
  Email engagement   (20)  — opens/clicks/step from email_sends
  State match        (15)  — counties.state
  Contact confidence (15)  — ldc.confidence

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


# ── Factor scorers ──────────────────────────────────────────────────────────────

def score_amount(amount) -> int:
    """25 pts. NULL/0 -> 8 (unknown). NOTE: the FL/TX/AZ pool currently has no
    amount data, so this resolves to 8 for everyone until amounts are backfilled."""
    if amount is None or amount <= 0:
        return 8
    if amount >= 100_000:
        return 25
    if amount >= 50_000:
        return 20
    if amount >= 25_000:
        return 15
    if amount >= 10_000:
        return 10
    return 5


def score_age(filed_date, today: date) -> int:
    """25 pts. Sweet spot 6-18 months (motivated, not yet resolved). Two cases
    not in the spec use documented defaults: missing date -> 10, <3 months -> 10
    (too fresh / still in shock, like the bottom of the curve)."""
    if not filed_date:
        return 10
    months = (today - filed_date).days / 30.44
    if months < 3:
        return 10            # too fresh — documented default (spec starts at 3-6)
    if months < 6:
        return 15            # 3-6 months — too fresh, still in shock
    if months < 18:
        return 25            # 6-18 months — sweet spot
    if months < 36:
        return 20            # 18-36 months
    if months <= 60:
        return 10            # 36-60 months
    return 5                 # >60 months — likely resolved or given up


def score_engagement(eng) -> int:
    """20 pts. eng = (opened, clicked, max_step) for this email, or None if the
    contact has never been emailed (a fresh step-1 candidate)."""
    if eng is None:
        return 8             # no sends yet — step-1 candidate, no engagement
    opened, clicked, max_step = eng
    if opened and clicked:
        return 20
    if opened:
        return 12
    if (max_step or 0) <= 1:
        return 8             # no opens, only step 1 sent
    return 3                 # no opens, already on step 2+


def score_state(state) -> int:
    """15 pts — pipeline/match-rate quality by state."""
    return {"FL": 15, "TX": 10, "AZ": 8, "GA": 5, "NY": 5, "IL": 5}.get(
        (state or "").upper(), 5)


def score_confidence(conf) -> int:
    """15 pts — enrichment contact confidence."""
    return {"high": 15, "medium": 8, "low": 3}.get((conf or "").lower(), 3)


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
                total = (score_amount(amount)
                         + score_age(filed_date, today)
                         + score_engagement(eng.get((email or "").lower()))
                         + score_state(state)
                         + score_confidence(conf))
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
