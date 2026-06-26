"""Password hashing with bcrypt (via passlib).

Passwords are hashed with a slow, salted, purpose-built hash — never a fast
general-purpose hash. Only the hash is stored in ``creators.password_hash``;
plaintext is never stored and never logged.
"""

from __future__ import annotations

from passlib.context import CryptContext

# bcrypt has a 72-byte input limit; passlib handles truncation but we also cap at
# the API layer. Single scheme keeps verification fast and predictable.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Minimum password length enforced on signup (CLAUDE.md auth hardening).
MIN_PASSWORD_LENGTH = 8


def hash_password(plaintext: str) -> str:
    return _pwd_context.hash(plaintext)


def verify_password(plaintext: str, password_hash: str) -> bool:
    """Constant-time verify. Returns False on any mismatch or malformed hash."""
    try:
        return _pwd_context.verify(plaintext, password_hash)
    except ValueError:
        # Malformed/empty stored hash — treat as a failed verification, never raise.
        return False
