"""Read-only workout, route, and ECG summaries for Phase 5 views."""

from __future__ import annotations

from datetime import date
import math
import re
import sqlite3

from .analytics import AnalyticsError, DateWindow
from .version import HEALTH_SCHEMA_VERSION

WORKOUT_LABELS = {
    "HKWorkoutActivityTypeRunning": "Running",
    "HKWorkoutActivityTypeWalking": "Walking",
    "HKWorkoutActivityTypeCycling": "Cycling",
    "HKWorkoutActivityTypeSwimming": "Swimming",
    "HKWorkoutActivityTypeHiking": "Hiking",
    "HKWorkoutActivityTypeYoga": "Yoga",
    "HKWorkoutActivityTypeTraditionalStrengthTraining": "Traditional strength training",
    "HKWorkoutActivityTypeFunctionalStrengthTraining": "Functional strength training",
    "HKWorkoutActivityTypeHighIntensityIntervalTraining": "High-intensity interval training",
    "HKWorkoutActivityTypeElliptical": "Elliptical",
    "HKWorkoutActivityTypeRowing": "Rowing",
    "HKWorkoutActivityTypeStairClimbing": "Stair climbing",
    "HKWorkoutActivityTypeCoreTraining": "Core training",
    "HKWorkoutActivityTypeDance": "Dance",
    "HKWorkoutActivityTypeCooldown": "Cooldown",
    "HKWorkoutActivityTypeOther": "Other",
}


def require_phase_five(connection: sqlite3.Connection) -> None:
    row = connection.execute("SELECT schema_version FROM manifest WHERE id = 1").fetchone()
    if not row or int(row[0]) < 3:
        raise AnalyticsError("reimport_required", "Re-import the source to parse workouts, routes, and ECGs.")
    if int(row[0]) > HEALTH_SCHEMA_VERSION:
        raise AnalyticsError(
            "newer_database",
            "This health database was created by a newer viewer. Upgrade the application to open it.",
        )


def workout_label(identifier: str) -> str:
    if identifier in WORKOUT_LABELS:
        return WORKOUT_LABELS[identifier]
    raw = identifier.removeprefix("HKWorkoutActivityType") or identifier
    label = re.sub(r"(?<!^)(?=[A-Z])", " ", raw).strip()
    return label or "Unknown workout type"


def _positive_int(value: int, name: str, maximum: int) -> int:
    if value < 1 or value > maximum:
        raise AnalyticsError("invalid_pagination", f"Choose a valid {name}.")
    return value


def _display_distance(metres: float | None, unit_system: str) -> tuple[float | None, str]:
    if metres is None:
        return None, "km" if unit_system == "metric" else "mi"
    if unit_system == "imperial":
        return round(metres / 1609.344, 2), "mi"
    return round(metres / 1000, 2), "km"


