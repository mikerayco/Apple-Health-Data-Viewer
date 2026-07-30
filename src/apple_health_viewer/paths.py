"""Cross-platform paths for local application data."""

from __future__ import annotations

import os
from pathlib import Path
import platform
from typing import Mapping

APP_DIRECTORY = "Apple Health Data Viewer"
LINUX_DIRECTORY = "apple-health-data-viewer"


def platform_data_dir(
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the native per-user data directory without third-party helpers."""
    os_name = system or platform.system()
    values = environ if environ is not None else os.environ
    user_home = home or Path.home()

    if os_name == "Darwin":
        return user_home / "Library" / "Application Support" / APP_DIRECTORY
    if os_name == "Windows":
        local_app_data = values.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else user_home / "AppData" / "Local"
        return base / APP_DIRECTORY

    xdg_data_home = values.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else user_home / ".local" / "share"
    return base / LINUX_DIRECTORY


def normalize_path(value: str | Path) -> Path:
    """Expand a user path and make it absolute without requiring it to exist."""
    return Path(value).expanduser().resolve(strict=False)
