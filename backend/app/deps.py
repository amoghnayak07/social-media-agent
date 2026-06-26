"""The auth dependency every protected route depends on.

One place enforces the whole auth contract (CLAUDE.md "Auth middleware"):
  1. read the JWT from the HttpOnly cookie; verify signature + expiry -> 401
  2. on state-changing methods, verify X-CSRF-Token header == csrf cookie -> 403
  3. resolve and attach the current creator (401 if the row is gone)

The returned Creator is the "authenticated creator" every ownership check uses.
login/signup do NOT use this dependency (no cookie exists yet), so they are exempt.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Request
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.errors import AuthError, CSRFError
from app.models import Creator
from app.security.cookies import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER
from app.security.tokens import decode_access_token

# Methods that mutate state must carry a matching CSRF token.
_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


async def get_current_creator(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Creator:
    # 1. JWT from cookie
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise AuthError("Not authenticated.")
    try:
        claims = decode_access_token(token)
    except JWTError as exc:
        # Covers bad signature AND expiry — both resolve to "log in again".
        raise AuthError("Your session has expired. Please log in again.") from exc

    # 2. CSRF double-submit check on state-changing methods.
    if request.method in _STATE_CHANGING:
        cookie_token = request.cookies.get(CSRF_COOKIE)
        header_token = request.headers.get(CSRF_HEADER)
        if not cookie_token or not header_token or cookie_token != header_token:
            raise CSRFError("Missing or invalid CSRF token.")

    # 3. Resolve the creator.
    sub = claims.get("sub")
    try:
        creator_id = uuid.UUID(str(sub))
    except (ValueError, TypeError) as exc:
        raise AuthError("Invalid session.") from exc

    creator = await db.scalar(select(Creator).where(Creator.id == creator_id))
    if creator is None:
        # Token was valid but the account no longer exists.
        raise AuthError("Account not found.")
    return creator
