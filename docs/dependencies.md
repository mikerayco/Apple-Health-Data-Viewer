# Dependency review

**Reviewed:** 2026-08-08 for Phase 6

**Sources:** official PyPI metadata and repositories, OSV, GitHub Advisory Database, and official GitHub Action releases

## Direct and build dependencies

| Package | Locked/latest | Purpose | License | Six-month health signal |
|---|---:|---|---|---|
| Flask | 3.1.3 | Local web application | BSD-3-Clause | Release 2026-02-19; upstream commit 2026-07-30 |
| setuptools | 83.0.0 | Standards-based package build | MIT | Release 2026-07-04; upstream commit 2026-08-07 |

Both exact versions were current, not yanked, compatible with Python 3.12–3.14, and free of known matching OSV/GitHub advisories at review time.

## Locked Flask runtime graph

| Package | Locked/latest | License | Latest release/activity observation |
|---|---:|---|---|
| Werkzeug | 3.1.8 | BSD-3-Clause | Release 2026-04-02; upstream commit 2026-07-30 |
| Click | 8.4.2 | BSD-3-Clause | Release 2026-06-24; upstream commit 2026-07-24 |
| MarkupSafe | 3.0.3 | BSD-3-Clause | Release and last observed commit 2025-09-27 |
| Jinja2 | 3.1.6 | BSD-3-Clause | Release 2025-03-05; last observed commit 2025-06-14 |
| itsdangerous | 2.2.0 | BSD-3-Clause | Release 2024-04-16; last observed commit 2025-06-14 |
| Blinker | 1.9.0 | MIT | Release 2024-11-08; last observed commit 2025-06-14 |

No known vulnerability matched any exact locked runtime version in OSV or GitHub's advisory data on the review date. The current locks include fixes for the then-relevant advisories affecting older setuptools, Flask, Jinja2, and Werkzeug versions.

MarkupSafe, Jinja2, itsdangerous, and Blinker did not show direct release/default-branch activity in the preceding six months. They remain latest, unarchived dependencies in Flask's maintained runtime graph. Replacing them while retaining Flask is not practical, so v0.6 accepts this bounded risk and will reassess if a component is archived, explicitly unsupported, incompatible, or vulnerable without a fix.

## CI actions and base image

- `actions/checkout` is pinned to commit `3d3c42e5aac5ba805825da76410c181273ba90b1` (v7.0.1).
- `actions/setup-python` is pinned to commit `5fda3b95a4ea91299a34e894583c3862153e4b97` (v7.0.0).
- Both were current, MIT-licensed, active in August 2026, and had no matching public advisory in the checked sources.
- The Docker Python base is pinned by digest. Recheck that digest and upstream image advisories at release time.

## Controls and accepted limitations

- Exact Python versions are pinned in `requirements.lock`; no optional Flask extensions are installed.
- Dependabot monitors pip and GitHub Actions.
- CI covers supported Python versions and all three OS families.
- [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) records every installed package and license.
- The lock does not yet include artifact hashes. Exact pins reduce accidental drift but do not provide immutable package-artifact verification. A release must install from the reviewed index and record any exception until a cross-platform hash lock is adopted.
- Vulnerability results are point-in-time evidence, not a guarantee; repeat the review for every release.

No JavaScript package, CDN asset, web font, ORM, task queue, or external database dependency is used.
