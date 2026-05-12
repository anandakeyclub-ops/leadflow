"""
enrich_palm_beach_from_dbpr.py  (improved)
──────────────────────────────────────────
Changes vs original:
  • Scored matching: token-overlap similarity replaces early-return
    exact-only logic.  Picks highest-confidence DBPR record.
  • Only processes leads not yet enriched (skips matched_dbpr) unless
    FORCE_REENRICH=1 env var is set.
  • Match confidence stored: enrichment_status = matched_dbpr_high /
    matched_dbpr_medium / matched_dbpr_low.
  • Placeholder emails use .leadflow.invalid domain so they are trivial
    to filter in generate_email_list.py.
"""

import csv
import os
import re
from pathlib import Path
from typing import Optional

from app.core.db import get_connection


BASE_DIR = Path(__file__).resolve().parents[2]
DBPR_PATH = BASE_DIR / "data" / "reference" / "dbpr_contractors.csv"
FORCE_REENRICH = os.getenv("FORCE_REENRICH", "0") == "1"


# ── text helpers ──────────────────────────────────────────────

def norm_text(value: Optional[str]) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()

def token_overlap(a: str, b: str) -> float:
    ta, tb = set(norm_text(a).split()), set(norm_text(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

def addr_prefix_match(a: str, b: str) -> bool:
    pa, pb = a.split(), b.split()
    return len(pa) >= 2 and len(pb) >= 2 and pa[:2] == pb[:2]

def pick(row: dict, *keys: str, default: str = "") -> str:
    lowered = {str(k).strip().lower(): k for k in row.keys()}
    for key in keys:
        if key.lower() in lowered:
            return str(row.get(lowered[key.lower()], default) or default).strip()
    for actual_lower, actual in lowered.items():
        for key in keys:
            if key.lower() in actual_lower:
                return str(row.get(actual, default) or default).strip()
    return default

def build_placeholder_email(name: str, lead_id: int) -> str:
    base = norm_text(name).replace(" ", ".")
    base = re.sub(r"\.+", ".", base).strip(".") or "lead"
    return f"{base}.{lead_id}@noemail.leadflow.invalid"


# ── DBPR loading ──────────────────────────────────────────────

def load_dbpr_rows() -> list[dict]:
    if not DBPR_PATH.exists():
        raise FileNotFoundError(f"DBPR file not found: {DBPR_PATH}")
    out = []
    with DBPR_PATH.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            # Primary business name
            biz = pick(row, "name", "company_name", "business name", "licensee name")
            # Owner full name — column is owner_full_name in this dataset
            owner = pick(row, "owner_full_name", "owner_name", "owner full name", "owner")
            if not biz and not owner:
                continue
            out.append({
                "business_name":     biz,
                "owner_name":        owner,
                "email":             pick(row, "email"),
                "phone":             pick(row, "phone", "primary phone"),
                "mailing_address_1": pick(row, "address", "address 1", "mailing address"),
                "city":              pick(row, "city"),
                "state":             pick(row, "state", default="FL"),
                "zip":               pick(row, "zip", "zipcode"),
                "license_number":    pick(row, "license_number", "license number", "license no"),
                "license_type":      pick(row, "license_type", "license type", "trade"),
                "norm_biz":          norm_text(biz),
                "norm_owner":        norm_text(owner),
                "norm_addr":         norm_text(pick(row, "address", "address 1")),
            })
    return out


# ── matching ─────────────────────────────────────────────────

def score_candidate(dbpr: dict, t_biz: str, t_own: str, t_addr: str, t_debtor: str = "") -> float:
    nb      = dbpr["norm_biz"]
    no      = dbpr["norm_owner"]
    na      = dbpr["norm_addr"]

    # Match against business name
    biz_score = max(
        (1.0 if nb == t_biz else token_overlap(nb, t_biz)) if t_biz else 0.0,
        (1.0 if nb == t_own else token_overlap(nb, t_own)) if t_own else 0.0,
        (1.0 if nb == t_debtor else token_overlap(nb, t_debtor)) if t_debtor else 0.0,
    )
    # Match against owner name — key for residential permits
    owner_score = max(
        (1.0 if no == t_own else token_overlap(no, t_own)) if t_own and no else 0.0,
        (1.0 if no == t_debtor else token_overlap(no, t_debtor)) if t_debtor and no else 0.0,
    )
    name_score = max(biz_score, owner_score)

    addr_score = 0.0
    if t_addr and na:
        if na == t_addr:
            addr_score = 1.0
        elif addr_prefix_match(na, t_addr):
            addr_score = 0.6
        else:
            addr_score = token_overlap(na, t_addr) * 0.4

    return round(name_score * 0.7 + addr_score * 0.3, 4)

def choose_best_match(dbpr_rows, business_name, owner_name, address_1, debtor_name="", min_score=0.35):
    t_biz    = norm_text(business_name)
    t_own    = norm_text(owner_name)
    t_addr   = norm_text(address_1)
    t_debtor = norm_text(debtor_name)
    best, best_s = None, 0.0
    for r in dbpr_rows:
        s = score_candidate(r, t_biz, t_own, t_addr, t_debtor)
        if s > best_s:
            best_s, best = s, r
    return (best, best_s) if best_s >= min_score and best else None

def score_to_confidence(s: float) -> str:
    if s >= 0.85: return "high"
    if s >= 0.55: return "medium"
    return "low"


# ── main ─────────────────────────────────────────────────────

def main():
    dbpr_rows = load_dbpr_rows()
    print(f"  DBPR rows loaded: {len(dbpr_rows)}")

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                status_filter = "" if FORCE_REENRICH else \
                    "AND ml.enrichment_status NOT LIKE 'matched_dbpr%'"

                cur.execute(f"""
                    SELECT ml.id, np.business_name, np.owner_name, np.address_1,
                           ct.id, ct.email, nl.debtor_name
                    FROM matched_leads ml
                    JOIN normalized_permits np ON ml.permit_id = np.id
                    JOIN normalized_liens nl   ON ml.lien_id = nl.id
                    JOIN counties c            ON ml.county_id = c.id
                    LEFT JOIN contacts ct      ON ml.id = ct.lead_id
                    WHERE c.county_name = 'Palm Beach'
                    {status_filter}
                    ORDER BY ml.id
                """)
                rows = cur.fetchall()
                print(f"  Leads to process: {len(rows)}")

                upserted, unmatched = 0, 0

                for lead_id, biz, own, addr, contact_id, existing_email, debtor_name in rows:
                    result = choose_best_match(dbpr_rows, biz or "", own or "", addr or "", debtor_name or "")
                    if result is None:
                        unmatched += 1
                        # Insert placeholder so lead still appears in email list
                        # with a clearly invalid email that generate_email_list.py filters out
                        placeholder_email = build_placeholder_email(own or debtor_name or "lead", lead_id)
                        cur.execute("""
                            INSERT INTO contacts (lead_id, full_name, email, enrichment_status, last_enriched_at)
                            VALUES (%s, %s, %s, 'no_dbpr_match', NOW())
                            ON CONFLICT (lead_id) DO UPDATE SET
                                enrichment_status = 'no_dbpr_match',
                                last_enriched_at  = NOW()
                        """, (lead_id, own or debtor_name or "Unknown", placeholder_email))
                        continue

                    match, score = result
                    confidence   = score_to_confidence(score)
                    full_name    = biz or own or "Unknown"
                    email        = match["email"] or existing_email or build_placeholder_email(full_name, lead_id)

                    cur.execute("""
                        INSERT INTO contacts (
                            lead_id, full_name, primary_phone, secondary_phone,
                            email, mailing_address_1, city, state, zip,
                            enrichment_vendor, enrichment_score, enrichment_status,
                            last_enriched_at
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                        ON CONFLICT (lead_id) DO UPDATE SET
                            full_name         = EXCLUDED.full_name,
                            primary_phone     = COALESCE(EXCLUDED.primary_phone, contacts.primary_phone),
                            email             = COALESCE(EXCLUDED.email, contacts.email),
                            mailing_address_1 = COALESCE(EXCLUDED.mailing_address_1, contacts.mailing_address_1),
                            city              = COALESCE(EXCLUDED.city, contacts.city),
                            state             = COALESCE(EXCLUDED.state, contacts.state),
                            zip               = COALESCE(EXCLUDED.zip, contacts.zip),
                            enrichment_vendor  = EXCLUDED.enrichment_vendor,
                            enrichment_score   = EXCLUDED.enrichment_score,
                            enrichment_status  = EXCLUDED.enrichment_status,
                            last_enriched_at   = NOW()
                    """, (
                        lead_id, full_name, match["phone"] or None, None,
                        email,
                        match["mailing_address_1"] or addr or "",
                        match["city"] or "", match["state"] or "FL", match["zip"] or "",
                        "dbpr_csv", round(score * 100, 1),
                        f"matched_dbpr_{confidence}",
                    ))

                    cur.execute("""
                        UPDATE matched_leads
                        SET enrichment_status = %s, updated_at = NOW()
                        WHERE id = %s
                    """, (f"matched_dbpr_{confidence}", lead_id))

                    upserted += 1

        print(f"  Palm Beach leads enriched : {upserted}")
        print(f"  No DBPR match found       : {unmatched}")

    finally:
        conn.close()

if __name__ == "__main__":
    main()