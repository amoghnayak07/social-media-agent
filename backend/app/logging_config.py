"""Secret-scrubbing logging.

The single most common leak path is an error handler that logs a request payload
containing an API key or a token (CLAUDE.md security requirements). So scrubbing
is done at the *formatter* level: every log line — including exception tracebacks
— is passed through ``scrub_text`` before it is emitted. This is a backstop, not
a license to log secrets; code must still never pass a secret to the logger.

Passwords are never logged in the first place (we hash on receipt and never put
the plaintext into a log call), but the key/value patterns below also redact a
stray ``password=...`` just in case.
"""

from __future__ import annotations

import logging
import re

# Patterns for known secret shapes. Order doesn't matter; all are applied.
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # Anthropic / OpenAI style API keys
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"sk-proj-[A-Za-z0-9_\-]+"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    # Google OAuth access / refresh tokens
    re.compile(r"ya29\.[A-Za-z0-9_\-]+"),
    re.compile(r"1//[A-Za-z0-9_\-]+"),
    # Authorization: Bearer <token>
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    # Structured "key": "value" / key=value for sensitive field names. Capture
    # the field+separator, redact only the value.
    re.compile(
        r"(?i)(\"?(?:api[_-]?key|password|passwd|access_token|refresh_token|"
        r"token|authorization|secret|client_secret)\"?\s*[:=]\s*\"?)([^\"\s,}&]+)"
    ),
]

_REDACTED = "***REDACTED***"


def scrub_text(text: str) -> str:
    """Redact anything that looks like a secret from a string."""
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        if pat.groups >= 2:
            out = pat.sub(lambda m: m.group(1) + _REDACTED, out)
        else:
            out = pat.sub(_REDACTED, out)
    return out


class ScrubbingFormatter(logging.Formatter):
    """A Formatter that scrubs the fully-rendered record (message + traceback)."""

    def format(self, record: logging.LogRecord) -> str:
        return scrub_text(super().format(record))


def setup_logging(level: int = logging.INFO) -> None:
    """Install the scrubbing formatter on the root logger. Idempotent."""
    root = logging.getLogger()
    root.setLevel(level)
    formatter = ScrubbingFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Replace handlers' formatters (uvicorn installs its own handlers); also add
    # one stream handler if none exist so our app logs are scrubbed everywhere.
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
    else:
        for h in root.handlers:
            h.setFormatter(formatter)
