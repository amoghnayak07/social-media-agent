"""Validate a bring-your-own LLM API key with the cheapest possible call.

Each supported provider has a *list-models* endpoint that authenticates the key
but spends ZERO tokens — the ideal "cheap test call before storing" the security
policy asks for. We check the KEY only (not the chosen model string); the model
is stored as the creator entered it and exercised in later phases.

Error policy (CLAUDE.md "External API calls"):
  - 401/403  -> the key is bad. Do NOT retry. Surface a clear, actionable message.
  - 5xx / timeout / network -> transient. Tell the creator to try again; don't store.
The API key is NEVER logged. httpx error reprs carry the URL/status, not headers,
and the logging formatter scrubs secrets as a backstop.
"""

from __future__ import annotations

import logging

import httpx

from app.errors import AppError, ValidationError

logger = logging.getLogger("app.llm.validate")

# Supported providers and their token-free auth probe.
SUPPORTED_PROVIDERS = ("anthropic", "openai", "xai")

# Sensible default model per provider (used by the UI; not enforced here).
DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5",
    "xai": "grok-4",
}

_TIMEOUT = httpx.Timeout(10.0)


def mask_key(api_key: str) -> str:
    """Masked display form, e.g. 'sk-...4f2a'. Never the full key."""
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:3]}...{api_key[-4:]}"


def _probe(provider: str, api_key: str) -> tuple[str, dict[str, str]]:
    """Return (url, headers) for the provider's list-models probe."""
    if provider == "anthropic":
        return (
            "https://api.anthropic.com/v1/models",
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
    if provider == "openai":
        return ("https://api.openai.com/v1/models", {"Authorization": f"Bearer {api_key}"})
    if provider == "xai":
        # xAI exposes an OpenAI-compatible API.
        return ("https://api.x.ai/v1/models", {"Authorization": f"Bearer {api_key}"})
    raise ValidationError(
        f"Unsupported provider '{provider}'. Choose one of: "
        f"{', '.join(SUPPORTED_PROVIDERS)}.",
        code="unsupported_provider",
    )


async def validate_key(provider: str, api_key: str) -> str:
    """Validate the key via the provider's list-models endpoint.

    Returns the masked key hint on success. Raises AppError (never leaking the
    key) on rejection or transient failure.
    """
    url, headers = _probe(provider, api_key)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        # Network/timeout — transient. Scrub-safe: exc carries no auth header.
        logger.warning("Key validation network error for provider=%s: %s", provider, exc)
        raise AppError(
            "Couldn't reach the provider to verify your API key. Please try again.",
            code="key_validation_unavailable",
            status_code=502,
        ) from exc

    if resp.status_code in (401, 403):
        # Definitive rejection — do not retry, do not store.
        raise AppError(
            "Your API key was rejected by the provider. Check the key and try again.",
            code="invalid_api_key",
            status_code=400,
        )
    if resp.status_code >= 500:
        logger.warning("Key validation got %s from provider=%s", resp.status_code, provider)
        raise AppError(
            "The provider is having trouble right now. Please try again in a moment.",
            code="key_validation_unavailable",
            status_code=502,
        )
    if resp.status_code != 200:
        # Anything else unexpected (e.g. 429) — surface generically, don't store.
        logger.warning("Key validation unexpected %s from provider=%s", resp.status_code, provider)
        raise AppError(
            "Couldn't verify your API key. Please try again.",
            code="key_validation_failed",
            status_code=400,
        )

    return mask_key(api_key)
