"""Canonical metric registry, normalization, and materialized aggregates."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import groupby
import math
import sqlite3
from statistics import median
from typing import Callable, Iterable, Iterator


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    identifier: str
    label: str
    category: str
    kind: str
    canonical_unit: str
    metric_unit: str
    imperial_unit: str
    decimals: int = 0


_DEFINITIONS = (
    MetricDefinition("steps", "HKQuantityTypeIdentifierStepCount", "Steps", "activity", "cumulative", "count", "steps", "steps"),
    MetricDefinition("walking_distance", "HKQuantityTypeIdentifierDistanceWalkingRunning", "Walking + running distance", "activity", "cumulative", "m", "km", "mi", 1),
    MetricDefinition("cycling_distance", "HKQuantityTypeIdentifierDistanceCycling", "Cycling distance", "activity", "cumulative", "m", "km", "mi", 1),
    MetricDefinition("active_energy", "HKQuantityTypeIdentifierActiveEnergyBurned", "Active energy", "activity", "cumulative", "kcal", "kcal", "kcal"),
    MetricDefinition("basal_energy", "HKQuantityTypeIdentifierBasalEnergyBurned", "Basal energy", "activity", "cumulative", "kcal", "kcal", "kcal"),
    MetricDefinition("exercise_minutes", "HKQuantityTypeIdentifierAppleExerciseTime", "Exercise", "activity", "cumulative", "min", "min", "min"),
    MetricDefinition("flights_climbed", "HKQuantityTypeIdentifierFlightsClimbed", "Flights climbed", "activity", "cumulative", "count", "flights", "flights"),
    MetricDefinition("stand_minutes", "HKQuantityTypeIdentifierAppleStandTime", "Stand time", "activity", "cumulative", "min", "min", "min"),
    MetricDefinition("heart_rate", "HKQuantityTypeIdentifierHeartRate", "Heart rate", "heart", "measurement", "count/min", "bpm", "bpm"),
    MetricDefinition("resting_heart_rate", "HKQuantityTypeIdentifierRestingHeartRate", "Resting heart rate", "heart", "measurement", "count/min", "bpm", "bpm"),
    MetricDefinition("walking_heart_rate", "HKQuantityTypeIdentifierWalkingHeartRateAverage", "Walking heart rate", "heart", "measurement", "count/min", "bpm", "bpm"),
    MetricDefinition("hrv_sdnn", "HKQuantityTypeIdentifierHeartRateVariabilitySDNN", "HRV (SDNN)", "heart", "measurement", "ms", "ms", "ms"),
    MetricDefinition("respiratory_rate", "HKQuantityTypeIdentifierRespiratoryRate", "Respiratory rate", "heart", "measurement", "count/min", "breaths/min", "breaths/min", 1),
    MetricDefinition("oxygen_saturation", "HKQuantityTypeIdentifierOxygenSaturation", "Oxygen saturation", "heart", "measurement", "%", "%", "%", 1),
    MetricDefinition("vo2_max", "HKQuantityTypeIdentifierVO2Max", "VO₂ max", "heart", "measurement", "mL/min·kg", "mL/min·kg", "mL/min·kg", 1),
    MetricDefinition("blood_pressure_systolic", "HKQuantityTypeIdentifierBloodPressureSystolic", "Systolic blood pressure", "heart", "measurement", "mmHg", "mmHg", "mmHg"),
    MetricDefinition("blood_pressure_diastolic", "HKQuantityTypeIdentifierBloodPressureDiastolic", "Diastolic blood pressure", "heart", "measurement", "mmHg", "mmHg", "mmHg"),
    MetricDefinition("blood_glucose", "HKQuantityTypeIdentifierBloodGlucose", "Blood glucose", "heart", "measurement", "mg/dL", "mmol/L", "mg/dL", 1),
    MetricDefinition("body_mass", "HKQuantityTypeIdentifierBodyMass", "Body mass", "body", "measurement", "kg", "kg", "lb", 1),
    MetricDefinition("body_mass_index", "HKQuantityTypeIdentifierBodyMassIndex", "BMI", "body", "measurement", "count", "BMI", "BMI", 1),
    MetricDefinition("body_fat", "HKQuantityTypeIdentifierBodyFatPercentage", "Body fat", "body", "measurement", "%", "%", "%", 1),
    MetricDefinition("lean_body_mass", "HKQuantityTypeIdentifierLeanBodyMass", "Lean body mass", "body", "measurement", "kg", "kg", "lb", 1),
    MetricDefinition("height", "HKQuantityTypeIdentifierHeight", "Height", "body", "measurement", "m", "cm", "ft", 1),
)

METRICS = {definition.key: definition for definition in _DEFINITIONS}
METRICS_BY_IDENTIFIER = {definition.identifier: definition for definition in _DEFINITIONS}
SLEEP_IDENTIFIER = "HKCategoryTypeIdentifierSleepAnalysis"
CATEGORIES = {
    category: tuple(definition for definition in _DEFINITIONS if definition.category == category)
    for category in ("activity", "heart", "body")
}


@dataclass(frozen=True)
class NormalizedRecord:
    metric_key: str | None
    numeric_value: float | None
    canonical_unit: str | None
    start_utc: str | None
    end_utc: str | None
    local_start_date: str | None
    local_end_date: str | None
    issue: str | None = None


@dataclass(frozen=True)
class _Contribution:
    start: float
    end: float
    value: float
    source_id: int
    device_id: int


@dataclass(frozen=True)
class AggregateResult:
    daily_rows: int
    sleep_sessions: int


class AggregationCancelled(RuntimeError):
    pass


def parse_health_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _convert_to_canonical(definition: MetricDefinition, value: float, unit: str) -> float | None:
    normalized = unit.strip().replace("μ", "u")
    canonical = definition.canonical_unit
    if canonical == "count":
        return value if normalized in {"count", ""} else None
    if canonical == "count/min":
        return value if normalized in {"count/min", "bpm"} else None
    if canonical == "m":
        factors = {"m": 1.0, "km": 1000.0, "cm": 0.01, "mm": 0.001, "mi": 1609.344, "ft": 0.3048, "in": 0.0254}
        return value * factors[normalized] if normalized in factors else None
    if canonical == "kg":
        factors = {"kg": 1.0, "g": 0.001, "lb": 0.45359237, "lbs": 0.45359237, "st": 6.35029318}
        return value * factors[normalized] if normalized in factors else None
    if canonical == "kcal":
        factors = {"kcal": 1.0, "Cal": 1.0, "cal": 0.001, "kJ": 0.239005736}
        return value * factors[normalized] if normalized in factors else None
    if canonical == "min":
        factors = {"min": 1.0, "s": 1 / 60, "sec": 1 / 60, "hr": 60.0, "h": 60.0}
        return value * factors[normalized] if normalized in factors else None
    if canonical in {"ms", "mmHg", "mL/min·kg"}:
        accepted = {
            "ms": {"ms"},
            "mmHg": {"mmHg"},
            "mL/min·kg": {"mL/min·kg", "mL/min/kg"},
        }[canonical]
        return value if normalized in accepted else None
    if canonical == "%":
        if normalized == "%":
            return value
        return value * 100 if normalized in {"1", "ratio"} else None
    if canonical == "mg/dL":
        if normalized == "mg/dL":
            return value
        if normalized == "mmol/L":
            return value * 18.0182
        return None
    return None


def normalize_record(
    identifier: str,
    unit: str | None,
    value: str | None,
    start: str,
    end: str,
) -> NormalizedRecord:
    start_time = parse_health_datetime(start)
    end_time = parse_health_datetime(end)
    dates = {
        "start_utc": _utc_text(start_time) if start_time else None,
        "end_utc": _utc_text(end_time) if end_time else None,
        "local_start_date": start_time.date().isoformat() if start_time else None,
        "local_end_date": end_time.date().isoformat() if end_time else None,
    }
    if identifier == SLEEP_IDENTIFIER:
        issue = None if start_time and end_time and end_time >= start_time else "invalid_metric_timestamp"
        return NormalizedRecord("sleep", None, None, issue=issue, **dates)
    definition = METRICS_BY_IDENTIFIER.get(identifier)
    if not definition:
        return NormalizedRecord(None, None, None, **dates)
    if not start_time or not end_time or end_time < start_time:
        return NormalizedRecord(definition.key, None, definition.canonical_unit, issue="invalid_metric_timestamp", **dates)
    try:
        numeric = float(value) if value is not None else math.nan
    except ValueError:
        numeric = math.nan
    if not math.isfinite(numeric):
        return NormalizedRecord(definition.key, None, definition.canonical_unit, issue="invalid_metric_value", **dates)
    canonical = _convert_to_canonical(definition, numeric, unit or "")
    if canonical is None or not math.isfinite(canonical):
        return NormalizedRecord(definition.key, None, definition.canonical_unit, issue="unsupported_metric_unit", **dates)
    return NormalizedRecord(definition.key, canonical, definition.canonical_unit, **dates)


def display_value(definition: MetricDefinition, canonical_value: float, unit_system: str) -> tuple[float, str]:
    unit = definition.metric_unit if unit_system == "metric" else definition.imperial_unit
    value = canonical_value
    if definition.canonical_unit == "m":
        if unit == "km":
            value /= 1000
        elif unit == "mi":
            value /= 1609.344
        elif unit == "cm":
            value *= 100
        elif unit == "ft":
            value /= 0.3048
    elif definition.canonical_unit == "kg" and unit == "lb":
        value /= 0.45359237
    elif definition.key == "blood_glucose" and unit == "mmol/L":
        value /= 18.0182
    return value, unit


def format_value(definition: MetricDefinition, canonical_value: float, unit_system: str) -> str:
    value, unit = display_value(definition, canonical_value, unit_system)
    decimals = definition.decimals
    if definition.key == "blood_glucose" and unit == "mg/dL":
        decimals = 0
    number = f"{value:,.{decimals}f}"
    return f"{number} {unit}"


def _insert_health_types(connection: sqlite3.Connection) -> None:
    connection.executemany(
        """
        INSERT INTO health_types(
          metric_key, type_identifier, display_label, category, value_kind,
          canonical_unit, metric_unit, imperial_unit, support_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'supported')
        """,
        (
            (
                item.key,
                item.identifier,
                item.label,
                item.category,
                item.kind,
                item.canonical_unit,
                item.metric_unit,
                item.imperial_unit,
            )
            for item in _DEFINITIONS
        ),
    )
    connection.execute(
        """
        INSERT INTO health_types(
          metric_key, type_identifier, display_label, category, value_kind,
          canonical_unit, metric_unit, imperial_unit, support_status
        ) VALUES ('sleep', ?, 'Sleep', 'sleep', 'interval', 's', 'hr', 'hr', 'supported')
        """,
        (SLEEP_IDENTIFIER,),
    )


def _daily_stats(values: list[tuple[float, str, int, int]]) -> dict[str, object]:
    numbers = [item[0] for item in values]
    latest = max(values, key=lambda item: item[1])
    return {
        "value_sum": sum(numbers),
        "value_mean": sum(numbers) / len(numbers),
        "value_median": median(numbers),
        "value_min": min(numbers),
        "value_max": max(numbers),
        "latest_value": latest[0],
        "latest_at": latest[1],
        "sample_count": len(numbers),
        "covered_seconds": 0.0,
        "overlap_seconds": 0.0,
        "is_estimate": 0,
    }


def _combined_cumulative(intervals: list[_Contribution]) -> tuple[float, float, float]:
    points: dict[float, list[float]] = defaultdict(list)
    events: dict[float, list[tuple[int, int, float, int]]] = defaultdict(list)
    for index, item in enumerate(intervals):
        if item.end <= item.start:
            points[item.start].append(item.value)
            continue
        rate = item.value / (item.end - item.start)
        events[item.start].append((1, index, rate, item.source_id))
        events[item.end].append((-1, index, rate, item.source_id))
    active: dict[int, tuple[float, int]] = {}
    total = sum(max(values) for values in points.values())
    covered = overlap = 0.0
    previous: float | None = None
    for timestamp in sorted(events):
        if previous is not None and timestamp > previous and active:
            seconds = timestamp - previous
            total += max(rate for rate, _source in active.values()) * seconds
            covered += seconds
            if len(active) > 1:
                overlap += seconds
        for direction, index, rate, source_id in sorted(events[timestamp], key=lambda event: event[0]):
            if direction < 0:
                active.pop(index, None)
            else:
                active[index] = (rate, source_id)
        previous = timestamp
    return total, covered, overlap


def _split_contributions(row: sqlite3.Row) -> Iterator[tuple[str, _Contribution]]:
    start = parse_health_datetime(str(row["start_date"]))
    end = parse_health_datetime(str(row["end_date"]))
    if not start or not end:
        return
    value = float(row["numeric_value"])
    source_id = int(row["source_id"])
    device_id = int(row["device_id"] or 0)
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    if end_utc <= start_utc:
        yield start.date().isoformat(), _Contribution(start_utc.timestamp(), start_utc.timestamp(), value, source_id, device_id)
        return
    total_seconds = (end_utc - start_utc).total_seconds()
    cursor = start_utc
    zone = start.tzinfo
    while cursor < end_utc:
        local = cursor.astimezone(zone)
        next_midnight = datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
        boundary = min(end_utc, next_midnight)
        seconds = (boundary - cursor).total_seconds()
        yield local.date().isoformat(), _Contribution(
            cursor.timestamp(), boundary.timestamp(), value * seconds / total_seconds, source_id, device_id
        )
        cursor = boundary


def _store_daily(
    connection: sqlite3.Connection,
    metric_key: str,
    local_date: str,
    scope: str,
    source_key: int,
    stats: dict[str, object],
) -> None:
    connection.execute(
        """
        INSERT INTO daily_metrics(
          metric_key, local_date, scope, source_key, value_sum, value_mean,
          value_median, value_min, value_max, latest_value, latest_at,
          sample_count, covered_seconds, overlap_seconds, is_estimate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metric_key,
            local_date,
            scope,
            source_key,
            stats.get("value_sum"),
            stats.get("value_mean"),
            stats.get("value_median"),
            stats.get("value_min"),
            stats.get("value_max"),
            stats.get("latest_value"),
            stats.get("latest_at"),
            stats["sample_count"],
            stats["covered_seconds"],
            stats["overlap_seconds"],
            stats["is_estimate"],
        ),
    )


