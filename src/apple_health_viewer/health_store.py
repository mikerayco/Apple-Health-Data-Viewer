"""Streaming Apple Health XML parser and raw inventory database."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import BinaryIO, Callable, Iterable
from urllib.parse import quote
import xml.etree.ElementTree as ET

from .version import __version__

HEALTH_SCHEMA_VERSION = 1
BATCH_SIZE = 5_000
HEADER_LIMIT = 1024 * 1024
MAX_ATTRIBUTE_LENGTH = 64 * 1024


class ImportCancelled(RuntimeError):
    pass


class HealthParseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


@dataclass(frozen=True)
class ParseResult:
    record_count: int
    workout_count: int
    duplicate_count: int
    warning_count: int
    export_date: str | None
    export_version: str | None
    source_fingerprint: str
    type_count: int
    source_count: int


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;

CREATE TABLE manifest (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  schema_version INTEGER NOT NULL,
  parser_version TEXT NOT NULL,
  export_version TEXT,
  locale TEXT,
  export_date TEXT,
  source_fingerprint TEXT NOT NULL,
  profile_present INTEGER NOT NULL,
  imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  record_count INTEGER NOT NULL,
  workout_count INTEGER NOT NULL,
  duplicate_count INTEGER NOT NULL,
  warning_count INTEGER NOT NULL
);

CREATE TABLE sources (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL DEFAULT '',
  UNIQUE(name, version)
);

CREATE TABLE devices (
  id INTEGER PRIMARY KEY,
  description TEXT NOT NULL UNIQUE
);

CREATE TABLE records (
  id INTEGER PRIMARY KEY,
  fingerprint TEXT NOT NULL UNIQUE,
  type_identifier TEXT NOT NULL,
  unit TEXT,
  value TEXT,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  device_id INTEGER REFERENCES devices(id),
  creation_date TEXT,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL
);

CREATE TABLE record_metadata (
  record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY(record_id, key, value)
) WITHOUT ROWID;

CREATE TABLE record_instantaneous_beats (
  record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
  bpm TEXT NOT NULL,
  time TEXT NOT NULL
);

CREATE TABLE workouts (
  id INTEGER PRIMARY KEY,
  fingerprint TEXT NOT NULL UNIQUE,
  activity_type TEXT NOT NULL,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  device_id INTEGER REFERENCES devices(id),
  creation_date TEXT,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  duration TEXT,
  duration_unit TEXT,
  total_distance TEXT,
  total_distance_unit TEXT,
  total_energy TEXT,
  total_energy_unit TEXT
);

CREATE TABLE workout_metadata (
  workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY(workout_id, key, value)
) WITHOUT ROWID;

CREATE TABLE workout_statistics (
  workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
  type_identifier TEXT NOT NULL,
  start_date TEXT,
  end_date TEXT,
  average TEXT,
  minimum TEXT,
  maximum TEXT,
  sum_value TEXT,
  unit TEXT
);

CREATE TABLE workout_events (
  workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  event_date TEXT NOT NULL,
  duration TEXT,
  duration_unit TEXT
);

CREATE TABLE workout_routes (
  workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
  path TEXT NOT NULL
);

CREATE TABLE activity_summaries (
  id INTEGER PRIMARY KEY,
  fingerprint TEXT NOT NULL UNIQUE,
  date_components TEXT,
  active_energy_burned TEXT,
  active_energy_goal TEXT,
  active_energy_unit TEXT,
  exercise_time TEXT,
  exercise_goal TEXT,
  stand_hours TEXT,
  stand_goal TEXT
);

CREATE TABLE type_inventory (
  element_kind TEXT NOT NULL,
  type_identifier TEXT NOT NULL,
  unit TEXT NOT NULL DEFAULT '',
  encountered_count INTEGER NOT NULL,
  stored_count INTEGER NOT NULL,
  first_start TEXT,
  last_end TEXT,
  support_status TEXT NOT NULL,
  PRIMARY KEY(element_kind, type_identifier, unit)
) WITHOUT ROWID;

CREATE TABLE file_inventory (
  path TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  size_bytes INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE import_issues (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL,
  element_kind TEXT,
  type_identifier TEXT,
  count INTEGER NOT NULL DEFAULT 1,
  message TEXT NOT NULL,
  UNIQUE(code, element_kind, type_identifier, message)
);

"""

INDEXES = """
CREATE INDEX records_type_start_idx ON records(type_identifier, start_date);
CREATE INDEX records_source_idx ON records(source_id);
CREATE INDEX workouts_type_start_idx ON workouts(activity_type, start_date);
"""


