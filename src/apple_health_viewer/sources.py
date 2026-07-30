"""Safe source acquisition and Apple Health export discovery."""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import os
import shutil
import stat
from typing import BinaryIO, Callable, Iterable, Iterator
import zipfile

from werkzeug.datastructures import FileStorage

MAX_ARCHIVE_FILES = 100_000
MAX_ARCHIVE_BYTES = 100 * 1024**3
MAX_SINGLE_FILE_BYTES = 50 * 1024**3
MAX_COMPRESSION_RATIO = 10_000


class SourceValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


@dataclass(frozen=True)
class SourceSpec:
    kind: str
    path: Path
    label: str
    managed_id: str | None = None


@dataclass
class OpenedExport:
    stream: BinaryIO
    total_bytes: int
    source_files: list[tuple[str, int]]
    export_member: str
    open_asset: Callable[[str], AbstractContextManager[BinaryIO]]


def safe_relative_path(value: str) -> Path:
    normalized = value.replace("\\", "/")
    if "\x00" in normalized:
        raise SourceValidationError("unsafe_path", "The source contains an unsafe filename.")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or not pure.parts:
        raise SourceValidationError("unsafe_path", "The source contains an unsafe filename.")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise SourceValidationError("unsafe_path", "The source contains an unsafe filename.")
    if ":" in pure.parts[0]:
        raise SourceValidationError("unsafe_path", "The source contains an unsafe filename.")
    return Path(*pure.parts)


def _choose_export(candidates: Iterable[str]) -> str:
    found = sorted(set(candidates))
    valid = [
        name
        for name in found
        if name == "export.xml" or len(PurePosixPath(name).parts) == 2
    ]
    if len(valid) == 1:
        return valid[0]
    if not valid:
        raise SourceValidationError("missing_export", "No export.xml file was found in this source.")
    raise SourceValidationError(
        "ambiguous_export",
        "More than one possible export.xml file was found. Choose a single Apple Health export.",
    )


def locate_directory_export(root: Path) -> Path:
    root = root.resolve(strict=True)
    direct = root / "export.xml"
    candidates: list[Path] = []
    if direct.is_file():
        candidates.append(direct)
    for child in root.iterdir():
        candidate = child / "export.xml"
        if child.is_dir() and candidate.is_file():
            candidates.append(candidate)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SourceValidationError("missing_export", "No export.xml file was found in this folder.")
    raise SourceValidationError(
        "ambiguous_export",
        "More than one export.xml file was found. Choose a single Apple Health export folder.",
    )


def inspect_zip(path: Path) -> tuple[str, list[tuple[str, int]]]:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        raise SourceValidationError("invalid_zip", "The selected file is not a valid ZIP archive.") from error

    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_FILES:
            raise SourceValidationError("archive_too_many_files", "The ZIP contains too many files.")
        total = 0
        names: set[str] = set()
        files: list[tuple[str, int]] = []
        exports: list[str] = []
        for info in infos:
            relative = safe_relative_path(info.filename.rstrip("/")) if info.filename.rstrip("/") else None
            if relative is None:
                continue
            normalized = relative.as_posix()
            if normalized in names:
                raise SourceValidationError("duplicate_archive_path", "The ZIP contains duplicate filenames.")
            names.add(normalized)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise SourceValidationError("archive_symlink", "ZIP symbolic links are not accepted.")
            if info.flag_bits & 0x1:
                raise SourceValidationError("encrypted_zip", "Encrypted ZIP archives are not supported.")
            if info.file_size > MAX_SINGLE_FILE_BYTES:
                raise SourceValidationError("archive_file_too_large", "A file in the ZIP exceeds the safety limit.")
            total += info.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise SourceValidationError("archive_too_large", "The ZIP exceeds the uncompressed safety limit.")
            if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                raise SourceValidationError("archive_ratio", "The ZIP has an unsafe compression ratio.")
            if not info.is_dir():
                files.append((normalized, info.file_size))
                if relative.name == "export.xml" and len(relative.parts) <= 2:
                    exports.append(normalized)
        return _choose_export(exports), files


def directory_inventory(root: Path) -> list[tuple[str, int]]:
    root = root.resolve(strict=True)
    files: list[tuple[str, int]] = []
    total = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        resolved = path.resolve(strict=True)
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        size = path.stat().st_size
        if size > MAX_SINGLE_FILE_BYTES:
            raise SourceValidationError("folder_file_too_large", "A source file exceeds the safety limit.")
        total += size
        if total > MAX_ARCHIVE_BYTES:
            raise SourceValidationError("folder_too_large", "The folder exceeds the safety limit.")
        files.append((relative.as_posix(), size))
        if len(files) > MAX_ARCHIVE_FILES:
            raise SourceValidationError("folder_too_many_files", "The folder contains too many files.")
    return files


