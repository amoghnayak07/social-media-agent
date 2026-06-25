"""Database engine and session management (async).

This is the single place that knows how to talk to Postgres. Everything above
(routes, services) asks for a session here and never constructs its own engine.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# The engine is the long-lived connection pool to Postgres. Created once at
# import time and shared across the whole app. asyncpg is the driver underneath.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,        # set True to log every SQL statement (noisy; dev only)
    pool_pre_ping=True,  # check a connection is alive before using it (survives DB sleep/restart)
)

# A factory that hands out new AsyncSession objects. expire_on_commit=False keeps
# ORM objects usable after commit (handy when returning them from a request).
SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    """Base class all SQLAlchemy ORM models inherit from (Phase 1 onward)."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield a session, always close it afterward.

    Used as `db: AsyncSession = Depends(get_db)` in route handlers.
    """
    async with SessionLocal() as session:
        yield session
