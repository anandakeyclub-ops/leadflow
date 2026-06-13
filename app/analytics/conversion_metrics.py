"""Builds funnel metrics from GA4 and DB conversion data."""
from __future__ import annotations
from dataclasses import dataclass, asdict

@dataclass
class ConversionFunnel:
    landing_users: int = 0
    questionnaire_start: int = 0
    questionnaire_complete: int = 0
    calendly_booking: int = 0
    stripe_checkout_started: int = 0
    stripe_payment_success: int = 0
    revenue: float = 0.0
    visitor_to_questionnaire_pct: float = 0.0
    questionnaire_completion_pct: float = 0.0
    booking_to_checkout_pct: float = 0.0
    checkout_to_payment_pct: float = 0.0
    visitor_to_payment_pct: float = 0.0
    biggest_leak: str = "Not enough data yet"
    status: str = "ok"
    error: str = ""

def _pct(n, d):
    return round((n / d) * 100, 1) if d else 0.0

def build_conversion_funnel(ga4: dict, db_conversions: dict | None = None) -> dict:
    try:
        f = ConversionFunnel(
            landing_users=int(ga4.get("users", 0) or 0),
            questionnaire_start=int(ga4.get("questionnaire_start", 0) or 0),
            questionnaire_complete=int(ga4.get("questionnaire_complete", 0) or 0),
            calendly_booking=int(ga4.get("calendly_booking", 0) or 0),
            stripe_checkout_started=int(ga4.get("stripe_checkout_started", 0) or 0),
            stripe_payment_success=int(ga4.get("stripe_payment_success", 0) or 0),
            revenue=float((db_conversions or {}).get("revenue_today", 0.0) or 0.0),
        )
        f.visitor_to_questionnaire_pct = _pct(f.questionnaire_start, f.landing_users)
        f.questionnaire_completion_pct = _pct(f.questionnaire_complete, f.questionnaire_start)
        f.booking_to_checkout_pct = _pct(f.stripe_checkout_started, f.calendly_booking)
        f.checkout_to_payment_pct = _pct(f.stripe_payment_success, f.stripe_checkout_started)
        f.visitor_to_payment_pct = _pct(f.stripe_payment_success, f.landing_users)
        leaks = [("Landing → Questionnaire", f.visitor_to_questionnaire_pct), ("Questionnaire Start → Complete", f.questionnaire_completion_pct), ("Booking → Checkout", f.booking_to_checkout_pct), ("Checkout → Payment", f.checkout_to_payment_pct)]
        meaningful = [(name, pct) for name, pct in leaks if pct > 0]
        f.biggest_leak = min(meaningful, key=lambda x: x[1])[0] if meaningful else ("Landing → Questionnaire" if f.landing_users > 0 else "Not enough traffic yet")
        return asdict(f)
    except Exception as e:
        return asdict(ConversionFunnel(status="error", error=str(e)))
