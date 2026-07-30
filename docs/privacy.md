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

Before any public push, run:

```bash
python3 scripts/check_repository_privacy.py
```

Also inspect the complete staged diff and Git object history. Automated checks reduce risk but do not replace human review.

## App-managed uploads

The planned app retains uploaded ZIP/folder sources by default for future parser upgrades. It will show retained-source and processed-database disk usage and provide a deletion control. Users should be told that local exports may also be copied by operating-system backups.

Configured-path imports are read in place and are never duplicated.

## Diagnostics

Future diagnostics must be opt-in and privacy-safe. They may contain parser version, export schema version, type counts, warnings, and performance metadata. They must exclude health values, DOB, timestamps tied to events, coordinates, source paths, ECG samples, and device identifiers.

## Medical boundary

The project provides descriptive data exploration only. It is not medical software and does not diagnose, treat, predict, or recommend care.
