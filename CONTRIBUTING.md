# Contributing

Thank you for helping build Apple Health Data Viewer.

## Privacy first

Do not submit real Apple Health exports or derived personal data in commits, pull requests, issues, screenshots, logs, test snapshots, or CI artifacts. This includes:

- `export.xml`, `export_cda.xml`, and export ZIP files;
- workout GPX routes and ECG CSV files;
- parsed SQLite databases and generated dashboard JSON;
- dates of birth, coordinates, device names, source paths, and health values.

Use only the repository's deterministic synthetic fixtures. If a defect occurs only with private data, reduce it to a small invented fixture before submitting it.

## Local checks

Run before opening a pull request:

```bash
python3 scripts/generate_synthetic_fixtures.py --check
python3 -m unittest discover -s tests -v
python3 scripts/check_repository_privacy.py
```

Every behavior change requires tests. Parser, aggregation, unit-conversion, source-overlap, and insight calculations require hand-auditable cases.

## Pull requests

- Keep changes focused and minimal.
- Explain user-visible behavior and privacy implications.
- Add or update tests and documentation.
- Do not add a dependency without documenting its purpose, license, current maintenance, and simpler alternatives.
- Confirm that no runtime asset or request depends on an external network service.

## Medical boundary

Contributions must use descriptive, neutral language and must not add diagnosis, treatment recommendations, medical risk scores, or unsupported causal claims.
