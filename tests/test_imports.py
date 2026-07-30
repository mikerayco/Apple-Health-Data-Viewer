from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlparse
import zipfile

from apple_health_viewer import create_app
from apple_health_viewer.database import create_import, get_import, get_setting, managed_sources, recent_imports, update_import
from apple_health_viewer.health_store import ImportCancelled, read_data_quality
from apple_health_viewer.import_manager import ImportBusyError
from apple_health_viewer.sources import SourceSpec

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "synthetic" / "apple_health_export"


def fixture_zip() -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in FIXTURE.rglob("*"):
            if path.is_file():
                archive.write(path, Path("apple_health_export") / path.relative_to(FIXTURE))
    return output.getvalue()


class ImportManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name) / "app-data"
        self.app = create_app(
            {
                "TESTING": True,
                "DATA_DIR": self.data_dir,
                "SECRET_KEY": "synthetic-test-secret",
                "OPEN_BROWSER": False,
            }
        )
        self.manager = self.app.extensions["import_manager"]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_successful_import_activates_validated_database(self) -> None:
        job = self.manager.start(SourceSpec("configured_path", FIXTURE, "Synthetic export"))
        result = self.manager.wait(job.id, timeout=10)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result"]["record_count"], 13)
        self.assertEqual(result["result"]["duplicate_count"], 1)
        self.assertTrue(self.manager.active_database.is_file())
        self.assertEqual(get_setting(self.app.config["SETTINGS_DATABASE"], "setup_complete"), "true")
        self.assertIsNotNone(read_data_quality(self.manager.active_database))
        history = recent_imports(self.app.config["SETTINGS_DATABASE"])
        self.assertEqual(history[0]["status"], "succeeded")
        self.assertEqual(history[0]["workout_count"], 1)

    def test_failed_reimport_preserves_previous_database(self) -> None:
        first = self.manager.start(SourceSpec("configured_path", FIXTURE, "Synthetic export"))
        self.assertEqual(self.manager.wait(first.id, timeout=10)["status"], "succeeded")
        before = sha256(self.manager.active_database.read_bytes()).hexdigest()

        malformed = Path(self.temporary.name) / "malformed"
        malformed.mkdir()
        (malformed / "export.xml").write_text("<HealthData><Record>", encoding="utf-8")
        second = self.manager.start(SourceSpec("configured_path", malformed, "Malformed export"))
        result = self.manager.wait(second.id, timeout=10)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "malformed_xml")
        self.assertEqual(sha256(self.manager.active_database.read_bytes()).hexdigest(), before)

    def test_cancelled_import_preserves_previous_database(self) -> None:
        self.manager.active_database.write_bytes(b"previous-synthetic-database")
        entered = threading.Event()

        def blocking_parse(*_args, cancelled, **_kwargs):
            entered.set()
            while not cancelled():
                threading.Event().wait(0.01)
            raise ImportCancelled()

        with patch("apple_health_viewer.import_manager.parse_export", side_effect=blocking_parse):
            job = self.manager.start(SourceSpec("configured_path", FIXTURE, "Synthetic export"))
            self.assertTrue(entered.wait(2))
            self.assertTrue(self.manager.cancel(job.id))
            result = self.manager.wait(job.id, timeout=5)

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.manager.active_database.read_bytes(), b"previous-synthetic-database")

    def test_restart_recovers_staging_and_running_job(self) -> None:
        job_id = "synthetic-interrupted-job"
        create_import(
            self.app.config["SETTINGS_DATABASE"],
            job_id=job_id,
            source_kind="configured_path",
            source_label="Synthetic interrupted export",
            source_id=None,
        )
        update_import(self.app.config["SETTINGS_DATABASE"], job_id, status="running")
        stale = self.manager.staging_dir / job_id
        stale.mkdir()
        (stale / "health.sqlite3").write_bytes(b"incomplete")

        create_app(
            {
                "TESTING": True,
                "DATA_DIR": self.data_dir,
                "SECRET_KEY": "synthetic-test-secret",
            }
        )

        self.assertFalse(stale.exists())
        recovered = get_import(self.app.config["SETTINGS_DATABASE"], job_id)
        self.assertEqual(recovered["status"], "failed")
        self.assertEqual(recovered["error_code"], "interrupted")

    def test_only_one_import_runs_at_a_time(self) -> None:
        release = threading.Event()
        entered = threading.Event()

        def blocking_parse(*_args, **_kwargs):
            entered.set()
            release.wait(2)
            raise ImportCancelled()

        with patch("apple_health_viewer.import_manager.parse_export", side_effect=blocking_parse):
            first = self.manager.start(SourceSpec("configured_path", FIXTURE, "First"))
            self.assertTrue(entered.wait(2))
            with self.assertRaises(ImportBusyError):
                self.manager.start(SourceSpec("configured_path", FIXTURE, "Second"))
            release.set()
            self.manager.wait(first.id, timeout=5)


class ImportRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name) / "app-data"
        self.app = create_app(
            {
                "TESTING": True,
                "DATA_DIR": self.data_dir,
                "SECRET_KEY": "synthetic-test-secret",
                "OPEN_BROWSER": False,
            }
        )
        self.client = self.app.test_client()
        self.manager = self.app.extensions["import_manager"]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def token(self) -> str:
        token = "synthetic-csrf-token"
        with self.client.session_transaction() as session:
            session["_csrf_token"] = token
        return token

    def job_from_response(self, response) -> str:
        return Path(urlparse(response.headers["Location"]).path).name

    def test_zip_upload_is_retained_imported_and_reported(self) -> None:
        response = self.client.post(
            "/imports/upload/zip",
            data={
                "csrf_token": self.token(),
                "zip_file": (BytesIO(fixture_zip()), "Synthetic Health.zip"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        result = self.manager.wait(self.job_from_response(response), timeout=10)
        self.assertEqual(result["status"], "succeeded")
        sources = managed_sources(self.app.config["SETTINGS_DATABASE"])
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["label"], "Synthetic Health.zip")
        self.assertTrue(Path(sources[0]["path"]).is_file())
        quality = self.client.get("/api/data-quality")
        self.assertEqual(quality.status_code, 200)
        self.assertEqual(quality.json["status"], "ready")
        self.assertEqual(quality.json["manifest"]["record_count"], 13)
        page = self.client.get("/data-quality")
        self.assertIn(b"BloodGlucose", page.data)
        overview = self.client.get("/api/overview?period=all")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json["status"], "ready")
        steps = self.client.get(
            "/api/metrics/steps?period=custom&start=2024-01-02&end=2024-01-02"
        )
        self.assertEqual(steps.json["value"], 1500.0)
        self.assertEqual(steps.json["estimated_days"], 1)
        sleep = self.client.get(
            "/api/sleep?period=custom&start=2024-01-02&end=2024-01-02"
        )
        self.assertEqual(sleep.json["average_asleep_hours"], 7.0)
        preference = self.client.post(
            "/settings/preferences",
            data={"csrf_token": self.token(), "theme": "system", "unit_system": "imperial"},
        )
        self.assertEqual(preference.status_code, 302)
        body = self.client.get("/api/metrics/body_mass?period=all")
        self.assertEqual(body.json["formatted_value"], "154.3 lb")
        self.assertIn(b"Goal completion", self.client.get("/activity?period=all").data)
        self.assertIn(b"Blood glucose", self.client.get("/heart?period=all").data)
        self.assertIn(b"7.0 hr", self.client.get("/sleep?period=all").data)
        self.assertIn(b"154.3 lb", self.client.get("/body?period=all").data)
        dashboard = self.client.get("/overview?period=all")
        self.assertIn(b"1,500 steps", dashboard.data)
        self.assertEqual(self.client.get("/api/metrics/not-real?period=all").status_code, 404)
        self.assertEqual(self.client.get("/api/overview?period=invalid").status_code, 400)
        self.assertEqual(self.client.get("/api/metrics/steps?period=all&source=-1").status_code, 400)
        self.assertEqual(
            self.client.get("/api/metrics/steps?period=all&source=1&device=1").status_code,
            400,
        )
        invalid_page = self.client.get("/overview?period=invalid")
        self.assertIn(b"Adjust the selected range", invalid_page.data)
        self.assertEqual(self.client.get("/").headers["Location"], "/overview")

    def test_folder_upload_and_explicit_source_deletion(self) -> None:
        files = [
            (BytesIO(path.read_bytes()), (Path("apple_health_export") / path.relative_to(FIXTURE)).as_posix())
            for path in FIXTURE.rglob("*")
            if path.is_file()
        ]
        response = self.client.post(
            "/imports/upload/folder",
            data={"csrf_token": self.token(), "folder_files": files},
            content_type="multipart/form-data",
        )
        result = self.manager.wait(self.job_from_response(response), timeout=10)
        self.assertEqual(result["status"], "succeeded")
        source = managed_sources(self.app.config["SETTINGS_DATABASE"])[0]
        retained_path = Path(source["path"])
        self.assertTrue(retained_path.is_dir())

        response = self.client.post(
            f"/imports/source/{source['id']}/delete",
            data={"csrf_token": self.token()},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(retained_path.exists())
        self.assertTrue(self.manager.active_database.is_file())
        self.assertEqual(managed_sources(self.app.config["SETTINGS_DATABASE"]), [])

    def test_import_status_does_not_expose_source_path(self) -> None:
        job = self.manager.start(SourceSpec("configured_path", FIXTURE, "Synthetic export"))
        self.manager.wait(job.id, timeout=10)
        response = self.client.get(f"/api/imports/{job.id}/status")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(str(FIXTURE), response.get_data(as_text=True))

    def test_empty_data_quality_api(self) -> None:
        response = self.client.get("/api/data-quality")
        self.assertEqual(response.json, {"status": "empty"})
        overview = self.client.get("/api/overview")
        self.assertEqual(overview.status_code, 409)
        self.assertEqual(overview.json["status"], "empty")

    def test_metric_api_rejects_unknown_metric_and_invalid_period(self) -> None:
        self.assertEqual(self.client.get("/api/metrics/not-real").status_code, 409)
        self.assertEqual(self.client.get("/api/overview?period=invalid").status_code, 409)
