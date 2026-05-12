from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any
import json

from app.core.db import get_connection

router = APIRouter()


class LandingSubmissionPayload(BaseModel):
    first_name: str
    email: EmailStr
    phone: Optional[str] = None
    quiz_answers: Optional[Dict[str, Any]] = None
    source: Optional[str] = "landing_page"
    booking_url: Optional[str] = "https://taxcasereview.org/book"
    click_token: Optional[str] = None


@router.post("/submit")
def submit_landing(payload: LandingSubmissionPayload):
    conn = None
    cur = None

    try:
        conn = get_connection()
        cur = conn.cursor()

        lead_id = None
        outreach_event_id = None
        attribution_source = None

        if payload.click_token:
            cur.execute(
                """
                SELECT lead_id, outreach_event_id
                FROM email_click_tracking
                WHERE tracking_token = %s
                LIMIT 1
                """,
                (payload.click_token,),
            )
            click_row = cur.fetchone()

            if click_row:
                lead_id = click_row[0]
                outreach_event_id = click_row[1]
                attribution_source = "email_click"

        cur.execute(
            """
            INSERT INTO landing_submissions (
                first_name,
                email,
                phone,
                quiz_answers,
                source,
                booking_url,
                payment_status,
                payment_amount,
                submission_status,
                click_token,
                lead_id,
                outreach_event_id,
                attribution_source,
                created_at,
                updated_at
            )
            VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            RETURNING id
            """,
            (
                payload.first_name.strip(),
                payload.email,
                payload.phone,
                json.dumps(payload.quiz_answers or {}),
                payload.source,
                payload.booking_url,
                "pending",
                399.00,
                "submitted",
                payload.click_token,
                lead_id,
                outreach_event_id,
                attribution_source,
            ),
        )

        submission_id = cur.fetchone()[0]

        cur.execute(
            """
            INSERT INTO outreach_events (
                lead_id,
                channel,
                event_type,
                template_name,
                notes,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, NOW())
            """,
            (
                lead_id,
                "landing_page",
                "submitted",
                "landing_quiz",
                f"submission_id={submission_id}; click_token={payload.click_token}; attribution={attribution_source}",
            ),
        )

        conn.commit()

        return {
            "status": "success",
            "message": "Submission received",
            "submission_id": submission_id,
            "payment_status": "pending",
            "payment_amount": 399.00,
            "next_step": "stripe_checkout",
            "attribution": {
                "click_token": payload.click_token,
                "lead_id": lead_id,
                "outreach_event_id": outreach_event_id,
                "source": attribution_source,
            },
        }

    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()