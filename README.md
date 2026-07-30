# Apple Health Data Viewer

A private, local-first web dashboard for exploring data exported from Apple Health.

> [!IMPORTANT]
> This project is under active development. Complete Apple Health exports can now be imported and inventoried locally; metric calculations and analytical charts arrive in later phases.

## Product goals

- Run locally on macOS, Linux, and Windows.
- Import an Apple Health ZIP, an unzipped export folder, or a configured local path.
- Store processed data in SQLite for fast filtering and repeat visits.
- Visualize activity, heart and vitals, sleep, body measurements, workouts, routes, and ECGs.
- Work fully offline with no telemetry, analytics, CDN assets, or health-data network requests.
- Provide descriptive personal trends—not diagnosis or medical advice.

See [`plan.md`](plan.md) for the implementation plan and [`docs/adr/`](docs/adr/) for accepted architecture decisions.

## Privacy

Apple Health exports can contain dates of birth, clinical metadata, precise workout locations, ECG samples, and years of personal measurements.

**Never commit or upload an Apple Health export, parsed database, screenshot containing real values, or diagnostic output containing personal paths.** This repository uses synthetic fixtures only. See [`docs/privacy.md`](docs/privacy.md).

## Development status

| Phase | Status |
|---|---|
| 0 — Repository and privacy foundation | Complete |
| 1 — Local application shell | Complete |
| 2 — Transactional import | Complete |
| 3 — Metrics and aggregation | Not started |
| 4 — Dashboard and insights | Not started |
| 5 — Workouts, routes, and ECG | Not started |
| 6 — Hardening and public release | Not started |

## Quick start

Requires Python 3.12 or newer.

### macOS and Linux

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
apple-health-viewer
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
apple-health-viewer
```

Open <http://127.0.0.1:8787>. See [`docs/importing.md`](docs/importing.md) for source options and transactional replacement, and [`docs/configuration.md`](docs/configuration.md) for paths and environment variables.

### Docker

```bash
docker compose up --build
```

Docker is optional; native execution remains the default. See [`docs/docker.md`](docs/docker.md).

## Development checks

```bash
python scripts/generate_synthetic_fixtures.py --check
python -m compileall -q src scripts tests
python -m unittest discover -s tests -v
python scripts/check_repository_privacy.py
```

No real health data is required for automated development or CI. Dependency decisions are documented in [`docs/dependencies.md`](docs/dependencies.md). The privacy-safe Phase 2 full-export benchmark is recorded in [`docs/performance.md`](docs/performance.md).

## Medical disclaimer

This software is for personal data exploration only. It is not a medical device, does not provide medical advice, and must not be used to diagnose, prevent, monitor, predict, treat, or alleviate any disease or medical condition. Consult a qualified healthcare professional for medical concerns.

## License

[MIT](LICENSE) © 2026 Mike Rayco. Third-party components and their notices will be listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
