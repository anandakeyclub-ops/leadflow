from fastapi import APIRouter, Request, Response, HTTPException

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
    conn = get_connection()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, open_count
                    FROM email_tracking
                    WHERE tracking_token = %s
                    """,
                    (token,),
                )
                row = cur.fetchone()

                if not row:
                    raise HTTPException(status_code=404, detail="Tracking token not found")

                tracking_id, open_count = row
                user_agent = request.headers.get("user-agent", "")
                forwarded_for = request.headers.get("x-forwarded-for")
                client_ip = (
                    forwarded_for.split(",")[0].strip()
                    if forwarded_for
                    else (request.client.host if request.client else None)
                )

                if open_count == 0:
                    cur.execute(
                        """
                        UPDATE email_tracking
                        SET
                            first_opened_at = NOW(),
                            last_opened_at = NOW(),
                            open_count = 1,
                            first_open_ip = %s,
                            last_open_ip = %s,
                            first_user_agent = %s,
                            last_user_agent = %s
                        WHERE id = %s
                        """,
                        (client_ip, client_ip, user_agent, user_agent, tracking_id),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE email_tracking
                        SET
                            last_opened_at = NOW(),
                            open_count = open_count + 1,
                            last_open_ip = %s,
                            last_user_agent = %s
                        WHERE id = %s
                        """,
                        (client_ip, user_agent, tracking_id),
                    )

        headers = {
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        }
        return Response(content=PIXEL_GIF, media_type="image/gif", headers=headers)

    finally:
        conn.close()