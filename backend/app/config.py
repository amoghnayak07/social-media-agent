"""Application configuration.

All settings are read from environment variables (or a local `.env` file in dev).
pydantic-settings validates them at startup, so a missing/misspelled required var
fails loudly and immediately instead of surfacing as a confusing error deep in a
request. Secrets live ONLY here in memory at runtime — never hardcoded, never logged.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Postgres connection string. Local Docker URL in dev, Neon URL in prod.
    # Must use the asyncpg driver, e.g.
    #   postgresql+asyncpg://postgres:postgres@localhost:5432/comment_agent
    DATABASE_URL: str

    # Crown-jewel secrets. Required in real runs; defaulted to empty so the app
    # can boot for the Phase 0 /health check before Phase 2 wires them in.
    ENCRYPTION_KEY: str = ""
    JWT_SECRET: str = ""

    # CORS: the single browser origin allowed to call this API.
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    # --- Auth / cookies (Phase 2) ---
    # Short token lifetime is the mitigation for stateless-JWT non-revocability
    # (CLAUDE.md: 15-30 min). logout only clears the client cookie.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    # Cookie security attributes. Dev defaults work for localhost over http
    # (frontend :5173 and backend :8000 are the same *site*, so SameSite=lax is
    # sent). In prod the two are cross-site (vercel.app vs onrender.com) so they
    # MUST be COOKIE_SECURE=true + COOKIE_SAMESITE=none, and CORS allows creds.
    # These must agree with the cookie settings or "works locally, breaks in prod".
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"  # lax | strict | none

    # YouTube OAuth (server-side only; wired up in Phase 3).
    YOUTUBE_CLIENT_ID: str = ""
    YOUTUBE_CLIENT_SECRET: str = ""
    # Must EXACTLY match an authorized redirect URI registered on the Google
    # Cloud OAuth client (and is sent on both the auth request and token exchange).
    YOUTUBE_REDIRECT_URI: str = "http://localhost:8000/api/platform/youtube/callback"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (read env once, reuse everywhere)."""
    return Settings()
