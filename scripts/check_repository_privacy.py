#!/usr/bin/env python3
"""Fail when repository candidates or history contain likely private health data."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 3 * 1024 * 1024
FIXTURE_PREFIX = Path("tests/fixtures/synthetic")
FIXTURE_GENERATOR = Path("scripts/generate_synthetic_fixtures.py")
EXPECTED_FIXTURES = {
    FIXTURE_PREFIX / "apple_health_export/export.xml",
    FIXTURE_PREFIX / "apple_health_export/workout-routes/route_2024-01-02_080000.gpx",
    FIXTURE_PREFIX / "apple_health_export/electrocardiograms/ecg_2024-01-03.csv",
}

BLOCKED_DIRS = {
    "apple-health-data",
    "apple_health_export",
    "from_claude",
    "app-data",
    "diagnostics",
}
BLOCKED_NAMES = {
    "export.xml",
    "export_cda.xml",
    "dashboard_data.json",
    "health_agg.json",
    "sleep_agg.json",
    "sleep_lines.txt",
}
BLOCKED_SUFFIXES = {".db", ".key", ".p12", ".pem", ".pfx", ".sqlite", ".zip"}
BLOCKED_MAGIC = {
    b"SQLite format 3\x00": "SQLite database signature",
    b"PK\x03\x04": "ZIP archive signature",
    b"PK\x05\x06": "ZIP archive signature",
    b"PK\x07\x08": "ZIP archive signature",
}


@dataclass(frozen=True)
class Finding:
    path: Path
    reason: str


def intended_files(root: Path) -> list[Path]:
    command = [
        "git",
        "-C",
        str(root),
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    ]
    result = subprocess.run(command, check=True, capture_output=True)
    return [root / item.decode() for item in result.stdout.split(b"\0") if item]


def is_fixture_path(relative: Path) -> bool:
    try:
        relative.relative_to(FIXTURE_PREFIX)
    except ValueError:
        return False
    return True


def content_patterns() -> list[tuple[re.Pattern[str], str]]:
    private_key_marker = "BEGIN " + "PRIVATE KEY"
    github_token = "github" + "_pat_"
    user_root = "/" + "Users/"
    windows_user_root = "C:" + "\\Users\\"
    health_doctype = "<!DOCTYPE " + "HealthData"
    return [
        (re.compile(re.escape(private_key_marker)), "private-key marker"),
        (re.compile(r"ghp_[A-Za-z0-9]{30,}"), "GitHub token-like value"),
        (re.compile(re.escape(github_token) + r"[A-Za-z0-9_]{20,}"), "GitHub token-like value"),
        (re.compile(r"AKIA[A-Z0-9]{16}"), "AWS access-key-like value"),
        (re.compile(re.escape(user_root) + r"[^/\s]+/"), "absolute macOS user path"),
        (re.compile(re.escape(windows_user_root), re.IGNORECASE), "absolute Windows user path"),
        (re.compile(re.escape(health_doctype)), "Apple Health export document"),
    ]


def _scan_item(relative: Path, data: bytes, size: int) -> list[Finding]:
    findings: list[Finding] = []
    expected_fixture = relative in EXPECTED_FIXTURES
    under_fixture_root = is_fixture_path(relative)

    if under_fixture_root and not expected_fixture:
        findings.append(Finding(relative, "unexpected synthetic fixture file"))
    if not expected_fixture and any(part in BLOCKED_DIRS for part in relative.parts):
        findings.append(Finding(relative, "private/generated data directory"))
    if not expected_fixture and relative.name in BLOCKED_NAMES:
        findings.append(Finding(relative, "private Apple Health/generated filename"))
    if not expected_fixture and relative.suffix.lower() in BLOCKED_SUFFIXES:
        findings.append(Finding(relative, "private archive/database/credential file type"))
    if size > MAX_FILE_BYTES:
        findings.append(Finding(relative, f"file exceeds {MAX_FILE_BYTES // 1024 // 1024} MiB"))
        return findings
    for signature, reason in BLOCKED_MAGIC.items():
        if data.startswith(signature):
            findings.append(Finding(relative, reason))

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return findings

    if expected_fixture and "synthetic" not in text.lower():
        findings.append(Finding(relative, "fixture lacks an explicit synthetic marker"))
    for pattern, reason in content_patterns():
        if reason == "Apple Health export document" and (
            relative == FIXTURE_GENERATOR or expected_fixture
        ):
            continue
        if pattern.search(text):
            findings.append(Finding(relative, reason))
    return findings


def scan(root: Path, paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        try:
            data = path.read_bytes()
        except OSError:
            continue
        findings.extend(_scan_item(relative, data, len(data)))
    return sorted(set(findings), key=lambda item: (str(item.path), item.reason))


def scan_history(root: Path) -> tuple[list[Finding], int]:
    """Scan every blob/path pair in every commit reachable from local Git refs."""
    commits = subprocess.run(
        ["git", "-C", str(root), "rev-list", "--all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    pairs: set[tuple[str, str]] = set()
    for commit in commits:
        kind = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-t", commit],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if kind != "commit":
            continue
        tree = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-r", "-z", commit],
            check=True,
            capture_output=True,
        ).stdout
        for entry in tree.split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            _mode, kind, object_id = metadata.decode("ascii").split()
            if kind == "blob":
                pairs.add((object_id, raw_path.decode("utf-8", errors="surrogateescape")))

    findings: list[Finding] = []
    blobs: dict[str, tuple[int, bytes]] = {}
    for object_id, name in sorted(pairs):
        if object_id not in blobs:
            size = int(
                subprocess.run(
                    ["git", "-C", str(root), "cat-file", "-s", object_id],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            data = b""
            if size <= MAX_FILE_BYTES:
                data = subprocess.run(
                    ["git", "-C", str(root), "cat-file", "blob", object_id],
                    check=True,
                    capture_output=True,
                ).stdout
            blobs[object_id] = (size, data)
        size, data = blobs[object_id]
        findings.extend(_scan_item(Path(name), data, size))

    reachable = subprocess.run(
        ["git", "-C", str(root), "rev-list", "--objects", "--all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    pathless = 0
    for line in reachable:
        object_id = line.split(" ", 1)[0]
        if object_id in blobs:
            continue
        kind = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-t", object_id],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if kind != "blob":
            continue
        size = int(
            subprocess.run(
                ["git", "-C", str(root), "cat-file", "-s", object_id],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        data = b""
        if size <= MAX_FILE_BYTES:
            data = subprocess.run(
                ["git", "-C", str(root), "cat-file", "blob", object_id],
                check=True,
                capture_output=True,
            ).stdout
        findings.extend(_scan_item(Path(f".git-object/{object_id}"), data, size))
        pathless += 1
    return sorted(set(findings), key=lambda item: (str(item.path), item.reason)), len(pairs) + pathless


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="also scan all blobs reachable from Git refs")
    args = parser.parse_args()
    try:
        paths = intended_files(ROOT)
        findings = scan(ROOT, paths)
        history_count = 0
        if args.history:
            historical, history_count = scan_history(ROOT)
            findings = sorted(set(findings + historical), key=lambda item: (str(item.path), item.reason))
    except (subprocess.CalledProcessError, ValueError) as error:
        print(f"Unable to enumerate repository files: {error}", file=sys.stderr)
        return 2

    if findings:
        print("Repository privacy check failed:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding.path}: {finding.reason}", file=sys.stderr)
        return 1

    detail = f" and {history_count} historical blobs" if args.history else ""
    print(f"Repository privacy check passed ({len(paths)} candidate files{detail} scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
