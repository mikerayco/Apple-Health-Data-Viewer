from __future__ import annotations

from io import BytesIO
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
import zipfile

from werkzeug.datastructures import FileStorage

from apple_health_viewer.sources import (
    SourceSpec,
    SourceValidationError,
    inspect_zip,
    locate_directory_export,
    open_export,
    retain_folder_upload,
    retain_zip_upload,
    safe_relative_path,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "synthetic" / "apple_health_export"


def fixture_zip() -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in FIXTURE.rglob("*"):
            if path.is_file():
                archive.write(path, Path("apple_health_export") / path.relative_to(FIXTURE))
    return output.getvalue()


class PathSafetyTests(unittest.TestCase):
    def test_safe_relative_paths(self) -> None:
        self.assertEqual(safe_relative_path("folder/export.xml"), Path("folder/export.xml"))
        self.assertEqual(safe_relative_path("folder\\export.xml"), Path("folder/export.xml"))

    def test_unsafe_relative_paths_are_rejected(self) -> None:
        for value in ("../export.xml", "/export.xml", "C:/export.xml", "folder/../export.xml", ""):
            with self.subTest(value=value), self.assertRaises(SourceValidationError):
                safe_relative_path(value)


class DirectoryDiscoveryTests(unittest.TestCase):
    def test_direct_and_single_wrapper_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            direct = root / "export.xml"
            direct.write_text("<HealthData/>", encoding="utf-8")
            self.assertEqual(locate_directory_export(root), direct.resolve())
            direct.unlink()
            wrapper = root / "apple_health_export"
            wrapper.mkdir()
            wrapped = wrapper / "export.xml"
            wrapped.write_text("<HealthData/>", encoding="utf-8")
            self.assertEqual(locate_directory_export(root), wrapped.resolve())

    def test_missing_and_ambiguous_exports_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(SourceValidationError, "No export.xml"):
                locate_directory_export(root)
            for name in ("one", "two"):
                folder = root / name
                folder.mkdir()
                (folder / "export.xml").write_text("<HealthData/>", encoding="utf-8")
            with self.assertRaisesRegex(SourceValidationError, "More than one"):
                locate_directory_export(root)


class ZipSafetyTests(unittest.TestCase):
    def test_valid_wrapped_zip_is_inspected_and_streamed_without_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "health.zip"
            archive_path.write_bytes(fixture_zip())
            member, files = inspect_zip(archive_path)
            self.assertEqual(member, "apple_health_export/export.xml")
            self.assertTrue(any(name.endswith(".gpx") for name, _ in files))
            with open_export(SourceSpec("zip_upload", archive_path, "health.zip")) as opened:
                self.assertEqual(opened.export_member, member)
                self.assertIn(b"<HealthData", opened.stream.read(4096))
            self.assertEqual(list(Path(directory).iterdir()), [archive_path])

    def test_zip_traversal_and_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            traversal = Path(directory) / "traversal.zip"
            with zipfile.ZipFile(traversal, "w") as archive:
                archive.writestr("../export.xml", "<HealthData/>")
            with self.assertRaisesRegex(SourceValidationError, "unsafe filename"):
                inspect_zip(traversal)

            symlink = Path(directory) / "symlink.zip"
            info = zipfile.ZipInfo("export.xml")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(symlink, "w") as archive:
                archive.writestr(info, "target")
            with self.assertRaisesRegex(SourceValidationError, "symbolic links"):
                inspect_zip(symlink)

    def test_duplicate_zip_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "duplicate.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr("export.xml", "<HealthData/>")
                    archive.writestr("export.xml", "<HealthData/>")
            with self.assertRaisesRegex(SourceValidationError, "duplicate"):
                inspect_zip(archive_path)


class RetainedUploadTests(unittest.TestCase):
    def test_zip_upload_is_retained_under_generated_source_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upload = FileStorage(stream=BytesIO(fixture_zip()), filename="My Export.zip")
            path, label, size = retain_zip_upload(upload, root, "synthetic-id")
            self.assertEqual(path, root / "synthetic-id" / "apple-health-export.zip")
            self.assertEqual(label, "My Export.zip")
            self.assertEqual(size, len(fixture_zip()))
            self.assertTrue(path.is_file())

    def test_folder_upload_rejects_duplicate_paths(self) -> None:
        uploads = [
            FileStorage(stream=BytesIO(b"one"), filename="apple_health_export/export.xml"),
            FileStorage(stream=BytesIO(b"two"), filename="apple_health_export/export.xml"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SourceValidationError, "duplicate"):
                retain_folder_upload(uploads, Path(directory), "duplicate-folder")

    def test_folder_upload_preserves_safe_relative_paths(self) -> None:
        uploads = [
            FileStorage(stream=BytesIO(path.read_bytes()), filename=(Path("apple_health_export") / path.relative_to(FIXTURE)).as_posix())
            for path in FIXTURE.rglob("*")
            if path.is_file()
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, label, size = retain_folder_upload(uploads, root, "synthetic-folder")
            self.assertEqual(path, root / "synthetic-folder")
            self.assertEqual(label, "apple_health_export")
            self.assertGreater(size, 0)
            self.assertTrue((path / "apple_health_export" / "export.xml").is_file())
