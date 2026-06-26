"""YouTube (Google) OAuth 2.0 — authorization, token exchange, refresh, channel lookup.

This module is the only place that knows Google's OAuth/API URLs and payload
shapes. It returns normalized values (token dicts, a (channel_id, title) tuple)
or raises a typed AppError; callers above never see a Google response object.

Secrets discipline (CLAUDE.md): access/refresh tokens and the client secret are
NEVER logged. On failure we log the HTTP status and Google's `error` code only —
never the response body (which can echo a token) and never the request payload.
"""

from __future__ import annotations

import datetime
import logging
import secrets
import urllib.parse
import uuid

import httpx
from jose import JWTError, jwt

from app.config import get_settings
from app.errors import AppError, AuthError

logger = logging.getLogger("app.integrations.youtube.oauth")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# force-ssl covers reading comment threads AND posting/moderating replies later
# (Phase 9), so the creator consents once. It also permits channels.list(mine).
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

# The OAuth `state` is a short-lived signed token tying the round-trip to a
# creator — this is the CSRF defense for the callback and our (stateless) way to
# know which creator the redirect belongs to without a server-side store.
_STATE_PURPOSE = "yt_oauth_state"
_STATE_TTL_MINUTES = 10
_STATE_ALG = "HS256"

_TIMEOUT = httpx.Timeout(15.0)


def _jwt_secret() -> str:
    secret = get_settings().JWT_SECRET
    if not secret:
        raise RuntimeError("JWT_SECRET is not set; cannot sign the OAuth state token.")
    return secret


# --- state token ---------------------------------------------------------------
def create_state_token(creator_id: uuid.UUID) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    claims = {
        "sub": str(creator_id),
        "purpose": _STATE_PURPOSE,
        "nonce": secrets.token_urlsafe(8),
        "iat": now,
        "exp": now + datetime.timedelta(minutes=_STATE_TTL_MINUTES),
    }
    return jwt.encode(claims, _jwt_secret(), algorithm=_STATE_ALG)


def decode_state_token(state: str) -> uuid.UUID:
    """Verify the state token and return the creator id. Raises AuthError if the
    token is forged, expired, or not an OAuth-state token."""
    try:
        claims = jwt.decode(state, _jwt_secret(), algorithms=[_STATE_ALG])
    except JWTError as exc:
        raise AuthError("Invalid or expired OAuth state.") from exc
    if claims.get("purpose") != _STATE_PURPOSE:
        raise AuthError("Invalid OAuth state.")
    try:
        return uuid.UUID(str(claims["sub"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthError("Invalid OAuth state.") from exc


# --- authorization URL ---------------------------------------------------------
def build_authorization_url(state: str) -> str:
    settings = get_settings()
    if not settings.YOUTUBE_CLIENT_ID:
        raise AppError(
            "YouTube is not configured on the server.",
            code="youtube_not_configured",
            status_code=503,
        )
    params = {
        "client_id": settings.YOUTUBE_CLIENT_ID,
        "redirect_uri": settings.YOUTUBE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        # offline + consent so Google returns a refresh_token (first consent).
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


# --- token exchange / refresh --------------------------------------------------
async def _post_token(data: dict[str, str]) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(GOOGLE_TOKEN_URL, data=data)
    except httpx.HTTPError as exc:
        logger.warning("Google token endpoint network error: %s", exc)
        raise AppError(
            "Couldn't reach YouTube to complete sign-in. Please try again.",
            code="youtube_unavailable",
            status_code=502,
        ) from exc

    if resp.status_code != 200:
        # Log Google's error CODE only — never the body (may contain a token).
        try:
            err_code = resp.json().get("error", "unknown")
        except ValueError:
            err_code = "unparseable"
        logger.warning("Google token exchange failed: status=%s error=%s", resp.status_code, err_code)
        # invalid_grant = expired/reused code or revoked consent — not retryable.
        raise AuthError(
            "YouTube sign-in failed or expired. Please connect again.",
        )
    return resp.json()


async def exchange_code(code: str) -> dict:
    """Exchange an authorization code for tokens. Returns the raw token dict
    (access_token, expires_in, optional refresh_token, scope, token_type)."""
    settings = get_settings()
    return await _post_token(
        {
            "client_id": settings.YOUTUBE_CLIENT_ID,
            "client_secret": settings.YOUTUBE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.YOUTUBE_REDIRECT_URI,
        }
    )


async def refresh_access_token(refresh_token: str) -> dict:
    """Use a stored refresh token to mint a fresh access token. (Used by the read
    client in Phase 3b; included here so all token flows live in one place.)"""
    settings = get_settings()
    return await _post_token(
        {
            "client_id": settings.YOUTUBE_CLIENT_ID,
            "client_secret": settings.YOUTUBE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    )


# --- channel lookup ------------------------------------------------------------
async def fetch_channel(access_token: str) -> tuple[str, str | None]:
    """Return (channel_id, channel_title) for the authenticated account.

    channel_id is the platform-native account id stored on platform_accounts.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{YOUTUBE_API_BASE}/channels",
                params={"part": "id,snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("YouTube channels.list network error: %s", exc)
        raise AppError(
            "Couldn't reach YouTube. Please try again.",
            code="youtube_unavailable",
            status_code=502,
        ) from exc

    if resp.status_code in (401, 403):
        raise AuthError("YouTube rejected the connection. Please connect again.")
    if resp.status_code != 200:
        logger.warning("YouTube channels.list failed: status=%s", resp.status_code)
        raise AppError(
            "Couldn't read your YouTube channel. Please try again.",
            code="youtube_channel_lookup_failed",
            status_code=502,
        )

    items = resp.json().get("items") or []
    if not items:
        raise AppError(
            "No YouTube channel is associated with this Google account.",
            code="youtube_no_channel",
            status_code=400,
        )
    channel = items[0]
    return channel["id"], channel.get("snippet", {}).get("title")
