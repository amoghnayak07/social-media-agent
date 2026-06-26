"""Auth cookie helpers — one place that knows the cookie names and attributes.

Two cookies are set on login:
  - access_token  (HttpOnly): the JWT. Not JS-readable, so it survives XSS.
  - csrf_token    (readable): the double-submit CSRF token the frontend echoes
    back in the X-CSRF-Token header on state-changing requests.

Cookie security attributes come from settings so dev (localhost, http, same-site)
and prod (cross-site, https) can differ without code changes. They MUST agree
with the CORS config or login works locally and breaks in prod.
"""

from __future__ import annotations

from fastapi import Response

from app.config import get_settings

ACCESS_COOKIE = "access_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"


def set_auth_cookies(response: Response, access_token: str, csrf_token: str) -> None:
    settings = get_settings()
    max_age = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    common = {
        "max_age": max_age,
        "secure": settings.COOKIE_SECURE,
        "samesite": settings.COOKIE_SAMESITE,
        "path": "/",
    }
    # JWT: HttpOnly so JS (and thus XSS) cannot read it.
    response.set_cookie(ACCESS_COOKIE, access_token, httponly=True, **common)
    # CSRF token: deliberately readable by JS so the SPA can echo it in a header.
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, **common)


def clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    # Match attributes used when setting, or the browser won't clear them.
    common = {"samesite": settings.COOKIE_SAMESITE, "secure": settings.COOKIE_SECURE, "path": "/"}
    response.delete_cookie(ACCESS_COOKIE, httponly=True, **common)
    response.delete_cookie(CSRF_COOKIE, httponly=False, **common)