def _build_measurement_metric(
    connection: sqlite3.Connection,
    definition: MetricDefinition,
    cancelled: Callable[[], bool],
) -> int:
    rows = connection.execute(
        """
        SELECT numeric_value, start_utc, local_start_date, source_id, device_id
        FROM records
        WHERE metric_key = ? AND numeric_value IS NOT NULL
        ORDER BY local_start_date, start_utc
        """,
        (definition.key,),
    )
    inserted = 0
    for local_date, group in groupby(rows, key=lambda row: str(row["local_start_date"])):
        if cancelled():
            raise AggregationCancelled()
        values = [
            (
                float(row["numeric_value"]),
                str(row["start_utc"]),
                int(row["source_id"]),
                int(row["device_id"] or 0),
            )
            for row in group
        ]
        _store_daily(connection, definition.key, local_date, "combined", 0, _daily_stats(values))
        inserted += 1
        by_source: dict[int, list[tuple[float, str, int, int]]] = defaultdict(list)
        by_device: dict[int, list[tuple[float, str, int, int]]] = defaultdict(list)
        for item in values:
            by_source[item[2]].append(item)
            if item[3]:
                by_device[item[3]].append(item)
        for source_id, source_values in by_source.items():
            _store_daily(connection, definition.key, local_date, "source", source_id, _daily_stats(source_values))
            inserted += 1
        for device_id, device_values in by_device.items():
            _store_daily(connection, definition.key, local_date, "device", device_id, _daily_stats(device_values))
            inserted += 1
    return inserted


