# Apple Health Data Viewer

A private, local-first web dashboard for exploring data exported from Apple Health.

> [!IMPORTANT]
> This project is under active development. Complete Apple Health exports can be imported locally, and Phase 6 is hardening security, privacy, platform guidance, and release verification around the complete dashboard.

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
| 3 — Metrics and aggregation | Complete |
| 4 — Dashboard and insights | Complete |
| 5 — Workouts, routes, and ECG | Complete |
| 6 — Hardening and public release | In progress |

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

Open <http://127.0.0.1:8787>. See the clean-clone guide for [macOS](docs/platforms/macos.md), [Windows](docs/platforms/windows.md), or [Linux](docs/platforms/linux.md). Import behavior is documented in [`docs/importing.md`](docs/importing.md), calculations in [`docs/metrics.md`](docs/metrics.md), and paths/environment variables in [`docs/configuration.md`](docs/configuration.md).

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
python scripts/check_repository_privacy.py --history
```

No real health data is required for automated development or CI. Dependency decisions are documented in [`docs/dependencies.md`](docs/dependencies.md), compatibility in [`docs/compatibility.md`](docs/compatibility.md), the security review in [`docs/threat-model.md`](docs/threat-model.md), and release gates in [`docs/release-checklist.md`](docs/release-checklist.md). Privacy-safe benchmarks are recorded in [`docs/performance.md`](docs/performance.md).

## Medical disclaimer

This software is for personal data exploration only. It is not a medical device, does not provide medical advice, and must not be used to diagnose, prevent, monitor, predict, treat, or alleviate any disease or medical condition. Consult a qualified healthcare professional for medical concerns.

## License

[MIT](LICENSE) © 2026 Mike Rayco. Third-party components and their notices will be listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
