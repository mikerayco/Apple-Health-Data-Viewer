"""Defensive, streaming GPX route and ECG CSV import helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
import csv
import io
import math
import re
import sqlite3
from typing import BinaryIO, Callable
import xml.etree.ElementTree as ET

from .metrics import parse_health_datetime

MAX_ROUTE_POINTS = 1_000_000
MAX_ECG_SAMPLES = 5_000_000
MAX_ECG_METADATA_FIELDS = 256
MAX_ECG_METADATA_LENGTH = 64 * 1024
MAX_ECG_ROW_CHARACTERS = 1024 * 1024
MAX_ECG_COLUMNS = 32


class AssetParseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


class AssetImportCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class RouteImportResult:
    route_id: int
    point_count: int


@dataclass(frozen=True)
class ECGImportResult:
    ecg_id: int
    sample_count: int


class _BoundedTextLines:
    def __init__(self, stream: io.TextIOWrapper, cancelled: Callable[[], bool]) -> None:
        self.stream = stream
        self.cancelled = cancelled
        self.line_count = 0

    def __iter__(self) -> _BoundedTextLines:
        return self

    def __next__(self) -> str:
        line = self.stream.readline(MAX_ECG_ROW_CHARACTERS + 1)
        if not line:
            raise StopIteration
        self.line_count += 1
        if self.line_count % 100 == 0 and self.cancelled():
            raise AssetImportCancelled()
        if len(line) > MAX_ECG_ROW_CHARACTERS:
            raise AssetParseError("oversized_ecg_row", "An ECG CSV row exceeds the safety limit.")
        return line


class _PrefixedStream:
    def __init__(self, prefix: bytes, stream: BinaryIO) -> None:
        self.prefix = memoryview(prefix)
        self.offset = 0
        self.stream = stream

    def read(self, size: int = -1) -> bytes:
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


def _finite(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _haversine_metres(left: tuple[float, float], right: tuple[float, float]) -> float:
    radius = 6_371_008.8
    lat1, lon1 = map(math.radians, left)
    lat2, lon2 = map(math.radians, right)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    value = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    value = min(1.0, max(0.0, value))
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def parse_gpx_into(
    connection: sqlite3.Connection,
    workout_id: int,
    path: str,
    stream: BinaryIO,
    cancelled: Callable[[], bool] = lambda: False,
) -> RouteImportResult:
    """Stream a linked GPX file into route tables while retaining every valid point."""
    prefix = stream.read(64 * 1024)
    upper = prefix.upper()
    if b"<!ENTITY" in upper or b" SYSTEM " in upper or b" PUBLIC " in upper:
        raise AssetParseError("unsafe_gpx", "A route file contains unsupported external XML declarations.")

    connection.execute("SAVEPOINT route_asset")
    existing = connection.execute(
        "SELECT id FROM workout_routes WHERE workout_id = ? AND path = ?",
        (workout_id, path),
    ).fetchone()
    route_id = (
        int(existing[0])
        if existing
        else int(
            connection.execute(
                "INSERT INTO workout_routes(workout_id, path) VALUES (?, ?)",
                (workout_id, path),
            ).lastrowid
        )
    )
    count = 0
    segment_index = -1
    elevation_pairs = 0
    distance = gain = loss = 0.0
    minimum_lat = minimum_lon = math.inf
    maximum_lat = maximum_lon = -math.inf
    first_time: str | None = None
    last_time: str | None = None
    previous_coordinates: tuple[float, float] | None = None
    previous_elevation: float | None = None
    batch: list[tuple[object, ...]] = []
    stack: list[ET.Element] = []
    active_point: ET.Element | None = None
    parser_events = 0
    try:
        for event, element in ET.iterparse(
            _PrefixedStream(prefix, stream), events=("start", "end")
        ):
            parser_events += 1
            if parser_events % 2000 == 0 and cancelled():
                raise AssetImportCancelled()
            tag = _tag(element.tag)
            if event == "start":
                stack.append(element)
                if tag == "trkseg":
                    segment_index += 1
                    previous_coordinates = None
                    previous_elevation = None
                elif tag == "trkpt":
                    active_point = element
                    if segment_index < 0:
                        segment_index = 0
                continue
            if tag != "trkpt":
                if active_point is None:
                    if len(stack) > 1:
                        stack[-2].remove(element)
                    element.clear()
                stack.pop()
                continue
            latitude = _finite(element.get("lat"))
            longitude = _finite(element.get("lon"))
            if latitude is None or longitude is None or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                if len(stack) > 1:
                    stack[-2].remove(element)
                element.clear()
                active_point = None
                stack.pop()
                continue
            elevation = timestamp = None
            horizontal_accuracy = vertical_accuracy = speed = course = None
            for child in element.iter():
                name = _tag(child.tag).lower()
                text = child.text.strip() if child.text else None
                if name == "ele":
                    elevation = _finite(text)
                elif name == "time":
                    timestamp = text
                elif name in {"horizontalaccuracy", "hacc"}:
                    horizontal_accuracy = _finite(text)
                elif name in {"verticalaccuracy", "vacc"}:
                    vertical_accuracy = _finite(text)
                elif name == "speed":
                    speed = _finite(text)
                elif name == "course":
                    course = _finite(text)
            if previous_coordinates is not None:
                distance += _haversine_metres(previous_coordinates, (latitude, longitude))
            if elevation is not None and previous_elevation is not None:
                elevation_pairs += 1
                change = elevation - previous_elevation
                if change > 0:
                    gain += change
                else:
                    loss -= change
            previous_coordinates = (latitude, longitude)
            previous_elevation = elevation
            minimum_lat = min(minimum_lat, latitude)
            maximum_lat = max(maximum_lat, latitude)
            minimum_lon = min(minimum_lon, longitude)
            maximum_lon = max(maximum_lon, longitude)
            first_time = first_time or timestamp
            last_time = timestamp or last_time
            batch.append(
                (
                    route_id,
                    count,
                    segment_index,
                    latitude,
                    longitude,
                    elevation,
                    timestamp,
                    horizontal_accuracy,
                    vertical_accuracy,
                    speed,
                    course,
                )
            )
            count += 1
            if count > MAX_ROUTE_POINTS:
                raise AssetParseError("route_too_large", "A route contains too many points.")
            if len(batch) >= 1000:
                connection.executemany(
                    """
                    INSERT INTO route_points(
                      route_id, sequence, segment, latitude, longitude, elevation_m, recorded_at,
                      horizontal_accuracy_m, vertical_accuracy_m, speed_mps, course_degrees
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    batch,
                )
                batch.clear()
            if len(stack) > 1:
                stack[-2].remove(element)
            element.clear()
            active_point = None
            stack.pop()
        if batch:
            connection.executemany(
                """
                INSERT INTO route_points(
                  route_id, sequence, segment, latitude, longitude, elevation_m, recorded_at,
                  horizontal_accuracy_m, vertical_accuracy_m, speed_mps, course_degrees
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                batch,
            )
        if count < 2:
            raise AssetParseError("route_without_points", "A route file does not contain enough valid points.")
        connection.execute(
            """
            UPDATE workout_routes SET point_count = ?, segment_count = ?, distance_m = ?,
              elevation_gain_m = ?, elevation_loss_m = ?, min_latitude = ?, max_latitude = ?,
              min_longitude = ?, max_longitude = ?, start_time = ?, end_time = ? WHERE id = ?
            """,
            (
                count,
                segment_index + 1,
                distance,
                gain if elevation_pairs else None,
                loss if elevation_pairs else None,
                minimum_lat,
                maximum_lat,
                minimum_lon,
                maximum_lon,
                first_time,
                last_time,
                route_id,
            ),
        )
        connection.execute("RELEASE route_asset")
        return RouteImportResult(route_id, count)
    except AssetImportCancelled:
        connection.execute("ROLLBACK TO route_asset")
        connection.execute("RELEASE route_asset")
        raise
    except ET.ParseError as error:
        connection.execute("ROLLBACK TO route_asset")
        connection.execute("RELEASE route_asset")
        raise AssetParseError("malformed_gpx", "A route file is malformed.") from error
    except Exception:
        connection.execute("ROLLBACK TO route_asset")
        connection.execute("RELEASE route_asset")
        raise


def _metadata_value(metadata: dict[str, str], *names: str) -> str | None:
    normalized = {re.sub(r"[^a-z0-9]", "", key.lower()): value for key, value in metadata.items()}
    for name in names:
        value = normalized.get(re.sub(r"[^a-z0-9]", "", name.lower()))
        if value:
            return value
    return None


def _read_ecg_preamble(
    reader: csv.reader,
    cancelled: Callable[[], bool],
) -> tuple[dict[str, str], list[str]]:
    metadata: dict[str, str] = {}
    for row_number, row in enumerate(reader, start=1):
        if row_number % 100 == 0 and cancelled():
            raise AssetImportCancelled()
        if row_number > MAX_ECG_METADATA_FIELDS + 32:
            raise AssetParseError("excessive_ecg_preamble", "An ECG sample header was not found within the safety limit.")
        if len(row) > MAX_ECG_COLUMNS:
            raise AssetParseError("excessive_ecg_columns", "An ECG CSV row contains too many columns.")
        if not row or not any(cell.strip() for cell in row):
            continue
        first = row[0].strip().lower()
        second = row[1].strip().lower() if len(row) > 1 else ""
        if first in {"sample", "time", "time (s)", "seconds"} and any(
            name in second for name in ("amplitude", "voltage", "microvolt", "value")
        ):
            return metadata, [cell.strip() for cell in row]
        if len(row) < 2:
            continue
        key = row[0].strip()
        value = ",".join(row[1:]).strip()
        if len(key) > MAX_ECG_METADATA_LENGTH or len(value) > MAX_ECG_METADATA_LENGTH:
            raise AssetParseError("oversized_ecg_metadata", "An ECG metadata value exceeds the safety limit.")
        metadata[key] = value
        if len(metadata) > MAX_ECG_METADATA_FIELDS:
            raise AssetParseError("excessive_ecg_metadata", "An ECG contains too many metadata fields.")
    raise AssetParseError("invalid_ecg_csv", "An ECG CSV does not contain a recognizable sample header.")


def parse_ecg_into(
    connection: sqlite3.Connection,
    path: str,
    stream: BinaryIO,
    cancelled: Callable[[], bool] = lambda: False,
) -> ECGImportResult:
    """Parse one Apple ECG CSV variant without interpreting its classification."""
    text = io.TextIOWrapper(stream, encoding="utf-8-sig", errors="replace", newline="")
    reader = csv.reader(_BoundedTextLines(text, cancelled))
    try:
        metadata, sample_header = _read_ecg_preamble(reader, cancelled)
    except csv.Error as error:
        text.detach()
        raise AssetParseError("malformed_ecg_csv", "An ECG CSV is malformed.") from error
    except AssetImportCancelled:
        text.detach()
        raise
    except AssetParseError:
        text.detach()
        raise

    sample_rate_text = _metadata_value(metadata, "Sample Rate", "Sampling Rate") or ""
    sample_rate_match = re.search(r"([-+]?[0-9]+(?:\.[0-9]+)?)", sample_rate_text)
    sample_rate = _finite(sample_rate_match.group(1)) if sample_rate_match else None
    recorded_raw = _metadata_value(metadata, "Recorded Date", "Recording Date", "Date")
    recorded = parse_health_datetime(recorded_raw) if recorded_raw else None
    if recorded is None:
        text.detach()
        raise AssetParseError("invalid_ecg_date", "An ECG recording date is missing or invalid.")
    recorded_at = recorded.astimezone(UTC).isoformat().replace("+00:00", "Z")
    local_recorded_date = recorded.date().isoformat()
    classification = _metadata_value(metadata, "Classification", "Result")
    symptoms = _metadata_value(metadata, "Symptoms")
    device = _metadata_value(metadata, "Device")
    lead = _metadata_value(metadata, "Lead")
    unit = _metadata_value(metadata, "Unit", "Amplitude Unit") or "µV"
    first_header = sample_header[0].strip().lower()
    first_is_time = "time" in first_header or "second" in first_header
    if not first_is_time and (sample_rate is None or sample_rate <= 0):
        text.detach()
        raise AssetParseError(
            "invalid_ecg_sample_rate",
            "An index-based ECG waveform requires a finite positive sample rate.",
        )

    connection.execute("SAVEPOINT ecg_asset")
    ecg_id = int(
        connection.execute(
            """
            INSERT INTO ecgs(
              path, recorded_at, local_recorded_date, sample_rate_hz,
              classification, symptoms, device, lead, amplitude_unit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                path,
                recorded_at,
                local_recorded_date,
                sample_rate,
                classification,
                symptoms,
                device,
                lead,
                unit,
            ),
        ).lastrowid
    )
    connection.executemany(
        "INSERT INTO ecg_metadata(ecg_id, key, value) VALUES (?, ?, ?)",
        ((ecg_id, key, value) for key, value in metadata.items()),
    )
    count = 0
    processed_rows = 0
    first_time: float | None = None
    last_time: float | None = None
    batch: list[tuple[int, int, float, float]] = []
    try:
        for row in reader:
            processed_rows += 1
            if len(row) > MAX_ECG_COLUMNS:
                raise AssetParseError("excessive_ecg_columns", "An ECG CSV row contains too many columns.")
            if processed_rows % 2000 == 0 and cancelled():
                raise AssetImportCancelled()
            if processed_rows > MAX_ECG_SAMPLES * 2:
                raise AssetParseError("excessive_ecg_rows", "An ECG contains too many CSV rows.")
                raise AssetImportCancelled()
            if len(row) < 2:
                continue
            first = _finite(row[0].strip())
            amplitude = _finite(row[1].strip())
            if first is None or amplitude is None:
                continue
            sample_time = first if first_is_time else first / sample_rate
            if not math.isfinite(sample_time) or sample_time < 0:
                raise AssetParseError(
                    "invalid_ecg_sample_time",
                    "An ECG waveform contains an invalid sample time.",
                )
            if last_time is not None and sample_time < last_time:
                raise AssetParseError(
                    "nonmonotonic_ecg_time",
                    "An ECG waveform contains nonmonotonic sample times.",
                )
            first_time = sample_time if first_time is None else first_time
            last_time = sample_time
            batch.append((ecg_id, count, sample_time, amplitude))
            count += 1
            if count > MAX_ECG_SAMPLES:
                raise AssetParseError("ecg_too_large", "An ECG contains too many waveform samples.")
            if len(batch) >= 2000:
                connection.executemany(
                    "INSERT INTO ecg_samples(ecg_id, sequence, time_seconds, amplitude) VALUES (?, ?, ?, ?)",
                    batch,
                )
                batch.clear()
        if batch:
            connection.executemany(
                "INSERT INTO ecg_samples(ecg_id, sequence, time_seconds, amplitude) VALUES (?, ?, ?, ?)",
                batch,
            )
        if not count:
            raise AssetParseError("ecg_without_samples", "An ECG CSV contains no valid waveform samples.")
        duration = last_time - first_time if count > 1 and last_time is not None and first_time is not None else 0.0
        if not math.isfinite(duration) or duration < 0:
            raise AssetParseError("invalid_ecg_duration", "An ECG waveform has an invalid duration.")
        connection.execute(
            "UPDATE ecgs SET duration_seconds = ?, sample_count = ? WHERE id = ?",
            (duration, count, ecg_id),
        )
        connection.execute("RELEASE ecg_asset")
        return ECGImportResult(ecg_id, count)
    except AssetImportCancelled:
        connection.execute("ROLLBACK TO ecg_asset")
        connection.execute("RELEASE ecg_asset")
        raise
    except csv.Error as error:
        connection.execute("ROLLBACK TO ecg_asset")
        connection.execute("RELEASE ecg_asset")
        raise AssetParseError("malformed_ecg_csv", "An ECG CSV is malformed.") from error
    except Exception:
        connection.execute("ROLLBACK TO ecg_asset")
        connection.execute("RELEASE ecg_asset")
        raise
    finally:
        text.detach()
