"""
app/api/bookings/calendly.py
============================
FastAPI endpoint that receives Calendly webhook data from the Next.js site
and saves bookings to the LeadFlow database.

Routes:
  POST /api/bookings/calendly  — create or update booking from Calendly webhook
  GET  /api/bookings/status    — check booking status by email or calendly_event_id
  GET  /api/bookings/pending   — list pending bookings for retargeting
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bookings", tags=["bookings"])

# ── DB connection ─────────────────────────────────────────────────────────────

def get_db():
    from app.core.db import get_connection
    return get_connection()


# ── Pydantic models ───────────────────────────────────────────────────────────

class CalendlyBookingPayload(BaseModel):
    event_type: str                    # "invitee.created" or "invitee.canceled"
    name: Optional[str] = None
    email: str
    phone: Optional[str] = None
    scheduled_at: Optional[str] = None   # ISO datetime string
    calendly_event_uri: Optional[str] = None
    invitee_uri: Optional[str] = None
    status: Optional[str] = "pending"
    # Attribution fields passed via UTM params in Calendly booking URL
    traffic_source: Optional[str] = None
    email_campaign: Optional[str] = None
    email_step: Optional[int] = None
    utm_content: Optional[str] = None
    # Quiz answers passed via custom questions in Calendly
    quiz_debt_range: Optional[str] = None
    quiz_years_owed: Optional[str] = None
    quiz_returns_filed: Optional[str] = None
    quiz_state: Optional[str] = None
    quiz_county: Optional[str] = None


class BookingStatusResponse(BaseModel):
    found: bool
    booking_id: Optional[int] = None
    email: Optional[str] = None
    status: Optional[str] = None
    scheduled_at: Optional[str] = None
    paid: bool = False
    crm_synced: bool = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_scheduled_at(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string to datetime object."""
    if not dt_str:
        return None
    try:
        # Handle both Z suffix and +00:00
        dt_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None


def find_lien_for_email(conn, email: str) -> Optional[dict]:
    """Look up the most recent lien for this email in the LeadFlow DB."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    nl.id,
                    nl.debtor_name,
                    nl.amount,
                    nl.filed_date,
                    c.county_name,
                    nl.state_code
                FROM lien_dbpr_contacts ldc
                JOIN normalized_liens nl ON nl.id = ldc.lien_id
                LEFT JOIN counties c ON c.id = nl.county_id
                WHERE LOWER(ldc.email) = LOWER(%s)
                ORDER BY nl.filed_date DESC
                LIMIT 1
            """, (email,))
            row = cur.fetchone()
            if row:
                return {
                    "lien_id":    row[0],
                    "debtor_name": row[1],
                    "amount":     float(row[2]) if row[2] else None,
                    "filed_date": str(row[3]) if row[3] else None,
                    "county":     row[4],
                    "state":      row[5],
                }
    except Exception as e:
        logger.warning(f"Lien lookup failed for {email}: {e}")
    return None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/calendly")
