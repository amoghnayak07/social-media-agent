"""FastAPI application entry point.

Run locally with:  uvicorn app.main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.errors import register_exception_handlers
from app.logging_config import setup_logging
from app.routers import auth, llm_credentials, platform_accounts, youtube

settings = get_settings()

# Install secret-scrubbing logging before anything else can log.
setup_logging()

app = FastAPI(
    title="Comment Agent API",
    version="0.1.0",
)

# CORS: browsers block cross-origin requests by default. The frontend (Vite, a
# different origin) must be explicitly allowed, with credentials enabled so the
# auth cookie is sent. allow_credentials=True forbids a wildcard origin, so we
# name the exact FRONTEND_ORIGIN. This is the #1 "works locally, breaks in prod"
# bug — wiring it from the start. It MUST agree with the cookie SameSite/Secure
# settings (see app/security/cookies.py).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Structured errors: typed AppError -> clean JSON, validation errors stripped of
# submitted values, catch-all 500 with no internals/secrets.
register_exception_handlers(app)

# Routes.
app.include_router(auth.router)
app.include_router(llm_credentials.router)
app.include_router(youtube.router)
app.include_router(platform_accounts.router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Returns OK if the app is up. No DB call — kept trivial so
    it answers instantly even during a cold start."""
    return {"status": "ok"}
