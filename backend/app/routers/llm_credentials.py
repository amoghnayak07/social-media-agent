"""LLM credential endpoints — bring-your-own key, encrypted at rest.

Every row is scoped to the authenticated creator: queries filter on `creator_id`,
so a creator can never read or mutate another's credential, and an id from the
client is never trusted without that ownership check. The plaintext key is
validated, encrypted, and stored; only the masked `key_hint` is ever returned.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_creator
from app.errors import NotFoundError, ValidationError
from app.llm.validate import validate_key
from app.models import Creator, LlmCredential
from app.schemas import (
    LlmCredentialCreate,
    LlmCredentialResponse,
    LlmCredentialUpdate,
)
from app.security.crypto import encrypt

router = APIRouter(prefix="/api/llm-credentials", tags=["llm-credentials"])


async def _get_owned(db: AsyncSession, creator: Creator, cred_id: uuid.UUID) -> LlmCredential:
    cred = await db.scalar(
        select(LlmCredential).where(
            LlmCredential.id == cred_id,
            LlmCredential.creator_id == creator.id,  # ownership enforced in the query
        )
    )
    if cred is None:
        raise NotFoundError("Credential not found.")
    return cred


@router.get("", response_model=list[LlmCredentialResponse])
async def list_credentials(
    creator: Creator = Depends(get_current_creator),
    db: AsyncSession = Depends(get_db),
) -> list[LlmCredential]:
    result = await db.scalars(
        select(LlmCredential)
        .where(LlmCredential.creator_id == creator.id)
        .order_by(LlmCredential.created_at.desc())
    )
    return list(result)


@router.post("", response_model=LlmCredentialResponse, status_code=status.HTTP_201_CREATED)
async def create_credential(
    body: LlmCredentialCreate,
    creator: Creator = Depends(get_current_creator),
    db: AsyncSession = Depends(get_db),
) -> LlmCredential:
    # Validate the key BEFORE storing — a rejected key never gets persisted.
    key_hint = await validate_key(body.provider, body.api_key)
    cred = LlmCredential(
        creator_id=creator.id,
        provider=body.provider,
        model=body.model,
        api_key_enc=encrypt(body.api_key),  # encrypted at rest; DB never sees plaintext
        key_hint=key_hint,
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)
    return cred


@router.put("/{cred_id}", response_model=LlmCredentialResponse)
async def update_credential(
    cred_id: uuid.UUID,
    body: LlmCredentialUpdate,
    creator: Creator = Depends(get_current_creator),
    db: AsyncSession = Depends(get_db),
) -> LlmCredential:
    if body.model is None and body.api_key is None:
        raise ValidationError("Provide a new model and/or api_key to update.")

    cred = await _get_owned(db, creator, cred_id)

    if body.model is not None:
        cred.model = body.model
    if body.api_key is not None:
        # Re-validate the rotated key before persisting; provider is immutable.
        cred.key_hint = await validate_key(cred.provider, body.api_key)
        cred.api_key_enc = encrypt(body.api_key)

    await db.commit()
    await db.refresh(cred)
    return cred


@router.delete("/{cred_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    cred_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    db: AsyncSession = Depends(get_db),
) -> Response:
    cred = await _get_owned(db, creator, cred_id)
    await db.delete(cred)  # hard delete
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
