from datetime import date

HIGH_VALUE_KEYWORDS = [
    "roof", "roofing", "pool", "addition", "renovation", "alteration",
    "construction", "remodel", "repair", "electrical", "plumbing", "hvac", "solar"
]


def days_old(dt):
    if not dt:
        return 9999
    return (date.today() - dt).days


def keyword_score(description: str) -> int:
    if not description:
        return 0
    text = description.lower()
    hits = sum(1 for kw in HIGH_VALUE_KEYWORDS if kw in text)
    return min(hits * 4, 16)


def recency_points(days: int, max_points: int) -> int:
    if days <= 7:
        return max_points
    if days <= 30:
        return int(max_points * 0.75)
    if days <= 60:
        return int(max_points * 0.5)
    if days <= 90:
        return int(max_points * 0.25)
    return 0


def confidence_points(confidence: str) -> int:
    if confidence == "high":
        return 20
    if confidence == "medium":
        return 12
    return 0


def amount_points(amount) -> int:
    if amount is None:
        return 0
    amt = float(amount)
    if amt >= 50000:
        return 30
    if amt >= 20000:
        return 22
    if amt > 0:
        return 14
    return 0


def score_lead(permit_date, lien_date, permit_description, match_confidence, lien_amount=None):
    score = 0
    score += recency_points(days_old(permit_date), 25)
    score += recency_points(days_old(lien_date), 15)
    score += confidence_points(match_confidence)
    score += keyword_score(permit_description)
    score += amount_points(lien_amount)
    if permit_date and lien_date:
        score += 10
    return min(score, 100)
