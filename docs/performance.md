# Performance Benchmarks

Private compatibility benchmarks record only sizes, counts, elapsed time, peak memory, and software/hardware versions. They never record health values, dates, source/device names, coordinates, screenshots, or private paths.

## Phase 2 full-export import

**Date:** 2026-07-30

**Application:** 0.2.0

**Export:** private local HealthKit Export Version 14 fixture (not committed)

**Machine:** Apple M1 Pro, 16 GiB RAM, macOS 26.5.2

**Runtime:** CPython 3.13.1

| Measure | Result |
|---|---:|
| `export.xml` size | 2,180,710,697 bytes |
| Stored records | 4,741,049 |
| Stored workouts | 1,170 |
| Exact duplicates skipped | 8,498 |
| Inventoried types | 77 |
| Sources | 683 |
| Nonfatal warnings | 0 |
| Completed database size | 1,961,099,264 bytes |
| End-to-end elapsed time | 355.00 seconds |
| Peak resident memory | 48,218,112 bytes |

The run included folder inventory, XML hashing, streaming parsing, exact deduplication, metadata/workout storage, index creation, SQLite integrity validation, and atomic activation. The temporary database was removed immediately after the benchmark.

Peak memory remained below 50 MB and did not retain processed XML children. Results are specific to this reference machine and dataset and are not a performance guarantee.

## Phase 3 normalized import and dashboard queries

**Date:** 2026-07-30

**Application:** 0.3.0

**Export:** same private local HealthKit Export Version 14 fixture (not committed)

**Machine:** Apple M1 Pro, 16 GiB RAM, macOS 26.5.2

**Runtime:** CPython 3.13.1

| Measure | Result |
|---|---:|
| Stored records | 4,741,049 |
| Stored workouts | 1,170 |
| Exact duplicates skipped | 8,498 |
| Normalization warnings | 0 |
| Materialized daily metric rows | 143,495 |
| Reconstructed sleep sessions | 3,460 |
| Supported registry types | 24 |
| Completed database size | 2,778,705,920 bytes |
| End-to-end elapsed time | 663.54 seconds |
| Peak resident memory | 46,874,624 bytes |
| Overview query, 30 days | 54.13 ms |
| Overview query, one year | 54.65 ms |
| Overview query, all time | 113.81 ms |

The Phase 3 run additionally included timestamp/unit normalization, source/device summaries, interval-aware cumulative aggregation, sleep reconstruction, and the supporting indexes. Query timings include date-window resolution, metric summaries, exact medians for overview measurements, sleep summary, unit conversion, and response-payload construction. No health values, dates, names, coordinates, or private paths were recorded.
