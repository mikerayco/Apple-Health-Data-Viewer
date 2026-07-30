from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from apple_health_viewer import create_app
from apple_health_viewer.config import DEFAULT_HOST, DEFAULT_PORT, base_config
from apple_health_viewer.paths import platform_data_dir


class PlatformPathTests(unittest.TestCase):
    def test_macos_path(self) -> None:
        result = platform_data_dir(system="Darwin", environ={}, home=Path("/synthetic/home"))
        self.assertEqual(
            result,
            Path("/synthetic/home/Library/Application Support/Apple Health Data Viewer"),
        )

    def test_windows_local_app_data(self) -> None:
        result = platform_data_dir(
            system="Windows",
            environ={"LOCALAPPDATA": "C:/Synthetic/Local"},
            home=Path("C:/Synthetic"),
        )
        self.assertEqual(result, Path("C:/Synthetic/Local/Apple Health Data Viewer"))

    def test_linux_xdg_path_and_fallback(self) -> None:
        configured = platform_data_dir(
            system="Linux",
            environ={"XDG_DATA_HOME": "/synthetic/data"},
            home=Path("/synthetic/home"),
        )
        fallback = platform_data_dir(system="Linux", environ={}, home=Path("/synthetic/home"))
        self.assertEqual(configured, Path("/synthetic/data/apple-health-data-viewer"))
        self.assertEqual(fallback, Path("/synthetic/home/.local/share/apple-health-data-viewer"))


class ConfigurationTests(unittest.TestCase):
    def test_environment_configuration(self) -> None:
        config = base_config(
            {
                "AHV_DATA_DIR": "/synthetic/app-data",
                "AHV_HEALTH_DATA_PATH": "/synthetic/export",
                "AHV_HOST": "localhost",
                "AHV_PORT": "9000",
                "AHV_OPEN_BROWSER": "false",
            }
        )
        self.assertEqual(config["DATA_DIR"], Path("/synthetic/app-data"))
        self.assertEqual(config["HEALTH_DATA_PATH"], Path("/synthetic/export"))
        self.assertEqual(config["HOST"], "localhost")
        self.assertEqual(config["PORT"], 9000)
        self.assertFalse(config["OPEN_BROWSER"])

    def test_defaults_are_local(self) -> None:
        config = base_config({})
        self.assertEqual(config["HOST"], DEFAULT_HOST)
        self.assertEqual(config["PORT"], DEFAULT_PORT)
        self.assertIn("localhost", config["TRUSTED_HOSTS"])
        self.assertIn("127.0.0.1", config["TRUSTED_HOSTS"])

    def test_invalid_environment_port_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "AHV_PORT"):
            base_config({"AHV_PORT": "not-a-port"})
        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            base_config({"AHV_PORT": "70000"})

    def test_explicit_overrides_take_precedence_and_secret_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            first = create_app(
                {
                    "TESTING": True,
                    "DATA_DIR": data_dir,
                    "HOST": "localhost",
                    "PORT": 9999,
                }
            )
            second = create_app({"TESTING": True, "DATA_DIR": data_dir})
            key_file = data_dir / "session.key"
            key_value = key_file.read_text(encoding="utf-8")

        self.assertEqual(first.config["HOST"], "localhost")
        self.assertEqual(first.config["PORT"], 9999)
        self.assertEqual(first.config["SECRET_KEY"], second.config["SECRET_KEY"])
        self.assertEqual(first.config["SECRET_KEY"], key_value)


class OfflineAssetTests(unittest.TestCase):
    def test_runtime_templates_and_assets_have_no_remote_urls(self) -> None:
        package = Path(__file__).resolve().parents[1] / "src" / "apple_health_viewer"
        paths = [
            *package.joinpath("templates").glob("*.html"),
            *package.joinpath("static").rglob("*.*"),
        ]
        for path in paths:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("https://", text)
                self.assertNotIn("http://", text)