class _HashingReader:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.digest = sha256()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = self.stream.read(size)
        self.digest.update(data)
        self.bytes_read += len(data)
        return data


class _PrefixedReader:
    def __init__(self, prefix: bytes, stream: _HashingReader) -> None:
        self.prefix = memoryview(prefix)
        self.offset = 0
        self.stream = stream

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        remaining = len(self.prefix) - self.offset
        if size < 0:
            head = self.prefix[self.offset :].tobytes()
            self.offset = len(self.prefix)
            return head + self.stream.read()
        if remaining >= size:
            data = self.prefix[self.offset : self.offset + size].tobytes()
            self.offset += size
            return data
        head = self.prefix[self.offset :].tobytes()
        self.offset = len(self.prefix)
        return head + self.stream.read(size - len(head))


def _tag(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _bounded(value: str | None, field: str) -> str | None:
    if value is not None and len(value) > MAX_ATTRIBUTE_LENGTH:
        raise HealthParseError("oversized_attribute", f"An XML {field} value exceeds the safety limit.")
    return value


def _metadata(element: ET.Element) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for child in element:
        if _tag(child.tag) != "MetadataEntry":
            continue
        key = _bounded(child.get("key"), "metadata key")
        value = _bounded(child.get("value"), "metadata")
        if key is not None and value is not None:
            entries.append((key, value))
    return sorted(entries)


def _element_payload(element: ET.Element) -> object:
    return (
        _tag(element.tag),
        sorted((key, _bounded(value, key) or "") for key, value in element.attrib.items()),
        [_element_payload(child) for child in element],
    )


def _fingerprint(kind: str, attributes: dict[str, str], children: Iterable[object] = ()) -> str:
    payload = json.dumps(
        [kind, sorted(attributes.items()), list(children)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _file_kind(path: str) -> str:
    lower = path.lower()
    if "/workout-routes/" in f"/{lower}" or lower.endswith(".gpx"):
        return "workout_route"
    if "/electrocardiograms/" in f"/{lower}" or lower.endswith(".csv"):
        return "electrocardiogram"
    if lower.endswith("export_cda.xml"):
        return "clinical_cda"
    if lower.endswith("export.xml"):
        return "health_export"
    return "other"


def create_health_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def parse_export(
    stream: BinaryIO,
    database_path: Path,
    *,
    total_bytes: int,
    source_files: list[tuple[str, int]],
    progress: Callable[[int, int, str], None],
    cancelled: Callable[[], bool],
) -> ParseResult:
    """Stream one complete export into a new staging database."""
    hashing = _HashingReader(stream)
    prefix = hashing.read(HEADER_LIMIT)
    if b"<HealthData" not in prefix:
        raise HealthParseError("invalid_root", "The XML does not contain an Apple HealthData root element.")
    upper_prefix = prefix.upper()
    if b"<!ENTITY" in upper_prefix or b" SYSTEM " in upper_prefix or b" PUBLIC " in upper_prefix:
        raise HealthParseError("unsafe_xml", "External or custom XML entities are not accepted.")
    export_version_match = re.search(rb"HealthKit Export Version:\s*([^\r\n<-]+)", prefix)
    export_version = (
        export_version_match.group(1).decode("utf-8", errors="replace").strip()
        if export_version_match
        else None
    )
    reader = _PrefixedReader(prefix, hashing)

    connection = create_health_database(database_path)
    source_cache: dict[tuple[str, str], int] = {}
    device_cache: dict[str, int] = {}
    inventory: dict[tuple[str, str, str], dict[str, object]] = defaultdict(
        lambda: {"encountered": 0, "stored": 0, "first": None, "last": None, "status": "inventory_only"}
    )
    issue_counts: dict[tuple[str, str, str, str], int] = defaultdict(int)
    record_count = workout_count = duplicate_count = warning_count = 0
    export_date: str | None = None
    locale: str | None = None
    profile_present = False
    elements_seen = 0
    stack: list[str] = []
    root_element: ET.Element | None = None

    def source_id(attributes: dict[str, str]) -> int:
        name = _bounded(attributes.get("sourceName"), "source name") or "Unknown source"
        version = _bounded(attributes.get("sourceVersion"), "source version") or ""
        key = (name, version)
        if key not in source_cache:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO sources(name, version) VALUES (?, ?)", key
            )
            if cursor.rowcount:
                source_cache[key] = int(cursor.lastrowid)
            else:
                source_cache[key] = int(
                    connection.execute(
                        "SELECT id FROM sources WHERE name = ? AND version = ?", key
                    ).fetchone()[0]
                )
        return source_cache[key]

    def device_id(attributes: dict[str, str]) -> int | None:
        description = _bounded(attributes.get("device"), "device")
        if not description:
            return None
        if description not in device_cache:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO devices(description) VALUES (?)", (description,)
            )
            if cursor.rowcount:
                device_cache[description] = int(cursor.lastrowid)
            else:
                device_cache[description] = int(
                    connection.execute(
                        "SELECT id FROM devices WHERE description = ?", (description,)
                    ).fetchone()[0]
                )
        return device_cache[description]

    def count_type(
        kind: str,
        identifier: str,
        unit: str,
        start: str | None,
        end: str | None,
        stored: bool,
        status: str,
    ) -> None:
        item = inventory[(kind, identifier, unit)]
        item["encountered"] = int(item["encountered"]) + 1
        item["stored"] = int(item["stored"]) + int(stored)
        item["status"] = status
        if start and (item["first"] is None or start < str(item["first"])):
            item["first"] = start
        if end and (item["last"] is None or end > str(item["last"])):
            item["last"] = end

    def add_issue(code: str, kind: str, identifier: str, message: str) -> None:
        nonlocal warning_count
        issue_counts[(code, kind, identifier, message)] += 1
        warning_count += 1

    try:
        parser = ET.iterparse(reader, events=("start", "end"))
        for event, element in parser:
            tag = _tag(element.tag)
            if event == "start":
                stack.append(tag)
                if len(stack) == 1 and tag != "HealthData":
                    raise HealthParseError("invalid_root", "The XML root is not HealthData.")
                if len(stack) == 1:
                    root_element = element
                    locale = _bounded(element.get("locale"), "locale")
                continue

            parent = stack[-2] if len(stack) > 1 else None
            top_level = parent == "HealthData"
            elements_seen += 1
            if cancelled():
                raise ImportCancelled()

            if top_level and tag == "ExportDate":
                export_date = _bounded(element.get("value"), "export date")
            elif top_level and tag == "Me":
                profile_present = True
            elif top_level and tag == "Record":
                attributes = {key: _bounded(value, key) or "" for key, value in element.attrib.items()}
                identifier = attributes.get("type", "")
                start = attributes.get("startDate", "")
                end = attributes.get("endDate", "")
                if not identifier or not start or not end:
                    add_issue("missing_record_field", "Record", identifier or "Unknown", "A record was missing required attributes.")
                else:
                    metadata = _metadata(element)
                    child_payload = [_element_payload(child) for child in element]
                    fingerprint = _fingerprint("Record", attributes, child_payload)
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO records(
                          fingerprint, type_identifier, unit, value, source_id, device_id,
                          creation_date, start_date, end_date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            fingerprint,
                            identifier,
                            attributes.get("unit") or None,
                            attributes.get("value") or None,
                            source_id(attributes),
                            device_id(attributes),
                            attributes.get("creationDate") or None,
                            start,
                            end,
                        ),
                    )
                    stored = bool(cursor.rowcount)
                    if stored:
                        record_id = int(cursor.lastrowid)
                        connection.executemany(
                            "INSERT OR IGNORE INTO record_metadata(record_id, key, value) VALUES (?, ?, ?)",
                            ((record_id, key, value) for key, value in metadata),
                        )
                        for child in element:
                            if _tag(child.tag) != "HeartRateVariabilityMetadataList":
                                continue
                            connection.executemany(
                                "INSERT INTO record_instantaneous_beats(record_id, bpm, time) VALUES (?, ?, ?)",
                                (
                                    (
                                        record_id,
                                        _bounded(beat.get("bpm"), "instantaneous bpm") or "",
                                        _bounded(beat.get("time"), "instantaneous beat time") or "",
                                    )
                                    for beat in child
                                    if _tag(beat.tag) == "InstantaneousBeatsPerMinute"
                                ),
                            )
                        record_count += 1
                    else:
                        duplicate_count += 1
                    count_type("Record", identifier, attributes.get("unit", ""), start, end, stored, "imported")
            elif top_level and tag == "Workout":
                attributes = {key: _bounded(value, key) or "" for key, value in element.attrib.items()}
                identifier = attributes.get("workoutActivityType", "")
                start = attributes.get("startDate", "")
                end = attributes.get("endDate", "")
                child_payload = [_element_payload(child) for child in element]
                if not identifier or not start or not end:
                    add_issue("missing_workout_field", "Workout", identifier or "Unknown", "A workout was missing required attributes.")
                else:
                    fingerprint = _fingerprint("Workout", attributes, child_payload)
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO workouts(
                          fingerprint, activity_type, source_id, device_id, creation_date,
                          start_date, end_date, duration, duration_unit, total_distance,
                          total_distance_unit, total_energy, total_energy_unit
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            fingerprint,
                            identifier,
                            source_id(attributes),
                            device_id(attributes),
                            attributes.get("creationDate") or None,
                            start,
                            end,
                            attributes.get("duration") or None,
                            attributes.get("durationUnit") or None,
                            attributes.get("totalDistance") or None,
                            attributes.get("totalDistanceUnit") or None,
                            attributes.get("totalEnergyBurned") or None,
                            attributes.get("totalEnergyBurnedUnit") or None,
                        ),
                    )
                    stored = bool(cursor.rowcount)
                    if stored:
                        workout_id = int(cursor.lastrowid)
                        for child in element:
                            child_tag = _tag(child.tag)
                            child_attrs = {
                                key: _bounded(value, key) or "" for key, value in child.attrib.items()
                            }
                            if child_tag == "MetadataEntry" and child_attrs.get("key"):
                                connection.execute(
                                    "INSERT OR IGNORE INTO workout_metadata(workout_id, key, value) VALUES (?, ?, ?)",
                                    (workout_id, child_attrs["key"], child_attrs.get("value", "")),
                                )
                            elif child_tag == "WorkoutStatistics":
                                connection.execute(
                                    """
                                    INSERT INTO workout_statistics(
                                      workout_id, type_identifier, start_date, end_date, average,
                                      minimum, maximum, sum_value, unit
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        workout_id,
                                        child_attrs.get("type", "Unknown"),
                                        child_attrs.get("startDate"),
                                        child_attrs.get("endDate"),
                                        child_attrs.get("average"),
                                        child_attrs.get("minimum"),
                                        child_attrs.get("maximum"),
                                        child_attrs.get("sum"),
                                        child_attrs.get("unit"),
                                    ),
                                )
                            elif child_tag == "WorkoutEvent":
                                connection.execute(
                                    """
                                    INSERT INTO workout_events(workout_id, event_type, event_date, duration, duration_unit)
                                    VALUES (?, ?, ?, ?, ?)
                                    """,
                                    (
                                        workout_id,
                                        child_attrs.get("type", "Unknown"),
                                        child_attrs.get("date", ""),
                                        child_attrs.get("duration"),
                                        child_attrs.get("durationUnit"),
                                    ),
                                )
                            elif child_tag == "WorkoutRoute":
                                for reference in child:
                                    route_path = _bounded(reference.get("path"), "route path")
                                    if _tag(reference.tag) == "FileReference" and route_path:
                                        connection.execute(
                                            "INSERT INTO workout_routes(workout_id, path) VALUES (?, ?)",
                                            (workout_id, route_path),
                                        )
                        workout_count += 1
                    else:
                        duplicate_count += 1
                    count_type("Workout", identifier, "", start, end, stored, "imported")
            elif top_level and tag == "ActivitySummary":
                attributes = {key: _bounded(value, key) or "" for key, value in element.attrib.items()}
                fingerprint = _fingerprint("ActivitySummary", attributes)
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO activity_summaries(
                      fingerprint, date_components, active_energy_burned, active_energy_goal,
                      active_energy_unit, exercise_time, exercise_goal, stand_hours, stand_goal
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fingerprint,
                        attributes.get("dateComponents") or None,
                        attributes.get("activeEnergyBurned") or None,
                        attributes.get("activeEnergyBurnedGoal") or None,
                        attributes.get("activeEnergyBurnedUnit") or None,
                        attributes.get("appleExerciseTime") or None,
                        attributes.get("appleExerciseTimeGoal") or None,
                        attributes.get("appleStandHours") or None,
                        attributes.get("appleStandHoursGoal") or None,
                    ),
                )
                stored = bool(cursor.rowcount)
                if not stored:
                    duplicate_count += 1
                date = attributes.get("dateComponents") or None
                count_type("ActivitySummary", "ActivitySummary", attributes.get("activeEnergyBurnedUnit", ""), date, date, stored, "imported")
            elif top_level and tag not in {"ExportDate", "Me"}:
                identifier = _bounded(element.get("type"), "type") or tag
                start = _bounded(element.get("startDate"), "start date")
                end = _bounded(element.get("endDate"), "end date")
                status = "inventory_only" if tag in {"Correlation", "ClinicalRecord", "Audiogram", "VisionPrescription"} else "unknown"
                count_type(tag, identifier, _bounded(element.get("unit"), "unit") or "", start, end, False, status)

            if top_level:
                element.clear()
                if root_element is not None:
                    root_element.clear()
            stack.pop()

            if elements_seen % BATCH_SIZE == 0:
                connection.commit()
                progress(hashing.bytes_read, record_count + workout_count, "Parsing export.xml")

        if not export_date:
            add_issue("missing_export_date", "HealthData", "ExportDate", "The export did not include an ExportDate element.")
        if not profile_present:
            add_issue("missing_profile", "HealthData", "Me", "The export did not include a Me profile element.")

        for (kind, identifier, unit), item in inventory.items():
            connection.execute(
                """
                INSERT INTO type_inventory(
                  element_kind, type_identifier, unit, encountered_count, stored_count,
                  first_start, last_end, support_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kind,
                    identifier,
                    unit,
                    item["encountered"],
                    item["stored"],
                    item["first"],
                    item["last"],
                    item["status"],
                ),
            )
        for path, size in source_files:
            connection.execute(
                "INSERT OR IGNORE INTO file_inventory(path, kind, size_bytes) VALUES (?, ?, ?)",
                (path, _file_kind(path), size),
            )
        for (code, kind, identifier, message), count in issue_counts.items():
            connection.execute(
                """
                INSERT INTO import_issues(code, element_kind, type_identifier, count, message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (code, kind, identifier, count, message),
            )

        progress(total_bytes, record_count + workout_count, "Indexing imported records")
        connection.executescript(INDEXES)
        fingerprint = hashing.digest.hexdigest()
        connection.execute(
            """
            INSERT INTO manifest(
              id, schema_version, parser_version, export_version, locale, export_date,
              source_fingerprint, profile_present, record_count, workout_count,
              duplicate_count, warning_count
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                HEALTH_SCHEMA_VERSION,
                __version__,
                export_version,
                locale,
                export_date,
                fingerprint,
                int(profile_present),
                record_count,
                workout_count,
                duplicate_count,
                warning_count,
            ),
        )
        connection.commit()
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise HealthParseError("database_integrity", "The staged health database failed validation.")
        progress(total_bytes, record_count + workout_count, "Validating database")
        return ParseResult(
            record_count=record_count,
            workout_count=workout_count,
            duplicate_count=duplicate_count,
            warning_count=warning_count,
            export_date=export_date,
            export_version=export_version,
            source_fingerprint=fingerprint,
            type_count=len(inventory),
            source_count=len(source_cache),
        )
    except ET.ParseError as error:
        raise HealthParseError("malformed_xml", "The export.xml file is malformed or incomplete.") from error
    finally:
        connection.close()


