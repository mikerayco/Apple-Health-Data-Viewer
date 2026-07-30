from __future__ import annotations

import csv
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from scripts.check_repository_privacy import scan

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "synthetic" / "apple_health_export"


class SyntheticFixtureTests(unittest.TestCase):
    def test_generated_fixtures_are_current(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/generate_synthetic_fixtures.py", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_export_is_parseable_and_explicitly_synthetic(self) -> None:
        export = FIXTURE_ROOT / "export.xml"
        tags: list[str] = []
        records = 0
        sources: set[str] = set()
        profile: dict[str, str] | None = None

        for _, element in ET.iterparse(export, events=("end",)):
            tag = element.tag.rsplit("}", 1)[-1]
            tags.append(tag)
            if tag == "Record":
                records += 1
                sources.add(element.get("sourceName", ""))
            elif tag == "Me":
                profile = dict(element.attrib)
            element.clear()

        self.assertGreaterEqual(records, 10)
        self.assertIn("Workout", tags)
        self.assertIn("ActivitySummary", tags)
        self.assertTrue(sources)
        self.assertTrue(all(source.startswith("Synthetic ") for source in sources))
        self.assertIsNotNone(profile)
        self.assertEqual(
            profile["HKCharacteristicTypeIdentifierDateOfBirth"],
            "1990-01-01",
        )

    def test_route_contains_only_invented_points(self) -> None:
        route = FIXTURE_ROOT / "workout-routes" / "route_2024-01-02_080000.gpx"
        root = ET.parse(route).getroot()
        points = [element for element in root.iter() if element.tag.endswith("trkpt")]
        self.assertEqual(len(points), 3)
        self.assertTrue(all(abs(float(point.get("lat", "90"))) < 0.01 for point in points))
        self.assertTrue(all(abs(float(point.get("lon", "180"))) < 0.01 for point in points))

    def test_ecg_is_small_and_labeled_synthetic(self) -> None:
        ecg = FIXTURE_ROOT / "electrocardiograms" / "ecg_2024-01-03.csv"
        with ecg.open(encoding="utf-8", newline="") as file:
            rows = list(csv.reader(file))
        self.assertEqual(rows[0], ["Name", "Synthetic Person"])
        self.assertIn(["Sample Rate", "512 Hz"], rows)
        self.assertLess(len(rows), 30)


class RepositorySafetyTests(unittest.TestCase):
    def test_repository_privacy_check_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/check_repository_privacy.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_privacy_scanner_rejects_export_and_database_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export = root / "export.xml"
            database = root / "health.sqlite"
            private_key = root / "local.key"
            export.write_text("<HealthData/>", encoding="utf-8")
            database.write_bytes(b"SQLite format 3\\0")
            private_key.write_text("synthetic blocked file", encoding="utf-8")
            findings = scan(root, [export, database, private_key])

        reasons = {(finding.path, finding.reason) for finding in findings}
        self.assertIn((Path("export.xml"), "private Apple Health/generated filename"), reasons)
        self.assertIn((Path("health.sqlite"), "private archive/database/credential file type"), reasons)
        self.assertIn((Path("local.key"), "private archive/database/credential file type"), reasons)

    def test_private_workspace_paths_are_ignored(self) -> None:
        private_paths = [
            "apple-health-data/export.xml",
            "apple-health-data/export_cda.xml",
            "from_claude/dashboard_data.json",
            "app-data/health.sqlite3",
            "private-export.zip",
        ]
        result = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            cwd=ROOT,
            input="\n".join(private_paths) + "\n",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.splitlines()), set(private_paths))

    def test_docker_context_excludes_private_workspace_data(self) -> None:
        patterns = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        for expected in ("apple-health-data", "from_claude", "*.zip", "*.sqlite"):
            self.assertIn(expected, patterns)

    def test_mit_license_names_copyright_holder(self) -> None:
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("Copyright (c) 2026 Mike Rayco", license_text)


if __name__ == "__main__":
    unittest.main()
