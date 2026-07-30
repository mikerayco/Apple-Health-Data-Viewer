"""Server-rendered local application and import routes."""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any
import uuid

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import SecurityError

from .database import (
    add_managed_source,
    delete_setting,
    get_managed_source,
    get_setting,
    managed_sources,
    recent_imports,
    remove_managed_source,
    schema_version,
    set_setting,
)
from .health_store import read_data_quality
from .import_manager import ImportBusyError, ImportManager
from .security import csrf_protected
from .sources import (
    SourceSpec,
    SourceValidationError,
    disk_usage,
    retain_folder_upload,
    retain_zip_upload,
)
from .version import __version__

web = Blueprint("web", __name__)

NAVIGATION = (
    ("overview", "Overview", "overview"),
    ("activity", "Activity", "activity"),
    ("heart", "Vitals & Metabolic", "heart"),
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
        "title": "Vitals & Metabolic",
        "eyebrow": "Signals and measurements",
        "description": "Heart, respiratory, oxygen, blood pressure, and blood glucose trends will live here.",
        "metrics": ("Resting HR", "HRV", "Blood glucose", "VO₂ max"),
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
}


def _database() -> Path:
    return Path(current_app.config["SETTINGS_DATABASE"])


def _manager() -> ImportManager:
    return current_app.extensions["import_manager"]


def _stored_setting(key: str, default: str) -> str:
    return get_setting(_database(), key, default) or default


def _source() -> tuple[Path | None, str | None, str | None]:
    configured = current_app.config.get("HEALTH_DATA_PATH")
    if configured:
        path = Path(configured)
        return path, "command line or environment", path.name
    active_id = get_setting(_database(), "active_managed_source_id")
    if active_id:
        managed = get_managed_source(_database(), active_id)
        if managed:
            return Path(str(managed["path"])), "retained upload", str(managed["label"])
    stored = get_setting(_database(), "health_data_path")
    if stored:
        path = Path(stored)
        return path, "local settings", path.name
    label = get_setting(_database(), "active_source_label")
    return None, "last successful import" if label else None, label


def _page_context(active_page: str, **values: Any) -> dict[str, Any]:
    source, source_origin, source_label = _source()
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
        "source_name": source_label or (source.name if source else None),
        "source_origin": source_origin,
        "version": __version__,
        **values,
    }


def _quality() -> dict[str, object] | None:
    return read_data_quality(_manager().active_database)


def _start_import(source: SourceSpec):
    try:
        job = _manager().start(source)
    except ImportBusyError:
        flash("Another import is already running.", "warning")
        return redirect(url_for("web.setup"))
    return redirect(url_for("web.import_progress", job_id=job.id))


@web.get("/")
def index():
    if _stored_setting("setup_complete", "false") == "true":
        return redirect(url_for("web.overview"))
    return redirect(url_for("web.setup"))


@web.get("/setup")
def setup():
    free, total = disk_usage(Path(current_app.config["DATA_DIR"]))
    return render_template(
        "setup.html",
        **_page_context(
            "setup",
            retained_sources=managed_sources(_database()),
            import_history=recent_imports(_database(), 5),
            import_busy=_manager().is_busy(),
            free_bytes=free,
            total_bytes=total,
            has_health_database=_manager().active_database.is_file(),
        ),
    )


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
    flash("Source saved. Start the import when you are ready.", "success")
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


@web.post("/imports/configured")
@csrf_protected
def import_configured_source():
    configured = current_app.config.get("HEALTH_DATA_PATH")
    stored = get_setting(_database(), "health_data_path")
    value = configured or stored
    if not value:
        flash("Configure a local source path first.", "error")
        return redirect(url_for("web.setup"))
    path = Path(value)
    return _start_import(SourceSpec("configured_path", path, path.name))


@web.post("/imports/upload/zip")
@csrf_protected
def upload_zip():
    if _manager().is_busy():
        flash("Another import is already running.", "warning")
        return redirect(url_for("web.setup"))
    upload = request.files.get("zip_file")
    if not upload or not upload.filename:
        flash("Choose an Apple Health ZIP file.", "error")
        return redirect(url_for("web.setup"))
    source_id = uuid.uuid4().hex
    try:
        path, label, size = retain_zip_upload(upload, _manager().sources_dir, source_id)
    except SourceValidationError as error:
        flash(error.public_message, "error")
        return redirect(url_for("web.setup"))
    except OSError:
        flash("The ZIP could not be retained in local app storage.", "error")
        return redirect(url_for("web.setup"))
    add_managed_source(
        _database(),
        source_id=source_id,
        kind="zip_upload",
        label=label,
        source_path=path,
        size_bytes=size,
    )
    return _start_import(SourceSpec("zip_upload", path, label, source_id))