def _store_cumulative_day(
    connection: sqlite3.Connection,
    definition: MetricDefinition,
    local_date: str,
    intervals: list[_Contribution],
) -> int:
    if not intervals:
        return 0
    total, covered, overlap = _combined_cumulative(intervals)
    _store_daily(
        connection,
        definition.key,
        local_date,
        "combined",
        0,
        {
            "value_sum": total,
            "sample_count": len(intervals),
            "covered_seconds": covered,
            "overlap_seconds": overlap,
            "is_estimate": int(overlap > 0),
        },
    )
    inserted = 1
    by_source: dict[int, list[_Contribution]] = defaultdict(list)
    for interval in intervals:
        by_source[interval.source_id].append(interval)
    for source_id, source_intervals in by_source.items():
        source_total = sum(interval.value for interval in source_intervals)
        source_covered = _combined_cumulative(source_intervals)[1]
        _store_daily(
            connection,
            definition.key,
            local_date,
            "source",
            source_id,
            {
                "value_sum": source_total,
                "sample_count": len(source_intervals),
                "covered_seconds": source_covered,
                "overlap_seconds": 0.0,
                "is_estimate": 0,
            },
        )
        inserted += 1
    by_device: dict[int, list[_Contribution]] = defaultdict(list)
    for interval in intervals:
        if interval.device_id:
            by_device[interval.device_id].append(interval)
    for device_id, device_intervals in by_device.items():
        _store_daily(
            connection,
            definition.key,
            local_date,
            "device",
            device_id,
            {
                "value_sum": sum(interval.value for interval in device_intervals),
                "sample_count": len(device_intervals),
                "covered_seconds": _combined_cumulative(device_intervals)[1],
                "overlap_seconds": 0.0,
                "is_estimate": 0,
            },
        )
        inserted += 1
    return inserted


