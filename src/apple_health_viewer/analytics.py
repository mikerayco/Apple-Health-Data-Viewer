"""Read-only date windows and source-aware dashboard summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterable
from urllib.parse import quote

from .metrics import CATEGORIES, METRICS, MetricDefinition, display_value, format_value, normalize_record
from .version import HEALTH_SCHEMA_VERSION

_DATABASE_LOCKS: dict[Path, Any] = {}
_DATABASE_LOCKS_GUARD = threading.Lock()


class _LockedHealthConnection(sqlite3.Connection):
    viewer_lock: Any = None

    def close(self) -> None:
        lock, self.viewer_lock = self.viewer_lock, None
        try:
            super().close()
        finally:
            if lock is not None:
                lock.release()


def register_database_lock(path: Path, lock: Any) -> None:
    with _DATABASE_LOCKS_GUARD:
        _DATABASE_LOCKS[path.resolve()] = lock


class AnalyticsError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


@dataclass(frozen=True)
class DateWindow:
    start: date
    end: date
    prior_start: date
    prior_end: date
    period: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def as_dict(self) -> dict[str, object]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "days": self.days,
            "period": self.period,
            "prior_start": self.prior_start.isoformat(),
            "prior_end": self.prior_end.isoformat(),
        }


def open_health_database(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    with _DATABASE_LOCKS_GUARD:
        lock = _DATABASE_LOCKS.get(resolved)
    if lock is not None:
        lock.acquire()
    if not path.is_file():
        if lock is not None:
            lock.release()
        raise AnalyticsError("empty", "Import an Apple Health export first.")
    uri = f"file:{quote(resolved.as_posix(), safe='/:')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, factory=_LockedHealthConnection)
    except Exception:
        if lock is not None:
            lock.release()
        raise
    connection.viewer_lock = lock
    connection.row_factory = sqlite3.Row
    try:
        version = connection.execute("SELECT schema_version FROM manifest WHERE id = 1").fetchone()
    except sqlite3.DatabaseError as error:
        connection.close()
        raise AnalyticsError("invalid_database", "The active health database could not be read.") from error
    try:
        schema = int(version[0]) if version else 0
    except (TypeError, ValueError):
        connection.close()
        raise AnalyticsError("invalid_database", "The active health database could not be read.")
    if schema < 2:
        connection.close()
        raise AnalyticsError("reimport_required", "Re-import the source to compute Phase 3 metrics.")
    if schema > HEALTH_SCHEMA_VERSION:
        connection.close()
        raise AnalyticsError(
            "newer_database",
            "This health database was created by a newer viewer. Upgrade the application to open it.",
        )
    return connection


def _available_bounds(connection: sqlite3.Connection) -> tuple[date, date] | None:
    schema = connection.execute("SELECT schema_version FROM manifest WHERE id = 1").fetchone()
    phase_five = bool(schema and int(schema[0]) >= 3)
    additions = (
        " UNION ALL SELECT local_start_date AS day FROM workouts WHERE local_start_date IS NOT NULL"
        " UNION ALL SELECT local_recorded_date AS day FROM ecgs WHERE local_recorded_date IS NOT NULL"
        if phase_five
        else ""
    )
    row = connection.execute(
        f"""
        SELECT MIN(day), MAX(day) FROM (
          SELECT local_date AS day FROM daily_metrics WHERE scope = 'combined'
          UNION ALL
          SELECT wake_date AS day FROM sleep_sessions
          {additions}
        )
        """
    ).fetchone()
    if not row or not row[0] or not row[1]:
        return None
    return date.fromisoformat(str(row[0])), date.fromisoformat(str(row[1]))


def resolve_window(
    connection: sqlite3.Connection,
    *,
    period: str = "30d",
    start: str | None = None,
    end: str | None = None,
) -> DateWindow:
    bounds = _available_bounds(connection)
    if not bounds:
        raise AnalyticsError("no_supported_metrics", "The export has no supported metric records.")
    first, latest = bounds
    if period == "custom":
        if not start or not end:
            raise AnalyticsError("invalid_range", "Custom ranges require start and end dates.")
        try:
            current_start = date.fromisoformat(start)
            current_end = date.fromisoformat(end)
        except ValueError as error:
            raise AnalyticsError("invalid_range", "Dates must use YYYY-MM-DD format.") from error
        if current_start > current_end:
            raise AnalyticsError("invalid_range", "The start date must not be after the end date.")
        if (current_end - current_start).days > 3660:
            raise AnalyticsError("invalid_range", "Custom ranges are limited to ten years.")
    elif period == "all":
        current_start, current_end = first, latest
    else:
        lengths = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}
        if period not in lengths:
            raise AnalyticsError("invalid_period", "Choose a valid date period.")
        current_end = latest
        current_start = current_end - timedelta(days=lengths[period] - 1)
    days = (current_end - current_start).days + 1
    prior_end = current_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=days - 1)
    return DateWindow(current_start, current_end, prior_start, prior_end, period)


def _filter_scope(source_id: int | None, device_id: int | None) -> tuple[str, int]:
    if source_id is not None and device_id is not None:
        raise AnalyticsError("invalid_filter", "Choose a source or a device, not both.")
    if source_id is not None:
        if source_id < 1:
            raise AnalyticsError("invalid_filter", "Choose a valid source.")
        return "source", source_id
    if device_id is not None:
        if device_id < 1:
            raise AnalyticsError("invalid_filter", "Choose a valid device.")
        return "device", device_id
    return "combined", 0


def _daily_rows(
    connection: sqlite3.Connection,
    metric_key: str,
    start: date,
    end: date,
    scope: str,
    source_key: int,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT * FROM daily_metrics
        WHERE metric_key = ? AND scope = ? AND source_key = ?
          AND local_date BETWEEN ? AND ?
        ORDER BY local_date
        """,
        (metric_key, scope, source_key, start.isoformat(), end.isoformat()),
    ).fetchall()