@web.post("/imports/upload/folder")
@csrf_protected
def upload_folder():
    if _manager().is_busy():
        flash("Another import is already running.", "warning")
        return redirect(url_for("web.setup"))
    uploads = [item for item in request.files.getlist("folder_files") if item.filename]
    source_id = uuid.uuid4().hex
    try:
        path, label, size = retain_folder_upload(uploads, _manager().sources_dir, source_id)
    except SourceValidationError as error:
        flash(error.public_message, "error")
        return redirect(url_for("web.setup"))
    except OSError:
        flash("The folder could not be retained in local app storage.", "error")
        return redirect(url_for("web.setup"))
    add_managed_source(
        _database(),
        source_id=source_id,
        kind="folder_upload",
        label=label,
        source_path=path,
        size_bytes=size,
    )
    return _start_import(SourceSpec("folder_upload", path, label, source_id))


@web.post("/imports/source/<source_id>")
@csrf_protected
def reimport_managed_source(source_id: str):
    source = get_managed_source(_database(), source_id)
    if not source:
        abort(404)
    return _start_import(
        SourceSpec(
            str(source["kind"]),
            Path(str(source["path"])),
            str(source["label"]),
            source_id,
        )
    )


@web.post("/imports/source/<source_id>/delete")
@csrf_protected
def delete_managed_source(source_id: str):
    if _manager().is_busy():
        flash("Wait for the active import before deleting a retained source.", "warning")
        return redirect(url_for("web.setup"))
    source = get_managed_source(_database(), source_id)
    if not source:
        abort(404)
    path = Path(str(source["path"])).resolve(strict=False)
    allowed = _manager().sources_dir.resolve(strict=True)
    try:
        relative = path.relative_to(allowed)
    except ValueError:
        abort(400, description="The retained source path is invalid.")
    if not relative.parts:
        abort(400, description="The retained source path is invalid.")
    source_root = allowed / relative.parts[0]
    if source_root.is_dir():
        shutil.rmtree(source_root)
    elif source_root.is_file():
        source_root.unlink()
    remove_managed_source(_database(), source_id)
    if get_setting(_database(), "active_managed_source_id") == source_id:
        set_setting(_database(), "active_managed_source_id", "")
    flash("Retained source deleted. The processed health database remains available.", "success")
    return redirect(url_for("web.setup"))


@web.get("/imports/<job_id>")
def import_progress(job_id: str):
    job = _manager().get(job_id)
    if not job:
        abort(404)
    return render_template("import_progress.html", **_page_context("setup", job=job))


@web.get("/api/imports/<job_id>/status")
def import_status(job_id: str):
    job = _manager().get(job_id)
    if not job:
        return jsonify({"error": "not_found"}), 404
    return jsonify(job)


@web.post("/imports/<job_id>/cancel")
@csrf_protected
def cancel_import(job_id: str):
    if not _manager().cancel(job_id):
        flash("That import is no longer running.", "warning")
    return redirect(url_for("web.import_progress", job_id=job_id))


@web.get("/overview")
def overview():
    return render_template("overview.html", **_page_context("overview", quality=_quality()))


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


def _category(key: str, active_page: str):
    return render_template(
        "category.html",
        **_page_context(active_page, page=CATEGORY_PAGES[key], quality=_quality()),
    )


@web.get("/data-quality")
def data_quality():
    quality = _quality()
    return render_template(
        "data_quality.html",
        **_page_context("data_quality", quality=quality),
    )


@web.get("/api/data-quality")
def data_quality_api():
    quality = _quality()
    if not quality:
        return jsonify({"status": "empty"})
    return jsonify({"status": "ready", **quality})


@web.get("/settings")
def settings():
    free, total = disk_usage(Path(current_app.config["DATA_DIR"]))
    health_bytes = _manager().active_database.stat().st_size if _manager().active_database.is_file() else 0
    retained_bytes = sum(int(item["size_bytes"]) for item in managed_sources(_database()))
    return render_template(
        "settings.html",
        **_page_context(
            "settings",
            schema_version=schema_version(_database()),
            health_bytes=health_bytes,
            retained_bytes=retained_bytes,
            free_bytes=free,
            total_bytes=total,
        ),
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
    return jsonify({"status": "ok", "version": __version__, "phase": 2})


@web.app_template_filter("bytesize")
def bytesize(value: object) -> str:
    size = float(value or 0)
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "0 B"


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
