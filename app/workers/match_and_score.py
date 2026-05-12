from app.core.db import get_connection
from app.services.matching import calculate_match
from app.services.scoring import score_lead
from rapidfuzz import fuzz
import re


def normalize_for_match(name: str) -> str:
    """Aggressive normalization for cross-source name matching."""
    if not name:
        return ""
    n = name.upper().strip()
    # Remove entity suffixes
    for suffix in [" LLC", " INC", " CORP", " LTD", " PA", " PL", " CO"]:
        n = n.replace(suffix, "")
    # Remove punctuation
    n = re.sub("[^A-Za-z0-9 ]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def name_match_score(name1: str, name2: str) -> float:
    """Fuzzy name match — returns 0-100."""
    n1 = normalize_for_match(name1)
    n2 = normalize_for_match(name2)
    if not n1 or not n2 or len(n1) < 3 or len(n2) < 3:
        return 0
    token_sort = fuzz.token_sort_ratio(n1, n2)
    partial    = fuzz.partial_ratio(n1, n2)
    return max(token_sort, partial)


def address_only_match(permit_address: str, lien_address: str) -> dict:
    """
    Pure address match for permits with no owner name.
    Requires street number match + high token similarity.
    """
    if not permit_address or not lien_address:
        return {"match_score": 0, "match_confidence": "low", "address_mode": "address_only"}

    addr_score = fuzz.token_sort_ratio(
        permit_address.upper().strip(),
        lien_address.upper().strip()
    )

    p_tokens = permit_address.strip().split()
    l_tokens = lien_address.strip().split()
    if not p_tokens or not l_tokens or p_tokens[0] != l_tokens[0]:
        return {"match_score": 0, "match_confidence": "low", "address_mode": "address_only"}

    if addr_score >= 85:
        confidence = "medium"
    else:
        return {"match_score": 0, "match_confidence": "low", "address_mode": "address_only"}

    return {
        "name_score":       0,
        "address_score":    addr_score,
        "match_score":      addr_score,
        "match_confidence": confidence,
        "address_mode":     "address_only",
    }


def flexible_match(permit_owner: str, permit_biz: str,
                   lien_debtor: str, lien_biz: str,
                   permit_address: str, lien_address: str) -> dict:
    """
    Flexible matching that tries all name combinations.
    Returns best match found across all combinations.
    Goal: maximize lead count while keeping false positive rate low.
    """
    # Build candidate name pairs to try
    permit_names = [n for n in [permit_owner, permit_biz] if n and len(n.strip()) > 2]
    lien_names   = [n for n in [lien_debtor, lien_biz]   if n and len(n.strip()) > 2]

    best_score = 0
    best_pair  = (None, None)

    for pn in permit_names:
        for ln in lien_names:
            s = name_match_score(pn, ln)
            if s > best_score:
                best_score = s
                best_pair  = (pn, ln)

    if best_score < 60:
        # Try address match as last resort
        return address_only_match(permit_address, lien_address)

    # Name matched — incorporate address if available
    both_have_address = bool(permit_address) and bool(lien_address)
    if both_have_address:
        addr_score = fuzz.token_sort_ratio(
            permit_address.upper().strip(),
            lien_address.upper().strip()
        )
        combined = round((best_score * 0.6) + (addr_score * 0.4), 2)
    else:
        addr_score = 0
        combined   = round(best_score * 0.9, 2)

    # Confidence thresholds — tuned for maximum leads at acceptable quality
    if combined >= 82:
        confidence = "high"
    elif combined >= 65:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "name_score":       best_score,
        "address_score":    addr_score,
        "match_score":      combined,
        "match_confidence": confidence,
        "address_mode":     "full" if both_have_address else "name_only",
        "matched_names":    best_pair,
    }


def main():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, county_id, owner_name, business_name, address_1,
               project_description, issued_date
        FROM normalized_permits
    """)
    permits = cur.fetchall()

    cur.execute("""
        SELECT id, county_id, debtor_name, business_name, address_1,
               filed_date, amount
        FROM normalized_liens
    """)
    liens = cur.fetchall()

    print(f"Permits loaded: {len(permits)}")
    print(f"Liens loaded  : {len(liens)}")
    print(f"Mode          : LIVE WRITE")

    processed       = 0
    skipped_low     = 0
    high_count      = 0
    medium_count    = 0
    name_only_count = 0
    addr_only_count = 0

    for permit_row in permits:
        permit_id, county_id, owner_name, permit_biz, permit_address,             project_description, issued_date = permit_row

        candidates = []

        for lien_row in liens:
            lien_id, lien_county_id, debtor_name, lien_biz, lien_address,                 filed_date, amount = lien_row

            if county_id != lien_county_id:
                continue

            match = flexible_match(
                owner_name, permit_biz,
                debtor_name, lien_biz,
                permit_address, lien_address
            )

            if match["match_confidence"] == "low":
                skipped_low += 1
                continue

            lead_score = score_lead(
                permit_date=issued_date,
                lien_date=filed_date,
                permit_description=project_description,
                match_confidence=match["match_confidence"],
                lien_amount=amount,
            )

            candidates.append({
                "lien_id":    lien_id,
                "match":      match,
                "lead_score": lead_score,
                "filed_date": filed_date,
                "amount":     amount,
            })

        if not candidates:
            continue

        candidates.sort(key=lambda c: (c["match"]["match_score"], c["lead_score"]), reverse=True)
        top = candidates[0]

        cur.execute("""
            INSERT INTO matched_leads (
                county_id, permit_id, lien_id, match_score, match_confidence,
                lead_score, lead_status, enrichment_status
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'new', 'pending')
            ON CONFLICT (county_id, permit_id, lien_id)
            DO UPDATE SET
                match_score      = EXCLUDED.match_score,
                match_confidence = EXCLUDED.match_confidence,
                lead_score       = EXCLUDED.lead_score,
                updated_at       = NOW()
            """,
            (
                county_id, permit_id, top["lien_id"],
                top["match"]["match_score"],
                top["match"]["match_confidence"],
                top["lead_score"],
            ),
        )
        processed += 1

        conf = top["match"]["match_confidence"]
        if conf == "high":
            high_count += 1
        else:
            medium_count += 1
        if top["match"].get("address_mode") == "name_only":
            name_only_count += 1
        if top["match"].get("address_mode") == "address_only":
            addr_only_count += 1

    conn.commit()
    cur.close()
    conn.close()

    print(f"Candidate comparisons : {len(permits) * len(liens)}")
    print(f"Skipped (low score)   : {skipped_low}")
    print(f"Matched leads written : {processed}")
    print(f"  High confidence     : {high_count}")
    print(f"  Medium confidence   : {medium_count}")
    print(f"  Name-only matches   : {name_only_count}")
    print(f"  Address-only matches: {addr_only_count}")


if __name__ == "__main__":
    main()