def _aggregate_rows(definition: MetricDefinition, rows: Iterable[sqlite3.Row]) -> dict[str, object] | None:
    items = list(rows)
    if not items:
        return None
    sample_count = sum(int(row["sample_count"]) for row in items)
    if definition.kind == "cumulative":
        value = sum(float(row["value_sum"] or 0) for row in items)
        minimum = maximum = median_value = None
    else:
        weighted = sum(float(row["value_mean"] or 0) * int(row["sample_count"]) for row in items)
        value = weighted / sample_count
        minimum = min(float(row["value_min"]) for row in items if row["value_min"] is not None)
        maximum = max(float(row["value_max"]) for row in items if row["value_max"] is not None)
        median_value = None
    latest_rows = [row for row in items if row["latest_at"]]
    latest = max(latest_rows, key=lambda row: str(row["latest_at"])) if latest_rows else None
    return {
        "canonical_value": value,
        "sample_count": sample_count,
        "tracked_days": len(items),
        "minimum": minimum,
        "maximum": maximum,
        "median": median_value,
        "latest_value": float(latest["latest_value"]) if latest and latest["latest_value"] is not None else None,
        "latest_at": str(latest["latest_at"]) if latest else None,
        "covered_seconds": sum(float(row["covered_seconds"]) for row in items),
        "overlap_seconds": sum(float(row["overlap_seconds"]) for row in items),
        "estimated_days": sum(int(row["is_estimate"]) for row in items),
    }


def _raw_filter(scope: str, source_key: int) -> tuple[str, list[object]]:
    if scope == "source":
        return " AND source_id = ?", [source_key]
    if scope == "device":
        return " AND device_id = ?", [source_key]
    return "", []


def _exact_values(
    connection: sqlite3.Connection,
    metric_key: str,
    window: DateWindow,
    scope: str,
    source_key: int,
) -> list[float]:
    condition, parameters = _raw_filter(scope, source_key)
    return [
        float(row[0])
        for row in connection.execute(
            f"""
            SELECT numeric_value FROM records
            WHERE metric_key = ? AND numeric_value IS NOT NULL
              AND local_start_date BETWEEN ? AND ?{condition}
            ORDER BY numeric_value
            """,
            [metric_key, window.start.isoformat(), window.end.isoformat(), *parameters],
        )
    ]