def _duration_text(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    minutes = round(seconds / 60)
    hours, remaining = divmod(minutes, 60)
    return f"{hours} hr {remaining} min" if hours else f"{remaining} min"


def _workout_filters(
    window: DateWindow,
    activity_type: str | None,
    search: str | None,
    source_id: int | None,
    device_id: int | None,
    route_only: bool,
) -> tuple[str, list[object]]:
    if source_id is not None and device_id is not None:
        raise AnalyticsError("invalid_filter", "Choose a source or a device, not both.")
    conditions = ["w.local_start_date BETWEEN ? AND ?"]
    parameters: list[object] = [window.start.isoformat(), window.end.isoformat()]
    if activity_type:
        if len(activity_type) > 160:
            raise AnalyticsError("invalid_filter", "Choose a valid workout type.")
        conditions.append("w.activity_type = ?")
        parameters.append(activity_type)
    if search:
        if len(search) > 100:
            raise AnalyticsError("invalid_filter", "Workout search is limited to 100 characters.")
        term = f"%{search.casefold()}%"
        conditions.append(
            "(lower(w.activity_type) LIKE ? OR w.source_id IN "
            "(SELECT id FROM sources WHERE lower(name) LIKE ?))"
        )
        parameters.extend((term, term))
    if source_id is not None:
        if source_id < 1:
            raise AnalyticsError("invalid_filter", "Choose a valid source.")
        conditions.append("w.source_id = ?")
        parameters.append(source_id)
    if device_id is not None:
        if device_id < 1:
            raise AnalyticsError("invalid_filter", "Choose a valid device.")
        conditions.append("w.device_id = ?")
        parameters.append(device_id)
    if route_only:
        conditions.append("EXISTS (SELECT 1 FROM workout_routes wr WHERE wr.workout_id = w.id AND wr.point_count > 1)")
    return " AND ".join(conditions), parameters


def workout_list(
    connection: sqlite3.Connection,
    window: DateWindow,
    unit_system: str,
    *,
    activity_type: str | None = None,
    search: str | None = None,
    source_id: int | None = None,
    device_id: int | None = None,
    route_only: bool = False,
    page: int = 1,
    per_page: int = 25,
) -> dict[str, object]:
    require_phase_five(connection)
    page = _positive_int(page, "page", 1_000_000)
    per_page = _positive_int(per_page, "page size", 100)
    where, parameters = _workout_filters(
        window, activity_type, search, source_id, device_id, route_only
    )
    total = int(
        connection.execute(f"SELECT COUNT(*) FROM workouts w WHERE {where}", parameters).fetchone()[0]
    )
    aggregate = connection.execute(
        f"""
        SELECT SUM(w.duration_seconds), AVG(w.duration_seconds), SUM(w.total_distance_m),
               SUM(w.total_energy_kcal)
        FROM workouts w WHERE {where}
        """,
        parameters,
    ).fetchone()
    rows = connection.execute(
        f"""
        SELECT w.id, w.activity_type, w.start_date, w.local_start_date, w.duration_seconds,
               w.total_distance_m, w.total_energy_kcal, s.name AS source_name,
               d.description AS device_name,
               EXISTS(SELECT 1 FROM workout_routes wr WHERE wr.workout_id = w.id AND wr.point_count > 1) AS has_route
        FROM workouts w JOIN sources s ON s.id = w.source_id
        LEFT JOIN devices d ON d.id = w.device_id
        WHERE {where} ORDER BY w.start_utc DESC, w.id DESC LIMIT ? OFFSET ?
        """,
        [*parameters, per_page, (page - 1) * per_page],
    ).fetchall()
    items: list[dict[str, object]] = []
    for row in rows:
        distance, distance_unit = _display_distance(row["total_distance_m"], unit_system)
        items.append(
            {
                "id": int(row["id"]),
                "activity_type": str(row["activity_type"]),
                "label": workout_label(str(row["activity_type"])),
                "start_date": str(row["start_date"]),
                "local_date": str(row["local_start_date"]),
                "duration_seconds": row["duration_seconds"],
                "duration": _duration_text(row["duration_seconds"]),
                "distance": distance,
                "distance_unit": distance_unit,
                "energy_kcal": round(float(row["total_energy_kcal"]), 1) if row["total_energy_kcal"] is not None else None,
                "source": str(row["source_name"]),
                "device": str(row["device_name"]) if row["device_name"] else None,
                "has_route": bool(row["has_route"]),
            }
        )
    types = [
        {"identifier": str(row["activity_type"]), "label": workout_label(str(row["activity_type"])), "count": int(row["count"])}
        for row in connection.execute(
            "SELECT activity_type, COUNT(*) AS count FROM workouts GROUP BY activity_type ORDER BY count DESC, activity_type"
        )
    ]
    sources = [dict(row) for row in connection.execute("SELECT id, name FROM sources WHERE id IN (SELECT source_id FROM workouts) ORDER BY name")]
    devices = [dict(row) for row in connection.execute("SELECT id, description AS name FROM devices WHERE id IN (SELECT device_id FROM workouts) ORDER BY description")]
    total_distance, distance_unit = _display_distance(aggregate[2], unit_system)
    return {
        "window": window.as_dict(),
        "items": items,
        "pagination": {"page": page, "per_page": per_page, "total": total, "pages": math.ceil(total / per_page) if total else 0},
        "summary": {
            "sessions": total,
            "total_duration": _duration_text(aggregate[0]),
            "average_duration": _duration_text(aggregate[1]),
            "total_distance": total_distance,
            "distance_unit": distance_unit,
            "total_energy_kcal": round(float(aggregate[3]), 1) if aggregate[3] is not None else None,
        },
        "filters": {"activity_types": types, "sources": sources, "devices": devices},
    }


def workout_detail(connection: sqlite3.Connection, workout_id: int, unit_system: str) -> dict[str, object]:
    require_phase_five(connection)
    if workout_id < 1:
        raise AnalyticsError("not_found", "That workout does not exist.")
    row = connection.execute(
        """
        SELECT w.*, s.name AS source_name, d.description AS device_name,
               wr.segment_count, wr.point_count, wr.route_distance_m,
               wr.elevation_gain_m, wr.elevation_loss_m
        FROM workouts w JOIN sources s ON s.id = w.source_id
        LEFT JOIN devices d ON d.id = w.device_id
        LEFT JOIN (
          SELECT workout_id, SUM(segment_count) AS segment_count, SUM(point_count) AS point_count,
                 SUM(distance_m) AS route_distance_m,
                 CASE WHEN COUNT(elevation_gain_m) = COUNT(*) THEN SUM(elevation_gain_m) END AS elevation_gain_m,
                 CASE WHEN COUNT(elevation_loss_m) = COUNT(*) THEN SUM(elevation_loss_m) END AS elevation_loss_m
          FROM workout_routes WHERE point_count > 1 GROUP BY workout_id
        ) wr ON wr.workout_id = w.id
        WHERE w.id = ?
        """,
        (workout_id,),
    ).fetchone()
    if not row:
        raise AnalyticsError("not_found", "That workout does not exist.")
    distance, distance_unit = _display_distance(row["total_distance_m"], unit_system)
    route_distance, _route_unit = _display_distance(row["route_distance_m"], unit_system)
    statistics = [dict(item) for item in connection.execute("SELECT type_identifier, start_date, end_date, average, minimum, maximum, sum_value, unit FROM workout_statistics WHERE workout_id = ?", (workout_id,))]
    events = [dict(item) for item in connection.execute("SELECT event_type, event_date, duration, duration_unit FROM workout_events WHERE workout_id = ? ORDER BY event_date", (workout_id,))]
    metadata = [dict(item) for item in connection.execute("SELECT key, value FROM workout_metadata WHERE workout_id = ? ORDER BY key", (workout_id,))]
    return {
        "id": workout_id,
        "activity_type": str(row["activity_type"]),
        "label": workout_label(str(row["activity_type"])),
        "start_date": str(row["start_date"]),
        "end_date": str(row["end_date"]),
        "duration": _duration_text(row["duration_seconds"]),
        "duration_seconds": row["duration_seconds"],
        "distance": distance,
        "distance_unit": distance_unit,
        "energy_kcal": round(float(row["total_energy_kcal"]), 1) if row["total_energy_kcal"] is not None else None,
        "source": str(row["source_name"]),
        "device": str(row["device_name"]) if row["device_name"] else None,
        "raw_identifier": str(row["activity_type"]),
        "statistics": statistics,
        "events": events,
        "metadata": metadata,
        "route": {
            "available": bool(row["segment_count"]),
            "segment_count": int(row["segment_count"] or 0),
            "point_count": int(row["point_count"] or 0),
            "distance": route_distance,
            "distance_unit": distance_unit,
            "elevation_gain_m": round(float(row["elevation_gain_m"]), 1) if row["elevation_gain_m"] is not None else None,
            "elevation_loss_m": round(float(row["elevation_loss_m"]), 1) if row["elevation_loss_m"] is not None else None,
        },
    }


def _sample_indexes(count: int, limit: int) -> list[int]:
    if count <= limit:
        return list(range(count))
    return sorted({round(index * (count - 1) / (limit - 1)) for index in range(limit)})


def _indexed_rows(
    connection: sqlite3.Connection,
    table: str,
    parent_column: str,
    parent_id: int,
    columns: str,
    indexes: list[int],
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for offset in range(0, len(indexes), 500):
        chunk = indexes[offset : offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(
            connection.execute(
                f"SELECT {columns} FROM {table} WHERE {parent_column} = ? AND sequence IN ({placeholders})",
                [parent_id, *chunk],
            ).fetchall()
        )
    return sorted(rows, key=lambda row: int(row["sequence"]))


def _route_segment_rows(
    connection: sqlite3.Connection,
    route_id: int,
    segment: int,
    count: int,
    first_sequence: int,
    last_sequence: int,
    limit: int,
) -> list[sqlite3.Row]:
    columns = (
        "sequence, segment, latitude, longitude, elevation_m, recorded_at, "
        "horizontal_accuracy_m, vertical_accuracy_m"
    )
    if count <= limit:
        return connection.execute(
            f"SELECT {columns} FROM route_points WHERE route_id = ? AND segment = ? ORDER BY sequence",
            (route_id, segment),
        ).fetchall()
    if limit < 6:
        indexes = sorted(
            {
                round(first_sequence + index * (last_sequence - first_sequence) / (limit - 1))
                for index in range(limit)
            }
        )
        return _indexed_rows(
            connection, "route_points", "route_id", route_id, columns, indexes
        )
    buckets = max(1, (limit - 2) // 4)
    return connection.execute(
        """
        WITH bucketed AS (
          SELECT sequence, segment, latitude, longitude, elevation_m, recorded_at,
                 horizontal_accuracy_m, vertical_accuracy_m,
                 CAST((sequence - ?) * ? / ? AS INTEGER) AS bucket
          FROM route_points WHERE route_id = ? AND segment = ?
        ), ranked AS (
          SELECT *,
                 ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY latitude, sequence) AS min_lat,
                 ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY latitude DESC, sequence) AS max_lat,
                 ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY longitude, sequence) AS min_lon,
                 ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY longitude DESC, sequence) AS max_lon
          FROM bucketed
        )
        SELECT sequence, segment, latitude, longitude, elevation_m, recorded_at,
               horizontal_accuracy_m, vertical_accuracy_m
        FROM ranked WHERE min_lat = 1 OR max_lat = 1 OR min_lon = 1 OR max_lon = 1
          OR sequence IN (?, ?)
        ORDER BY sequence LIMIT ?
        """,
        (
            first_sequence,
            buckets,
            count,
            route_id,
            segment,
            first_sequence,
            last_sequence,
            limit,
        ),
    ).fetchall()


def _mark_elevation_gaps(
    connection: sqlite3.Connection,
    route_id: int,
    rows: list[sqlite3.Row],
) -> list[dict[str, object]]:
    selected = {int(row["sequence"]): dict(row) for row in rows}
    pending: dict[int, bool] = {}
    for source in connection.execute(
        "SELECT sequence, segment, elevation_m FROM route_points WHERE route_id = ? ORDER BY sequence",
        (route_id,),
    ):
        segment = int(source["segment"])
        if source["elevation_m"] is None:
            pending[segment] = True
        sequence = int(source["sequence"])
        if sequence not in selected:
            continue
        selected[sequence]["elevation_gap_before"] = pending.get(segment, False)
        if source["elevation_m"] is not None:
            pending[segment] = False
    return [selected[index] for index in sorted(selected)]


def _route_render_rows(
    connection: sqlite3.Connection,
    route_id: int,
    limit: int,
) -> list[dict[str, object]]:
    segments = connection.execute(
        """
        SELECT segment, COUNT(*) AS count, MIN(sequence) AS first_sequence,
               MAX(sequence) AS last_sequence
        FROM route_points WHERE route_id = ? GROUP BY segment ORDER BY segment
        """,
        (route_id,),
    ).fetchall()
    minimum = len(segments) * 2
    if limit < minimum:
        raise AnalyticsError(
            "invalid_limit",
            f"Choose a route point limit of at least {minimum} for all track segments.",
        )
    total = sum(int(segment["count"]) for segment in segments)
    remaining = limit
    rows: list[sqlite3.Row] = []
    for index, segment in enumerate(segments):
        later_minimum = (len(segments) - index - 1) * 2
        proportional = round(limit * int(segment["count"]) / total)
        allocation = max(2, min(proportional, remaining - later_minimum))
        selected = _route_segment_rows(
            connection,
            route_id,
            int(segment["segment"]),
            int(segment["count"]),
            int(segment["first_sequence"]),
            int(segment["last_sequence"]),
            allocation,
        )
        rows.extend(selected)
        remaining -= len(selected)
    ordered = sorted(rows, key=lambda row: int(row["sequence"]))
    return _mark_elevation_gaps(connection, route_id, ordered)


def workout_route(connection: sqlite3.Connection, workout_id: int, limit: int = 1200) -> dict[str, object]:
    require_phase_five(connection)
    if limit < 2 or limit > 5000:
        raise AnalyticsError("invalid_limit", "Choose a route point limit from 2 to 5000.")
    routes = connection.execute(
        "SELECT * FROM workout_routes WHERE workout_id = ? AND point_count > 1 ORDER BY id",
        (workout_id,),
    ).fetchall()
    if not routes:
        raise AnalyticsError("not_found", "That workout has no imported route.")
    total_segments = sum(int(route["segment_count"] or 1) for route in routes)
    if limit < total_segments * 2:
        raise AnalyticsError(
            "invalid_limit",
            f"Choose a route point limit of at least {total_segments * 2} for all track segments.",
        )
    selected_routes = routes
    original_count = sum(int(route["point_count"]) for route in routes)
    selected_count = original_count
    remaining = limit
    points: list[dict[str, object]] = []
    segment_offset = 0
    point_offset = 0
    for route_index, route in enumerate(selected_routes):
        later_segments = sum(
            int(item["segment_count"] or 1) for item in selected_routes[route_index + 1 :]
        )
        route_minimum = int(route["segment_count"] or 1) * 2
        proportional = round(limit * int(route["point_count"]) / selected_count)
        allocation = max(
            route_minimum,
            min(proportional, remaining - later_segments * 2),
        )
        rows = _route_render_rows(connection, int(route["id"]), allocation)
        points.extend(
            {
                **row,
                "segment": segment_offset + int(row["segment"]),
                "route_sequence": point_offset + int(row["sequence"]),
            }
            for row in rows
        )
        segment_offset += int(route["segment_count"] or 1)
        point_offset += int(route["point_count"])
        remaining -= len(rows)
    return {
        "workout_id": workout_id,
        "segment_count": sum(int(route["segment_count"] or 1) for route in routes),
        "returned_segment_count": sum(
            int(route["segment_count"] or 1) for route in selected_routes
        ),
        "original_point_count": original_count,
        "returned_point_count": len(points),
        "downsampled": len(points) < original_count,
        "distance_m": sum(float(route["distance_m"] or 0) for route in routes),
        "elevation_gain_m": (
            sum(float(route["elevation_gain_m"]) for route in routes)
            if all(route["elevation_gain_m"] is not None for route in routes)
            else None
        ),
        "elevation_loss_m": (
            sum(float(route["elevation_loss_m"]) for route in routes)
            if all(route["elevation_loss_m"] is not None for route in routes)
            else None
        ),
        "bounds": {
            "min_latitude": min(float(route["min_latitude"]) for route in routes),
            "max_latitude": max(float(route["max_latitude"]) for route in routes),
            "min_longitude": min(float(route["min_longitude"]) for route in routes),
            "max_longitude": max(float(route["max_longitude"]) for route in routes),
        },
        "points": points,
        "map_note": "Route shown without geographic map tiles.",
    }


def ecg_list(
    connection: sqlite3.Connection,
    window: DateWindow,
    *,
    classification: str | None = None,
    page: int = 1,
    per_page: int = 25,
) -> dict[str, object]:
    require_phase_five(connection)
    page = _positive_int(page, "page", 1_000_000)
    per_page = _positive_int(per_page, "page size", 100)
    conditions = ["local_recorded_date BETWEEN ? AND ?"]
    parameters: list[object] = [window.start.isoformat(), window.end.isoformat()]
    if classification:
        if len(classification) > 160:
            raise AnalyticsError("invalid_filter", "Choose a valid ECG classification.")
        conditions.append("classification = ?")
        parameters.append(classification)
    where = " AND ".join(conditions)
    total = int(connection.execute(f"SELECT COUNT(*) FROM ecgs WHERE {where}", parameters).fetchone()[0])
    rows = connection.execute(
        f"SELECT id, recorded_at, duration_seconds, sample_rate_hz, classification, symptoms, device, lead, amplitude_unit, sample_count FROM ecgs WHERE {where} ORDER BY recorded_at DESC, id DESC LIMIT ? OFFSET ?",
        [*parameters, per_page, (page - 1) * per_page],
    ).fetchall()
    classifications = [str(row[0]) for row in connection.execute("SELECT DISTINCT classification FROM ecgs WHERE classification IS NOT NULL ORDER BY classification")]
    return {
        "window": window.as_dict(),
        "items": [dict(row) for row in rows],
        "pagination": {"page": page, "per_page": per_page, "total": total, "pages": math.ceil(total / per_page) if total else 0},
        "classifications": classifications,
    }


def _ecg_render_rows(
    connection: sqlite3.Connection,
    ecg_id: int,
    count: int,
    limit: int,
) -> list[sqlite3.Row]:
    if count <= limit:
        return _indexed_rows(
            connection,
            "ecg_samples",
            "ecg_id",
            ecg_id,
            "sequence, time_seconds, amplitude",
            list(range(count)),
        )
    buckets = max(1, (limit - 2) // 2)
    return connection.execute(
        """
        WITH bucketed AS (
          SELECT sequence, time_seconds, amplitude,
                 CAST(sequence * ? / ? AS INTEGER) AS bucket
          FROM ecg_samples WHERE ecg_id = ?
        ), ranked AS (
          SELECT sequence, time_seconds, amplitude,
                 ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY amplitude, sequence) AS low_rank,
                 ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY amplitude DESC, sequence) AS high_rank
          FROM bucketed
        )
        SELECT sequence, time_seconds, amplitude FROM ranked
        WHERE low_rank = 1 OR high_rank = 1 OR sequence IN (0, ?)
        ORDER BY sequence LIMIT ?
        """,
        (buckets, count, ecg_id, count - 1, limit),
    ).fetchall()


def ecg_detail(
    connection: sqlite3.Connection,
    ecg_id: int,
    limit: int = 5000,
    *,
    include_samples: bool = True,
) -> dict[str, object]:
    require_phase_five(connection)
    if limit < 100 or limit > 10_000:
        raise AnalyticsError("invalid_limit", "Choose an ECG sample limit from 100 to 10000.")
    row = connection.execute("SELECT * FROM ecgs WHERE id = ?", (ecg_id,)).fetchone()
    if not row:
        raise AnalyticsError("not_found", "That ECG does not exist.")
    count = int(row["sample_count"])
    samples = _ecg_render_rows(connection, ecg_id, count, limit) if include_samples else []
    safe_metadata_keys = {"softwareversion"}
    safe_metadata = []
    for item in connection.execute(
        "SELECT key, value FROM ecg_metadata WHERE ecg_id = ? ORDER BY key",
        (ecg_id,),
    ):
        normalized_key = re.sub(r"[^a-z0-9]", "", str(item["key"]).casefold())
        if normalized_key in safe_metadata_keys:
            safe_metadata.append(dict(item))
    payload = dict(row)
    payload.pop("path", None)
    payload.update(
        {
            "metadata": safe_metadata,
            "samples": [dict(item) for item in samples],
            "returned_sample_count": len(samples),
            "downsampled": include_samples and len(samples) < count,
            "diagnostic_interpretation": None,
            "medical_note": "Apple-provided classification only; no independent rhythm diagnosis is generated.",
        }
    )
    return payload
