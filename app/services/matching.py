from rapidfuzz import fuzz
import re

NOISE_TOKENS = {
    "llc", "inc", "corp", "ltd", "co", "company", "companies",
    "lp", "llp", "pa", "pl", "na", "nv",
    "associates", "association", "group", "holdings", "holding",
    "enterprises", "enterprise", "services", "service",
    "properties", "property", "partners", "partnership",
    "management", "investments", "investment", "realty",
    "trust", "foundation", "solutions", "consulting",
    "real", "estate", "apartments", "apartment",
    "homes", "home", "house", "housing",
    "construction", "builders", "builder", "development",
    "the", "and", "of", "at", "in", "for",
}

BIZ_MARKERS = {
    "LLC", "INC", "CORP", "LTD", "LP", "LLP", "ASSN", "ASSOCIATION",
    "PARTNERS", "CONSTRUCTION", "HOLDINGS",
}


def is_business_name(raw_name: str) -> bool:
    if not raw_name:
        return False
    return any(m in raw_name.upper() for m in BIZ_MARKERS)


def normalize_name(name: str) -> str:
    if not name:
        return ""
    name = name.upper().strip()
    name = re.sub(r"[^\w\s]", " ", name)
    tokens = [t for t in name.split() if t.lower() not in NOISE_TOKENS]
    return " ".join(tokens).strip()


def meaningful_token_count(name: str) -> int:
    if not name:
        return 0
    return len([t for t in name.upper().split() if t.lower() not in NOISE_TOKENS])


def _low_match() -> dict:
    return {
        "name_score":       0,
        "address_score":    0,
        "match_score":      0.0,
        "match_confidence": "low",
        "address_mode":     "name_only",
    }


def calculate_match(name1: str, name2: str, addr1: str, addr2: str) -> dict:
    """
    Match permit owner to lien debtor.

    Key rule for multi-token names: BOTH first token AND last token
    must score >= 65 independently. No fallbacks. This prevents:
      - Last-name-only: "Maureen Davis" vs "Lauren Davis" (first=~54 → reject)
      - First-name-only: "Bruce Orear" vs "Bruce Morey" (last=~31 → reject)
      - Different people: "Edward Mitchell" vs "Harold Mitchell" (first=~36 → reject)

    Middle initials are handled by token_sort_ratio which reorders tokens,
    so "Jose M Rivera" vs "Jose Rivera" scores high on token_sort.
    """
    n1 = normalize_name(name1)
    n2 = normalize_name(name2)
    a1 = (addr1 or "").strip()
    a2 = (addr2 or "").strip()

    # Reject empty/short
    if len(n1) < 3 or len(n2) < 3:
        return _low_match()
    if meaningful_token_count(name1) < 1 or meaningful_token_count(name2) < 1:
        return _low_match()

    # Never match person to business entity
    if is_business_name(name1) != is_business_name(name2):
        return _low_match()

    token_sort = fuzz.token_sort_ratio(n1, n2)
    partial    = fuzz.partial_ratio(n1, n2)

    # Suppress partial_ratio for very different length names
    len_ratio  = min(len(n1), len(n2)) / max(len(n1), len(n2)) if max(len(n1), len(n2)) > 0 else 0
    name_score = token_sort if len_ratio < 0.4 else max(token_sort, partial)

    # For multi-token names: BOTH first AND last must independently score >= 65
    # No fallback — if either fails, reject immediately
    n1_tokens = n1.split()
    n2_tokens = n2.split()
    if len(n1_tokens) >= 2 and len(n2_tokens) >= 2:
        first_score = fuzz.ratio(n1_tokens[0],  n2_tokens[0])
        last_score  = fuzz.ratio(n1_tokens[-1], n2_tokens[-1])
        if first_score < 80 or last_score < 65:
            return _low_match()

    # Score
    both_have_address = bool(a1) and bool(a2)
    if both_have_address:
        addr_score       = fuzz.token_sort_ratio(a1, a2)
        combined         = round((name_score * 0.6) + (addr_score * 0.4), 2)
        high_threshold   = 85
        medium_threshold = 72
    else:
        addr_score       = 0
        combined         = round(name_score * 0.9, 2)
        high_threshold   = 83
        medium_threshold = 80

    if combined >= high_threshold:
        confidence = "high"
    elif combined >= medium_threshold:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "name_score":       name_score,
        "address_score":    addr_score,
        "match_score":      combined,
        "match_confidence": confidence,
        "address_mode":     "full" if both_have_address else "name_only",
    }