def read_data_quality(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    uri = f"file:{quote(path.resolve().as_posix(), safe='/:')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        manifest_row = connection.execute("SELECT * FROM manifest WHERE id = 1").fetchone()
        if not manifest_row:
            return None
        inventory = [
            dict(row)
            for row in connection.execute(
                """
                SELECT element_kind, type_identifier, unit, encountered_count, stored_count,
                       first_start, last_end, support_status
                FROM type_inventory
                ORDER BY encountered_count DESC, type_identifier
                """
            )
        ]
        sources = [
            dict(row)
            for row in connection.execute(
                """
                SELECT s.name, s.version,
                       COALESCE(r.record_count, 0) AS record_count,
                       COALESCE(w.workout_count, 0) AS workout_count
                FROM sources s
                LEFT JOIN (
                  SELECT source_id, COUNT(*) AS record_count FROM records GROUP BY source_id
                ) r ON r.source_id = s.id
                LEFT JOIN (
                  SELECT source_id, COUNT(*) AS workout_count FROM workouts GROUP BY source_id
                ) w ON w.source_id = s.id
                ORDER BY record_count DESC, workout_count DESC, s.name
                """
            )
        ]
        files = [
            dict(row)
            for row in connection.execute(
                "SELECT kind, COUNT(*) AS count, SUM(size_bytes) AS size_bytes FROM file_inventory GROUP BY kind ORDER BY kind"
            )
        ]
        issues = [dict(row) for row in connection.execute("SELECT * FROM import_issues ORDER BY count DESC, code")]
        return {
            "manifest": dict(manifest_row),
            "inventory": inventory,
            "sources": sources,
            "files": files,
            "issues": issues,
            "database_bytes": path.stat().st_size,
        }
    finally:
        connection.close()