def _exact_median(
    connection: sqlite3.Connection,
    metric_key: str,
    window: DateWindow,
    scope: str,
    source_key: int,
) -> float | None:
    condition, parameters = _raw_filter(scope, source_key)
    values = [metric_key, window.start.isoformat(), window.end.isoformat(), *parameters]
    count = int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM records
            WHERE metric_key = ? AND numeric_value IS NOT NULL
              AND local_start_date BETWEEN ? AND ?{condition}
            """,
            values,
        ).fetchone()[0]
    )
    if not count:
        return None
    middle = (count - 1) // 2
    limit = 2 if count % 2 == 0 else 1
    rows = connection.execute(
        f"""
        SELECT numeric_value FROM records
        WHERE metric_key = ? AND numeric_value IS NOT NULL
          AND local_start_date BETWEEN ? AND ?{condition}
        ORDER BY numeric_value LIMIT ? OFFSET ?
        """,
        [*values, limit, middle],
    ).fetchall()
    return sum(float(row[0]) for row in rows) / len(rows)


def _decimate(points: list[dict[str, object]], limit: int = 240) -> list[dict[str, object]]:
    if len(points) <= limit:
        return points
    indexes = {round(index * (len(points) - 1) / (limit - 1)) for index in range(limit)}
    return [point for index, point in enumerate(points) if index in indexes]


def _decimate_trend(points: list[dict[str, object]], limit: int = 240) -> list[dict[str, object]]:
    if len(points) <= limit:
        return points
    indexes = sorted({round(index * (len(points) - 1) / (limit - 1)) for index in range(limit)})
    result: list[dict[str, object]] = []
    previous = -1
    for index in indexes:
        point = dict(points[index])
        point["gap_before"] = bool(result) and any(
            bool(item.get("gap_before")) for item in points[previous + 1 : index + 1]
        )
        result.append(point)
        previous = index
    return result


def resolve_granularity(window: DateWindow, requested: str | None = None) -> str:
    value = requested or "auto"
    if value not in {"auto", "day", "week", "month"}:
        raise AnalyticsError("invalid_granularity", "Choose day, week, month, or automatic chart grouping.")
    if value != "auto":
        return value
    if window.days <= 90:
        return "day"
    if window.days <= 730:
        return "week"
    return "month"


def _bucket_key(value: str, granularity: str) -> str:
    current = date.fromisoformat(value)
    if granularity == "week":
        return (current - timedelta(days=current.weekday())).isoformat()
    if granularity == "month":
        return current.replace(day=1).isoformat()
    return current.isoformat()


def _next_bucket(bucket: str, granularity: str) -> str:
    start = date.fromisoformat(bucket)
    if granularity == "week":
        return (start + timedelta(days=7)).isoformat()
    if granularity == "month":
        return (start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)).isoformat()
    return (start + timedelta(days=1)).isoformat()


def _bucket_end(bucket: str, granularity: str) -> str:
    start = date.fromisoformat(bucket)
    if granularity == "week":
        return (start + timedelta(days=6)).isoformat()
    if granularity == "month":
        next_month = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
        return (next_month - timedelta(days=1)).isoformat()
    return bucket


def _trend_points(
    definition: MetricDefinition,
    rows: list[sqlite3.Row],
    unit_system: str,
    granularity: str,
) -> list[dict[str, object]]:
    buckets: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        buckets.setdefault(_bucket_key(str(row["local_date"]), granularity), []).append(row)
    points: list[dict[str, object]] = []
    previous_bucket: str | None = None
    for bucket, bucket_rows in buckets.items():
        samples = sum(int(row["sample_count"]) for row in bucket_rows)
        if definition.kind == "cumulative":
            canonical = sum(float(row["value_sum"] or 0) for row in bucket_rows)
        else:
            canonical = sum(
                float(row["value_mean"] or 0) * int(row["sample_count"])
                for row in bucket_rows
            ) / samples
        points.append(
            {
                "date": bucket,
                "end_date": _bucket_end(bucket, granularity),
                "observed_start": str(bucket_rows[0]["local_date"]),
                "observed_end": str(bucket_rows[-1]["local_date"]),
                "gap_before": previous_bucket is not None and bucket != _next_bucket(previous_bucket, granularity),
                "value": _display_number(definition, canonical, unit_system),
                "samples": samples,
                "estimated": any(bool(row["is_estimate"]) for row in bucket_rows),
            }
        )
        previous_bucket = bucket
    return _decimate_trend(points)


def _comparison(current: float, prior: float | None) -> dict[str, float | None]:
    if prior is None:
        return {"absolute": None, "percent": None}
    absolute = current - prior
    percent = absolute / abs(prior) * 100 if prior else None
    return {"absolute": absolute, "percent": percent}


def _display_number(definition: MetricDefinition, value: float | None, unit_system: str) -> float | None:
    if value is None:
        return None
    return round(display_value(definition, value, unit_system)[0], definition.decimals + 2)


def _metric_filters(connection: sqlite3.Connection, metric_key: str) -> dict[str, list[dict[str, object]]]:
    sources = [
        dict(row)
        for row in connection.execute(
            """
            SELECT s.id, s.name, SUM(d.sample_count) AS sample_count
            FROM daily_metrics d JOIN sources s ON s.id = d.source_key
            WHERE d.metric_key = ? AND d.scope = 'source'
            GROUP BY s.id, s.name ORDER BY sample_count DESC, s.name
            """,
            (metric_key,),
        )
    ]
    devices = [
        dict(row)
        for row in connection.execute(
            """
            SELECT v.id, v.description AS name, SUM(d.sample_count) AS sample_count
            FROM daily_metrics d JOIN devices v ON v.id = d.source_key
            WHERE d.metric_key = ? AND d.scope = 'device'
            GROUP BY v.id, v.description ORDER BY sample_count DESC, v.description
            """,
            (metric_key,),
        )
    ]
    return {"sources": sources, "devices": devices}


def metric_summary(
    connection: sqlite3.Connection,
    metric_key: str,
    window: DateWindow,
    unit_system: str,
    *,
    source_id: int | None = None,
    device_id: int | None = None,
    include_filters: bool = True,
    granularity: str | None = None,
) -> dict[str, object]:
    definition = METRICS.get(metric_key)
    if not definition:
        raise AnalyticsError("unknown_metric", "That metric is not supported.")
    scope, source_key = _filter_scope(source_id, device_id)
    rows = _daily_rows(connection, metric_key, window.start, window.end, scope, source_key)
    current = _aggregate_rows(definition, rows)
    prior_rows = _daily_rows(connection, metric_key, window.prior_start, window.prior_end, scope, source_key)
    prior = _aggregate_rows(definition, prior_rows)
    chart_granularity = resolve_granularity(window, granularity)
    unit = definition.metric_unit if unit_system == "metric" else definition.imperial_unit
    payload: dict[str, object] = {
        "key": metric_key,
        "label": definition.label,
        "category": definition.category,
        "kind": definition.kind,
        "unit": unit,
        "available": current is not None,
        "window": window.as_dict(),
        "scope": scope,
        "source_key": source_key,
        "granularity": chart_granularity,
    }
    if include_filters:
        payload["filters"] = _metric_filters(connection, metric_key)
    if current is None:
        payload.update({"reason": "no_records_in_range", "trend": []})
        return payload
    if definition.kind == "measurement":
        current["median"] = _exact_median(connection, metric_key, window, scope, source_key)
        if definition.category == "body":
            exact = _exact_values(connection, metric_key, window, scope, source_key)
        else:
            exact = []
        if len(exact) >= 8:
            lower = exact[len(exact) // 4]
            upper = exact[(len(exact) * 3) // 4]
            spread = upper - lower
            current["suspected_outliers"] = sum(value < lower - 3 * spread or value > upper + 3 * spread for value in exact)
        else:
            current["suspected_outliers"] = 0
    prior_value = float(prior["canonical_value"]) if prior else None
    comparison = _comparison(float(current["canonical_value"]), prior_value)
    display_current = _display_number(definition, float(current["canonical_value"]), unit_system)
    formatted_value = format_value(definition, float(current["canonical_value"]), unit_system)
    formatted_latest = (
        format_value(definition, float(current["latest_value"]), unit_system)
        if current["latest_value"] is not None
        else None
    )
    payload.update(
        {
            "value": display_current,
            "formatted_value": formatted_value,
            "formatted_latest": formatted_latest,
            "headline_label": "Total" if definition.kind == "cumulative" else "Latest",
            "headline_value": formatted_value if definition.kind == "cumulative" else formatted_latest,
            "sample_count": current["sample_count"],
            "tracked_days": current["tracked_days"],
            "coverage_percent": round(int(current["tracked_days"]) / window.days * 100, 1),
            "minimum": _display_number(definition, current["minimum"], unit_system),
            "maximum": _display_number(definition, current["maximum"], unit_system),
            "median": _display_number(definition, current["median"], unit_system),
            "latest_value": _display_number(definition, current["latest_value"], unit_system),
            "latest_at": current["latest_at"],
            "suspected_outliers": current.get("suspected_outliers", 0),
            "overlap_seconds": round(float(current["overlap_seconds"]), 2),
            "estimated_days": current["estimated_days"],
            "comparison": {
                "absolute": _display_number(definition, comparison["absolute"], unit_system),
                "percent": round(comparison["percent"], 1) if comparison["percent"] is not None else None,
                "prior_value": _display_number(definition, prior_value, unit_system),
                "prior_tracked_days": int(prior["tracked_days"]) if prior else 0,
                "prior_sample_count": int(prior["sample_count"]) if prior else 0,
                "prior_coverage_percent": round(int(prior["tracked_days"]) / window.days * 100, 1) if prior else 0.0,
                "basis": f"{window.prior_start.isoformat()} to {window.prior_end.isoformat()}",
            },
            "trend": _trend_points(definition, rows, unit_system, chart_granularity),
            "method": (
                "Maximum active interval rate across overlapping cumulative samples; non-overlapping intervals are combined."
                if definition.kind == "cumulative" and scope == "combined"
                else "Source-specific raw cumulative sum."
                if definition.kind == "cumulative"
                else "Individual measurements; the headline is the sample-weighted mean."
            ),
        }
    )
    if metric_key == "blood_glucose":
        payload["medical_interpretation"] = None
        payload["meal_context"] = _latest_meal_context(connection, window, scope, source_key)
        payload["readings"] = _glucose_readings(connection, window, unit_system, scope, source_key)
        payload["readings_limited"] = int(payload["sample_count"]) > len(payload["readings"])
    return payload


def metric_trend_summary(
    connection: sqlite3.Connection,
    metric_key: str,
    window: DateWindow,
    unit_system: str,
    *,
    source_id: int | None = None,
    device_id: int | None = None,
    granularity: str | None = None,
) -> dict[str, object]:
    definition = METRICS.get(metric_key)
    if not definition:
        raise AnalyticsError("unknown_metric", "That metric is not supported.")
    scope, source_key = _filter_scope(source_id, device_id)
    rows = _daily_rows(connection, metric_key, window.start, window.end, scope, source_key)
    chart_granularity = resolve_granularity(window, granularity)
    return {
        "key": metric_key,
        "label": definition.label,
        "unit": definition.metric_unit if unit_system == "metric" else definition.imperial_unit,
        "available": bool(rows),
        "window": window.as_dict(),
        "granularity": chart_granularity,
        "scope": scope,
        "source_key": source_key,
        "trend": _trend_points(definition, rows, unit_system, chart_granularity),
    }


def _glucose_readings(
    connection: sqlite3.Connection,
    window: DateWindow,
    unit_system: str,
    scope: str,
    source_key: int,
) -> list[dict[str, object]]:
    condition, parameters = _raw_filter(scope, source_key)
    rows = connection.execute(
        f"""
        SELECT r.numeric_value, r.start_utc, s.name AS source_name,
               d.description AS device_name,
               (SELECT m.value FROM record_metadata m
                WHERE m.record_id = r.id AND m.key = 'HKBloodGlucoseMealTime'
                LIMIT 1) AS meal_context
        FROM records r
        JOIN sources s ON s.id = r.source_id
        LEFT JOIN devices d ON d.id = r.device_id
        WHERE r.metric_key = 'blood_glucose' AND r.numeric_value IS NOT NULL
          AND r.local_start_date BETWEEN ? AND ?{condition}
        ORDER BY r.start_utc DESC LIMIT 200
        """,
        [window.start.isoformat(), window.end.isoformat(), *parameters],
    ).fetchall()
    definition = METRICS["blood_glucose"]
    return [
        {
            "value": _display_number(definition, float(row["numeric_value"]), unit_system),
            "unit": definition.metric_unit if unit_system == "metric" else definition.imperial_unit,
            "timestamp": str(row["start_utc"]),
            "source": str(row["source_name"]),
            "device": str(row["device_name"]) if row["device_name"] else None,
            "meal_context": {"1": "Before meal", "2": "After meal"}.get(
                str(row["meal_context"]), "Meal context recorded" if row["meal_context"] else None
            ),
        }
        for row in rows
    ]


def _latest_meal_context(
    connection: sqlite3.Connection,
    window: DateWindow,
    scope: str,
    source_key: int,
) -> str | None:
    condition, parameters = _raw_filter(scope, source_key)
    row = connection.execute(
        f"""
        SELECT m.value FROM records r
        JOIN record_metadata m ON m.record_id = r.id
        WHERE r.metric_key = 'blood_glucose'
          AND r.local_start_date BETWEEN ? AND ?
          AND m.key = 'HKBloodGlucoseMealTime'{condition}
        ORDER BY r.start_utc DESC LIMIT 1
        """,
        [window.start.isoformat(), window.end.isoformat(), *parameters],
    ).fetchone()
    if not row:
        return None
    return {"1": "Before meal", "2": "After meal"}.get(str(row[0]), "Meal context recorded")


def _sleep_trend(rows: list[sqlite3.Row], granularity: str) -> list[dict[str, object]]:
    daily: dict[str, dict[str, object]] = {}
    for row in rows:
        key = str(row["wake_date"])
        item = daily.setdefault(
            key,
            {
                "asleep": 0.0,
                "in_bed": 0.0,
                "sessions": 0,
                "in_bed_only": False,
                "measured": False,
                "in_bed_measured": False,
            },
        )
        item["asleep"] = float(item["asleep"]) + float(row["asleep_seconds"])
        item["in_bed"] = float(item["in_bed"]) + float(row["in_bed_seconds"])
        item["sessions"] = int(item["sessions"]) + 1
        item["in_bed_only"] = bool(item["in_bed_only"]) or bool(row["in_bed_only"])
        item["measured"] = bool(item["measured"]) or float(row["asleep_seconds"]) > 0
        item["in_bed_measured"] = bool(item["in_bed_measured"]) or float(row["in_bed_seconds"]) > 0
    buckets: dict[str, list[tuple[str, dict[str, object]]]] = {}
    for day, item in daily.items():
        buckets.setdefault(_bucket_key(day, granularity), []).append((day, item))
    points: list[dict[str, object]] = []
    previous_bucket: str | None = None
    for bucket, items in buckets.items():
        nights = len(items)
        measured = [item for item in items if bool(item[1]["measured"])]
        in_bed_measured = [item for item in items if bool(item[1]["in_bed_measured"])]
        points.append(
            {
                "date": bucket,
                "end_date": _bucket_end(bucket, granularity),
                "observed_start": items[0][0],
                "observed_end": items[-1][0],
                "gap_before": previous_bucket is not None and bucket != _next_bucket(previous_bucket, granularity),
                "asleep_hours": round(sum(float(item[1]["asleep"]) for item in measured) / len(measured) / 3600, 2) if measured else None,
                "in_bed_hours": (
                    round(
                        sum(float(item[1]["in_bed"]) for item in in_bed_measured)
                        / len(in_bed_measured)
                        / 3600,
                        2,
                    )
                    if in_bed_measured
                    else None
                ),
                "nights": nights,
                "measured_nights": len(measured),
                "in_bed_nights": len(in_bed_measured),
                "sessions": sum(int(item[1]["sessions"]) for item in items),
                "in_bed_only": any(bool(item[1]["in_bed_only"]) for item in items),
            }
        )
        previous_bucket = bucket
    return _decimate_trend(points)


def sleep_summary(
    connection: sqlite3.Connection,
    window: DateWindow,
    granularity: str | None = None,
) -> dict[str, object]:
    rows = connection.execute(
        "SELECT * FROM sleep_sessions WHERE wake_date BETWEEN ? AND ? ORDER BY wake_date",
        (window.start.isoformat(), window.end.isoformat()),
    ).fetchall()
    prior = connection.execute(
        "SELECT * FROM sleep_sessions WHERE wake_date BETWEEN ? AND ?",
        (window.prior_start.isoformat(), window.prior_end.isoformat()),
    ).fetchall()
    chart_granularity = resolve_granularity(window, granularity)
    if not rows:
        return {
            "available": False,
            "reason": "no_records_in_range",
            "window": window.as_dict(),
            "granularity": chart_granularity,
            "trend": [],
        }
    asleep = sum(float(row["asleep_seconds"]) for row in rows)
    in_bed = sum(float(row["in_bed_seconds"]) for row in rows)
    awake = sum(float(row["awake_seconds"]) for row in rows)
    logged_nights = len({str(row["wake_date"]) for row in rows})
    measured_nights = len({str(row["wake_date"]) for row in rows if float(row["asleep_seconds"]) > 0})
    in_bed_nights = len({str(row["wake_date"]) for row in rows if float(row["in_bed_seconds"]) > 0})
    prior_logged_nights = len({str(row["wake_date"]) for row in prior})
    prior_measured_nights = len({str(row["wake_date"]) for row in prior if float(row["asleep_seconds"]) > 0})
    prior_asleep = sum(float(row["asleep_seconds"]) for row in prior) if prior else None
    current_average = asleep / measured_nights if measured_nights else None
    prior_average = prior_asleep / prior_measured_nights if prior_asleep is not None and prior_measured_nights else None
    comparison = _comparison(current_average, prior_average) if current_average is not None else {"absolute": None, "percent": None}
    return {
        "available": True,
        "window": window.as_dict(),
        "sessions": len(rows),
        "logged_nights": logged_nights,
        "measured_nights": measured_nights,
        "coverage_percent": round(logged_nights / window.days * 100, 1),
        "asleep_coverage_percent": round(measured_nights / window.days * 100, 1),
        "in_bed_nights": in_bed_nights,
        "in_bed_coverage_percent": round(in_bed_nights / window.days * 100, 1),
        "average_asleep_hours": round(current_average / 3600, 2) if current_average is not None else None,
        "total_asleep_hours": round(asleep / 3600, 2),
        "total_in_bed_hours": round(in_bed / 3600, 2) if in_bed_nights else None,
        "total_awake_hours": round(awake / 3600, 2),
        "stage_hours": {
            "core": round(sum(float(row["core_seconds"]) for row in rows) / 3600, 2),
            "deep": round(sum(float(row["deep_seconds"]) for row in rows) / 3600, 2),
            "rem": round(sum(float(row["rem_seconds"]) for row in rows) / 3600, 2),
            "unspecified": round(sum(float(row["unspecified_seconds"]) for row in rows) / 3600, 2),
        },
        "in_bed_only_sessions": sum(int(row["in_bed_only"]) for row in rows),
        "comparison": {
            "absolute_hours": round(float(comparison["absolute"]) / 3600, 2) if comparison["absolute"] is not None else None,
            "percent": round(float(comparison["percent"]), 1) if comparison["percent"] is not None else None,
            "prior_logged_nights": prior_logged_nights,
            "prior_measured_nights": prior_measured_nights,
            "prior_coverage_percent": round(prior_logged_nights / window.days * 100, 1),
            "basis": f"{window.prior_start.isoformat()} to {window.prior_end.isoformat()}",
        },
        "granularity": chart_granularity,
        "trend": _sleep_trend(rows, chart_granularity),
        "method": "Overlapping sleep intervals are unioned; staged sleep is kept separate from In Bed and assigned to the wake-up day.",
    }


def sleep_trend_summary(
    connection: sqlite3.Connection,
    window: DateWindow,
    granularity: str | None = None,
) -> dict[str, object]:
    rows = connection.execute(
        """
        SELECT wake_date, asleep_seconds, in_bed_seconds, in_bed_only
        FROM sleep_sessions WHERE wake_date BETWEEN ? AND ? ORDER BY wake_date
        """,
        (window.start.isoformat(), window.end.isoformat()),
    ).fetchall()
    chart_granularity = resolve_granularity(window, granularity)
    return {
        "available": bool(rows),
        "window": window.as_dict(),
        "granularity": chart_granularity,
        "trend": _sleep_trend(rows, chart_granularity),
    }


def _activity_goals(connection: sqlite3.Connection, window: DateWindow) -> dict[str, object]:
    rows = connection.execute(
        """
        SELECT * FROM activity_summaries
        WHERE date_components BETWEEN ? AND ? ORDER BY date_components
        """,
        (window.start.isoformat(), window.end.isoformat()),
    ).fetchall()
    days: list[dict[str, object]] = []
    for row in rows:
        day = str(row["date_components"] or "")
        try:
            active = float(row["active_energy_burned"]) if row["active_energy_burned"] is not None else None
            active_goal = float(row["active_energy_goal"]) if row["active_energy_goal"] is not None else None
            exercise = float(row["exercise_time"]) if row["exercise_time"] is not None else None
            exercise_goal = float(row["exercise_goal"]) if row["exercise_goal"] is not None else None
            stand = float(row["stand_hours"]) if row["stand_hours"] is not None else None
            stand_goal = float(row["stand_goal"]) if row["stand_goal"] is not None else None
        except ValueError:
            continue
        energy_unit = str(row["active_energy_unit"] or "kcal")
        if active is not None:
            active = normalize_record(
                "HKQuantityTypeIdentifierActiveEnergyBurned",
                energy_unit,
                str(active),
                f"{day} 00:00:00 +0000",
                f"{day} 00:00:00 +0000",
            ).numeric_value
        if active_goal is not None:
            active_goal = normalize_record(
                "HKQuantityTypeIdentifierActiveEnergyBurned",
                energy_unit,
                str(active_goal),
                f"{day} 00:00:00 +0000",
                f"{day} 00:00:00 +0000",
            ).numeric_value
        days.append(
            {
                "date": day,
                "active_energy": active,
                "active_goal": active_goal,
                "active_met": active is not None and active_goal is not None and active >= active_goal,
                "exercise_minutes": exercise,
                "exercise_goal": exercise_goal,
                "exercise_met": exercise is not None and exercise_goal is not None and exercise >= exercise_goal,
                "stand_hours": stand,
                "stand_goal": stand_goal,
                "stand_met": stand is not None and stand_goal is not None and stand >= stand_goal,
            }
        )
    return {
        "available": bool(days),
        "days": _decimate(days),
        "days_with_goals": len(days),
        "active_energy_met_days": sum(bool(day["active_met"]) for day in days),
        "exercise_met_days": sum(bool(day["exercise_met"]) for day in days),
        "stand_met_days": sum(bool(day["stand_met"]) for day in days),
    }


def _insight(
    *,
    key: str,
    kind: str,
    category: str,
    title: str,
    text: str,
    window: DateWindow,
    sample_count: int,
    coverage_percent: float,
    basis: str,
    method: str,
    note: str | None = None,
    sample_label: str = "samples",
) -> dict[str, object]:
    return {
        "key": key,
        "kind": kind,
        "category": category,
        "title": title,
        "text": text,
        "date_window": f"{window.start.isoformat()} to {window.end.isoformat()}",
        "sample_count": sample_count,
        "sample_label": sample_label,
        "coverage_percent": round(coverage_percent, 1),
        "comparison_basis": basis,
        "method": method,
        "note": note,
    }


def _direction(percent: float) -> tuple[str, float]:
    if abs(percent) < 0.05:
        return "essentially unchanged from", abs(percent)
    return ("higher than" if percent > 0 else "lower than"), abs(percent)


def _longest_streak(days: list[str]) -> int:
    longest = current = 0
    previous: date | None = None
    for value in sorted(set(days)):
        day = date.fromisoformat(value)
        current = current + 1 if previous and day == previous + timedelta(days=1) else 1
        longest = max(longest, current)
        previous = day
    return longest


def _rolling_baseline_insight(
    connection: sqlite3.Connection,
    definition: MetricDefinition,
    window: DateWindow,
) -> dict[str, object] | None:
    if window.days < 35:
        return None
    latest_start = window.end - timedelta(days=6)
    baseline_end = latest_start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=27)
    latest_rows = _daily_rows(connection, definition.key, latest_start, window.end, "combined", 0)
    baseline_rows = _daily_rows(connection, definition.key, baseline_start, baseline_end, "combined", 0)
    if len(latest_rows) < 5 or len(baseline_rows) < 14:
        return None
    latest = _aggregate_rows(definition, latest_rows)
    baseline = _aggregate_rows(definition, baseline_rows)
    if not latest or not baseline:
        return None
    latest_value = float(latest["canonical_value"])
    baseline_value = float(baseline["canonical_value"])
    if definition.kind == "cumulative":
        latest_value /= len(latest_rows)
        baseline_value /= len(baseline_rows)
    if not baseline_value:
        return None
    percent = (latest_value - baseline_value) / abs(baseline_value) * 100
    direction, magnitude = _direction(percent)
    return _insight(
        key=f"baseline-{definition.key}",
        kind="rolling_baseline",
        category=definition.category,
        title=f"{definition.label}: recent baseline",
        text=(
            f"The latest 7 days were {magnitude:.1f}% {direction} the preceding "
            "28-day personal baseline."
        ),
        window=window,
        sample_count=int(latest["sample_count"]),
        coverage_percent=len(latest_rows) / 7 * 100,
        basis=f"{baseline_start.isoformat()} to {baseline_end.isoformat()}",
        method=(
            "Average per tracked day in the latest 7 days compared with the average per tracked day in the preceding 28 days."
            if definition.kind == "cumulative"
            else "Latest 7-day sample-weighted mean compared with the preceding 28-day mean."
        ),
    )


def _paired_values(
    connection: sqlite3.Connection,
    left: str,
    right: str,
    window: DateWindow,
) -> list[tuple[float, float]]:
    def values(key: str) -> dict[str, float]:
        if key == "sleep":
            return {
                str(row["wake_date"]): float(row["value"])
                for row in connection.execute(
                    """
                    SELECT wake_date, SUM(asleep_seconds) / 3600.0 AS value
                    FROM sleep_sessions WHERE wake_date BETWEEN ? AND ?
                    GROUP BY wake_date HAVING SUM(asleep_seconds) > 0
                    """,
                    (window.start.isoformat(), window.end.isoformat()),
                )
            }
        definition = METRICS[key]
        column = "value_sum" if definition.kind == "cumulative" else "value_mean"
        return {
            str(row["local_date"]): float(row["value"])
            for row in connection.execute(
                f"""
                SELECT local_date, {column} AS value FROM daily_metrics
                WHERE metric_key = ? AND scope = 'combined' AND source_key = 0
                  AND local_date BETWEEN ? AND ? AND {column} IS NOT NULL
                """,
                (key, window.start.isoformat(), window.end.isoformat()),
            )
        }

    left_values = values(left)
    right_values = values(right)
    return [(left_values[day], right_values[day]) for day in sorted(left_values.keys() & right_values.keys())]


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    left_mean = sum(left for left, _right in pairs) / len(pairs)
    right_mean = sum(right for _left, right in pairs) / len(pairs)
    numerator = sum((left - left_mean) * (right - right_mean) for left, right in pairs)
    left_spread = sum((left - left_mean) ** 2 for left, _right in pairs)
    right_spread = sum((right - right_mean) ** 2 for _left, right in pairs)
    denominator = math.sqrt(left_spread * right_spread)
    return numerator / denominator if denominator else None


def _correlation_insight(
    connection: sqlite3.Connection,
    window: DateWindow,
    category: str | None,
) -> dict[str, object] | None:
    candidates = (
        ("steps", "sleep", "Steps", "sleep duration", {None, "activity", "sleep"}),
        ("steps", "resting_heart_rate", "Steps", "resting heart rate", {None, "activity", "heart"}),
        ("resting_heart_rate", "sleep", "Resting heart rate", "sleep duration", {None, "heart", "sleep"}),
    )
    for left, right, left_label, right_label, categories in candidates:
        if category not in categories:
            continue
        pairs = _paired_values(connection, left, right, window)
        paired_coverage = len(pairs) / window.days * 100
        if len(pairs) < 7 or paired_coverage < 40:
            continue
        coefficient = _pearson(pairs)
        if coefficient is None:
            continue
        magnitude = abs(coefficient)
        strength = "little" if magnitude < 0.2 else "a weak" if magnitude < 0.4 else "a moderate" if magnitude < 0.7 else "a strong"
        relationship = "positive" if coefficient > 0 else "negative"
        return _insight(
            key=f"correlation-{left}-{right}",
            kind="correlation",
            category=category or "overview",
            title="Paired-day association",
            text=(
                f"Across {len(pairs)} paired days, {left_label.lower()} and {right_label} "
                f"showed {strength} {relationship} association (r = {coefficient:.2f})."
            ),
            window=window,
            sample_count=len(pairs),
            coverage_percent=paired_coverage,
            basis="Calendar days containing both measures in the selected period.",
            method="Pearson correlation across paired daily values; missing days are excluded.",
            note="Correlation does not imply causation.",
            sample_label="paired days",
        )
    return None


def insights_summary(
    connection: sqlite3.Connection,
    window: DateWindow,
    unit_system: str,
    *,
    category: str | None = None,
    limit: int = 6,
    metric_summaries: dict[str, dict[str, object]] | None = None,
    sleep_data: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """Return deterministic, coverage-gated, non-diagnostic narratives."""
    overview_keys = (
        "steps",
        "active_energy",
        "exercise_minutes",
        "resting_heart_rate",
        "hrv_sdnn",
        "blood_glucose",
        "body_mass",
    )
    definitions = (
        CATEGORIES.get(category, ())
        if category
        else tuple(METRICS[key] for key in overview_keys)
    )
    available_summaries = metric_summaries or {}
    insights: list[dict[str, object]] = []
    period_count = 0
    for definition in definitions:
        summary = available_summaries.get(definition.key) or metric_summary(
            connection,
            definition.key,
            window,
            unit_system,
            include_filters=False,
        )
        if not summary["available"]:
            continue
        comparison = summary["comparison"]
        if (
            period_count < 2
            and summary["tracked_days"] >= 5
            and summary["coverage_percent"] >= 50
            and comparison["prior_tracked_days"] >= 5
            and comparison["prior_coverage_percent"] >= 50
            and comparison["percent"] is not None
            and (
                definition.kind != "cumulative"
                or (
                    summary["tracked_days"] == comparison["prior_tracked_days"]
                    and summary["coverage_percent"] >= 80
                )
            )
        ):
            direction, magnitude = _direction(float(comparison["percent"]))
            insights.append(
                _insight(
                    key=f"period-{definition.key}",
                    kind="period_comparison",
                    category=definition.category,
                    title=f"{definition.label}: period change",
                    text=f"{definition.label} was {magnitude:.1f}% {direction} the immediately preceding period.",
                    window=window,
                    sample_count=int(summary["sample_count"]),
                    coverage_percent=float(summary["coverage_percent"]),
                    basis=str(comparison["basis"]),
                    method=(
                        "Selected-period total compared with the equal-length preceding total."
                        if definition.kind == "cumulative"
                        else "Selected-period sample-weighted mean compared with the equal-length preceding mean."
                    ),
                )
            )
            period_count += 1

    baseline = next(
        (
            item
            for definition in definitions
            if (item := _rolling_baseline_insight(connection, definition, window)) is not None
        ),
        None,
    )
    if baseline:
        insights.append(baseline)

    if category in {None, "activity"}:
        for definition in CATEGORIES["activity"]:
            if definition.kind != "cumulative":
                continue
            rows = _daily_rows(connection, definition.key, window.start, window.end, "combined", 0)
            coverage = len(rows) / window.days * 100
            streak = _longest_streak([str(row["local_date"]) for row in rows])
            if len(rows) >= 5 and coverage >= 50 and streak >= 3:
                insights.append(
                    _insight(
                        key=f"streak-{definition.key}",
                        kind="consistency",
                        category="activity",
                        title=f"{definition.label}: recording consistency",
                        text=f"{definition.label} data appeared on {streak} consecutive days at its longest in this selection.",
                        window=window,
                        sample_count=sum(int(row["sample_count"]) for row in rows),
                        coverage_percent=coverage,
                        basis="Consecutive calendar days containing at least one aggregate.",
                        method="Longest run of tracked days; an unrecorded day ends the run and is not treated as zero.",
                    )
                )
                break

    if category in {None, "sleep"}:
        sleep = sleep_data or sleep_summary(connection, window)
        if sleep.get("available") and int(sleep["measured_nights"]) >= 5 and float(sleep["asleep_coverage_percent"]) >= 50:
            nights = [float(point["asleep_hours"]) for point in _sleep_trend(
                connection.execute(
                    "SELECT * FROM sleep_sessions WHERE wake_date BETWEEN ? AND ? ORDER BY wake_date",
                    (window.start.isoformat(), window.end.isoformat()),
                ).fetchall(),
                "day",
            ) if point["asleep_hours"] is not None]
            average = sum(nights) / len(nights)
            variation = math.sqrt(sum((value - average) ** 2 for value in nights) / len(nights))
            insights.append(
                _insight(
                    key="sleep-duration-consistency",
                    kind="consistency",
                    category="sleep",
                    title="Sleep duration consistency",
                    text=f"Nightly asleep duration varied by about {variation:.1f} hr around the selected-period average.",
                    window=window,
                    sample_count=int(sleep["measured_nights"]),
                    coverage_percent=float(sleep["asleep_coverage_percent"]),
                    basis=f"{sleep['measured_nights']} wake-up days with measured asleep duration.",
                    method="Population standard deviation of logged nightly asleep duration; missing nights are excluded.",
                    sample_label="measured nights",
                )
            )

    correlation = _correlation_insight(connection, window, category)
    if correlation:
        insights.append(correlation)
    return insights[:limit]


def category_summary(
    connection: sqlite3.Connection,
    category: str,
    window: DateWindow,
    unit_system: str,
    *,
    granularity: str | None = None,
) -> dict[str, object]:
    if category == "sleep":
        sleep = sleep_summary(connection, window, granularity)
        return {
            "category": "sleep",
            "window": window.as_dict(),
            "sleep": sleep,
            "metrics": [],
            "insights": insights_summary(
                connection,
                window,
                unit_system,
                category="sleep",
                sleep_data=sleep,
            ),
        }
    definitions = CATEGORIES.get(category)
    if not definitions:
        raise AnalyticsError("unknown_category", "That dashboard category is not supported.")
    metrics = [
        metric_summary(
            connection,
            item.key,
            window,
            unit_system,
            include_filters=False,
            granularity=granularity,
        )
        for item in definitions
    ]
    payload = {
        "category": category,
        "window": window.as_dict(),
        "metrics": [item for item in metrics if item["available"]],
        "unavailable": [item["key"] for item in metrics if not item["available"]],
        "insights": insights_summary(
            connection,
            window,
            unit_system,
            category=category,
            metric_summaries={str(item["key"]): item for item in metrics},
        ),
    }
    if category == "activity":
        payload["goals"] = _activity_goals(connection, window)
    return payload


def overview_summary(
    connection: sqlite3.Connection,
    window: DateWindow,
    unit_system: str,
    *,
    granularity: str | None = None,
) -> dict[str, object]:
    preferred = ("steps", "active_energy", "resting_heart_rate", "blood_glucose", "body_mass")
    metrics = [
        metric_summary(
            connection,
            key,
            window,
            unit_system,
            include_filters=False,
            granularity=granularity,
        )
        for key in preferred
    ]
    sleep = sleep_summary(connection, window, granularity)
    cards = [metric for metric in metrics if metric["available"]]
    if sleep.get("available") and sleep.get("average_asleep_hours") is not None:
        cards.append(
            {
                "key": "sleep",
                "label": "Average sleep",
                "available": True,
                "formatted_value": f"{sleep['average_asleep_hours']:.1f} hr",
                "headline_value": f"{sleep['average_asleep_hours']:.1f} hr",
                "headline_label": "Average",
                "tracked_days": sleep["measured_nights"],
                "coverage_percent": sleep["asleep_coverage_percent"],
                "comparison": sleep["comparison"],
            }
        )
    tracked = connection.execute(
        "SELECT COUNT(DISTINCT local_date) FROM daily_metrics WHERE scope = 'combined' AND local_date BETWEEN ? AND ?",
        (window.start.isoformat(), window.end.isoformat()),
    ).fetchone()[0]
    available_categories = connection.execute(
        """
        SELECT COUNT(DISTINCT h.category)
        FROM health_types h JOIN daily_metrics d ON d.metric_key = h.metric_key
        WHERE d.scope = 'combined' AND d.local_date BETWEEN ? AND ?
        """,
        (window.start.isoformat(), window.end.isoformat()),
    ).fetchone()[0]
    if sleep.get("available"):
        available_categories += 1
    source_count = connection.execute(
        """
        SELECT COUNT(DISTINCT source_id) FROM (
          SELECT source_key AS source_id FROM daily_metrics
          WHERE scope = 'source' AND local_date BETWEEN ? AND ?
          UNION ALL
          SELECT st.source_id FROM sleep_stages st
          JOIN sleep_sessions ss ON ss.id = st.session_id
          WHERE ss.wake_date BETWEEN ? AND ?
        )
        """,
        (
            window.start.isoformat(),
            window.end.isoformat(),
            window.start.isoformat(),
            window.end.isoformat(),
        ),
    ).fetchone()[0]
    device_count = connection.execute(
        """
        SELECT COUNT(DISTINCT source_key) FROM daily_metrics
        WHERE scope = 'device' AND local_date BETWEEN ? AND ?
        """,
        (window.start.isoformat(), window.end.isoformat()),
    ).fetchone()[0]
    manifest = connection.execute(
        "SELECT imported_at, export_date FROM manifest WHERE id = 1"
    ).fetchone()
    bounds = _available_bounds(connection)
    return {
        "window": window.as_dict(),
        "cards": cards,
        "insights": insights_summary(
            connection,
            window,
            unit_system,
            metric_summaries={str(item["key"]): item for item in metrics},
            sleep_data=sleep,
        ),
        "freshness": {
            "first_date": bounds[0].isoformat() if bounds else None,
            "latest_date": bounds[1].isoformat() if bounds else None,
            "imported_at": str(manifest["imported_at"]) if manifest else None,
            "export_date": str(manifest["export_date"]) if manifest and manifest["export_date"] else None,
        },
        "coverage": {
            "tracked_days": int(tracked),
            "period_days": window.days,
            "percent": round(int(tracked) / window.days * 100, 1),
            "categories": int(available_categories),
            "sources": int(source_count),
            "devices": int(device_count),
            "missing_days": max(0, window.days - int(tracked)),
        },
    }
