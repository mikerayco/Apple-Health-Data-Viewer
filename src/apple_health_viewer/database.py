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
