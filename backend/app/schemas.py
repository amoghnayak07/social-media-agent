"""Pydantic request/response models for the API boundary.

Response models never include a plaintext secret — LLM credentials surface only
the masked `key_hint`. Request validation runs before any handler logic, and the
validation error handler strips submitted values so a password never echoes back.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.security.passwords import MIN_PASSWORD_LENGTH

# bcrypt only hashes the first 72 bytes; cap here so behaviour is predictable.
MAX_PASSWORD_LENGTH = 72

Provider = Literal["anthropic", "openai", "xai"]


# --- Auth ----------------------------------------------------------------------
class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    display_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class CreatorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None
    created_at: datetime.datetime


# --- LLM credentials -----------------------------------------------------------
class LlmCredentialCreate(BaseModel):
    provider: Provider
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1, max_length=500)


class LlmCredentialUpdate(BaseModel):
    # Rotate the key and/or change the model. At least one must be provided
    # (enforced in the handler so we can return a clean structured error).
    model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, min_length=1, max_length=500)


class LlmCredentialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    model: str
    key_hint: str  # masked only — never the plaintext key
    created_at: datetime.datetime
    updated_at: datetime.datetime
