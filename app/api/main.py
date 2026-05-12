from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.landing import router as landing_router
from app.api.routes.stripe_checkout import router as stripe_router
from app.api.routes.stripe_verify import router as stripe_verify_router
from app.api.routes.tracking import router as tracking_router
from app.api.routes.click_tracking import router as click_router
from app.api.routes.attribution import router as attribution_router
from app.api.routes.broward import router as broward_router
from app.api.tcr_events import router as tcr_router

app = FastAPI(title="Leadflow API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(landing_router, prefix="/landing", tags=["landing"])
app.include_router(stripe_router, prefix="/stripe", tags=["stripe"])
app.include_router(stripe_verify_router, prefix="/stripe", tags=["stripe"])
app.include_router(tracking_router, prefix="/t", tags=["tracking"])
app.include_router(click_router, prefix="/t", tags=["tracking"])
app.include_router(attribution_router, prefix="/attribution", tags=["attribution"])
app.include_router(broward_router, prefix="/broward", tags=["broward"])
app.include_router(tcr_router)   


@app.get("/")
def root():
    return {"status": "ok", "app": "Leadflow API"}