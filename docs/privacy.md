# Privacy Model

Apple Health exports are exceptionally sensitive. They can contain health measurements, dates of birth, clinical metadata, device details, precise workout coordinates, and ECG waveforms.

## Runtime promise

The supported v1 configuration will:

- process data on the user's computer;
- bind the web server to loopback only by default;
- make no telemetry, analytics, CDN, font, map-tile, update-check, or health-data network requests;
- store processed data in a local SQLite database;
- avoid persistent browser storage for health records; and
- keep debug mode disabled.

See [ADR-0003](adr/0003-offline-privacy-boundary.md).

## Repository policy

Real or derived personal health data must never enter Git history, Docker build contexts, CI artifacts, issues, or pull requests. Prohibited material includes:

- Apple export ZIPs, `export.xml`, and `export_cda.xml`;
- workout GPX files and ECG CSV files;
- processed SQLite databases or generated dashboard data;
- screenshots or logs containing real values;
- dates of birth, coordinates, names, device identifiers, or absolute private paths.

Only deterministic synthetic fixtures under `tests/fixtures/` are allowed.

## Local private testing

Private exports may be used locally for compatibility and performance checks if they remain ignored or outside the repository. The application and test tooling must open configured exports read-only. Private benchmark reports may record file size, record count, elapsed time, peak memory, and database size, but not health values or coordinates.

Before any public push, run from a full clone:

```bash
python3 scripts/check_repository_privacy.py --history
```

The scanner enforces the exact generated-fixture allowlist and checks every reachable Git blob. Also inspect the complete staged diff and large Git objects manually. Automated checks reduce risk but cannot prove that arbitrary content contains no personal identifier.

## App-managed uploads

The app retains uploaded ZIP/folder sources by default for future parser upgrades. Settings shows retained-source and processed-database disk usage. **Manage imports** deletes an individual retained source; **Settings → Remove processed health data** deletes the active processed database while retaining sources and preferences. These actions are separate to prevent ambiguous deletion.

Configured-path imports are read in place and are never duplicated or deleted by the app. Multipart temporary files are written inside the private application-data filesystem rather than a small system/container temporary partition.

Local exports and databases may also be copied by Time Machine, File History, snapshots, cloud-synced folders, or other backup software.

## Complete removal

For a full reset, stop the viewer and remove its entire platform application-data directory listed in [`configuration.md`](configuration.md). This removes the settings database, session key, active/recovery health databases, staging data, and retained uploads. Docker users can remove the `viewer-data` volume after stopping Compose. Operating-system trash/recycle bins and backups may retain recoverable copies.

Platform-specific steps are documented for [macOS](platforms/macos.md), [Windows](platforms/windows.md), and [Linux](platforms/linux.md).

## Diagnostics

Future diagnostics must be opt-in and privacy-safe. They may contain parser version, export schema version, type counts, warnings, and performance metadata. They must exclude health values, DOB, timestamps tied to events, coordinates, source paths, ECG samples, and device identifiers.

## Medical boundary

The project provides descriptive data exploration only. It is not medical software and does not diagnose, treat, predict, or recommend care.
