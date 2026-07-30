from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from apple_health_viewer import create_app
from apple_health_viewer.database import get_setting, schema_version, set_setting


class AppTestCase(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def database(self) -> Path:
        return Path(self.app.config["SETTINGS_DATABASE"])

    def authorize_form(self, token: str = "synthetic-csrf-token") -> str:
        with self.client.session_transaction() as session:
            session["_csrf_token"] = token
        return token


class ApplicationShellTests(AppTestCase):
    def test_first_run_redirects_to_setup(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/setup")

    def test_all_application_pages_render(self) -> None:
        for path in (
            "/setup",
            "/overview",
            "/activity",
            "/heart",
            "/sleep",
            "/body",
            "/workouts",
            "/ecgs",
            "/data-quality",
            "/settings",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Apple Health Data Viewer", response.data)
                self.assertIn(b"Not medical advice", response.data)

    def test_health_endpoint_is_minimal(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"phase": 5, "status": "ok", "version": "0.5.0"})
        self.assertNotIn(str(self.data_dir).encode(), response.data)

    def test_security_headers_are_applied(self) -> None:
        response = self.client.get("/setup")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Cross-Origin-Resource-Policy"], "same-origin")
        policy = response.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertNotIn("https:", policy)

    def test_invalid_host_is_rejected(self) -> None:
        response = self.client.get("/setup", headers={"Host": "not-local.example"})
        self.assertEqual(response.status_code, 400)

    def test_missing_page_uses_accessible_error_shell(self) -> None:
        response = self.client.get("/not-a-page")
        self.assertEqual(response.status_code, 404)
        self.assertIn(b"That view slipped out of range", response.data)

    def test_post_without_csrf_is_rejected(self) -> None:
        response = self.client.post(
            "/settings/preferences",
            data={"theme": "dark", "unit_system": "metric"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"form expired", response.data)

    def test_preferences_are_saved_and_rendered(self) -> None:
        token = self.authorize_form()
        response = self.client.post(
            "/settings/preferences",
            data={"csrf_token": token, "theme": "dark", "unit_system": "imperial"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_setting(self.database, "theme"), "dark")
        self.assertEqual(get_setting(self.database, "unit_system"), "imperial")
        self.assertIn(b'data-theme="dark"', response.data)
        self.assertIn(b"Imperial", response.data)

    def test_theme_redirect_does_not_accept_external_target(self) -> None:
        token = self.authorize_form()
        response = self.client.post(
            "/settings/theme",
            data={"csrf_token": token, "theme": "light", "next": "https://example.invalid"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/overview")


class SourceConfigurationTests(AppTestCase):
    def test_existing_folder_can_be_saved_without_modification(self) -> None:
        source = Path(self.temporary.name) / "synthetic-export"
        source.mkdir()
        marker = source / "leave-untouched.txt"
        marker.write_text("synthetic", encoding="utf-8")
        token = self.authorize_form()

        response = self.client.post(
            "/setup/source",
            data={"csrf_token": token, "health_data_path": str(source)},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_setting(self.database, "health_data_path"), str(source.resolve()))
        self.assertEqual(marker.read_text(encoding="utf-8"), "synthetic")
        self.assertIn(b"Source saved", response.data)

    def test_missing_source_is_not_saved(self) -> None:
        token = self.authorize_form()
        missing = Path(self.temporary.name) / "missing"
        response = self.client.post(
            "/setup/source",
            data={"csrf_token": token, "health_data_path": str(missing)},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(get_setting(self.database, "health_data_path"))
        self.assertIn(b"does not exist", response.data)

    def test_removing_setting_never_deletes_source(self) -> None:
        source = Path(self.temporary.name) / "synthetic-export"
        source.mkdir()
        set_setting(self.database, "health_data_path", str(source))
        token = self.authorize_form()

        response = self.client.post(
            "/setup/source/remove",
            data={"csrf_token": token},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(source.exists())
        self.assertIsNone(get_setting(self.database, "health_data_path"))

    def test_explicit_source_takes_precedence_over_stored_source(self) -> None:
        explicit = Path(self.temporary.name) / "explicit-source"
        stored = Path(self.temporary.name) / "stored-source"
        explicit.mkdir()
        stored.mkdir()
        set_setting(self.database, "health_data_path", str(stored))

        app = create_app(
            {
                "TESTING": True,
                "DATA_DIR": self.data_dir,
                "SECRET_KEY": "synthetic-test-secret",
                "HEALTH_DATA_PATH": explicit,
            }
        )
        response = app.test_client().get("/setup")
        self.assertIn(b"explicit-source", response.data)
        self.assertNotIn(b"stored-source", response.data)


class DatabaseFoundationTests(AppTestCase):
    def test_initial_migration_and_defaults(self) -> None:
        self.assertEqual(schema_version(self.database), 2)
        with closing(sqlite3.connect(self.database)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            migrations = connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
        self.assertTrue({"settings", "schema_migrations", "import_history", "managed_sources"} <= tables)
        self.assertEqual(migrations, [(1, "001_initial.sql"), (2, "002_import_sources.sql")])
        self.assertEqual(get_setting(self.database, "theme"), "system")
        self.assertEqual(get_setting(self.database, "unit_system"), "metric")

    def test_app_creation_is_migration_idempotent(self) -> None:
        create_app(
            {
                "TESTING": True,
                "DATA_DIR": self.data_dir,
                "SECRET_KEY": "synthetic-test-secret",
            }
        )
        with closing(sqlite3.connect(self.database)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        self.assertEqual(count, 2)
