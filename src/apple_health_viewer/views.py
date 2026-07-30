"""Server-rendered Phase 1 application shell."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import SecurityError

from .database import delete_setting, get_setting, schema_version, set_setting
from .security import csrf_protected
from .version import __version__

web = Blueprint("web", __name__)

NAVIGATION = (
    ("overview", "Overview", "overview"),
    ("activity", "Activity", "activity"),
    ("heart", "Heart & Vitals", "heart"),
    ("sleep", "Sleep", "sleep"),
    ("body", "Body", "body"),
    ("workouts", "Workouts", "workouts"),
    ("ecgs", "ECGs", "ecg"),
    ("data_quality", "Data Quality", "quality"),
)

CATEGORY_PAGES = {
    "activity": {
        "title": "Activity",
        "eyebrow": "Movement over time",
        "description": "Steps, distance, energy, exercise, flights, and stand time will live here.",
        "metrics": ("Steps", "Active energy", "Exercise", "Distance"),
    },
    "heart": {
        "title": "Heart & Vitals",
        "eyebrow": "Signals and measurements",
        "description": "Resting heart rate, HRV, respiratory rate, oxygen saturation, and other available vitals.",
        "metrics": ("Resting HR", "HRV", "Respiratory", "VO₂ max"),
    },
    "sleep": {
        "title": "Sleep",
        "eyebrow": "Rest and consistency",
        "description": "Duration, stages, bedtime, wake time, and coverage—with missing nights kept visible.",
        "metrics": ("Time asleep", "In bed", "Consistency", "Nights logged"),
    },
    "body": {
        "title": "Body",
        "eyebrow": "Long-term measures",
        "description": "Weight, BMI, body composition, and measurement frequency without silent outlier removal.",
        "metrics": ("Body mass", "BMI", "Body fat", "Measurements"),
    },
    "workouts": {
        "title": "Workouts",
        "eyebrow": "Sessions and routes",
        "description": "Workout types, frequency, duration, distance, energy, and offline route detail.",
        "metrics": ("Sessions", "Duration", "Distance", "Workout types"),
    },
    "ecgs": {
        "title": "ECGs",
        "eyebrow": "Local waveform review",
        "description": "Apple-provided ECG classifications, metadata, and waveforms without independent diagnosis.",
        "metrics": ("Recordings", "Latest", "Sample rate", "Duration"),
    },
    "data-quality": {
        "title": "Data Quality",
        "eyebrow": "Coverage and provenance",
        "description": "Supported types, source overlap, duplicates, missing periods, and import warnings.",
        "metrics": ("Record types", "Sources", "Coverage", "Warnings"),
    },
}


def _database() -> Path:
    return Path(current_app.config["SETTINGS_DATABASE"])


def _stored_setting(key: str, default: str) -> str:
    return get_setting(_database(), key, default) or default


def _source() -> tuple[Path | None, str | None]:
    configured = current_app.config.get("HEALTH_DATA_PATH")
    if configured:
        return Path(configured), "command line or environment"
    stored = get_setting(_database(), "health_data_path")
    return (Path(stored), "local settings") if stored else (None, None)


def _page_context(active_page: str, **values: Any) -> dict[str, Any]:
    source, source_origin = _source()
    theme = _stored_setting("theme", "system")
    if theme not in {"system", "light", "dark"}:
        theme = "system"
    unit_system = _stored_setting("unit_system", "metric")
    if unit_system not in {"metric", "imperial"}:
        unit_system = "metric"
    next_theme = {"system": "light", "light": "dark", "dark": "system"}[theme]
    return {
        "active_page": active_page,
        "navigation": NAVIGATION,
        "theme": theme,
        "next_theme": next_theme,
        "unit_system": unit_system,
        "source_name": source.name if source else None,
        "source_origin": source_origin,
        "version": __version__,
        **values,
    }


@web.get("/")
def index():
    if _stored_setting("setup_complete", "false") == "true":
        return redirect(url_for("web.overview"))
    return redirect(url_for("web.setup"))


@web.get("/setup")
def setup():
    return render_template("setup.html", **_page_context("setup"))


@web.post("/setup/source")
@csrf_protected
def save_source():
    if current_app.config.get("HEALTH_DATA_PATH"):
        flash("The source is controlled by a command-line option or environment variable.", "warning")
        return redirect(url_for("web.setup"))

    raw_path = request.form.get("health_data_path", "").strip()
    if not raw_path:
        flash("Enter the path to an Apple Health ZIP or unzipped folder.", "error")
        return redirect(url_for("web.setup"))

    source = Path(raw_path).expanduser().resolve(strict=False)
    if not source.exists():
        flash("That local path does not exist.", "error")
        return redirect(url_for("web.setup"))
    if source.is_file() and source.suffix.lower() != ".zip":
        flash("Choose an Apple Health ZIP or an unzipped export folder.", "error")
        return redirect(url_for("web.setup"))
    if not source.is_file() and not source.is_dir():
        flash("The selected source is not a regular file or folder.", "error")
        return redirect(url_for("web.setup"))

    set_setting(_database(), "health_data_path", str(source))
    flash("Source saved. Parsing and import begin in Phase 2.", "success")
    return redirect(url_for("web.setup"))


@web.post("/setup/source/remove")
@csrf_protected
def remove_source():
    if current_app.config.get("HEALTH_DATA_PATH"):
        flash("Remove the command-line option or environment variable to clear this source.", "warning")
    else:
        delete_setting(_database(), "health_data_path")
        flash("Saved source removed. No source files were changed.", "success")
    return redirect(url_for("web.setup"))


@web.get("/overview")
def overview():
    return render_template("overview.html", **_page_context("overview"))


@web.get("/activity")
def activity():
    return _category("activity", "activity")


@web.get("/heart")
def heart():
    return _category("heart", "heart")


@web.get("/sleep")
def sleep():
    return _category("sleep", "sleep")


@web.get("/body")
def body():
    return _category("body", "body")


@web.get("/workouts")
def workouts():
    return _category("workouts", "workouts")


@web.get("/ecgs")
def ecgs():
    return _category("ecgs", "ecgs")


@web.get("/data-quality")
def data_quality():
    return _category("data-quality", "data_quality")


def _category(key: str, active_page: str):
    return render_template(
        "category.html",
        **_page_context(active_page, page=CATEGORY_PAGES[key]),
    )


@web.get("/settings")
def settings():
    return render_template(
        "settings.html",
        **_page_context("settings", schema_version=schema_version(_database())),
    )


@web.post("/settings/preferences")
@csrf_protected
def save_preferences():
    theme = request.form.get("theme", "")
    unit_system = request.form.get("unit_system", "")
    if theme not in {"system", "light", "dark"}:
        flash("Choose a valid theme.", "error")
        return redirect(url_for("web.settings"))
    if unit_system not in {"metric", "imperial"}:
        flash("Choose Metric or Imperial units.", "error")
        return redirect(url_for("web.settings"))
    set_setting(_database(), "theme", theme)
    set_setting(_database(), "unit_system", unit_system)
    flash("Preferences saved locally.", "success")
    return redirect(url_for("web.settings"))


@web.post("/settings/theme")
@csrf_protected
def cycle_theme():
    theme = request.form.get("theme", "system")
    if theme not in {"system", "light", "dark"}:
        theme = "system"
    set_setting(_database(), "theme", theme)
    target = request.form.get("next", "")
    if not target.startswith("/") or target.startswith("//"):
        target = url_for("web.overview")
    return redirect(target)


@web.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "version": __version__, "phase": 1})


@web.app_errorhandler(400)
def bad_request(error):
    if isinstance(error, SecurityError):
        return current_app.response_class("Bad Request\n", status=400, mimetype="text/plain")
    return render_template(
        "error.html",
        **_page_context("error", status=400, message=str(error.description)),
    ), 400


@web.app_errorhandler(404)
def not_found(_error):
    return render_template(
        "error.html",
        **_page_context("error", status=404, message="That page does not exist."),
    ), 404
