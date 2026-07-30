from __future__ import annotations

from contextlib import closing
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from apple_health_viewer.assets import (
    AssetImportCancelled,
    AssetParseError,
    parse_ecg_into,
    parse_gpx_into,
)
from apple_health_viewer.health_store import create_health_database


class ECGAssetTests(unittest.TestCase):
    def database(self, directory: str):
        return create_health_database(Path(directory) / "health.sqlite3")

    def test_invalid_recorded_date_is_rejected_before_insert(self) -> None:
        csv = b"Recorded Date,not-a-date\nSample Rate,512 Hz\nSample,Amplitude\n0,10\n1,20\n"
        with tempfile.TemporaryDirectory() as directory, closing(self.database(directory)) as connection:
            with self.assertRaisesRegex(AssetParseError, "date"):
                parse_ecg_into(connection, "electrocardiograms/invalid.csv", BytesIO(csv))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ecgs").fetchone()[0], 0)

    def test_failed_ecg_rolls_back_flushed_samples(self) -> None:
        rows = "".join(f"{index},{index % 10}\n" for index in range(2200))
        csv = (
            "Recorded Date,2024-01-03 08:00:00 +0000\n"
            "Sample Rate,512 Hz\nSample,Amplitude\n" + rows
        ).encode()
        with tempfile.TemporaryDirectory() as directory, closing(self.database(directory)) as connection:
            def cancelled() -> bool:
                return connection.execute("SELECT COUNT(*) FROM ecg_samples").fetchone()[0] > 0

            with self.assertRaises(AssetImportCancelled):
                parse_ecg_into(
                    connection,
                    "electrocardiograms/cancel-after-flush.csv",
                    BytesIO(csv),
                    cancelled=cancelled,
                )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ecgs").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ecg_samples").fetchone()[0], 0)

    def test_oversized_single_csv_row_is_rejected(self) -> None:
        csv = b"Recorded Date," + b"x" * (1024 * 1024 + 1) + b"\n"
        with tempfile.TemporaryDirectory() as directory, closing(self.database(directory)) as connection:
            with self.assertRaisesRegex(AssetParseError, "row"):
                parse_ecg_into(connection, "electrocardiograms/large.csv", BytesIO(csv))

    def test_index_samples_require_positive_sample_rate(self) -> None:
        variants = (
            b"Recorded Date,2024-01-03 08:00:00 +0000\nSample,Amplitude\n0,1\n1,2\n",
            b"Recorded Date,2024-01-03 08:00:00 +0000\nSample Rate,0 Hz\nSample,Amplitude\n0,1\n1,2\n",
            b"Recorded Date,2024-01-03 08:00:00 +0000\nSample Rate,-512 Hz\nSample,Amplitude\n0,1\n1,2\n",
        )
        with tempfile.TemporaryDirectory() as directory, closing(self.database(directory)) as connection:
            for index, csv in enumerate(variants):
                with self.subTest(index=index), self.assertRaisesRegex(AssetParseError, "sample rate"):
                    parse_ecg_into(
                        connection,
                        f"electrocardiograms/rate-{index}.csv",
                        BytesIO(csv),
                    )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ecgs").fetchone()[0], 0)

    def test_time_voltage_header_variant_is_supported(self) -> None:
        csv = (
            "Recorded Date,2024-01-03 08:00:00 -0500\n"
            "Classification,Synthetic Classification\n"
            "Time (s),Voltage (uV)\n"
            "5.000,10\n6.000,-20\n"
        ).encode()
        with tempfile.TemporaryDirectory() as directory, closing(self.database(directory)) as connection:
            result = parse_ecg_into(
                connection,
                "electrocardiograms/variant.csv",
                BytesIO(csv),
            )
            row = connection.execute(
                "SELECT recorded_at, local_recorded_date, sample_count, duration_seconds FROM ecgs WHERE id = ?",
                (result.ecg_id,),
            ).fetchone()
        self.assertEqual(tuple(row), ("2024-01-03T13:00:00Z", "2024-01-03", 2, 1.0))

    def test_nonmonotonic_sample_times_are_rejected_and_rolled_back(self) -> None:
        csv = (
            "Recorded Date,2024-01-03 08:00:00 +0000\n"
            "Time (s),Amplitude\n5,10\n4,20\n"
        ).encode()
        with tempfile.TemporaryDirectory() as directory, closing(self.database(directory)) as connection:
            with self.assertRaisesRegex(AssetParseError, "nonmonotonic"):
                parse_ecg_into(
                    connection,
                    "electrocardiograms/nonmonotonic.csv",
                    BytesIO(csv),
                )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ecgs").fetchone()[0], 0)

    def test_cancellation_is_checked_while_scanning_preamble(self) -> None:
        csv = "".join(f"Metadata {index},value\n" for index in range(300)).encode()
        with tempfile.TemporaryDirectory() as directory, closing(self.database(directory)) as connection:
            with self.assertRaises(AssetImportCancelled):
                parse_ecg_into(
                    connection,
                    "electrocardiograms/cancel.csv",
                    BytesIO(csv),
                    cancelled=lambda: True,
                )


class GPXAssetTests(unittest.TestCase):
    def test_track_segments_do_not_create_false_connections(self) -> None:
        gpx = b'''<gpx><trk>
<trkseg><trkpt lat="0" lon="0"/><trkpt lat="0" lon="0.001"/></trkseg>
<trkseg><trkpt lat="40" lon="40"/><trkpt lat="40" lon="40.001"/></trkseg>
</trk></gpx>'''
        with tempfile.TemporaryDirectory() as directory, closing(
            create_health_database(Path(directory) / "health.sqlite3")
        ) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            result = parse_gpx_into(connection, 1, "workout-routes/segments.gpx", BytesIO(gpx))
            route = connection.execute(
                "SELECT segment_count, distance_m, elevation_gain_m FROM workout_routes WHERE id = ?",
                (result.route_id,),
            ).fetchone()
            segments = [
                row[0]
                for row in connection.execute(
                    "SELECT segment FROM route_points WHERE route_id = ? ORDER BY sequence",
                    (result.route_id,),
                )
            ]
        self.assertEqual(route[0], 2)
        self.assertLess(route[1], 250)
        self.assertIsNone(route[2])
        self.assertEqual(segments, [0, 0, 1, 1])

    def test_malformed_gpx_rolls_back_flushed_points(self) -> None:
        points = "".join(f'<trkpt lat="0" lon="{index / 10000}"/>' for index in range(1001))
        gpx = f"<gpx><trk><trkseg>{points}".encode()
        with tempfile.TemporaryDirectory() as directory, closing(
            create_health_database(Path(directory) / "health.sqlite3")
        ) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            with self.assertRaisesRegex(AssetParseError, "malformed"):
                parse_gpx_into(connection, 1, "workout-routes/malformed.gpx", BytesIO(gpx))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workout_routes").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM route_points").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
