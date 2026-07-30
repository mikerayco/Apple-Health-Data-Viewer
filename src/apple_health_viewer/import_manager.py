"""Single-worker transactional import orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import threading
from typing import Any
import uuid

from flask import Flask

from .database import (
    create_import,
    get_import,
    recover_interrupted_imports,
    set_setting,
    update_import,
)
from .health_store import HealthParseError, ImportCancelled, ParseResult, parse_export
from .sources import SourceSpec, SourceValidationError, disk_usage, open_export
from .version import __version__

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class ImportBusyError(RuntimeError):
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
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._jobs: dict[str, ImportJob] = {}
        self._active_job: str | None = None
        recover_interrupted_imports(self.settings_database)
        self._clean_stale_staging()

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

        create_import(
            self.settings_database,
            job_id=job_id,
            source_kind=source.kind,
            source_label=source.label,
            source_id=source.managed_id,
        )
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

    def _run(self, job: ImportJob) -> None:
        stage = self.staging_dir / job.id
        staged_database = stage / "health.sqlite3"
        stage.mkdir(parents=True, exist_ok=False)
        job.update(status="running", phase="Validating source")
        update_import(self.settings_database, job.id, status="running", parser_version=__version__)

        try:
            with open_export(job.source) as opened:
                job.update(total_bytes=opened.total_bytes, phase="Checking available disk space")
                free, _total = disk_usage(self.data_dir)
                required = int(opened.total_bytes * 1.5) + 128 * 1024**2
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
                )

            if job.cancel_event.is_set():
                raise ImportCancelled()
            job.update(phase="Activating imported snapshot", progress=0.99)
            with staged_database.open("rb") as database_file:
                os.fsync(database_file.fileno())
            os.replace(staged_database, self.active_database)
            database_bytes = self.active_database.stat().st_size
            set_setting(self.settings_database, "setup_complete", "true")
            set_setting(self.settings_database, "active_source_label", job.source.label)
            if job.source.managed_id:
                set_setting(self.settings_database, "active_managed_source_id", job.source.managed_id)
            else:
                set_setting(self.settings_database, "active_managed_source_id", "")
            finished = _now()
            update_import(
                self.settings_database,
                job.id,
                status="succeeded",
                finished_at=finished,
                record_count=result.record_count,
                workout_count=result.workout_count,
                duplicate_count=result.duplicate_count,
                warning_count=result.warning_count,
                health_database_bytes=database_bytes,
                export_date=result.export_date,
                error_code=None,
            )
            job.update(status="succeeded", phase="Import complete", progress=1.0, result=result)
        except ImportCancelled:
            update_import(
                self.settings_database,
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
            update_import(
                self.settings_database,
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
            update_import(
                self.settings_database,
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error_message(code: str) -> str | None:
    messages = {
        "interrupted": "The app stopped during import. The previous database is unchanged.",
        "cancelled": "The import was cancelled. The previous database is unchanged.",
        "unexpected": "The import failed unexpectedly. The previous database is unchanged.",
    }
    return messages.get(code)
