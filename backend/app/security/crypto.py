"""Fernet encryption for secrets at rest (LLM API keys, OAuth tokens).

The crown jewel ``ENCRYPTION_KEY`` lives ONLY in the deployment env / local .env.
Plaintext is encrypted before it touches the DB and decrypted only in memory at
the point of use. The DB never sees plaintext; a plaintext secret is never
returned to the frontend and never logged.

If its leak equals every user key leaking, so this module is deliberately small
and has exactly one responsibility.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().ENCRYPTION_KEY
    if not key:
        # Fail loudly at first use rather than silently storing recoverable junk.
        raise RuntimeError(
            "ENCRYPTION_KEY is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and put it in the backend .env."
        )
    # Fernet expects url-safe base64 32-byte key as bytes.
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> bytes:
    """Encrypt a UTF-8 string, returning ciphertext bytes for a `bytea` column."""
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    """Decrypt ciphertext bytes back to the original string. Raises ValueError on
    tampered/garbage input or a wrong key (never echoes the ciphertext)."""
    try:
        return _fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Could not decrypt value (wrong key or corrupt data).") from exc
