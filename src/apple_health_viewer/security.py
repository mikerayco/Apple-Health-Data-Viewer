"""Local web security helpers."""

from __future__ import annotations

from functools import wraps
import hmac
from pathlib import Path
import secrets
import shutil
import tempfile
from typing import Any, BinaryIO, Callable, TypeVar, cast
from urllib.parse import urlsplit

from flask import Request, Response, abort, current_app, request, session

F = TypeVar("F", bound=Callable[..., Any])
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
UPLOAD_PATHS = {"/imports/upload/zip", "/imports/upload/folder"}
UPLOAD_RESERVE_BYTES = 128 * 1024**2


class PrivateUploadRequest(Request):
    """Spool multipart files directly into private application storage."""

    def _get_file_stream(
        self,
        total_content_length: int | None,
        content_type: str | None,
        filename: str | None = None,
        content_length: int | None = None,
    ) -> BinaryIO:
        del total_content_length, content_type, filename, content_length
        directory = Path(current_app.config["UPLOAD_TEMP_DIR"])
        return tempfile.TemporaryFile(mode="wb+", dir=directory)


def guard_unsafe_request() -> Response | None:
    """Reject cross-origin writes and impossible uploads before parsing request bodies."""
    if request.method not in UNSAFE_METHODS:
        return

    fetch_site = request.headers.get("Sec-Fetch-Site", "").lower()
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        abort(403, description="Cross-origin changes are not accepted.")

    origin = request.headers.get("Origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme != request.scheme or parsed.netloc != request.host:
            abort(403, description="Cross-origin changes are not accepted.")

    if request.path in UPLOAD_PATHS and request.content_length is not None:
        free = shutil.disk_usage(Path(current_app.config["UPLOAD_TEMP_DIR"])).free
        peak_required = request.content_length * 2 + UPLOAD_RESERVE_BYTES
        if peak_required > free:
            return current_app.response_class(
                "Insufficient Storage\n",
                status=507,
                mimetype="text/plain",
            )
    return None


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
