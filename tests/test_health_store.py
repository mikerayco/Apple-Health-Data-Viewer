from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from apple_health_viewer.health_store import (
    HealthParseError,
    ImportCancelled,
    parse_export,
    read_data_quality,
)
from apple_health_viewer.sources import SourceSpec, open_export

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "synthetic" / "apple_health_export"


class HealthStoreTests(unittest.TestCase):
    def test_streaming_parse_inventory_and_exact_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            special_directory = Path(directory) / "data ? local"
            special_directory.mkdir()
            database = special_directory / "health.sqlite3"
            progress: list[tuple[int, int, str]] = []
            with open_export(SourceSpec("configured_path", FIXTURE, "Synthetic export")) as opened:
                result = parse_export(
                    opened.stream,
                    database,
                    total_bytes=opened.total_bytes,
                    source_files=opened.source_files,
                    progress=lambda read, items, phase: progress.append((read, items, phase)),
                    cancelled=lambda: False,
                )
            quality = read_data_quality(database)
            with closing(sqlite3.connect(database)) as connection:
                glucose = connection.execute(
                    """
                    SELECT r.value, r.unit, m.key, m.value
                    FROM records r
                    JOIN record_metadata m ON m.record_id = r.id
                    WHERE r.type_identifier = 'HKQuantityTypeIdentifierBloodGlucose'
                    """
                ).fetchone()
                route = connection.execute("SELECT path FROM workout_routes").fetchone()
                beats = connection.execute(
                    "SELECT bpm, time FROM record_instantaneous_beats ORDER BY time"
                ).fetchall()

        self.assertEqual(result.record_count, 11)
        self.assertEqual(result.workout_count, 1)
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(result.warning_count, 0)
        self.assertEqual(result.export_version, "14")
        self.assertEqual(len(result.source_fingerprint), 64)
        self.assertTrue(progress)
        self.assertEqual(glucose, ("90", "mg/dL", "HKBloodGlucoseMealTime", "1"))
        self.assertEqual(route[0], "workout-routes/route_2024-01-02_080000.gpx")
        self.assertEqual(beats, [("60", "0.0"), ("62", "1.0")])
        self.assertIsNotNone(quality)
        self.assertEqual(quality["manifest"]["record_count"], 11)
        identifiers = {item["type_identifier"] for item in quality["inventory"]}
        self.assertIn("HKQuantityTypeIdentifierBloodGlucose", identifiers)
        file_kinds = {item["kind"]: item["count"] for item in quality["files"]}
        self.assertEqual(file_kinds["workout_route"], 1)
        self.assertEqual(file_kinds["electrocardiogram"], 1)

    def test_malformed_xml_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "export.xml"
            source.write_bytes(b'<?xml version="1.0"?><HealthData><Record>')
            database = Path(directory) / "health.sqlite3"
            with source.open("rb") as stream:
                with self.assertRaisesRegex(HealthParseError, "malformed"):
                    parse_export(
                        stream,
                        database,
                        total_bytes=source.stat().st_size,
                        source_files=[("export.xml", source.stat().st_size)],
                        progress=lambda *_: None,
                        cancelled=lambda: False,
                    )

    def test_custom_entities_are_rejected_before_parsing(self) -> None:
        xml = (
            b'<?xml version="1.0"?>\n<!DOCTYPE '
            b'HealthData [<!ENTITY private "not allowed">]>\n'
            b'<HealthData><ExportDate value="2024-01-01"/><Me/></HealthData>'
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "export.xml"
            source.write_bytes(xml)
            with source.open("rb") as stream:
                with self.assertRaisesRegex(HealthParseError, "entities"):
                    parse_export(
                        stream,
                        Path(directory) / "health.sqlite3",
                        total_bytes=len(xml),
                        source_files=[],
                        progress=lambda *_: None,
                        cancelled=lambda: False,
                    )

    def test_cancellation_is_checked_during_streaming(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "health.sqlite3"
            with (FIXTURE / "export.xml").open("rb") as stream:
                with self.assertRaises(ImportCancelled):
                    parse_export(
                        stream,
                        database,
                        total_bytes=(FIXTURE / "export.xml").stat().st_size,
                        source_files=[],
                        progress=lambda *_: None,
                        cancelled=lambda: True,
                    )

    def test_quality_reader_returns_none_without_database(self) -> None:
        self.assertIsNone(read_data_quality(Path("/synthetic/missing-health.sqlite3")))
