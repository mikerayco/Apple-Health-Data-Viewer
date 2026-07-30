# Apple Health Data Viewer

A private, local-first web dashboard for exploring data exported from Apple Health.

> [!IMPORTANT]
> This project is under active development. Phase 0 established the repository, privacy controls, synthetic fixtures, and CI; the application is not usable yet.

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
| 0 — Repository and privacy foundation | Complete locally; awaiting first push |
| 1 — Local application shell | Not started |
| 2 — Transactional import | Not started |
| 3 — Metrics and aggregation | Not started |
| 4 — Dashboard and insights | Not started |
| 5 — Workouts, routes, and ECG | Not started |
| 6 — Hardening and public release | Not started |

## Development checks

Requires Python 3.12 or newer.

```bash
python3 scripts/generate_synthetic_fixtures.py --check
python3 -m unittest discover -s tests -v
python3 scripts/check_repository_privacy.py
```

No real health data is required for automated development or CI.

## Medical disclaimer

This software is for personal data exploration only. It is not a medical device, does not provide medical advice, and must not be used to diagnose, prevent, monitor, predict, treat, or alleviate any disease or medical condition. Consult a qualified healthcare professional for medical concerns.

## License

[MIT](LICENSE) © 2026 Mike Rayco. Third-party components and their notices will be listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
