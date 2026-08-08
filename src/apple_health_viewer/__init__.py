"""Local-first Apple Health export viewer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Flask

from .config import configure_app
from .database import init_database
from .import_manager import ImportManager
from .version import __version__
from .views import web


def create_app(overrides: dict[str, Any] | None = None) -> Flask:
    """Create and configure the local Flask application."""
    app = Flask(__name__, instance_relative_config=False)
    configure_app(app, overrides or {})

    from .security import PrivateUploadRequest, apply_security_headers, csrf_token, guard_unsafe_request

    app.request_class = PrivateUploadRequest
    data_dir = Path(app.config["DATA_DIR"])
    init_database(app.config["SETTINGS_DATABASE"])
    app.extensions["import_manager"] = ImportManager(app)

    app.register_blueprint(web)

    app.jinja_env.globals["csrf_token"] = csrf_token
    app.before_request(guard_unsafe_request)
    app.after_request(apply_security_headers)
    return app