def _build_cumulative_metric(
    connection: sqlite3.Connection,
    definition: MetricDefinition,
    cancelled: Callable[[], bool],
) -> int:
    rows = connection.execute(
        """
        SELECT numeric_value, source_id, device_id, start_date, end_date, local_start_date
        FROM records
        WHERE metric_key = ? AND numeric_value IS NOT NULL
        ORDER BY local_start_date, start_utc
        """,
        (definition.key,),
    )
    carry: dict[str, list[_Contribution]] = defaultdict(list)
    inserted = 0
    for local_date, group in groupby(rows, key=lambda row: str(row["local_start_date"])):
        if cancelled():
            raise AggregationCancelled()
        for pending in sorted(day for day in carry if day < local_date):
            inserted += _store_cumulative_day(connection, definition, pending, carry.pop(pending))
        intervals = carry.pop(local_date, [])
        for row in group:
            for day, contribution in _split_contributions(row):
                if day == local_date:
                    intervals.append(contribution)
                else:
                    carry[day].append(contribution)
        inserted += _store_cumulative_day(connection, definition, local_date, intervals)
    for local_date in sorted(carry):
        inserted += _store_cumulative_day(connection, definition, local_date, carry[local_date])
    return inserted


def _union_seconds(intervals: Iterable[tuple[float, float]]) -> float:
    merged = 0.0
    end: float | None = None
    for start, stop in sorted(intervals):
        if stop <= start:
            continue
        if end is None or start > end:
            merged += stop - start
            end = stop
        elif stop > end:
            merged += stop - end
            end = stop
    return merged


