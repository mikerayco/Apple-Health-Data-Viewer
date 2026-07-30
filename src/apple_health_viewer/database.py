"""Small explicit SQLite settings store with numbered migrations."""

from __future__ import annotations

from contextlib import closing
from importlib import resources
from pathlib import Path
import sqlite3
from typing import Iterator

DEFAULT_SETTINGS = {
    "theme": "system",
    "unit_system": "metric",
    "setup_complete": "false",
}


def connect(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def _migration_files() -> Iterator[tuple[int, str, str]]:
    directory = resources.files("apple_health_viewer").joinpath("migrations")
    for item in sorted(directory.iterdir(), key=lambda entry: entry.name):
        if item.name[:3].isdigit() and item.name.endswith(".sql"):
            yield int(item.name[:3]), item.name, item.read_text(encoding="utf-8")


def init_database(path: str | Path) -> None:
    """Create or migrate the settings database and insert safe defaults."""
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(database)) as connection:
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        for version, name, script in _migration_files():
            if version <= current:
                continue
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + script
                + f"\nINSERT INTO schema_migrations(version, name) VALUES ({version}, '{name}');\n"
                + f"PRAGMA user_version = {version};\nCOMMIT;"
            )
            current = version

        with connection:
            connection.executemany(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                DEFAULT_SETTINGS.items(),
            )


def get_setting(path: str | Path, key: str, default: str | None = None) -> str | None:
    with closing(connect(path)) as connection:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_setting(path: str | Path, key: str, value: str) -> None:
    with closing(connect(path)) as connection, connection:
        connection.execute(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
              value = excluded.value,
              updated_at = CURRENT_TIMESTAMP
            """,
            (key, value),
        )


def delete_setting(path: str | Path, key: str) -> None:
    with closing(connect(path)) as connection, connection:
        connection.execute("DELETE FROM settings WHERE key = ?", (key,))


def schema_version(path: str | Path) -> int:
    with closing(connect(path)) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def create_import(
    path: str | Path,
    *,
    job_id: str,
    source_kind: str,
    source_label: str,
    source_id: str | None,
) -> int:
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            """
            INSERT INTO import_history(job_id, status, source_kind, source_label, source_id)
            VALUES (?, 'pending', ?, ?, ?)
            """,
            (job_id, source_kind, source_label, source_id),
        )
        return int(cursor.lastrowid)


_IMPORT_UPDATE_FIELDS = {
    "status",
    "finished_at",
    "parser_version",
    "warning_count",
    "record_count",
    "workout_count",
    "duplicate_count",
    "health_database_bytes",
    "export_date",
    "error_code",
}


def update_import(path: str | Path, job_id: str, **values: object) -> None:
    invalid = set(values) - _IMPORT_UPDATE_FIELDS
    if invalid:
        raise ValueError(f"Unsupported import fields: {', '.join(sorted(invalid))}")
    if not values:
        return
    assignments = ", ".join(f"{key} = ?" for key in values)
    parameters = [*values.values(), job_id]
    with closing(connect(path)) as connection, connection:
        connection.execute(
            f"UPDATE import_history SET {assignments} WHERE job_id = ?",
            parameters,
        )


def complete_import_activation(
    path: str | Path,
    job_id: str,
    *,
    source_label: str,
    managed_source_id: str | None,
    finished_at: str,
    record_count: int,
    workout_count: int,
    duplicate_count: int,
    warning_count: int,
    health_database_bytes: int,
    export_date: str | None,
) -> None:
    settings = {
        "setup_complete": "true",
        "active_source_label": source_label,
        "active_managed_source_id": managed_source_id or "",
    }
    with closing(connect(path)) as connection, connection:
        connection.executemany(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
              value = excluded.value,
              updated_at = CURRENT_TIMESTAMP
            """,
            settings.items(),
        )
        connection.execute(
            "DELETE FROM settings WHERE key IN ('activation_pending', 'activation_had_previous')"
        )
        connection.execute(
            """
            UPDATE import_history SET status = 'succeeded', finished_at = ?,
              record_count = ?, workout_count = ?, duplicate_count = ?, warning_count = ?,
              health_database_bytes = ?, export_date = ?, error_code = NULL
            WHERE job_id = ?
            """,
            (
                finished_at,
                record_count,
                workout_count,
                duplicate_count,
                warning_count,
                health_database_bytes,
                export_date,
                job_id,
            ),
        )


def get_import(path: str | Path, job_id: str) -> dict[str, object] | None:
    with closing(connect(path)) as connection:
        row = connection.execute(
            "SELECT * FROM import_history WHERE job_id = ?", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def recent_imports(path: str | Path, limit: int = 10) -> list[dict[str, object]]:
    with closing(connect(path)) as connection:
        rows = connection.execute(
            "SELECT * FROM import_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def recover_interrupted_imports(path: str | Path) -> int:
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE import_history
            SET status = 'failed', finished_at = CURRENT_TIMESTAMP, error_code = 'interrupted'
            WHERE status IN ('pending', 'running')
            """
        )
        return cursor.rowcount


def add_managed_source(
    path: str | Path,
    *,
    source_id: str,
    kind: str,
    label: str,
    source_path: str | Path,
    size_bytes: int,
) -> None:
    with closing(connect(path)) as connection, connection:
        connection.execute(
            """
            INSERT INTO managed_sources(id, kind, label, path, size_bytes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (source_id, kind, label, str(source_path), size_bytes),
        )


def get_managed_source(path: str | Path, source_id: str) -> dict[str, object] | None:
    with closing(connect(path)) as connection:
        row = connection.execute(
            "SELECT * FROM managed_sources WHERE id = ?", (source_id,)
        ).fetchone()
    return dict(row) if row else None


def managed_sources(path: str | Path) -> list[dict[str, object]]:
    with closing(connect(path)) as connection:
        rows = connection.execute(
            "SELECT * FROM managed_sources ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def remove_managed_source(path: str | Path, source_id: str) -> None:
    with closing(connect(path)) as connection, connection:
        connection.execute("DELETE FROM managed_sources WHERE id = ?", (source_id,))
