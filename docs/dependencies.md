# Dependency Review

Reviewed 2026-07-30 for Phase 1.

## Direct and build dependencies

| Package | Locked version | Purpose | License | Health signal |
|---|---:|---|---|---|
| Flask | 3.1.3 | Local web application | BSD-3-Clause | Release 2026-02-19; active Pallets repository |
| setuptools | 83.0.0 | Standards-based package build | MIT | Release 2026-07-04; active PyPA repository |

## Locked Flask runtime graph

| Package | Locked version | Latest release/activity observation |
|---|---:|---|
| Werkzeug | 3.1.8 | Release 2026-04-02 |
| Click | 8.4.2 | Release 2026-06-24 |
| MarkupSafe | 3.0.3 | Release 2025-09-27 |
| Jinja2 | 3.1.6 | Release 2025-03-05; non-archived Pallets repository, last default-branch commit observed 2025-06-14 |
| itsdangerous | 2.2.0 | Release 2024-04-16; non-archived Pallets repository, last default-branch commit observed 2025-06-14 |
| Blinker | 1.9.0 | Release 2024-11-08; non-archived Pallets Eco repository |

Jinja2, itsdangerous, and Blinker do not meet the project's preferred six-month activity signal. They remain required parts of the current, actively maintained Flask release and are not archived or deprecated. Phase 1 accepts this bounded risk because replacing them while retaining Flask is not practical.

Mitigations:

- exact pins in `requirements.lock`;
- Dependabot monitoring;
- CI across supported Python versions;
- vulnerability and upstream-status review before each release;
- no optional Flask extensions; and
- reassess Flask or the affected component if security support stops or a project is archived.

An OSV query on 2026-07-30 reported no known vulnerabilities for the exact locked versions. This is a point-in-time check, not a guarantee.

No JavaScript package, CDN asset, web font, ORM, task queue, or external database dependency is introduced in Phase 1.
