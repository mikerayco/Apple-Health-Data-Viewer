# Security Policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting or open a private security advisory for this repository. Do not disclose an exploitable issue or private health data in a public issue.

A useful report includes the affected version, reproduction steps using synthetic data, expected impact, and suggested mitigation. Never attach a real Apple Health export, database, ECG, workout route, screenshot, or log containing private paths or values.

## Supported versions

The project has not released v1. Security fixes currently target the `main` branch.

## Security boundary

The application is designed to bind to loopback only and operate without authentication or external network requests. Exposing it to a LAN or the internet is outside the supported v1 security model.

See [`docs/privacy.md`](docs/privacy.md) and [ADR-0003](docs/adr/0003-offline-privacy-boundary.md).
