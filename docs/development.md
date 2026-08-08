# Development Guide

## Requirements

- Python 3.12 or newer
- Git
- Docker only when working on optional container support

Create an isolated environment and install the exact runtime lock:

```bash
python3 -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
```

See [`dependencies.md`](dependencies.md) for the maintenance review and accepted transitive risk.

## Checks

Run the same fast checks used by CI from the activated environment:

```bash
python scripts/generate_synthetic_fixtures.py --check
python -m compileall -q src scripts tests
python -m unittest discover -s tests -v
python scripts/check_repository_privacy.py --history
```

Generate or refresh deterministic fixtures with:

```bash
python3 scripts/generate_synthetic_fixtures.py
```

Review fixture changes before committing them.

Run the scalable privacy-safe benchmark when import or query behavior changes:

```bash
python scripts/benchmark_synthetic.py --records 100000 --max-query-ms 500
```

See [`performance.md`](performance.md) for recorded methodology and results.

## Synthetic fixtures

All automated tests use invented data under `tests/fixtures/synthetic/`. Fixture values, identities, timestamps, coordinates, devices, and source names must be fictional and hand-auditable. Never copy a record from a private export and merely change selected fields.

The generator is the source of truth. CI checks that committed fixtures match its deterministic output and rejects every unexpected file under the synthetic fixture root.

## Private compatibility testing

A real Apple Health export may be configured locally for parser and performance validation, but it must remain outside Git and Docker build contexts. Do not turn private failures into captured snapshots. Reproduce the minimum structure with invented values in the synthetic generator.

Privacy-safe performance output may include:

- source byte size;
- parsed record/type counts;
- elapsed time;
- peak memory;
- database size; and
- parser/schema version.

It must not include raw records, health values, coordinates, DOB, source/device identifiers, screenshots, or absolute paths.

## Dependency policy

Before adding a direct dependency:

1. explain why the standard library and existing dependencies are insufficient;
2. confirm an active release, commit, maintained issue tracker, or security update within the previous six months;
3. review ownership, compatibility policy, license, and security posture;
4. inspect important transitive dependencies;
5. pin or lock the selected version; and
6. record vendored licenses in `THIRD_PARTY_NOTICES.md`.

Runtime assets must be vendored; CDN usage is prohibited.

## Architecture

Accepted decisions live in [`docs/adr/`](adr/). Create an ADR when changing the runtime stack, import transaction model, privacy boundary, source aggregation policy, or another durable cross-cutting decision.
