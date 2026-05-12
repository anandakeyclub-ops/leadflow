# app/api/tcr_events.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional
from uuid import UUID
from app.core.db import get_connection

router = APIRouter(prefix="/tcr", tags=["taxcasereview"])

VALID_EVENTS = {
    "lp_view", "quiz_start", "quiz_complete",
    "booking_view", "booking_complete",
    "checkout_started", "payment_success", "unsubscribe"
}

class TrackRequest(BaseModel):
    event_type:  str
    email:       Optional[EmailStr] = None
    lead_id:     Optional[UUID]     = None
    first_name:  Optional[str]      = None
    phone:       Optional[str]      = None
    metadata:    Optional[dict]     = {}
    source:      Optional[str]      = "web"
    click_token: Optional[str]      = None

@router.post("/events/track")
async def track_event(payload: TrackRequest):
    if payload.event_type not in VALID_EVENTS:
        raise HTTPException(400, f"Unknown event: {payload.event_type}")
    if not payload.email and not payload.lead_id:
        raise HTTPException(400, "email or lead_id required")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Get or create lead
            lead = _get_or_create_lead(cur, payload)

            # Safety — never process events for unsubscribed leads
            if lead["unsubscribed_at"] and payload.event_type != "unsubscribe":
                return {"status": "unsubscribed", "lead_id": str(lead["id"])}

            # Idempotency — deduplicate within 60s
            cur.execute("""
                SELECT id FROM tcr_events
                WHERE lead_id = %s AND event_type = %s
                AND event_time > NOW() - INTERVAL '60 seconds'
            """, (lead["id"], payload.event_type))
            if cur.fetchone():
                return {"status": "duplicate", "lead_id": str(lead["id"])}

            # Store event
            cur.execute("""
                INSERT INTO tcr_events
                    (lead_id, event_type, metadata, source, click_token)
                VALUES (%s, %s, %s, %s, %s)
            """, (lead["id"], payload.event_type,
                  payload.metadata or {}, payload.source, payload.click_token))

            # State machine
            _process_event(cur, lead, payload.event_type, payload.metadata or {})
            conn.commit()

            return {"status": "ok", "lead_id": str(lead["id"])}
    finally:
        conn.close()


def _get_or_create_lead(cur, payload):
    if payload.lead_id:
        cur.execute("SELECT * FROM tcr_leads WHERE id = %s", (payload.lead_id,))
        lead = cur.fetchone()
        if lead:
            return dict(zip([d[0] for d in cur.description], lead))

    if payload.email:
        cur.execute("SELECT * FROM tcr_leads WHERE email = %s", (payload.email,))
        row = cur.fetchone()
        if row:
            return dict(zip([d[0] for d in cur.description], row))

    cur.execute("""
        INSERT INTO tcr_leads (email, first_name, phone)
        VALUES (%s, %s, %s) RETURNING *
    """, (payload.email, payload.first_name, payload.phone))
    row = cur.fetchone()
    return dict(zip([d[0] for d in cur.description], row))


# Correct order: quiz → booking → payment
STATUS_MAP = {
    "quiz_start":       "QUIZ_STARTED",
    "quiz_complete":    "QUIZ_COMPLETED",
    "booking_view":     "BOOKING_VIEWED",
    "booking_complete": "BOOKING_COMPLETED",
    "checkout_started": "CHECKOUT_STARTED",
    "payment_success":  "CUSTOMER",
}

TIMESTAMP_MAP = {
    "quiz_start":       "quiz_started_at",
    "quiz_complete":    "quiz_completed_at",
    "booking_view":     "booking_viewed_at",
    "booking_complete": "booking_completed_at",
    "checkout_started": "checkout_started_at",
    "payment_success":  "paid_at",
    "unsubscribe":      "unsubscribed_at",
}

def _process_event(cur, lead, event_type, metadata):
    updates = {"last_event_at": "NOW()"}

    new_status = STATUS_MAP.get(event_type)
    if new_status:
        updates["status"] = f"'{new_status}'"

    ts_field = TIMESTAMP_MAP.get(event_type)
    if ts_field:
        updates[ts_field] = "NOW()"

    # Store quiz answers
    if event_type == "quiz_complete" and metadata.get("answers"):
        import json
        answers = json.dumps(metadata["answers"])
        updates["quiz_answers"] = f"'{answers}'::jsonb"

    # Store booking details
    if event_type == "booking_complete":
        if metadata.get("booking_id"):
            updates["booking_id"] = f"'{metadata['booking_id']}'"
        updates["current_sequence"] = "NULL"

    # Cancel all sequences on payment or unsubscribe
    if event_type in ("payment_success", "unsubscribe"):
        cur.execute("""
            UPDATE tcr_scheduled_emails
            SET status = 'cancelled', cancelled_at = NOW()
            WHERE lead_id = %s AND status = 'pending'
        """, (lead["id"],))
        updates["current_sequence"] = "NULL"

    set_parts = []
    vals = []
    for k, v in updates.items():
        if v in ("NOW()", "NULL") or v.startswith("'"):
            set_parts.append(f"{k} = {v}")
        else:
            set_parts.append(f"{k} = %s")
            vals.append(v)

    cur.execute(
        f"UPDATE tcr_leads SET {', '.join(set_parts)} WHERE id = %s",
        vals + [lead["id"]]
    )