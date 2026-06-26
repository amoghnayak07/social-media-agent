"""YouTube OAuth endpoints.

  GET /api/platform/youtube/connect   (authenticated) -> 302 to Google consent
  GET /api/platform/youtube/callback  (Google redirects here) -> store tokens

`connect` is authenticated (creator from JWT) and signs a `state` token carrying
the creator id. `callback` is NOT behind the auth dependency — Google redirects
the browser here and we can't control its headers/CSRF — so it trusts the signed
`state` instead (which is also the CSRF defense). On any failure the callback
redirects back to the frontend with a flag rather than dumping JSON at the user.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_creator
from app.errors import AppError
from app.integrations.youtube import oauth
from app.models import Creator, PlatformAccount, PlatformKind
from app.security.crypto import encrypt

router = APIRouter(prefix="/api/platform/youtube", tags=["youtube"])


def _frontend_redirect(status_value: str) -> RedirectResponse:
    base = get_settings().FRONTEND_ORIGIN.rstrip("/")
    return RedirectResponse(f"{base}/settings?youtube={status_value}")


@router.get("/connect")
async def connect(creator: Creator = Depends(get_current_creator)) -> RedirectResponse:
    state = oauth.create_state_token(creator.id)
    url = oauth.build_authorization_url(state)
    # 302 so the browser follows it to Google's consent screen.
    return RedirectResponse(url, status_code=302)


@router.get("/callback")
async def callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    # The user denied consent, or Google sent us back malformed.
    if error or not code or not state:
        return _frontend_redirect("error")

    try:
        creator_id = oauth.decode_state_token(state)
        tokens = await oauth.exchange_code(code)
        access_token = tokens["access_token"]
        # title isn't stored (no column for it); discard it.
        channel_id, _ = await oauth.fetch_channel(access_token)
    except AppError:
        # Surface a clean flag to the UI instead of a JSON error page.
        return _frontend_redirect("error")

    # One YouTube channel can't be owned by two creators (unique constraint).
    existing = await db.scalar(
        select(PlatformAccount).where(
            PlatformAccount.platform == PlatformKind.youtube,
            PlatformAccount.platform_account_id == channel_id,
        )
    )
    if existing is not None and existing.creator_id != creator_id:
        return _frontend_redirect("already_connected")

    expires_at = None
    if tokens.get("expires_in"):
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            seconds=int(tokens["expires_in"])
        )
    access_enc = encrypt(access_token)
    # Google only returns refresh_token on first consent; on reconnect keep the
    # one we already stored rather than nulling it out.
    refresh_token = tokens.get("refresh_token")
    refresh_enc = encrypt(refresh_token) if refresh_token else None

    if existing is None:
        db.add(
            PlatformAccount(
                creator_id=creator_id,
                platform=PlatformKind.youtube,
                platform_account_id=channel_id,
                access_token_enc=access_enc,
                refresh_token_enc=refresh_enc,
                token_expires_at=expires_at,
                status="connected",
            )
        )
    else:
        existing.access_token_enc = access_enc
        if refresh_enc is not None:
            existing.refresh_token_enc = refresh_enc
        existing.token_expires_at = expires_at
        existing.status = "connected"

    await db.commit()
    return _frontend_redirect("connected")