def _sleep_stage(value: str) -> str:
    return value.removeprefix("HKCategoryValueSleepAnalysis").lower() or "unknown"


def _store_sleep_session(connection: sqlite3.Connection, records: list[dict[str, object]]) -> bool:
    intervals_by_stage: dict[str, list[tuple[float, float]]] = defaultdict(list)
    sources: set[int] = set()
    for item in records:
        intervals_by_stage[str(item["stage"])].append((float(item["start"]), float(item["end"])))
        sources.add(int(item["source_id"]))
    asleep_stages = {"asleep", "asleepcore", "asleepdeep", "asleeprem", "asleepunspecified"}
    asleep_intervals = [interval for stage in asleep_stages for interval in intervals_by_stage.get(stage, [])]
    in_bed_intervals = intervals_by_stage.get("inbed", [])
    awake_intervals = intervals_by_stage.get("awake", [])
    if not asleep_intervals and not in_bed_intervals:
        return False
    start = min(float(item["start"]) for item in records)
    end = max(float(item["end"]) for item in records)
    last = max(records, key=lambda item: float(item["end"]))
    cursor = connection.execute(
        """
        INSERT INTO sleep_sessions(
          wake_date, start_utc, end_utc, asleep_seconds, in_bed_seconds,
          awake_seconds, core_seconds, deep_seconds, rem_seconds,
          unspecified_seconds, sample_count, source_count, in_bed_only
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            last["local_end_date"],
            datetime.fromtimestamp(start, UTC).isoformat().replace("+00:00", "Z"),
            datetime.fromtimestamp(end, UTC).isoformat().replace("+00:00", "Z"),
            _union_seconds(asleep_intervals),
            _union_seconds(in_bed_intervals),
            _union_seconds(awake_intervals),
            _union_seconds(intervals_by_stage.get("asleepcore", [])),
            _union_seconds(intervals_by_stage.get("asleepdeep", [])),
            _union_seconds(intervals_by_stage.get("asleeprem", [])),
            _union_seconds(intervals_by_stage.get("asleep", []) + intervals_by_stage.get("asleepunspecified", [])),
            len(records),
            len(sources),
            int(not asleep_intervals and bool(in_bed_intervals)),
        ),
    )
    session_id = int(cursor.lastrowid)
    connection.executemany(
        """
        INSERT INTO sleep_stages(session_id, record_id, stage, start_utc, end_utc, source_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            (session_id, item["record_id"], item["stage"], item["start_utc"], item["end_utc"], item["source_id"])
            for item in records
        ),
    )
    return True


