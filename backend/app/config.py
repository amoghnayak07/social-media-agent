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

    # YouTube OAuth (server-side only; wired up in Phase 3).
    YOUTUBE_CLIENT_ID: str = ""
    YOUTUBE_CLIENT_SECRET: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (read env once, reuse everywhere)."""
    return Settings()
