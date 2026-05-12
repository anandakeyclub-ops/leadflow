import os
import stripe
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.db import get_connection

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")


class CheckoutRequest(BaseModel):
    submission_id: int


@router.post("/create-checkout-session")
def create_checkout_session(payload: CheckoutRequest):
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")

    conn = get_connection()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        email,
                        click_token,
                        lead_id,
                        outreach_event_id,
                        attribution_source,
                        payment_amount
                    FROM landing_submissions
                    WHERE id = %s
                    """,
                    (payload.submission_id,),
                )
                row = cur.fetchone()

                if not row:
                    raise HTTPException(status_code=404, detail="Submission not found")

                email, click_token, lead_id, outreach_event_id, attribution_source, amount = row

                session = stripe.checkout.Session.create(
                    payment_method_types=["card"],
                    customer_email=email,
                    line_items=[
                        {
                            "price_data": {
                                "currency": "usd",
                                "product_data": {
                                    "name": "Tax Case Review",
                                },
                                "unit_amount": int(float(amount) * 100),
                            },
                            "quantity": 1,
                        }
                    ],
                    mode="payment",
                    success_url=os.getenv("STRIPE_SUCCESS_URL"),
                    cancel_url=os.getenv("STRIPE_CANCEL_URL"),
                    metadata={
                        "submission_id": str(payload.submission_id),
                        "click_token": click_token or "",
                        "lead_id": str(lead_id or ""),
                        "outreach_event_id": str(outreach_event_id or ""),
                        "attribution_source": attribution_source or "",
                    },
                )

                cur.execute(
                    """
                    INSERT INTO payment_sessions (
                        submission_id,
                        session_id,
                        click_token,
                        lead_id,
                        outreach_event_id,
                        attribution_source,
                        payment_status,
                        amount,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (session_id)
                    DO UPDATE SET
                        click_token = EXCLUDED.click_token,
                        lead_id = EXCLUDED.lead_id,
                        outreach_event_id = EXCLUDED.outreach_event_id,
                        attribution_source = EXCLUDED.attribution_source,
                        payment_status = EXCLUDED.payment_status,
                        amount = EXCLUDED.amount,
                        updated_at = NOW()
                    """,
                    (
                        payload.submission_id,
                        session.id,
                        click_token,
                        lead_id,
                        outreach_event_id,
                        attribution_source,
                        "pending",
                        amount,
                    ),
                )

        return {
            "status": "success",
            "checkout_url": session.url,
            "session_id": session.id,
        }

    finally:
        conn.close()