def _build_sleep(connection: sqlite3.Connection, cancelled: Callable[[], bool]) -> int:
    rows = connection.execute(
        """
        SELECT id, value, source_id, start_utc, end_utc, local_end_date
        FROM records
        WHERE metric_key = 'sleep' AND start_utc IS NOT NULL AND end_utc IS NOT NULL
        ORDER BY start_utc, end_utc
        """
    )
    session: list[dict[str, object]] = []
    session_end = 0.0
    count = 0
    for index, row in enumerate(rows):
        if index % 1000 == 0 and cancelled():
            raise AggregationCancelled()
        start = datetime.fromisoformat(str(row["start_utc"]).replace("Z", "+00:00")).timestamp()
        end = datetime.fromisoformat(str(row["end_utc"]).replace("Z", "+00:00")).timestamp()
        if end < start:
            continue
        item = {
            "record_id": int(row["id"]),
            "stage": _sleep_stage(str(row["value"] or "Unknown")),
            "source_id": int(row["source_id"]),
            "start": start,
            "end": end,
            "start_utc": str(row["start_utc"]),
            "end_utc": str(row["end_utc"]),
            "local_end_date": str(row["local_end_date"]),
        }
        if session and start > session_end + 4 * 3600:
            count += int(_store_sleep_session(connection, session))
            session = []
        session.append(item)
        session_end = max(session_end, end) if session else end
    if session:
        count += int(_store_sleep_session(connection, session))
    return count


def build_aggregates(
    connection: sqlite3.Connection,
    cancelled: Callable[[], bool] = lambda: False,
) -> AggregateResult:
    """Build deterministic, query-efficient summaries from normalized raw rows."""
    _insert_health_types(connection)
    daily_rows = 0
    for definition in _DEFINITIONS:
        if cancelled():
            raise AggregationCancelled()
        if definition.kind == "cumulative":
            daily_rows += _build_cumulative_metric(connection, definition, cancelled)
        else:
            daily_rows += _build_measurement_metric(connection, definition, cancelled)
    sleep_sessions = _build_sleep(connection, cancelled)
    return AggregateResult(daily_rows=daily_rows, sleep_sessions=sleep_sessions)
