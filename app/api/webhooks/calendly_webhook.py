"""
app/api/webhooks/calendly_webhook.py
====================================
Native Calendly webhook receiver. Calendly POSTs its events directly here
(no Next.js transformer in between).

Route:
  POST /api/webhooks/calendly  — native Calendly invitee.created / invitee.canceled

Flow:
  1. Verify the `Calendly-Webhook-Signature` header against CALENDLY_WEBHOOK_SECRET
     (HMAC-SHA256 over "<timestamp>.<raw_body>", Stripe-style). Bad/missing → 401.
  2. Parse + transform Calendly's nested payload into the existing
     CalendlyBookingPayload shape. Unparseable / wrong shape → 422.
  3. Delegate to the existing upsert/cancel logic in calendly_booking_api
     (handle_calendly_webhook) — no duplicated DB code. Success → 200.

Register the subscription (once) via the Calendly API, pointing `url` at
https://leadflow-api-x7pf.onrender.com/api/webhooks/calendly with the same
`signing_key` you put in CALENDLY_WEBHOOK_SECRET.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from app.api.bookings.calendly_booking_api import (
    CalendlyBookingPayload,
    handle_calendly_webhook,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# Reject events older than this (seconds) to blunt replay attacks.
SIGNATURE_TOLERANCE_SECONDS = 300


# ── Signature verification ──────────────────────────────────────────────────────

def _parse_signature_header(header: str) -> tuple[Optional[str], Optional[str]]:
    """Calendly sends 'Calendly-Webhook-Signature: t=<ts>,v1=<hex>'."""
    t = v1 = None
    for part in header.split(","):
        part = part.strip()
        if part.startswith("t="):
            t = part[2:]
        elif part.startswith("v1="):
            v1 = part[3:]
    return t, v1


def verify_signature(secret: str, header: Optional[str], raw_body: bytes) -> bool:
    """
    Verify the Calendly webhook signature.

    Calendly signs HMAC-SHA256 over the string "<timestamp>.<raw_request_body>"
    using the subscription's signing_key, hex-encoded. We compare in constant
    time and reject stale timestamps.
    """
    if not header:
        return False
    t, v1 = _parse_signature_header(header)
    if not t or not v1:
        return False

    signed_payload = f"{t}.{raw_body.decode('utf-8')}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, v1):
        return False

    # Replay guard — timestamp must be recent.
    try:
        if abs(time.time() - int(t)) > SIGNATURE_TOLERANCE_SECONDS:
            logger.warning("Calendly webhook timestamp outside tolerance (replay?)")
            return False
    except ValueError:
        return False

    return True


# ── Native → internal payload transform ─────────────────────────────────────────

def _to_int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _extract_phone(payload: dict) -> Optional[str]:
    """Phone arrives as the SMS-reminder number or as a custom question."""
    phone = payload.get("text_reminder_number")
    if phone:
        return phone
    for qa in payload.get("questions_and_answers") or []:
        q = (qa.get("question") or "").lower()
        if "phone" in q or "mobile" in q or "cell" in q:
            return qa.get("answer")
    return None


def _extract_quiz(payload: dict) -> dict:
    """Map Calendly custom-question answers onto the quiz_* fields by keyword."""
    out: dict[str, str] = {}
    for qa in payload.get("questions_and_answers") or []:
        q = (qa.get("question") or "").lower()
        a = qa.get("answer")
        if not a:
            continue
        if "debt" in q:
            out["debt_range"] = a
        elif "year" in q:
            out["years_owed"] = a
        elif "return" in q or "filed" in q:
            out["returns_filed"] = a
        elif "county" in q:
            out["county"] = a
        elif "state" in q:
            out["state"] = a
    return out


def transform_calendly_payload(data: dict) -> CalendlyBookingPayload:
    """
    Convert Calendly's native webhook body into the existing
    CalendlyBookingPayload. Raises (KeyError / ValidationError) on bad shape,
    which the route maps to 422.
    """
    event = data["event"]                       # "invitee.created" / "invitee.canceled"
    p = data["payload"]                          # nested invitee object
    sched = p.get("scheduled_event") or {}
    tracking = p.get("tracking") or {}
    quiz = _extract_quiz(p)

    return CalendlyBookingPayload(
        event_type=event,
        name=p.get("name"),
        email=p["email"],                       # required — missing → ValidationError → 422
        phone=_extract_phone(p),
        scheduled_at=sched.get("start_time"),
        # scheduled_event uri is the stable id shared by created + canceled events,
        # so it dedupes correctly on the bookings.calendly_event_id UNIQUE key.
        calendly_event_uri=sched.get("uri") or p.get("uri"),
        invitee_uri=p.get("uri"),
        traffic_source=tracking.get("utm_source"),
        email_campaign=tracking.get("utm_campaign"),
        email_step=_to_int(tracking.get("utm_term")),
        utm_content=tracking.get("utm_content"),
        quiz_debt_range=quiz.get("debt_range"),
        quiz_years_owed=quiz.get("years_owed"),
        quiz_returns_filed=quiz.get("returns_filed"),
        quiz_state=quiz.get("state"),
        quiz_county=quiz.get("county"),
    )


# ── Route ───────────────────────────────────────────────────────────────────────

@router.post("/calendly")
async def calendly_native_webhook(request: Request):
    """Receive a native Calendly webhook, verify it, and persist the booking."""
    secret = os.getenv("CALENDLY_WEBHOOK_SECRET", "")
    if not secret:
        logger.error("CALENDLY_WEBHOOK_SECRET not configured — cannot verify webhook")
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    raw_body = await request.body()
    signature = request.headers.get("Calendly-Webhook-Signature")

    # 1. Signature first — never parse an unverified body.
    if not verify_signature(secret, signature, raw_body):
        logger.warning("Rejected Calendly webhook: bad signature")
        raise HTTPException(status_code=401, detail="Invalid Calendly webhook signature")

    # 2. Parse + transform.
    try:
        data = json.loads(raw_body)
        payload = transform_calendly_payload(data)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, ValidationError) as e:
        logger.warning(f"Rejected Calendly webhook: unparseable payload — {e}")
        raise HTTPException(status_code=422, detail=f"Unparseable Calendly payload: {e}")

    # 3. Reuse the existing upsert/cancel logic — no duplicated DB code.
    result = await handle_calendly_webhook(payload)
    return result
