"""Connected platform accounts (list + disconnect).

Ownership-scoped exactly like llm_credentials: every query filters on
`creator_id`, and a client-supplied id is never trusted without that check.
Encrypted OAuth tokens are never serialized — the response model omits them.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_creator
from app.errors import NotFoundError
from app.models import Creator, PlatformAccount
from app.schemas import PlatformAccountResponse

router = APIRouter(prefix="/api/platform-accounts", tags=["platform-accounts"])


@router.get("", response_model=list[PlatformAccountResponse])
async def list_accounts(
    creator: Creator = Depends(get_current_creator),
    db: AsyncSession = Depends(get_db),
) -> list[PlatformAccount]:
    result = await db.scalars(
        select(PlatformAccount)
        .where(PlatformAccount.creator_id == creator.id)
        .order_by(PlatformAccount.created_at.desc())
    )
    return list(result)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_account(
    account_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    db: AsyncSession = Depends(get_db),
) -> Response:
    account = await db.scalar(
        select(PlatformAccount).where(
            PlatformAccount.id == account_id,
            PlatformAccount.creator_id == creator.id,  # ownership enforced in the query
        )
    )
    if account is None:
        raise NotFoundError("Platform account not found.")
    # Cascade removes the account's posts/comments/voice_examples.
    await db.delete(account)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
