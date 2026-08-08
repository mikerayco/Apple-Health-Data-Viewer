"""Server-rendered local application and import routes."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
from typing import Any
import uuid

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import SecurityError

from .analytics import (
    AnalyticsError,
    category_summary,
    insights_summary,
    metric_summary,
    metric_trend_summary,
    open_health_database,
    overview_summary,
    resolve_window,
    sleep_summary,
    sleep_trend_summary,
)
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
from .import_manager import ImportBusyError, ImportManager, ImportStartError
from .security import csrf_protected
from .sources import (
    SourceSpec,
    SourceValidationError,
    disk_usage,
    retain_folder_upload,
    retain_zip_upload,
)
from .specialized import (
    ecg_detail,
    ecg_list,
    workout_detail,
    workout_list,
    workout_route,
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
        "description": "Source-aware steps, distance, energy, exercise, flights, and stand summaries.",
        "metrics": ("Steps", "Active energy", "Exercise", "Distance"),
    },
    "heart": {
        "title": "Vitals & Metabolic",
        "eyebrow": "Signals and measurements",
        "description": "Heart, respiratory, oxygen, blood pressure, and blood glucose measurements with visible coverage.",
        "metrics": ("Resting HR", "HRV", "Blood glucose", "VO₂ max"),
    },
    "sleep": {
        "title": "Sleep",
        "eyebrow": "Rest and consistency",
        "description": "Duration, stages, wake-day sessions, and coverage—with missing nights kept visible.",
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
        "loopback_only": str(current_app.config["HOST"]) in {"127.0.0.1", "localhost", "::1", "[::1]"},
        "version": __version__,
        **values,
    }


def _quality() -> dict[str, object] | None:
    manager = _manager()
    with manager.database_lock:
        return read_data_quality(manager.active_database)


def _date_parameters() -> dict[str, str | None]:
    return {
        "period": request.args.get("period", "30d"),
        "start": request.args.get("start"),
        "end": request.args.get("end"),
    }


def _granularity_parameter() -> str | None:
    return request.args.get("granularity")


def _integer_parameter(name: str, default: int) -> int:
    value = request.args.get(name)
    if value in {None, ""}:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise AnalyticsError("invalid_parameter", f"Choose a valid {name}.") from error


def _filter_id(name: str) -> int | None:
    value = request.args.get(name)
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except ValueError as error:
        raise AnalyticsError("invalid_filter", f"Choose a valid {name}.") from error


def _dashboard_data(kind: str) -> tuple[dict[str, object] | None, AnalyticsError | None]:
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            window = resolve_window(connection, **_date_parameters())
            unit_system = _stored_setting("unit_system", "metric")
            if kind == "overview":
                return overview_summary(
                    connection,
                    window,
                    unit_system,
                    granularity=_granularity_parameter(),
                ), None
            return category_summary(
                connection,
                kind,
                window,
                unit_system,
                granularity=_granularity_parameter(),
            ), None
    except AnalyticsError as error:
        return None, error


def _start_import(source: SourceSpec):
    try:
        job = _manager().start(source)
    except ImportBusyError:
        flash("Another import is already running.", "warning")
        return redirect(url_for("web.setup"))
    except ImportStartError:
        flash("The import could not start. Check local storage and try again.", "error")
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
    dashboard, analytics_error = _dashboard_data("overview")
    return render_template(
        "overview.html",
        **_page_context(
            "overview",
            quality=_quality(),
            dashboard=dashboard,
            analytics_error=analytics_error,
            selected_period=request.args.get("period", "30d"),
            selected_granularity=request.args.get("granularity", "auto"),
        ),
    )


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
    dashboard = analytics_error = None
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            window = resolve_window(connection, **_date_parameters())
            dashboard = workout_list(
                connection,
                window,
                _stored_setting("unit_system", "metric"),
                activity_type=request.args.get("type") or None,
                search=request.args.get("q") or None,
                source_id=_filter_id("source"),
                device_id=_filter_id("device"),
                route_only=request.args.get("route") == "1",
                page=_integer_parameter("page", 1),
                per_page=_integer_parameter("per_page", 25),
            )
    except AnalyticsError as error:
        analytics_error = error
    return render_template(
        "workouts.html",
        **_page_context(
            "workouts",
            dashboard=dashboard,
            analytics_error=analytics_error,
            quality=_quality(),
            selected_period=request.args.get("period", "30d"),
            selected_granularity=request.args.get("granularity", "auto"),
        ),
    )


@web.get("/workouts/<int:workout_id>")
def workout(workout_id: int):
    detail = analytics_error = None
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            detail = workout_detail(
                connection,
                workout_id,
                _stored_setting("unit_system", "metric"),
            )
    except AnalyticsError as error:
        analytics_error = error
    if analytics_error and analytics_error.code == "not_found":
        abort(404)
    return render_template(
        "workout_detail.html",
        **_page_context("workouts", workout=detail, analytics_error=analytics_error),
    )


@web.get("/ecgs")
def ecgs():
    dashboard = analytics_error = None
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            window = resolve_window(connection, **_date_parameters())
            dashboard = ecg_list(
                connection,
                window,
                classification=request.args.get("classification") or None,
                page=_integer_parameter("page", 1),
                per_page=_integer_parameter("per_page", 25),
            )
    except AnalyticsError as error:
        analytics_error = error
    return render_template(
        "ecgs.html",
        **_page_context(
            "ecgs",
            dashboard=dashboard,
            analytics_error=analytics_error,
            quality=_quality(),
            selected_period=request.args.get("period", "30d"),
            selected_granularity=request.args.get("granularity", "auto"),
        ),
    )


@web.get("/ecgs/<int:ecg_id>")
def ecg(ecg_id: int):
    detail = analytics_error = None
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            detail = ecg_detail(connection, ecg_id, include_samples=False)
    except AnalyticsError as error:
        analytics_error = error
    if analytics_error and analytics_error.code == "not_found":
        abort(404)
    return render_template(
        "ecg_detail.html",
        **_page_context("ecgs", ecg=detail, analytics_error=analytics_error),
    )


def _category(key: str, active_page: str):
    dashboard = analytics_error = None
    if key in {"activity", "heart", "sleep", "body"}:
        dashboard, analytics_error = _dashboard_data(key)
    return render_template(
        "category.html",
        **_page_context(
            active_page,
            page=CATEGORY_PAGES[key],
            quality=_quality(),
            dashboard=dashboard,
            analytics_error=analytics_error,
            selected_period=request.args.get("period", "30d"),
            selected_granularity=request.args.get("granularity", "auto"),
        ),
    )


@web.get("/data-quality")
def data_quality():
    quality = _quality()
    return render_template(
        "data_quality.html",
        **_page_context("data_quality", quality=quality),
    )


@web.get("/api/overview")
def overview_api():
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            window = resolve_window(connection, **_date_parameters())
            return jsonify(
                {
                    "status": "ready",
                    **overview_summary(
                        connection,
                        window,
                        _stored_setting("unit_system", "metric"),
                        granularity=_granularity_parameter(),
                    ),
                }
            )
    except AnalyticsError as error:
        status = 409 if error.code in {"empty", "reimport_required", "no_supported_metrics"} else 400
        return jsonify({"status": error.code, "message": error.public_message}), status


@web.get("/api/metrics/<metric_key>")
def metric_api(metric_key: str):
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            window = resolve_window(connection, **_date_parameters())
            return jsonify(
                {
                    "status": "ready",
                    **metric_summary(
                        connection,
                        metric_key,
                        window,
                        _stored_setting("unit_system", "metric"),
                        source_id=_filter_id("source"),
                        device_id=_filter_id("device"),
                        granularity=_granularity_parameter(),
                    ),
                }
            )
    except AnalyticsError as error:
        status = 404 if error.code == "unknown_metric" else 409 if error.code in {"empty", "reimport_required", "no_supported_metrics"} else 400
        return jsonify({"status": error.code, "message": error.public_message}), status


@web.get("/api/metrics/<metric_key>/trend")
def metric_trend_api(metric_key: str):
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            window = resolve_window(connection, **_date_parameters())
            return jsonify(
                {
                    "status": "ready",
                    **metric_trend_summary(
                        connection,
                        metric_key,
                        window,
                        _stored_setting("unit_system", "metric"),
                        source_id=_filter_id("source"),
                        device_id=_filter_id("device"),
                        granularity=_granularity_parameter(),
                    ),
                }
            )
    except AnalyticsError as error:
        status = 404 if error.code == "unknown_metric" else 409 if error.code in {"empty", "reimport_required", "no_supported_metrics"} else 400
        return jsonify({"status": error.code, "message": error.public_message}), status


@web.get("/api/sleep")
def sleep_api():
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            window = resolve_window(connection, **_date_parameters())
            return jsonify(
                {
                    "status": "ready",
                    **sleep_summary(connection, window, _granularity_parameter()),
                }
            )
    except AnalyticsError as error:
        status = 409 if error.code in {"empty", "reimport_required", "no_supported_metrics"} else 400
        return jsonify({"status": error.code, "message": error.public_message}), status


@web.get("/api/sleep/trend")
def sleep_trend_api():
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            window = resolve_window(connection, **_date_parameters())
            return jsonify(
                {
                    "status": "ready",
                    **sleep_trend_summary(connection, window, _granularity_parameter()),
                }
            )
    except AnalyticsError as error:
        status = 409 if error.code in {"empty", "reimport_required", "no_supported_metrics"} else 400
        return jsonify({"status": error.code, "message": error.public_message}), status


@web.get("/api/workouts")
def workouts_api():
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            window = resolve_window(connection, **_date_parameters())
            return jsonify(
                {
                    "status": "ready",
                    **workout_list(
                        connection,
                        window,
                        _stored_setting("unit_system", "metric"),
                        activity_type=request.args.get("type") or None,
                        search=request.args.get("q") or None,
                        source_id=_filter_id("source"),
                        device_id=_filter_id("device"),
                        route_only=request.args.get("route") == "1",
                        page=_integer_parameter("page", 1),
                        per_page=_integer_parameter("per_page", 25),
                    ),
                }
            )
    except AnalyticsError as error:
        status = 409 if error.code in {"empty", "reimport_required", "no_supported_metrics"} else 400
        return jsonify({"status": error.code, "message": error.public_message}), status


@web.get("/api/workouts/<int:workout_id>")
def workout_api(workout_id: int):
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            return jsonify(
                {
                    "status": "ready",
                    **workout_detail(
                        connection,
                        workout_id,
                        _stored_setting("unit_system", "metric"),
                    ),
                }
            )
    except AnalyticsError as error:
        status = 404 if error.code == "not_found" else 409 if error.code in {"empty", "reimport_required"} else 400
        return jsonify({"status": error.code, "message": error.public_message}), status


@web.get("/api/workouts/<int:workout_id>/route")
def workout_route_api(workout_id: int):
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            return jsonify(
                {
                    "status": "ready",
                    **workout_route(connection, workout_id, _integer_parameter("limit", 1200)),
                }
            )
    except AnalyticsError as error:
        status = 404 if error.code == "not_found" else 409 if error.code in {"empty", "reimport_required"} else 400
        return jsonify({"status": error.code, "message": error.public_message}), status


@web.get("/api/ecgs")
def ecgs_api():
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            window = resolve_window(connection, **_date_parameters())
            return jsonify(
                {
                    "status": "ready",
                    **ecg_list(
                        connection,
                        window,
                        classification=request.args.get("classification") or None,
                        page=_integer_parameter("page", 1),
                        per_page=_integer_parameter("per_page", 25),
                    ),
                }
            )
    except AnalyticsError as error:
        status = 409 if error.code in {"empty", "reimport_required", "no_supported_metrics"} else 400
        return jsonify({"status": error.code, "message": error.public_message}), status


@web.get("/api/ecgs/<int:ecg_id>")
def ecg_api(ecg_id: int):
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            return jsonify(
                {
                    "status": "ready",
                    **ecg_detail(connection, ecg_id, _integer_parameter("limit", 5000)),
                }
            )
    except AnalyticsError as error:
        status = 404 if error.code == "not_found" else 409 if error.code in {"empty", "reimport_required"} else 400
        return jsonify({"status": error.code, "message": error.public_message}), status


@web.get("/api/insights")
def insights_api():
    category = request.args.get("category")
    if category not in {None, "activity", "heart", "sleep", "body"}:
        return jsonify({"status": "invalid_category", "message": "Choose a valid insight category."}), 400
    try:
        with closing(open_health_database(_manager().active_database)) as connection:
            window = resolve_window(connection, **_date_parameters())
            return jsonify(
                {
                    "status": "ready",
                    "window": window.as_dict(),
                    "insights": insights_summary(
                        connection,
                        window,
                        _stored_setting("unit_system", "metric"),
                        category=category,
                    ),
                }
            )
    except AnalyticsError as error:
        status = 409 if error.code in {"empty", "reimport_required", "no_supported_metrics"} else 400
        return jsonify({"status": error.code, "message": error.public_message}), status


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


@web.post("/settings/data/remove")
@csrf_protected
def remove_processed_data():
    if request.form.get("confirmation") != "remove":
        flash("Confirm that you want to remove the processed health database.", "warning")
        return redirect(url_for("web.settings"))
    try:
        _manager().remove_processed_data()
    except ImportBusyError:
        flash("Wait for the active import before removing processed data.", "warning")
        return redirect(url_for("web.settings"))
    except (OSError, sqlite3.Error):
        flash("The processed database could not be removed. Close other local viewer windows and try again.", "error")
        return redirect(url_for("web.settings"))
    flash("Processed health data removed. Retained sources and preferences were not deleted.", "success")
    return redirect(url_for("web.setup"))


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
    return jsonify({"status": "ok", "version": __version__, "phase": 6})


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
