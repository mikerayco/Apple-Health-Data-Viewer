"""Runtime configuration and precedence rules."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat
from typing import Any, Mapping

from flask import Flask

from .paths import normalize_path, platform_data_dir

ENV_PREFIX = "AHV_"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


def ensure_private_directory(path: Path) -> None:
    """Create a sensitive data directory with restrictive POSIX permissions."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        try:
            path.chmod(0o700)
        except OSError as error:
            raise PermissionError("Application data permissions could not be restricted.") from error
        if stat.S_IMODE(path.stat().st_mode) != 0o700:
            raise PermissionError("Application data permissions could not be restricted.")


def ensure_private_file(path: Path) -> None:
    if os.name == "posix":
        try:
            path.chmod(0o600)
        except OSError as error:
            raise PermissionError("Application data permissions could not be restricted.") from error
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise PermissionError("Application data permissions could not be restricted.")


def _env_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_port(value: str | None) -> int:
    if not value:
        return DEFAULT_PORT
    try:
        port = int(value)
    except ValueError as error:
        raise ValueError("AHV_PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise ValueError("AHV_PORT must be between 1 and 65535")
    return port


def _load_or_create_secret(data_dir: Path) -> str:
    path = data_dir / "session.key"
    try:
        value = path.read_text(encoding="utf-8").strip()
        ensure_private_file(path)
        return value
    except FileNotFoundError:
        pass

    ensure_private_directory(data_dir)
    value = secrets.token_urlsafe(48)
    try:
        with path.open("x", encoding="utf-8") as file:
            file.write(value)
        ensure_private_file(path)
        return value
    except FileExistsError:
        value = path.read_text(encoding="utf-8").strip()
        ensure_private_file(path)
        return value


def base_config(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Resolve environment and platform defaults before explicit overrides."""
    values = environ if environ is not None else os.environ
    data_value = values.get(f"{ENV_PREFIX}DATA_DIR")
    data_dir = normalize_path(data_value) if data_value else platform_data_dir(environ=values)
    source_value = values.get(f"{ENV_PREFIX}HEALTH_DATA_PATH")

    return {
        "DATA_DIR": data_dir,
        "HEALTH_DATA_PATH": normalize_path(source_value) if source_value else None,
        "HOST": values.get(f"{ENV_PREFIX}HOST", DEFAULT_HOST),
        "PORT": _env_port(values.get(f"{ENV_PREFIX}PORT")),
        "OPEN_BROWSER": _env_bool(values.get(f"{ENV_PREFIX}OPEN_BROWSER"), True),
        "TESTING": False,
        "TRUSTED_HOSTS": ["localhost", "127.0.0.1", "[::1]"],
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Strict",
        "SESSION_COOKIE_SECURE": False,
        "MAX_CONTENT_LENGTH": 100 * 1024**3,
        "MAX_FORM_MEMORY_SIZE": 512 * 1024,
        "MAX_FORM_PARTS": 100_000,
    }


def configure_app(app: Flask, overrides: Mapping[str, Any]) -> None:
    """Apply defaults, environment, then explicit CLI/test overrides."""
    app.config.from_mapping(base_config())
    app.config.from_mapping(overrides)

    data_dir = normalize_path(app.config["DATA_DIR"])
    ensure_private_directory(data_dir)
    upload_temp_dir = data_dir / "tmp"
    ensure_private_directory(upload_temp_dir)
    app.config["DATA_DIR"] = data_dir
    app.config["UPLOAD_TEMP_DIR"] = upload_temp_dir
    app.config["SETTINGS_DATABASE"] = data_dir / "settings.sqlite3"

    source = app.config.get("HEALTH_DATA_PATH")
    app.config["HEALTH_DATA_PATH"] = normalize_path(source) if source else None

    host = str(app.config["HOST"])
    trusted = list(app.config["TRUSTED_HOSTS"])
    if host not in {"0.0.0.0", "::"} and host not in trusted:
        trusted.append(host)
    app.config["TRUSTED_HOSTS"] = trusted

    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _load_or_create_secret(data_dir)
