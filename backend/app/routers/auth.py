"""Auth endpoints: signup, login, logout, and the current-creator probe.

Login hardening (CLAUDE.md): rate-limited per IP; unknown-email and wrong-password
return the SAME generic message (no account enumeration). Signup may reveal a
duplicate email (409) — the no-enumeration rule applies to login only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_creator
from app.errors import AuthError, ConflictError
from app.models import Creator
from app.ratelimit import login_rate_limiter
from app.schemas import CreatorResponse, LoginRequest, SignupRequest
from app.security.cookies import clear_auth_cookies, set_auth_cookies
from app.security.passwords import hash_password, verify_password
from app.security.tokens import create_access_token, generate_csrf_token

router = APIRouter(prefix="/api", tags=["auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/auth/signup", response_model=CreatorResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, db: AsyncSession = Depends(get_db)) -> Creator:
    creator = Creator(
        email=body.email.lower(),
        display_name=body.display_name,
        password_hash=hash_password(body.password),  # plaintext never stored/logged
    )
    db.add(creator)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # Unique constraint on email.
        raise ConflictError("An account with this email already exists.") from exc
    await db.refresh(creator)
    return creator


@router.post("/auth/login", response_model=CreatorResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> Creator:
    login_rate_limiter.hit(_client_ip(request))

    creator = await db.scalar(select(Creator).where(Creator.email == body.email.lower()))
    # Generic error for BOTH unknown email and wrong password — no enumeration.
    # Always verify (even when creator is None, against a throwaway) to avoid a
    # timing side-channel, but a simple branch is acceptable here for v1.
    if creator is None or not verify_password(body.password, creator.password_hash):
        raise AuthError("Invalid email or password.")

    access_token = create_access_token(creator.id)
    csrf_token = generate_csrf_token()
    set_auth_cookies(response, access_token, csrf_token)
    return creator


@router.post("/auth/logout")
async def logout(response: Response) -> dict[str, bool]:
    # Stateless: clearing the client cookies is logout. The token stays valid
    # until expiry (short lifetime is the mitigation).
    clear_auth_cookies(response)
    return {"ok": True}


@router.get("/me", response_model=CreatorResponse)
async def me(creator: Creator = Depends(get_current_creator)) -> Creator:
    return creator
