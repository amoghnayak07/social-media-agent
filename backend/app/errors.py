"""Structured errors and the global exception handlers.

One error shape across the whole API: ``{"error": {"code", "message"}}`` — a
machine-readable ``code`` plus a human, actionable ``message``. Never a stack
trace, never an internal detail, never a secret in the response body (CLAUDE.md
"API boundary"). The real, scrubbed detail is logged server-side instead.

Per CLAUDE.md we do NOT wrap everything in one global try/catch; route code
raises a typed ``AppError`` for expected failures, and the catch-all handler
below only exists as the last line of defence for genuinely unexpected bugs.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("app.errors")


class AppError(Exception):
    """An expected, client-facing error. Carries a stable code, a safe message,
    and the HTTP status to return. Raise these from routes/services."""

    status_code: int = 400
    code: str = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


class CSRFError(AppError):
    status_code = 403
    code = "csrf_failed"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"


def _error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def register_exception_handlers(app: FastAPI) -> None:
    """Wire the handlers onto the FastAPI app (called once from main)."""

    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        # Expected error: the message is already safe to show the client.
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic validation errors can echo the offending INPUT (e.g. the
        # submitted password) back in `input`/`ctx`. Strip those — return only
        # field location + message so we never leak a secret into the response.
        fields = [
            {"field": ".".join(str(p) for p in err.get("loc", []) if p != "body"),
             "message": err.get("msg", "invalid")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error",
                               "message": "Request validation failed.",
                               "fields": fields}},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Last resort. Log the real (scrubbed-by-formatter) detail server-side;
        # return a generic 500 with no internals.
        logger.exception("Unhandled exception: %s", exc.__class__.__name__)
        return JSONResponse(
            status_code=500,
            content=_error_body("internal_error", "Something went wrong. Please try again."),
        )
