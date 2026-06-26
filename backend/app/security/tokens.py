"""Stateless auth tokens: signed JWT + CSRF token.

Auth is stateless — the JWT (HS256, signed with JWT_SECRET) carries the creator id
and an expiry and is self-contained; there is no server-side session store. The
short lifetime is the mitigation for non-revocability (logout only clears the
client cookie). JWT_SECRET is a crown-jewel secret: env-only, never logged.
"""

from __future__ import annotations

import datetime
import secrets
import uuid

from jose import jwt

from app.config import get_settings

ALGORITHM = "HS256"


def _secret() -> str:
    key = get_settings().JWT_SECRET
    if not key:
        raise RuntimeError(
            "JWT_SECRET is not set. Generate one with "
            "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"` "
            "and put it in the backend .env."
        )
    return key


def create_access_token(creator_id: uuid.UUID) -> str:
    """Issue a short-lived signed JWT for the given creator."""
    now = datetime.datetime.now(datetime.timezone.utc)
    expire = now + datetime.timedelta(
        minutes=get_settings().ACCESS_TOKEN_EXPIRE_MINUTES
    )
    claims = {"sub": str(creator_id), "iat": now, "exp": expire}
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Verify signature + expiry and return the claims.

    Raises jose.JWTError (incl. ExpiredSignatureError) on any invalid/expired
    token. Callers translate that into a 401.
    """
    return jwt.decode(token, _secret(), algorithms=[ALGORITHM])


def generate_csrf_token() -> str:
    """A random, unguessable token for the double-submit CSRF cookie."""
    return secrets.token_urlsafe(32)
