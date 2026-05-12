from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.core.db import get_connection

router = APIRouter()


@router.get("/c/{token}")
def track_click(token: str, request: Request):
    conn = get_connection()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, destination_url, click_count
                    FROM email_click_tracking
                    WHERE tracking_token = %s
                    """,
                    (token,),
                )
                row = cur.fetchone()

                if not row:
                    return RedirectResponse(url="https://taxcasereview.org")

                tracking_id, destination_url, click_count = row

                user_agent = request.headers.get("user-agent", "")
                forwarded_for = request.headers.get("x-forwarded-for")
                client_ip = (
                    forwarded_for.split(",")[0].strip()
                    if forwarded_for
                    else (request.client.host if request.client else None)
                )

                if click_count == 0:
                    cur.execute(
                        """
                        UPDATE email_click_tracking
                        SET
                            first_clicked_at = NOW(),
                            last_clicked_at = NOW(),
                            click_count = 1,
                            first_click_ip = %s,
                            last_click_ip = %s,
                            first_user_agent = %s,
                            last_user_agent = %s
                        WHERE id = %s
                        """,
                        (client_ip, client_ip, user_agent, user_agent, tracking_id),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE email_click_tracking
                        SET
                            last_clicked_at = NOW(),
                            click_count = click_count + 1,
                            last_click_ip = %s,
                            last_user_agent = %s
                        WHERE id = %s
                        """,
                        (client_ip, user_agent, tracking_id),
                    )

        separator = "&" if "?" in destination_url else "?"
        redirect_url = f"{destination_url}{separator}ct={token}"
        return RedirectResponse(url=redirect_url)

    finally:
        conn.close()