async def handle_calendly_webhook(payload: CalendlyBookingPayload):
    """
    Receive Calendly booking events from the Next.js webhook handler.
    Creates or updates a booking record in the LeadFlow database.
    """
    logger.info(f"Calendly webhook: {payload.event_type} — {payload.email}")

    conn = get_db()
    try:
        # Look up lien data for this contact
        lien = find_lien_for_email(conn, payload.email)

        # Build quiz_answers JSONB
        quiz_answers = {}
        if payload.quiz_debt_range:   quiz_answers["debt_range"]    = payload.quiz_debt_range
        if payload.quiz_years_owed:   quiz_answers["years_owed"]    = payload.quiz_years_owed
        if payload.quiz_returns_filed: quiz_answers["returns_filed"] = payload.quiz_returns_filed
        if payload.quiz_state:        quiz_answers["state"]         = payload.quiz_state
        if payload.quiz_county:       quiz_answers["county"]        = payload.quiz_county

        scheduled_at = parse_scheduled_at(payload.scheduled_at)

        with conn.cursor() as cur:

            if payload.event_type == "invitee.canceled":
                # Mark existing booking as canceled
                cur.execute("""
                    UPDATE bookings
                    SET status = 'canceled', updated_at = NOW()
                    WHERE calendly_event_id = %s OR (LOWER(email) = LOWER(%s) AND status = 'pending')
                    RETURNING id
                """, (payload.calendly_event_uri, payload.email))
                row = cur.fetchone()
                conn.commit()
                booking_id = row[0] if row else None
                logger.info(f"Booking canceled: id={booking_id} email={payload.email}")
                return {
                    "status": "ok",
                    "action": "canceled",
                    "booking_id": booking_id,
                }

            # invitee.created — insert or update
            cur.execute("""
                INSERT INTO bookings (
                    email, name, phone,
                    calendly_event_id, calendly_event_url,
                    scheduled_at, calendly_booked_at,
                    quiz_answers,
                    lien_id, lien_county, lien_amount,
                    traffic_source, email_campaign, email_step,
                    status
                ) VALUES (
                    %s, %s, %s,
                    %s, %s,
                    %s, NOW(),
                    %s::jsonb,
                    %s, %s, %s,
                    %s, %s, %s,
                    'pending'
                )
                ON CONFLICT (calendly_event_id) DO UPDATE SET
                    name              = EXCLUDED.name,
                    phone             = EXCLUDED.phone,
                    scheduled_at      = EXCLUDED.scheduled_at,
                    quiz_answers      = EXCLUDED.quiz_answers,
                    lien_id           = EXCLUDED.lien_id,
                    lien_county       = EXCLUDED.lien_county,
                    lien_amount       = EXCLUDED.lien_amount,
                    traffic_source    = EXCLUDED.traffic_source,
                    email_campaign    = EXCLUDED.email_campaign,
                    email_step        = EXCLUDED.email_step,
                    updated_at        = NOW()
                RETURNING id
            """, (
                payload.email,
                payload.name,
                payload.phone,
                payload.calendly_event_uri,
                payload.calendly_event_uri,
                scheduled_at,
                __import__('json').dumps(quiz_answers) if quiz_answers else '{}',
                lien["lien_id"]  if lien else None,
                lien["county"]   if lien else None,
                lien["amount"]   if lien else None,
                payload.traffic_source,
                payload.email_campaign,
                payload.email_step,
            ))
            row = cur.fetchone()
            booking_id = row[0] if row else None
            conn.commit()

        logger.info(f"Booking created: id={booking_id} email={payload.email} "
                    f"scheduled={scheduled_at} lien={lien is not None}")

        return {
            "status":     "ok",
            "action":     "created",
            "booking_id": booking_id,
            "lien_found": lien is not None,
            "lien_county": lien["county"] if lien else None,
        }

    except Exception as e:
        conn.rollback()
        logger.error(f"Booking insert failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.post("/payment-confirmed")
async def payment_confirmed(request: Request):
    """
    Called by Stripe webhook when payment is confirmed.
    Updates booking status to 'paid' and triggers CRM sync.
    """
    data = await request.json()
    email          = data.get("email")
    stripe_session = data.get("stripe_session_id")
    amount         = data.get("amount", 399)
    paid_at        = data.get("paid_at")

    if not email and not stripe_session:
        raise HTTPException(status_code=400, detail="email or stripe_session_id required")

    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Update booking to paid
            if stripe_session:
                cur.execute("""
                    UPDATE bookings
                    SET status='paid', stripe_session_id=%s,
                        amount_paid=%s, paid_at=%s, updated_at=NOW()
                    WHERE LOWER(email)=LOWER(%s) AND status='pending'
                    RETURNING id, email, name, phone, scheduled_at,
                              quiz_answers, lien_id, lien_county,
                              lien_amount, traffic_source, email_campaign,
                              email_step, calendly_event_id
                """, (stripe_session, amount, paid_at or datetime.now(timezone.utc), email))
            else:
                cur.execute("""
                    UPDATE bookings
                    SET status='paid', amount_paid=%s,
                        paid_at=%s, updated_at=NOW()
                    WHERE LOWER(email)=LOWER(%s) AND status='pending'
                    RETURNING id, email, name, phone, scheduled_at,
                              quiz_answers, lien_id, lien_county,
                              lien_amount, traffic_source, email_campaign,
                              email_step, calendly_event_id
                """, (amount, paid_at or datetime.now(timezone.utc), email))

            row = cur.fetchone()
            conn.commit()

            if not row:
                logger.warning(f"No pending booking found for email={email}")
                return {"status": "ok", "action": "no_pending_booking_found"}

            booking = {
                "id":               row[0],
                "email":            row[1],
                "name":             row[2],
                "phone":            row[3],
                "scheduled_at":     str(row[4]) if row[4] else None,
                "quiz_answers":     row[5] or {},
                "lien_id":          row[6],
                "lien_county":      row[7],
                "lien_amount":      float(row[8]) if row[8] else None,
                "traffic_source":   row[9],
                "email_campaign":   row[10],
                "email_step":       row[11],
                "calendly_event_id": row[12],
                "stripe_session_id": stripe_session,
                "amount_paid":      amount,
            }

        # Trigger CRM sync (async — don't block payment confirmation)
        try:
            from app.api.crm.sync import sync_paid_booking_to_crm
            crm_result = sync_paid_booking_to_crm(booking)
            logger.info(f"CRM sync: {crm_result}")
        except ImportError:
            logger.info("CRM sync module not yet built — skipping")
        except Exception as e:
            logger.error(f"CRM sync failed (non-blocking): {e}")

        return {
            "status":     "ok",
            "action":     "payment_confirmed",
            "booking_id": booking["id"],
            "email":      email,
        }

    except Exception as e:
        conn.rollback()
        logger.error(f"Payment confirmation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.get("/status")
async def get_booking_status(
    email: Optional[str] = None,
    calendly_event_id: Optional[str] = None,
):
    """Check booking status by email or Calendly event ID."""
    if not email and not calendly_event_id:
        raise HTTPException(status_code=400, detail="email or calendly_event_id required")

    conn = get_db()
    try:
        with conn.cursor() as cur:
            if calendly_event_id:
                cur.execute("""
                    SELECT id, email, status, scheduled_at,
                           stripe_session_id, crm_contact_id
                    FROM bookings WHERE calendly_event_id = %s
                """, (calendly_event_id,))
            else:
                cur.execute("""
                    SELECT id, email, status, scheduled_at,
                           stripe_session_id, crm_contact_id
                    FROM bookings WHERE LOWER(email) = LOWER(%s)
                    ORDER BY created_at DESC LIMIT 1
                """, (email,))

            row = cur.fetchone()
            if not row:
                return BookingStatusResponse(found=False)

            return BookingStatusResponse(
                found=True,
                booking_id=row[0],
                email=row[1],
                status=row[2],
                scheduled_at=str(row[3]) if row[3] else None,
                paid=row[2] == "paid",
                crm_synced=row[5] is not None,
            )
    finally:
        conn.close()


@router.get("/pending")
async def get_pending_bookings():
    """
    List pending bookings (booked but not paid) for retargeting.
    Used by the abandoned booking retargeting script.
    """
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    id, email, name, phone,
                    scheduled_at, calendly_booked_at,
                    lien_county, lien_amount,
                    retarget_email_1_sent,
                    retarget_email_2_sent,
                    feedback_sent,
                    EXTRACT(EPOCH FROM (NOW() - calendly_booked_at))/3600 AS hours_since_booking
                FROM bookings
                WHERE status = 'pending'
                  AND calendly_booked_at < NOW() - INTERVAL '23 hours'
                ORDER BY calendly_booked_at ASC
            """)
            rows = cur.fetchall()
            return {
                "count": len(rows),
                "bookings": [
                    {
                        "id":                    r[0],
                        "email":                 r[1],
                        "name":                  r[2],
                        "phone":                 r[3],
                        "scheduled_at":          str(r[4]) if r[4] else None,
                        "booked_at":             str(r[5]) if r[5] else None,
                        "lien_county":           r[6],
                        "lien_amount":           float(r[7]) if r[7] else None,
                        "retarget_1_sent":       r[8],
                        "retarget_2_sent":       r[9],
                        "feedback_sent":         r[10],
                        "hours_since_booking":   round(float(r[11]), 1) if r[11] else 0,
                    }
                    for r in rows
                ]
            }
    finally:
        conn.close()
