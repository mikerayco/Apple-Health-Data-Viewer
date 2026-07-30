#!/usr/bin/env python3
"""Fail when public repository candidates contain likely private health data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 3 * 1024 * 1024
FIXTURE_PREFIX = Path("tests/fixtures/synthetic")
FIXTURE_GENERATOR = Path("scripts/generate_synthetic_fixtures.py")

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


def is_fixture(relative: Path) -> bool:
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


def scan(root: Path, paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    patterns = content_patterns()

    for path in paths:
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        fixture = is_fixture(relative)

        if not fixture and any(part in BLOCKED_DIRS for part in relative.parts):
            findings.append(Finding(relative, "private/generated data directory"))
        if not fixture and path.name in BLOCKED_NAMES:
            findings.append(Finding(relative, "private Apple Health/generated filename"))
        if not fixture and path.suffix.lower() in BLOCKED_SUFFIXES:
            findings.append(Finding(relative, "private archive/database/credential file type"))

        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            findings.append(Finding(relative, f"file exceeds {MAX_FILE_BYTES // 1024 // 1024} MiB"))
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        if fixture:
            continue
        for pattern, reason in patterns:
            if relative == FIXTURE_GENERATOR and reason == "Apple Health export document":
                continue
            if pattern.search(text):
                findings.append(Finding(relative, reason))

    return sorted(set(findings), key=lambda item: (str(item.path), item.reason))


def main() -> int:
    try:
        paths = intended_files(ROOT)
    except subprocess.CalledProcessError as error:
        print(f"Unable to enumerate repository files: {error}", file=sys.stderr)
        return 2

    findings = scan(ROOT, paths)
    if findings:
        print("Repository privacy check failed:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding.path}: {finding.reason}", file=sys.stderr)
        return 1

    print(f"Repository privacy check passed ({len(paths)} candidate files scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
