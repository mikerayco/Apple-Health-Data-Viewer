from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from apple_health_viewer.analytics import resolve_window
from apple_health_viewer.health_store import parse_export
from apple_health_viewer.sources import SourceSpec, open_export
from apple_health_viewer.specialized import (
    ecg_detail,
    ecg_list,
    workout_detail,
    workout_list,
    workout_route,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "synthetic" / "apple_health_export"


class SpecializedViewsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "health.sqlite3"
        with open_export(SourceSpec("configured_path", FIXTURE, "Synthetic export")) as opened:
            self.result = parse_export(
                opened.stream,
                self.database,
                total_bytes=opened.total_bytes,
                source_files=opened.source_files,
                progress=lambda *_: None,
                cancelled=lambda: False,
                asset_opener=opened.open_asset,
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_linked_assets_are_imported_and_normalized(self) -> None:
        self.assertEqual(self.result.route_count, 1)
        self.assertEqual(self.result.ecg_count, 1)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            workout = connection.execute(
                "SELECT duration_seconds, total_distance_m FROM workouts"
            ).fetchone()
            route = connection.execute(
                "SELECT point_count, elevation_gain_m, elevation_loss_m FROM workout_routes"
            ).fetchone()
            ecg = connection.execute(
                "SELECT sample_count, sample_rate_hz, classification FROM ecgs"
            ).fetchone()
        self.assertEqual(tuple(workout), (1800.0, 5000.0))
        self.assertEqual(tuple(route), (3, 2.0, 1.0))
        self.assertEqual(tuple(ecg), (4, 512.0, "Sinus Rhythm"))

    def test_workout_list_detail_and_route_are_bounded(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            window = resolve_window(connection, period="all")
            listing = workout_list(connection, window, "metric")
            filtered = workout_list(
                connection,
                window,
                "imperial",
                activity_type="HKWorkoutActivityTypeRunning",
                search="running",
                route_only=True,
            )
            detail = workout_detail(connection, 1, "metric")
            route = workout_route(connection, 1, limit=2)
        self.assertEqual(listing["summary"]["sessions"], 1)
        self.assertEqual(listing["summary"]["total_distance"], 5.0)
        self.assertEqual(filtered["items"][0]["distance_unit"], "mi")
        self.assertEqual(detail["label"], "Running")
        self.assertTrue(detail["route"]["available"])
        self.assertEqual(route["original_point_count"], 3)
        self.assertEqual(route["returned_point_count"], 2)
        self.assertNotIn("path", route)
        self.assertEqual(route["points"][0]["sequence"], 0)
        self.assertEqual(route["points"][-1]["sequence"], 2)

    def test_route_response_marks_missing_elevation_intervals(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE route_points SET elevation_m = NULL WHERE route_id = 1 AND sequence = 1"
            )
            connection.commit()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            route = workout_route(connection, 1, limit=10)
        self.assertIsNone(route["points"][1]["elevation_m"])
        self.assertTrue(route["points"][2]["elevation_gap_before"])

    def test_route_downsampling_preserves_spatial_excursions(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM route_points WHERE route_id = 1")
            connection.executemany(
                """
                INSERT INTO route_points(route_id, sequence, latitude, longitude)
                VALUES (1, ?, ?, ?)
                """,
                (
                    (index, 10.0 if index == 50 else 0.0, index / 100.0)
                    for index in range(101)
                ),
            )
            connection.execute(
                """
                UPDATE workout_routes SET point_count = 101, min_latitude = 0,
                  max_latitude = 10, min_longitude = 0, max_longitude = 1 WHERE id = 1
                """
            )
            connection.commit()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            route = workout_route(connection, 1, limit=6)
        self.assertLessEqual(len(route["points"]), 6)
        self.assertIn(10.0, {point["latitude"] for point in route["points"]})

    def test_short_internal_route_segments_survive_downsampling(self) -> None:
        rows = []
        sequence = 0
        for segment, count in ((0, 100), (1, 2), (2, 100)):
            for index in range(count):
                rows.append((1, sequence, segment, float(segment), sequence / 1000))
                sequence += 1
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM route_points WHERE route_id = 1")
            connection.executemany(
                """
                INSERT INTO route_points(route_id, sequence, segment, latitude, longitude)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.execute(
                "UPDATE workout_routes SET point_count = ?, segment_count = 3 WHERE id = 1",
                (len(rows),),
            )
            connection.commit()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            route = workout_route(connection, 1, limit=10)
        self.assertEqual(route["returned_segment_count"], 3)
        self.assertEqual({point["segment"] for point in route["points"]}, {0, 1, 2})

    def test_multiple_route_segments_are_combined_deterministically(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            route_id = connection.execute(
                """
                INSERT INTO workout_routes(
                  workout_id, path, point_count, segment_count, distance_m, elevation_gain_m,
                  elevation_loss_m, min_latitude, max_latitude, min_longitude, max_longitude
                ) VALUES (1, 'workout-routes/segment-2.gpx', 2, 1, 100, 1, 1, 1, 2, 1, 2)
                """
            ).lastrowid
            connection.executemany(
                "INSERT INTO route_points(route_id, sequence, latitude, longitude) VALUES (?, ?, ?, ?)",
                ((route_id, 0, 1.0, 1.0), (route_id, 1, 2.0, 2.0)),
            )
            connection.execute(
                "UPDATE workout_routes SET elevation_gain_m = NULL, elevation_loss_m = NULL WHERE id = ?",
                (route_id,),
            )
            connection.commit()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            detail = workout_detail(connection, 1, "metric")
            route = workout_route(connection, 1, limit=10)
        self.assertEqual(detail["route"]["segment_count"], 2)
        self.assertEqual(route["segment_count"], 2)
        self.assertEqual({point["segment"] for point in route["points"]}, {0, 1})
        self.assertIsNone(detail["route"]["elevation_gain_m"])
        self.assertIsNone(route["elevation_gain_m"])

    def test_ecg_downsampling_preserves_waveform_extrema(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM ecg_samples WHERE ecg_id = 1")
            connection.executemany(
                "INSERT INTO ecg_samples(ecg_id, sequence, time_seconds, amplitude) VALUES (1, ?, ?, ?)",
                (
                    (index, index / 100.0, 999.0 if index == 73 else -999.0 if index == 140 else 0.0)
                    for index in range(201)
                ),
            )
            connection.execute(
                "UPDATE ecgs SET sample_count = 201, duration_seconds = 2 WHERE id = 1"
            )
            connection.commit()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            detail = ecg_detail(connection, 1, limit=100)
        amplitudes = {sample["amplitude"] for sample in detail["samples"]}
        self.assertLessEqual(len(detail["samples"]), 100)
        self.assertIn(999.0, amplitudes)
        self.assertIn(-999.0, amplitudes)

    def test_ecg_list_and_waveform_preserve_apple_wording(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                "INSERT INTO ecg_metadata(ecg_id, key, value) VALUES (1, 'Full Name', 'Synthetic Person')"
            )
            connection.commit()
            window = resolve_window(connection, period="all")
            listing = ecg_list(connection, window)
            detail = ecg_detail(connection, 1, limit=100)
        self.assertEqual(listing["pagination"]["total"], 1)
        self.assertEqual(listing["items"][0]["classification"], "Sinus Rhythm")
        self.assertEqual(detail["classification"], "Sinus Rhythm")
        self.assertEqual(detail["sample_count"], 4)
        self.assertEqual(len(detail["samples"]), 4)
        self.assertIsNone(detail["diagnostic_interpretation"])
        self.assertNotIn("path", detail)
        metadata_keys = {item["key"] for item in detail["metadata"]}
        self.assertNotIn("Name", metadata_keys)
        self.assertNotIn("Date of Birth", metadata_keys)
        self.assertNotIn("Full Name", metadata_keys)


if __name__ == "__main__":
    unittest.main()
