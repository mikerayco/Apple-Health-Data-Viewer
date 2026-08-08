"""Single-worker transactional import orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sqlite3
import threading
from typing import Any
import uuid

from flask import Flask

from .analytics import register_database_lock
from .config import ensure_private_directory, ensure_private_file
from .database import (
    complete_import_activation,
    create_import,
    delete_setting,
    get_import,
    get_setting,
    recover_interrupted_imports,
    set_setting,
    update_import,
    update_settings,
)
from .health_store import HealthParseError, ImportCancelled, ParseResult, parse_export
from .sources import SourceSpec, SourceValidationError, disk_usage, open_export
from .version import __version__

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class ImportBusyError(RuntimeError):
    pass


class ImportStartError(RuntimeError):
    pass


@dataclass
class ImportJob:
    id: str
    source: SourceSpec
    status: str = "pending"
    phase: str = "Waiting to start"
    progress: float = 0.0
    processed_items: int = 0
    total_bytes: int = 0
    error_code: str | None = None
    error_message: str | None = None
    result: ParseResult | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            result = self.result
            return {
                "id": self.id,
                "status": self.status,
                "phase": self.phase,
                "progress": round(self.progress, 4),
                "processed_items": self.processed_items,
                "total_bytes": self.total_bytes,
                "source_kind": self.source.kind,
                "source_label": self.source.label,
                "managed_source_id": self.source.managed_id,
                "error_code": self.error_code,
                "error_message": self.error_message,
                "result": {
                    "record_count": result.record_count,
                    "workout_count": result.workout_count,
                    "duplicate_count": result.duplicate_count,
                    "warning_count": result.warning_count,
                    "export_date": result.export_date,
                    "type_count": result.type_count,
                    "source_count": result.source_count,
                    "route_count": result.route_count,
                    "ecg_count": result.ecg_count,
                }
                if result
                else None,
            }

    def update(self, **values: Any) -> None:
        with self.lock:
            for key, value in values.items():
                setattr(self, key, value)


class ImportManager:
    def __init__(self, app: Flask) -> None:
        self.app = app
        self.settings_database = Path(app.config["SETTINGS_DATABASE"])
        self.data_dir = Path(app.config["DATA_DIR"])
        self.imports_dir = self.data_dir / "imports"
        self.staging_dir = self.imports_dir / "staging"
        self.sources_dir = self.imports_dir / "sources"
        self.active_database = self.data_dir / "health.sqlite3"
        self.previous_database = self.data_dir / "health.sqlite3.previous"
        ensure_private_directory(self.imports_dir)
        ensure_private_directory(self.staging_dir)
        ensure_private_directory(self.sources_dir)
        self._lock = threading.Lock()
        self.database_lock = threading.RLock()
        register_database_lock(self.active_database, self.database_lock)
        self._jobs: dict[str, ImportJob] = {}
        self._active_job: str | None = None
        self._recover_interrupted_removal()
        self._recover_interrupted_activation()
        recover_interrupted_imports(self.settings_database)
        self._clean_stale_staging()

    def _processed_paths(self) -> tuple[Path, ...]:
        return (
            self.data_dir / "health.sqlite3-journal",
            self.data_dir / "health.sqlite3-wal",
            self.data_dir / "health.sqlite3-shm",
            self.previous_database,
            self.active_database,
        )

    def _recover_interrupted_removal(self) -> None:
        keep_active = get_setting(self.settings_database, "setup_complete", "false") == "true"
        for original in self._processed_paths():
            tombstone = original.with_name(f"{original.name}.removing")
            if not tombstone.exists():
                continue
            if keep_active and not original.exists():
                os.replace(tombstone, original)
            else:
                tombstone.unlink()

    def _recover_interrupted_activation(self) -> None:
        pending = get_setting(self.settings_database, "activation_pending")
        had_previous = get_setting(self.settings_database, "activation_had_previous") == "true"
        if pending:
            if self.previous_database.is_file():
                os.replace(self.previous_database, self.active_database)
            elif not had_previous and self.active_database.is_file():
                self.active_database.unlink()
            delete_setting(self.settings_database, "activation_pending")
            delete_setting(self.settings_database, "activation_had_previous")
        elif self.previous_database.is_file():
            self.previous_database.unlink()

    def _clean_stale_staging(self) -> None:
        for child in self.staging_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            elif child.is_file():
                child.unlink(missing_ok=True)

    def is_busy(self) -> bool:
        with self._lock:
            if not self._active_job:
                return False
            active = self._jobs.get(self._active_job)
            return bool(active and active.status not in TERMINAL_STATUSES)

    def start(self, source: SourceSpec) -> ImportJob:
        with self._lock:
            if self._active_job:
                active = self._jobs.get(self._active_job)
                if active and active.status not in TERMINAL_STATUSES:
                    raise ImportBusyError("Another import is already running.")
            job_id = uuid.uuid4().hex
            job = ImportJob(job_id, source)
            self._jobs[job_id] = job
            self._active_job = job_id
            if len(self._jobs) > 20:
                for old_id in list(self._jobs):
                    if old_id != job_id and self._jobs[old_id].status in TERMINAL_STATUSES:
                        self._jobs.pop(old_id)
                        if len(self._jobs) <= 20:
                            break

        try:
            create_import(
                self.settings_database,
                job_id=job_id,
                source_kind=source.kind,
                source_label=source.label,
                source_id=source.managed_id,
            )
        except Exception as error:
            self.app.logger.error("Import could not start because of %s", type(error).__name__)
            job.update(
                status="failed",
                phase="Import could not start",
                error_code="start_failed",
                error_message="The import could not start. Check local storage and try again.",
            )
            with self._lock:
                if self._active_job == job.id:
                    self._active_job = None
            raise ImportStartError("The import could not start.") from error
        thread = threading.Thread(
            target=self._run,
            args=(job,),
            name=f"health-import-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        job = self._jobs.get(job_id)
        if job:
            return job.snapshot()
        stored = get_import(self.settings_database, job_id)
        if not stored:
            return None
        return {
            "id": job_id,
            "status": stored["status"],
            "phase": "Import complete" if stored["status"] == "succeeded" else "Import stopped",
            "progress": 1.0 if stored["status"] in TERMINAL_STATUSES else 0.0,
            "processed_items": int(stored.get("record_count") or 0),
            "total_bytes": 0,
            "source_kind": stored["source_kind"],
            "source_label": stored["source_label"],
            "managed_source_id": stored.get("source_id"),
            "error_code": stored.get("error_code"),
            "error_message": _error_message(str(stored.get("error_code") or "")),
            "result": {
                "record_count": int(stored.get("record_count") or 0),
                "workout_count": int(stored.get("workout_count") or 0),
                "duplicate_count": int(stored.get("duplicate_count") or 0),
                "warning_count": int(stored.get("warning_count") or 0),
                "export_date": stored.get("export_date"),
                "type_count": 0,
                "source_count": 0,
                "route_count": 0,
                "ecg_count": 0,
            }
            if stored["status"] == "succeeded"
            else None,
        }

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status in TERMINAL_STATUSES:
            return False
        job.cancel_event.set()
        job.update(phase="Cancelling safely")
        return True

    def wait(self, job_id: str, timeout: float = 30) -> dict[str, Any] | None:
        deadline = datetime.now(timezone.utc).timestamp() + timeout
        while datetime.now(timezone.utc).timestamp() < deadline:
            snapshot = self.get(job_id)
            if not snapshot or snapshot["status"] in TERMINAL_STATUSES:
                return snapshot
            threading.Event().wait(0.02)
        return self.get(job_id)

    def _progress(self, job: ImportJob, bytes_read: int, items: int, phase: str) -> None:
        total = max(job.total_bytes, 1)
        fraction = min(bytes_read / total, 0.97)
        job.update(progress=fraction, processed_items=items, phase=phase)

    def remove_processed_data(self) -> None:
        """Remove replaceable health snapshots while retaining settings and source uploads."""
        keys = (
            "activation_pending",
            "activation_had_previous",
            "active_source_label",
            "active_managed_source_id",
            "setup_complete",
        )
        with self._lock:
            if self._active_job:
                active = self._jobs.get(self._active_job)
                if active and active.status not in TERMINAL_STATUSES:
                    raise ImportBusyError("Wait for the active import before removing processed data.")
            previous_settings = {
                key: get_setting(self.settings_database, key)
                for key in keys
            }
            tombstones: list[tuple[Path, Path]] = []
            with self.database_lock:
                try:
                    for original in self._processed_paths():
                        if not original.exists():
                            continue
                        tombstone = original.with_name(f"{original.name}.removing")
                        os.replace(original, tombstone)
                        tombstones.append((original, tombstone))
                    update_settings(
                        self.settings_database,
                        {
                            "activation_pending": None,
                            "activation_had_previous": None,
                            "active_source_label": None,
                            "active_managed_source_id": None,
                            "setup_complete": "false",
                        },
                    )
                    for _original, tombstone in tombstones:
                        tombstone.unlink()
                except Exception:
                    for original, tombstone in reversed(tombstones):
                        if tombstone.exists() and not original.exists():
                            os.replace(tombstone, original)
                    update_settings(self.settings_database, previous_settings)
                    raise
                _fsync_directory(self.data_dir)

    def _run(self, job: ImportJob) -> None:
        stage = self.staging_dir / job.id
        staged_database = stage / "health.sqlite3"

        try:
            ensure_private_directory(stage)
            job.update(status="running", phase="Validating source")
            update_import(self.settings_database, job.id, status="running", parser_version=__version__)
            with open_export(job.source) as opened:
                job.update(total_bytes=opened.total_bytes, phase="Checking available disk space")
                free, _total = disk_usage(self.data_dir)
                linked_bytes = sum(
                    size
                    for name, size in opened.source_files
                    if name.lower().endswith((".gpx", ".csv"))
                )
                active_bytes = self.active_database.stat().st_size if self.active_database.is_file() else 0
                required = (
                    int(opened.total_bytes * 1.5 + linked_bytes * 2)
                    + active_bytes
                    + 128 * 1024**2
                )
                if free < required:
                    raise SourceValidationError(
                        "insufficient_disk",
                        "Not enough free disk space is available for a safe staged import.",
                    )
                result = parse_export(
                    opened.stream,
                    staged_database,
                    total_bytes=opened.total_bytes,
                    source_files=opened.source_files,
                    progress=lambda read, items, phase: self._progress(job, read, items, phase),
                    cancelled=job.cancel_event.is_set,
                    asset_opener=opened.open_asset,
                )

            if job.cancel_event.is_set():
                raise ImportCancelled()
            job.update(phase="Activating imported snapshot", progress=0.99)
            with staged_database.open("rb") as database_file:
                os.fsync(database_file.fileno())
            had_previous = self.active_database.is_file()
            if had_previous:
                try:
                    os.link(self.active_database, self.previous_database)
                except OSError:
                    shutil.copy2(self.active_database, self.previous_database)
                    with self.previous_database.open("rb") as previous_file:
                        os.fsync(previous_file.fileno())
                _fsync_directory(self.data_dir)
            set_setting(
                self.settings_database,
                "activation_had_previous",
                "true" if had_previous else "false",
            )
            set_setting(self.settings_database, "activation_pending", job.id)
            self.database_lock.acquire()
            try:
                os.replace(staged_database, self.active_database)
                ensure_private_file(self.active_database)
                _fsync_directory(self.data_dir)
                database_bytes = self.active_database.stat().st_size
                complete_import_activation(
                    self.settings_database,
                    job.id,
                    source_label=job.source.label,
                    managed_source_id=job.source.managed_id,
                    finished_at=_now(),
                    record_count=result.record_count,
                    workout_count=result.workout_count,
                    duplicate_count=result.duplicate_count,
                    warning_count=result.warning_count,
                    health_database_bytes=database_bytes,
                    export_date=result.export_date,
                )
            except Exception:
                if self.previous_database.is_file():
                    os.replace(self.previous_database, self.active_database)
                elif self.active_database.is_file():
                    self.active_database.unlink()
                _fsync_directory(self.data_dir)
                try:
                    delete_setting(self.settings_database, "activation_pending")
                    delete_setting(self.settings_database, "activation_had_previous")
                except sqlite3.Error:
                    pass
                raise
            finally:
                self.database_lock.release()
            if self.previous_database.is_file():
                try:
                    self.previous_database.unlink()
                    _fsync_directory(self.data_dir)
                except OSError:
                    pass
            job.update(status="succeeded", phase="Import complete", progress=1.0, result=result)
        except ImportCancelled:
            self._update_history(
                job.id,
                status="cancelled",
                finished_at=_now(),
                error_code="cancelled",
            )
            job.update(
                status="cancelled",
                phase="Import cancelled",
                error_code="cancelled",
                error_message="The import was cancelled. The previous database is unchanged.",
            )
        except (SourceValidationError, HealthParseError) as error:
            self._update_history(
                job.id,
                status="failed",
                finished_at=_now(),
                error_code=error.code,
            )
            job.update(
                status="failed",
                phase="Import failed safely",
                error_code=error.code,
                error_message=error.public_message,
            )
        except Exception as error:  # Keep private paths and values out of logs and UI.
            self.app.logger.error("Import failed with unexpected %s", type(error).__name__)
            self._update_history(
                job.id,
                status="failed",
                finished_at=_now(),
                error_code="unexpected",
            )
            job.update(
                status="failed",
                phase="Import failed safely",
                error_code="unexpected",
                error_message="The import failed unexpectedly. The previous database is unchanged.",
            )
        finally:
            shutil.rmtree(stage, ignore_errors=True)
            with self._lock:
                if self._active_job == job.id:
                    self._active_job = None

    def _update_history(self, job_id: str, **values: object) -> None:
        try:
            update_import(self.settings_database, job_id, **values)
        except sqlite3.Error as error:
            self.app.logger.error("Import history update failed with %s", type(error).__name__)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error_message(code: str) -> str | None:
    messages = {
        "interrupted": "The app stopped during import. The previous database is unchanged.",
        "cancelled": "The import was cancelled. The previous database is unchanged.",
        "unexpected": "The import failed unexpectedly. The previous database is unchanged.",
    }
    return messages.get(code)