@contextmanager
def open_export(spec: SourceSpec) -> Iterator[OpenedExport]:
    try:
        path = spec.path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise SourceValidationError("missing_source", "The configured source is no longer available.") from error
    if path.is_dir():
        export = locate_directory_export(path)
        inventory_root = export.parent.resolve(strict=True)
        files = directory_inventory(inventory_root)

        @contextmanager
        def open_directory_asset(name: str) -> Iterator[BinaryIO]:
            relative = safe_relative_path(name)
            candidate = inventory_root
            for part in relative.parts:
                candidate /= part
                if candidate.is_symlink():
                    raise SourceValidationError("asset_symlink", "Linked asset symbolic links are not accepted.")
            target = candidate.resolve(strict=True)
            try:
                target.relative_to(inventory_root)
            except ValueError as error:
                raise SourceValidationError("unsafe_path", "A linked asset path is unsafe.") from error
            if not target.is_file() or target.is_symlink():
                raise SourceValidationError("missing_asset", "A linked export asset is unavailable.")
            with target.open("rb") as asset:
                yield asset

        with export.open("rb") as stream:
            yield OpenedExport(
                stream,
                export.stat().st_size,
                files,
                export.name,
                open_directory_asset,
            )
        return

    if path.suffix.lower() != ".zip":
        raise SourceValidationError("unsupported_source", "Choose a ZIP archive or unzipped export folder.")
    export_member, files = inspect_zip(path)
    with zipfile.ZipFile(path) as archive, archive.open(export_member, "r") as stream:
        root = PurePosixPath(export_member).parent
        scoped: dict[str, tuple[str, int]] = {}
        for member, size in files:
            pure = PurePosixPath(member)
            try:
                relative = pure.relative_to(root) if root.parts else pure
            except ValueError:
                continue
            scoped[relative.as_posix()] = (member, size)

        @contextmanager
        def open_zip_asset(name: str) -> Iterator[BinaryIO]:
            relative = safe_relative_path(name).as_posix()
            selected = scoped.get(relative)
            if selected is None:
                raise SourceValidationError("missing_asset", "A linked export asset is unavailable.")
            with archive.open(selected[0], "r") as asset:
                yield asset

        info = archive.getinfo(export_member)
        scoped_files = [(relative, item[1]) for relative, item in sorted(scoped.items())]
        yield OpenedExport(stream, info.file_size, scoped_files, export_member, open_zip_asset)


def _copy_stream(source: BinaryIO, destination: Path, maximum: int) -> int:
    total = 0
    with destination.open("xb") as output:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise SourceValidationError("upload_too_large", "The upload exceeds the configured safety limit.")
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    return total


def retain_zip_upload(
    upload: FileStorage,
    sources_dir: Path,
    source_id: str,
    maximum: int = MAX_SINGLE_FILE_BYTES,
) -> tuple[Path, str, int]:
    label = PurePosixPath((upload.filename or "apple-health-export.zip").replace("\\", "/")).name
    if not label.lower().endswith(".zip"):
        raise SourceValidationError("invalid_zip_name", "Choose a file ending in .zip.")
    destination_dir = sources_dir / source_id
    destination_dir.mkdir(parents=True, exist_ok=False)
    destination = destination_dir / "apple-health-export.zip"
    try:
        size = _copy_stream(upload.stream, destination, maximum)
        inspect_zip(destination)
    except Exception:
        shutil.rmtree(destination_dir, ignore_errors=True)
        raise
    return destination, label, size


def retain_folder_upload(
    uploads: list[FileStorage],
    sources_dir: Path,
    source_id: str,
    maximum_total: int = MAX_ARCHIVE_BYTES,
) -> tuple[Path, str, int]:
    if not uploads:
        raise SourceValidationError("empty_folder", "Choose an unzipped Apple Health export folder.")
    if len(uploads) > MAX_ARCHIVE_FILES:
        raise SourceValidationError("folder_too_many_files", "The folder contains too many files.")

    destination = sources_dir / source_id
    destination.mkdir(parents=True, exist_ok=False)
    total = 0
    first_root: str | None = None
    seen: set[str] = set()
    try:
        for upload in uploads:
            relative = safe_relative_path(upload.filename or "")
            normalized = relative.as_posix()
            if normalized in seen:
                raise SourceValidationError("duplicate_folder_path", "The folder contains duplicate filenames.")
            seen.add(normalized)
            first_root = first_root or relative.parts[0]
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            remaining = maximum_total - total
            if remaining <= 0:
                raise SourceValidationError("upload_too_large", "The upload exceeds the safety limit.")
            total += _copy_stream(upload.stream, target, min(remaining, MAX_SINGLE_FILE_BYTES))
        locate_directory_export(destination)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination, first_root or "Apple Health export", total


def disk_usage(path: Path) -> tuple[int, int]:
    usage = shutil.disk_usage(path)
    return usage.free, usage.total
