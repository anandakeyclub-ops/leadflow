"""
app/api/routes/click_tracking.py
=================================
Email click tracking redirect.
GET /t/c/{token}?url={destination} — records click in email_clicks, redirects.

The tracking_id in the URL matches email_sends.tracking_id (UUID).
"""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from app.core.db import get_connection

router = APIRouter()

FALLBACK_URL = "https://taxcasereview.org"

@router.get("/c/{token}")
def track_click(token: str, url: str = "", request: Request = None):
    """
    Called when recipient clicks a tracked link.
    Inserts a row into email_clicks, updates email_sends.clicked_at,
    then redirects to the destination URL.
    """
    destination   = url or FALLBACK_URL
    user_agent    = request.headers.get("user-agent", "") if request else ""
    forwarded_for = request.headers.get("x-forwarded-for") if request else None
    client_ip     = (
        forwarded_for.split(",")[0].strip()
        if forwarded_for
        else (request.client.host if request and request.client else None)
    )

    try:
        conn = get_connection()
        with conn.cursor() as cur:
            # Insert click record (allow multiple clicks)
            cur.execute("""
                INSERT INTO email_clicks
                    (tracking_id, clicked_at, url, ip_address, user_agent)
                VALUES (%s::uuid, NOW(), %s, %s, %s)
            """, (token, destination, client_ip, user_agent))

            # Update email_sends.clicked_at on first click
            cur.execute("""
                UPDATE email_sends
                SET clicked_at = NOW()
                WHERE tracking_id = %s::uuid
                  AND clicked_at IS NULL
            """, (token,))

        conn.commit()
        conn.close()
    except Exception:
        pass  # Never fail — always redirect

    return RedirectResponse(url=destination, status_code=302)