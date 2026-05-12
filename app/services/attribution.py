from typing import Optional, Dict, Any

from app.core.db import get_connection


def get_click_attribution(click_token: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    ect.lead_id,
                    ect.outreach_event_id,
                    ect.template_name,
                    ect.recipient_email,
                    ect.destination_url
                FROM email_click_tracking ect
                WHERE ect.tracking_token = %s
                LIMIT 1
                """,
                (click_token,),
            )
            row = cur.fetchone()
            if not row:
                return None

            return {
                "lead_id": row[0],
                "outreach_event_id": row[1],
                "template_name": row[2],
                "recipient_email": row[3],
                "destination_url": row[4],
            }
    finally:
        conn.close()


def mark_booking_from_click(
    submission_id: int,
    booking_url: str | None = None,
    booking_notes: str | None = None,
) -> None:
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE landing_submissions
                    SET
                        booked_at = NOW(),
                        booking_url = COALESCE(%s, booking_url),
                        booking_notes = COALESCE(%s, booking_notes)
                    WHERE id = %s
                    """,
                    (booking_url, booking_notes, submission_id),
                )
    finally:
        conn.close()