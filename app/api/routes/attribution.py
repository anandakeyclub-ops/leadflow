from fastapi import APIRouter
from pydantic import BaseModel

from app.services.attribution import get_click_attribution, mark_booking_from_click

router = APIRouter()


class BookingAttributionRequest(BaseModel):
    submission_id: int
    booking_url: str | None = None
    booking_notes: str | None = None


@router.get("/click/{click_token}")
def lookup_click_attribution(click_token: str):
    if not click_token or not click_token.strip():
        return {"status": "no_token", "attribution": None}

    result = get_click_attribution(click_token.strip())
    if not result:
        return {"status": "not_found", "attribution": None}

    return {"status": "ok", "attribution": result}


@router.post("/booking")
def save_booking_attribution(payload: BookingAttributionRequest):
    mark_booking_from_click(
        submission_id=payload.submission_id,
        booking_url=payload.booking_url,
        booking_notes=payload.booking_notes,
    )
    return {"status": "ok", "message": "Booking attribution saved"}