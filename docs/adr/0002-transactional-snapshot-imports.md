# ADR-0002: Transactional snapshot imports

- **Status:** Accepted
- **Date:** 2026-07-30

## Context

Apple's “Export All Health Data” output is a complete snapshot. A user may import a newer full export after only a small amount of new data has accumulated. Appending the newer snapshot would duplicate old records, fail to reflect corrections/deletions, and depend on stable identifiers that are not guaranteed for every record.

Exports can be multiple gigabytes, so parsing on every app launch is too expensive. An interrupted or malformed replacement must not destroy the last working import.

## Decision

Persist parsed health data in SQLite. Every complete import or re-import will:

1. acquire and validate the source;
2. create a separate staging database;
3. stream-parse the complete snapshot into staging;
4. parse linked GPX/ECG assets;
5. create aggregates and indexes;
6. validate integrity, counts, and invariants;
7. close database handles; and
8. atomically replace the active health database only after success.

Settings and import history live in a separate database and survive replacement. Fatal failure or cancellation leaves the prior health database active. Interrupted staging files are detected and safely cleaned on restart.

The app does not append one full Apple export to another in v1.

## Consequences

### Positive

- Correctly models each export as a new source-of-truth snapshot.
- Avoids duplicate accumulation and stale historical records.
- Fast app startup after import.
- Strong rollback behavior.
- Simpler correctness model than record-level incremental reconciliation.

### Negative

- Re-import time scales with complete export size even when little changed.
- Temporary disk usage includes the prior database, staged database, and possibly uploaded/extracted source.
- Atomic file replacement behavior and open handles must be tested on Windows as well as POSIX systems.

### Required safeguards

- Preflight available disk space.
- Keep XML parsing and uploads streaming/bounded-memory.
- Batch writes and create nonessential indexes after bulk insertion.
- Never mutate a configured source folder.
- Show progress and allow cancellation.
- Preserve the old active database until staged validation succeeds.
- Version the parser/schema so derived data can be rebuilt when logic changes.

## Alternatives considered

### Append only records newer than the previous export date

Rejected because older records may be corrected or deleted, timestamps are not universal stable identities, and the complete export is not specified as an incremental feed.

### Upsert every record into the active database

Rejected for v1 because identifiers are inconsistent across types and in-place mutation weakens rollback and complicates deletions.

### Parse on every launch without persistence

Rejected because realistic exports are too large for acceptable startup latency.

### In-place truncate and rebuild

Rejected because a crash or parse error would leave no working dataset.
