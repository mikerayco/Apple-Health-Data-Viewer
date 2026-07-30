"""Local web security helpers."""

from __future__ import annotations

from functools import wraps
import hmac
import secrets
from typing import Any, Callable, TypeVar, cast

from flask import Response, abort, request, session

F = TypeVar("F", bound=Callable[..., Any])


def csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return cast(str, token)


def csrf_protected(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        expected = session.get("_csrf_token", "")
        provided = request.form.get("csrf_token", "")
        if not expected or not provided or not hmac.compare_digest(expected, provided):
            abort(400, description="The form expired. Refresh the page and try again.")
        return view(*args, **kwargs)

    return cast(F, wrapped)


def apply_security_headers(response: Response) -> Response:
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'none'; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'"
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response
