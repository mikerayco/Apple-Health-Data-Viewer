"""Read-only date windows and source-aware dashboard summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import sqlite3
from typing import Iterable
from urllib.parse import quote

from .metrics import CATEGORIES, METRICS, MetricDefinition, display_value, format_value, normalize_record


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
    if not path.is_file():
        raise AnalyticsError("empty", "Import an Apple Health export first.")
    uri = f"file:{quote(path.resolve().as_posix(), safe='/:')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        version = connection.execute("SELECT schema_version FROM manifest WHERE id = 1").fetchone()
    except sqlite3.DatabaseError as error:
        connection.close()
        raise AnalyticsError("invalid_database", "The active health database could not be read.") from error
    if not version or int(version[0]) < 2:
        connection.close()
        raise AnalyticsError("reimport_required", "Re-import the source to compute Phase 3 metrics.")
    return connection


def _available_bounds(connection: sqlite3.Connection) -> tuple[date, date] | None:
    row = connection.execute(
        """
        SELECT MIN(day), MAX(day) FROM (
          SELECT local_date AS day FROM daily_metrics WHERE scope = 'combined'
          UNION ALL
          SELECT wake_date AS day FROM sleep_sessions
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
) -> dict[str, object]:
    definition = METRICS.get(metric_key)
    if not definition:
        raise AnalyticsError("unknown_metric", "That metric is not supported.")
    scope, source_key = _filter_scope(source_id, device_id)
    rows = _daily_rows(connection, metric_key, window.start, window.end, scope, source_key)
    current = _aggregate_rows(definition, rows)
    prior_rows = _daily_rows(connection, metric_key, window.prior_start, window.prior_end, scope, source_key)
    prior = _aggregate_rows(definition, prior_rows)
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
                "basis": f"{window.prior_start.isoformat()} to {window.prior_end.isoformat()}",
            },
            "trend": _decimate(
                [
                    {
                        "date": str(row["local_date"]),
                        "value": _display_number(
                            definition,
                            float(row["value_sum"] if definition.kind == "cumulative" else row["value_mean"]),
                            unit_system,
                        ),
                        "samples": int(row["sample_count"]),
                        "estimated": bool(row["is_estimate"]),
                    }
                    for row in rows
                ]
            ),
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


def sleep_summary(connection: sqlite3.Connection, window: DateWindow) -> dict[str, object]:
    rows = connection.execute(
        "SELECT * FROM sleep_sessions WHERE wake_date BETWEEN ? AND ? ORDER BY wake_date",
        (window.start.isoformat(), window.end.isoformat()),
    ).fetchall()
    prior = connection.execute(
        "SELECT * FROM sleep_sessions WHERE wake_date BETWEEN ? AND ?",
        (window.prior_start.isoformat(), window.prior_end.isoformat()),
    ).fetchall()
    if not rows:
        return {"available": False, "reason": "no_records_in_range", "window": window.as_dict(), "trend": []}
    asleep = sum(float(row["asleep_seconds"]) for row in rows)
    in_bed = sum(float(row["in_bed_seconds"]) for row in rows)
    awake = sum(float(row["awake_seconds"]) for row in rows)
    logged_nights = len({str(row["wake_date"]) for row in rows})
    prior_asleep = sum(float(row["asleep_seconds"]) for row in prior) if prior else None
    current_average = asleep / len(rows)
    prior_average = prior_asleep / len(prior) if prior else None
    comparison = _comparison(current_average, prior_average)
    return {
        "available": True,
        "window": window.as_dict(),
        "sessions": len(rows),
        "logged_nights": logged_nights,
        "coverage_percent": round(logged_nights / window.days * 100, 1),
        "average_asleep_hours": round(current_average / 3600, 2),
        "total_asleep_hours": round(asleep / 3600, 2),
        "total_in_bed_hours": round(in_bed / 3600, 2),
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
            "basis": f"{window.prior_start.isoformat()} to {window.prior_end.isoformat()}",
        },
        "trend": _decimate(
            [
                {
                    "date": str(row["wake_date"]),
                    "asleep_hours": round(float(row["asleep_seconds"]) / 3600, 2),
                    "in_bed_hours": round(float(row["in_bed_seconds"]) / 3600, 2),
                    "in_bed_only": bool(row["in_bed_only"]),
                }
                for row in rows
            ]
        ),
        "method": "Overlapping sleep intervals are unioned; staged sleep is kept separate from In Bed and assigned to the wake-up day.",
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


def category_summary(
    connection: sqlite3.Connection,
    category: str,
    window: DateWindow,
    unit_system: str,
) -> dict[str, object]:
    if category == "sleep":
        return {"category": "sleep", "window": window.as_dict(), "sleep": sleep_summary(connection, window), "metrics": []}
    definitions = CATEGORIES.get(category)
    if not definitions:
        raise AnalyticsError("unknown_category", "That dashboard category is not supported in Phase 3.")
    metrics = [metric_summary(connection, item.key, window, unit_system, include_filters=False) for item in definitions]
    payload = {
        "category": category,
        "window": window.as_dict(),
        "metrics": [item for item in metrics if item["available"]],
        "unavailable": [item["key"] for item in metrics if not item["available"]],
    }
    if category == "activity":
        payload["goals"] = _activity_goals(connection, window)
    return payload


def overview_summary(connection: sqlite3.Connection, window: DateWindow, unit_system: str) -> dict[str, object]:
    preferred = ("steps", "active_energy", "resting_heart_rate", "blood_glucose", "body_mass")
    metrics = [metric_summary(connection, key, window, unit_system, include_filters=False) for key in preferred]
    sleep = sleep_summary(connection, window)
    cards = [metric for metric in metrics if metric["available"]]
    if sleep.get("available"):
        cards.append(
            {
                "key": "sleep",
                "label": "Average sleep",
                "available": True,
                "formatted_value": f"{sleep['average_asleep_hours']:.1f} hr",
                "headline_value": f"{sleep['average_asleep_hours']:.1f} hr",
                "headline_label": "Average",
                "tracked_days": sleep["logged_nights"],
                "coverage_percent": sleep["coverage_percent"],
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
    return {
        "window": window.as_dict(),
        "cards": cards,
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
