"""Streaming Apple Health XML parser and raw inventory database."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3
from contextlib import AbstractContextManager
from typing import BinaryIO, Callable, Iterable
from urllib.parse import quote
import xml.etree.ElementTree as ET

from .assets import AssetImportCancelled, AssetParseError, parse_ecg_into, parse_gpx_into
from .metrics import (
    METRICS_BY_IDENTIFIER,
    SLEEP_IDENTIFIER,
    AggregationCancelled,
    build_aggregates,
    normalize_record,
    parse_health_datetime,
)
from .sources import SourceValidationError
from .version import HEALTH_SCHEMA_VERSION, __version__

BATCH_SIZE = 5_000
HEADER_LIMIT = 1024 * 1024
MAX_ATTRIBUTE_LENGTH = 64 * 1024
MAX_XML_TOKEN_BYTES = 128 * 1024
MAX_XML_DEPTH = 64
MAX_ELEMENT_CHILDREN = 100_000


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
    route_count: int
    ecg_count: int


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = DELETE;
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
  end_date TEXT NOT NULL,
  metric_key TEXT,
  numeric_value REAL,
  canonical_unit TEXT,
  start_utc TEXT,
  end_utc TEXT,
  local_start_date TEXT,
  local_end_date TEXT
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

CREATE TABLE health_types (
  metric_key TEXT PRIMARY KEY,
  type_identifier TEXT NOT NULL UNIQUE,
  display_label TEXT NOT NULL,
  category TEXT NOT NULL,
  value_kind TEXT NOT NULL,
  canonical_unit TEXT NOT NULL,
  metric_unit TEXT NOT NULL,
  imperial_unit TEXT NOT NULL,
  support_status TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE daily_metrics (
  metric_key TEXT NOT NULL REFERENCES health_types(metric_key),
  local_date TEXT NOT NULL,
  scope TEXT NOT NULL CHECK(scope IN ('combined', 'source', 'device')),
  source_key INTEGER NOT NULL DEFAULT 0,
  value_sum REAL,
  value_mean REAL,
  value_median REAL,
  value_min REAL,
  value_max REAL,
  latest_value REAL,
  latest_at TEXT,
  sample_count INTEGER NOT NULL,
  covered_seconds REAL NOT NULL DEFAULT 0,
  overlap_seconds REAL NOT NULL DEFAULT 0,
  is_estimate INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(metric_key, local_date, scope, source_key)
) WITHOUT ROWID;

CREATE TABLE sleep_sessions (
  id INTEGER PRIMARY KEY,
  wake_date TEXT NOT NULL,
  start_utc TEXT NOT NULL,
  end_utc TEXT NOT NULL,
  asleep_seconds REAL NOT NULL,
  in_bed_seconds REAL NOT NULL,
  awake_seconds REAL NOT NULL,
  core_seconds REAL NOT NULL,
  deep_seconds REAL NOT NULL,
  rem_seconds REAL NOT NULL,
  unspecified_seconds REAL NOT NULL,
  sample_count INTEGER NOT NULL,
  source_count INTEGER NOT NULL,
  in_bed_only INTEGER NOT NULL
);

CREATE TABLE sleep_stages (
  session_id INTEGER NOT NULL REFERENCES sleep_sessions(id) ON DELETE CASCADE,
  record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
  stage TEXT NOT NULL,
  start_utc TEXT NOT NULL,
  end_utc TEXT NOT NULL,
  source_id INTEGER NOT NULL REFERENCES sources(id)
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
  start_utc TEXT,
  end_utc TEXT,
  local_start_date TEXT,
  duration TEXT,
  duration_unit TEXT,
  duration_seconds REAL,
  total_distance TEXT,
  total_distance_unit TEXT,
  total_distance_m REAL,
  total_energy TEXT,
  total_energy_unit TEXT,
  total_energy_kcal REAL
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
  id INTEGER PRIMARY KEY,
  workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  point_count INTEGER NOT NULL DEFAULT 0,
  segment_count INTEGER NOT NULL DEFAULT 0,
  distance_m REAL,
  elevation_gain_m REAL,
  elevation_loss_m REAL,
  min_latitude REAL,
  max_latitude REAL,
  min_longitude REAL,
  max_longitude REAL,
  start_time TEXT,
  end_time TEXT,
  UNIQUE(workout_id, path)
);

CREATE TABLE route_points (
  route_id INTEGER NOT NULL REFERENCES workout_routes(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  segment INTEGER NOT NULL DEFAULT 0,
  latitude REAL NOT NULL,
  longitude REAL NOT NULL,
  elevation_m REAL,
  recorded_at TEXT,
  horizontal_accuracy_m REAL,
  vertical_accuracy_m REAL,
  speed_mps REAL,
  course_degrees REAL,
  PRIMARY KEY(route_id, sequence)
) WITHOUT ROWID;

CREATE TABLE ecgs (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  recorded_at TEXT,
  local_recorded_date TEXT,
  duration_seconds REAL,
  sample_rate_hz REAL,
  classification TEXT,
  symptoms TEXT,
  device TEXT,
  lead TEXT,
  amplitude_unit TEXT,
  sample_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE ecg_metadata (
  ecg_id INTEGER NOT NULL REFERENCES ecgs(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY(ecg_id, key)
) WITHOUT ROWID;

CREATE TABLE ecg_samples (
  ecg_id INTEGER NOT NULL REFERENCES ecgs(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  time_seconds REAL NOT NULL,
  amplitude REAL NOT NULL,
  PRIMARY KEY(ecg_id, sequence)
) WITHOUT ROWID;

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
CREATE INDEX records_metric_date_idx ON records(metric_key, local_start_date, start_utc);
CREATE INDEX records_metric_value_idx ON records(metric_key, numeric_value);
CREATE INDEX sleep_sessions_wake_idx ON sleep_sessions(wake_date);
CREATE INDEX workouts_type_start_idx ON workouts(activity_type, local_start_date);
CREATE INDEX workout_routes_workout_idx ON workout_routes(workout_id);
CREATE INDEX ecgs_recorded_idx ON ecgs(local_recorded_date, recorded_at);
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


class _BoundedXMLReader:
    """Reject oversized XML tags or text before ElementTree materializes them."""

    def __init__(self, stream: _PrefixedReader) -> None:
        self.stream = stream
        self.in_tag = False
        self.quote: int | None = None
        self.token_bytes = 0

    def read(self, size: int = -1) -> bytes:
        data = self.stream.read(size)
        for byte in data:
            if self.in_tag:
                self.token_bytes += 1
                if self.quote is not None:
                    if byte == self.quote:
                        self.quote = None
                elif byte in {ord('"'), ord("'")}:
                    self.quote = byte
                elif byte == ord(">"):
                    self.in_tag = False
                    self.token_bytes = 0
            elif byte == ord("<"):
                self.in_tag = True
                self.token_bytes = 1
            else:
                self.token_bytes += 1
            if self.token_bytes > MAX_XML_TOKEN_BYTES:
                raise HealthParseError(
                    "oversized_xml_token",
                    "An XML tag or text value exceeds the structural safety limit.",
                )
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
    if "/electrocardiograms/" in f"/{lower}" and lower.endswith(".csv"):
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


def _duration_seconds(value: str | None, unit: str | None) -> float | None:
    number = None
    try:
        number = float(value) if value is not None else None
    except ValueError:
        return None
    factors = {"s": 1.0, "sec": 1.0, "min": 60.0, "hr": 3600.0, "h": 3600.0}
    factor = factors.get((unit or "").strip())
    if number is None or factor is None or not math.isfinite(number) or number < 0:
        return None
    converted = number * factor
    return converted if math.isfinite(converted) else None


def _workout_quantity(
    identifier: str,
    value: str | None,
    unit: str | None,
    start: str,
    end: str,
) -> float | None:
    if value is None:
        return None
    normalized = normalize_record(identifier, unit, value, start, end)
    return normalized.numeric_value


def parse_export(
    stream: BinaryIO,
    database_path: Path,
    *,
    total_bytes: int,
    source_files: list[tuple[str, int]],
    progress: Callable[[int, int, str], None],
    cancelled: Callable[[], bool],
    asset_opener: Callable[[str], AbstractContextManager[BinaryIO]] | None = None,
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
    reader = _BoundedXMLReader(_PrefixedReader(prefix, hashing))

    connection = create_health_database(database_path)
    source_cache: dict[tuple[str, str], int] = {}
    device_cache: dict[str, int] = {}
    inventory: dict[tuple[str, str, str], dict[str, object]] = defaultdict(
        lambda: {"encountered": 0, "stored": 0, "first": None, "last": None, "status": "inventory_only"}
    )
    issue_counts: dict[tuple[str, str, str, str], int] = defaultdict(int)
    record_count = workout_count = duplicate_count = warning_count = 0
    route_count = ecg_count = 0
    export_date: str | None = None
    locale: str | None = None
    profile_present = False
    elements_seen = 0
    stack: list[str] = []
    child_counts: list[int] = []
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

    def store_record(element: ET.Element, *, structural_child: bool = False) -> None:
        nonlocal record_count, duplicate_count
        attributes = {key: _bounded(value, key) or "" for key, value in element.attrib.items()}
        identifier = attributes.get("type", "")
        start = attributes.get("startDate", "")
        end = attributes.get("endDate", "")
        if not identifier or not start or not end:
            add_issue(
                "missing_record_field",
                "Record",
                identifier or "Unknown",
                "A record was missing required attributes.",
            )
            return
        metadata = _metadata(element)
        fingerprint = _fingerprint(
            "Record", attributes, [_element_payload(child) for child in element]
        )
        normalized = normalize_record(
            identifier,
            attributes.get("unit") or None,
            attributes.get("value") or None,
            start,
            end,
        )
        if normalized.issue:
            messages = {
                "invalid_metric_timestamp": "A supported metric had an invalid timestamp.",
                "invalid_metric_value": "A supported metric had a nonnumeric value.",
                "unsupported_metric_unit": "A supported metric used an unknown unit and was preserved without aggregation.",
            }
            add_issue(normalized.issue, "Record", identifier, messages[normalized.issue])
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO records(
              fingerprint, type_identifier, unit, value, source_id, device_id,
              creation_date, start_date, end_date, metric_key, numeric_value,
              canonical_unit, start_utc, end_utc, local_start_date, local_end_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                normalized.metric_key,
                normalized.numeric_value,
                normalized.canonical_unit,
                normalized.start_utc,
                normalized.end_utc,
                normalized.local_start_date,
                normalized.local_end_date,
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
        elif not structural_child:
            duplicate_count += 1
        support_status = (
            "supported"
            if identifier in METRICS_BY_IDENTIFIER or identifier == SLEEP_IDENTIFIER
            else "imported_not_visualized"
        )
        count_type(
            "Record",
            identifier,
            attributes.get("unit", ""),
            start,
            end,
            stored,
            support_status,
        )

    try:
        parser = ET.iterparse(reader, events=("start", "end"))
        for event, element in parser:
            tag = _tag(element.tag)
            if event == "start":
                if len(stack) >= MAX_XML_DEPTH:
                    raise HealthParseError("xml_too_deep", "The export XML exceeds the structural safety limit.")
                if child_counts and stack[-1] != "HealthData":
                    child_counts[-1] += 1
                    if child_counts[-1] > MAX_ELEMENT_CHILDREN:
                        raise HealthParseError("xml_too_wide", "An export XML element has too many children.")
                stack.append(tag)
                child_counts.append(0)
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
                store_record(element)
            elif top_level and tag == "Correlation":
                for child in element:
                    if _tag(child.tag) == "Record":
                        store_record(child, structural_child=True)
                identifier = _bounded(element.get("type"), "type") or "Correlation"
                start = _bounded(element.get("startDate"), "start date")
                end = _bounded(element.get("endDate"), "end date")
                count_type("Correlation", identifier, "", start, end, False, "imported_not_visualized")
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
                    start_time = parse_health_datetime(start)
                    end_time = parse_health_datetime(end)
                    distance_m = _workout_quantity(
                        "HKQuantityTypeIdentifierDistanceWalkingRunning",
                        attributes.get("totalDistance") or None,
                        attributes.get("totalDistanceUnit") or None,
                        start,
                        end,
                    )
                    energy_kcal = _workout_quantity(
                        "HKQuantityTypeIdentifierActiveEnergyBurned",
                        attributes.get("totalEnergyBurned") or None,
                        attributes.get("totalEnergyBurnedUnit") or None,
                        start,
                        end,
                    )
                    duration_seconds = _duration_seconds(
                        attributes.get("duration"), attributes.get("durationUnit")
                    )
                    if duration_seconds is None and start_time and end_time and end_time >= start_time:
                        duration_seconds = (end_time - start_time).total_seconds()
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO workouts(
                          fingerprint, activity_type, source_id, device_id, creation_date,
                          start_date, end_date, start_utc, end_utc, local_start_date,
                          duration, duration_unit, duration_seconds, total_distance,
                          total_distance_unit, total_distance_m, total_energy,
                          total_energy_unit, total_energy_kcal
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            fingerprint,
                            identifier,
                            source_id(attributes),
                            device_id(attributes),
                            attributes.get("creationDate") or None,
                            start,
                            end,
                            start_time.astimezone(UTC).isoformat().replace("+00:00", "Z") if start_time else None,
                            end_time.astimezone(UTC).isoformat().replace("+00:00", "Z") if end_time else None,
                            start_time.date().isoformat() if start_time else None,
                            attributes.get("duration") or None,
                            attributes.get("durationUnit") or None,
                            duration_seconds,
                            attributes.get("totalDistance") or None,
                            attributes.get("totalDistanceUnit") or None,
                            distance_m,
                            attributes.get("totalEnergyBurned") or None,
                            attributes.get("totalEnergyBurnedUnit") or None,
                            energy_kcal,
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
                                statistic_type = child_attrs.get("type", "Unknown")
                                connection.execute(
                                    """
                                    INSERT INTO workout_statistics(
                                      workout_id, type_identifier, start_date, end_date, average,
                                      minimum, maximum, sum_value, unit
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        workout_id,
                                        statistic_type,
                                        child_attrs.get("startDate"),
                                        child_attrs.get("endDate"),
                                        child_attrs.get("average"),
                                        child_attrs.get("minimum"),
                                        child_attrs.get("maximum"),
                                        child_attrs.get("sum"),
                                        child_attrs.get("unit"),
                                    ),
                                )
                                if statistic_type in {
                                    "HKQuantityTypeIdentifierDistanceWalkingRunning",
                                    "HKQuantityTypeIdentifierDistanceCycling",
                                    "HKQuantityTypeIdentifierDistanceSwimming",
                                }:
                                    normalized_distance = _workout_quantity(
                                        "HKQuantityTypeIdentifierDistanceWalkingRunning",
                                        child_attrs.get("sum"),
                                        child_attrs.get("unit"),
                                        child_attrs.get("startDate") or start,
                                        child_attrs.get("endDate") or end,
                                    )
                                    if normalized_distance is not None:
                                        connection.execute(
                                            "UPDATE workouts SET total_distance_m = COALESCE(total_distance_m, ?) WHERE id = ?",
                                            (normalized_distance, workout_id),
                                        )
                                elif statistic_type == "HKQuantityTypeIdentifierActiveEnergyBurned":
                                    normalized_energy = _workout_quantity(
                                        statistic_type,
                                        child_attrs.get("sum"),
                                        child_attrs.get("unit"),
                                        child_attrs.get("startDate") or start,
                                        child_attrs.get("endDate") or end,
                                    )
                                    if normalized_energy is not None:
                                        connection.execute(
                                            "UPDATE workouts SET total_energy_kcal = COALESCE(total_energy_kcal, ?) WHERE id = ?",
                                            (normalized_energy, workout_id),
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
                                            "INSERT OR IGNORE INTO workout_routes(workout_id, path) VALUES (?, ?)",
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
            child_counts.pop()

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
        if asset_opener is not None:
            progress(total_bytes, record_count + workout_count, "Parsing workout routes")
            route_references = connection.execute(
                "SELECT id, workout_id, path FROM workout_routes ORDER BY id"
            ).fetchall()
            for reference in route_references:
                if cancelled():
                    raise ImportCancelled()
                try:
                    with asset_opener(str(reference["path"])) as asset:
                        parse_gpx_into(
                            connection,
                            int(reference["workout_id"]),
                            str(reference["path"]),
                            asset,
                            cancelled,
                        )
                    route_count += 1
                except AssetImportCancelled as error:
                    raise ImportCancelled() from error
                except (AssetParseError, SourceValidationError, OSError, KeyError) as error:
                    add_issue(
                        getattr(error, "code", "route_asset_error"),
                        "WorkoutRoute",
                        "GPX",
                        "A linked workout route could not be imported.",
                    )

            progress(total_bytes, record_count + workout_count, "Parsing ECG recordings")
            for asset_path, _size in source_files:
                if _file_kind(asset_path) != "electrocardiogram":
                    continue
                if cancelled():
                    raise ImportCancelled()
                try:
                    with asset_opener(asset_path) as asset:
                        parse_ecg_into(connection, asset_path, asset, cancelled)
                    ecg_count += 1
                except AssetImportCancelled as error:
                    raise ImportCancelled() from error
                except (AssetParseError, SourceValidationError, OSError, KeyError) as error:
                    add_issue(
                        getattr(error, "code", "ecg_asset_error"),
                        "Electrocardiogram",
                        "ECG CSV",
                        "An ECG CSV could not be imported.",
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
        progress(total_bytes, record_count + workout_count, "Computing daily metrics")
        try:
            build_aggregates(connection, cancelled)
        except AggregationCancelled as error:
            raise ImportCancelled() from error
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
            route_count=route_count,
            ecg_count=ecg_count,
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
