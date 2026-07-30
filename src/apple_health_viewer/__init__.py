"""Local-first Apple Health export viewer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Flask

from .config import configure_app
from .database import init_database
from .version import __version__
from .views import web


def create_app(overrides: dict[str, Any] | None = None) -> Flask:
    """Create and configure the local Flask application."""
    app = Flask(__name__, instance_relative_config=False)
    configure_app(app, overrides or {})

    data_dir = Path(app.config["DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)
    init_database(app.config["SETTINGS_DATABASE"])

    app.register_blueprint(web)

    from .security import apply_security_headers, csrf_token

    app.jinja_env.globals["csrf_token"] = csrf_token
    app.after_request(apply_security_headers)
    return app
