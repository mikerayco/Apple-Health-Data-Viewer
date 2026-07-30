# Export Compatibility

## v1 support

The parser targets complete exports created by Apple's **Export All Health Data** flow. It uses the embedded export DTD/version for diagnostics, streams unknown elements safely, and inventories unsupported content rather than silently discarding it.

Supported inputs:

- `export.xml` records, correlations, workouts, workout statistics/events, routes, and activity summaries;
- linked GPX workout routes; and
- Apple ECG CSV files.

Inventoried but not visualized in v1:

- Clinical Records and CDA/FHIR resources;
- audiograms; and
- vision prescriptions.

## Compatibility table

| Export version | Fixture | Status |
|---|---|---|
| Synthetic HealthKit Export Version 14 subset | `tests/fixtures/synthetic/` | Streaming parser, exact dedup, metrics, workout statistics, linked GPX route points, and ECG waveform samples tested |
| Private HealthKit Export Version 14, 2.18 GB XML | Local only; not committed | Full transactional import plus Phase 3 normalization/aggregation validated; see `performance.md` |

Real exports are never committed. Private local compatibility/performance validation records no values, paths, coordinates, or screenshots. Add compatibility claims only after that validation and synthetic regression coverage.

## Reporting a format change

Do not attach an export or paste private XML. Report the export version, affected element/attribute names, parser error category, and a minimal invented example. Remove values, dates, names, device identifiers, coordinates, and paths.
