"""
app/api/routes/tracking.py
==========================
Email open tracking pixel.
GET /t/o/{token}.gif — records open in email_opens, returns 1x1 transparent GIF.

The tracking_id in the URL matches email_sends.tracking_id (UUID).
"""
from fastapi import APIRouter, Request, Response
from app.core.db import get_connection

router = APIRouter()

PIXEL_GIF = (
    b"GIF89a"
    b"\x01\x00\x01\x00"
    b"\x80\x00\x00"
    b"\x00\x00\x00"
    b"\xff\xff\xff"
    b"\x21\xf9\x04\x01\x00\x00\x00\x00"
    b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00"
    b"\x02\x02\x44\x01\x00"
    b"\x3b"
)

@router.get("/o/{token}.gif")
def email_open_pixel(token: str, request: Request):
    """
    Called when recipient opens the email (image loads).
    Inserts a row into email_opens and updates email_sends.opened_at.
    Always returns the pixel — never 404 (broken image = bad UX).
    """
    user_agent    = request.headers.get("user-agent", "")
    forwarded_for = request.headers.get("x-forwarded-for")
    client_ip     = (
        forwarded_for.split(",")[0].strip()
        if forwarded_for
        else (request.client.host if request.client else None)
    )

    try:
        conn = get_connection()
        with conn.cursor() as cur:
            # Only record first open per tracking_id
            cur.execute("""
                SELECT id FROM email_opens
                WHERE tracking_id = %s::uuid
                LIMIT 1
            """, (token,))
            existing = cur.fetchone()

            if not existing:
                # Insert open record
                cur.execute("""
                    INSERT INTO email_opens
                        (tracking_id, opened_at, ip_address, user_agent)
                    VALUES (%s::uuid, NOW(), %s, %s)
                    ON CONFLICT DO NOTHING
                """, (token, client_ip, user_agent))

                # Update email_sends.opened_at for easy reporting
                cur.execute("""
                    UPDATE email_sends
                    SET opened_at = NOW()
                    WHERE tracking_id = %s::uuid
                      AND opened_at IS NULL
                """, (token,))

        conn.commit()
        conn.close()
    except Exception:
        pass  # Never fail — always return the pixel

    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma":        "no-cache",
        "Expires":       "0",
    }
    return Response(content=PIXEL_GIF, media_type="image/gif", headers=headers)