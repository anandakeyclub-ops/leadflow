from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="LeadFlow API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://taxcasereview.org", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok", "service": "leadflow-api"}

# Bookings
try:
    from app.api.bookings.calendly_booking_api import router as bookings_router
    app.include_router(bookings_router)
except Exception as e:
    print(f"Warning: bookings router not loaded: {e}")

# Tracking — mounted at /t so URLs match the email pixel/link generators
# (open_pixel_url -> {base}/t/o/{id}.gif, tracked_link -> {base}/t/c/{id}).
try:
    from app.api.routes.tracking import router as tracking_router
    app.include_router(tracking_router, prefix="/t")
except Exception as e:
    print(f"Warning: tracking router not loaded: {e}")

try:
    from app.api.routes.click_tracking import router as click_router
    app.include_router(click_router, prefix="/t")
except Exception as e:
    print(f"Warning: click tracking router not loaded: {e}")

# CRM pipeline — create lead/case/calendar appointment after a confirmed
# Stripe payment. Called by the v0-tax-landing Stripe webhook.
try:
    from app.integrations.crm_pipeline import run_crm_pipeline

    @app.post("/crm-pipeline")
    async def crm_pipeline_endpoint(payload: dict):
        booking_data = payload.get("booking_data", {})
        payment_data = payload.get("payment_data", {})
        return run_crm_pipeline(booking_data, payment_data)
except Exception as e:
    print(f"Warning: crm-pipeline endpoint not loaded: {e}")
