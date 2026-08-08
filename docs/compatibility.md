# Version and database compatibility

## Application versions

The project uses semantic versioning for public releases. Pre-1.0 minor versions may change parser behavior or require a complete re-import. Patch versions must not intentionally change stored-data meaning without a documented compatibility note.

`pyproject.toml` and `apple_health_viewer.version.__version__` must match; the test suite enforces this release invariant.

## Processed health database

The v0.6 parser writes **health schema 3**. The application:

- rejects schema 1 with a complete re-import instruction;
- reads supported schemas 2–3, with Phase 5 features requiring schema 3; and
- rejects schemas newer than 3 with an application-upgrade instruction.

A full Apple Health export is the durable source of truth. Processed databases are replaceable caches and are never migrated in place across parser semantics. When a parser/schema update requires new derived data, re-import the complete export transactionally.

Downgrading the application is unsupported when the active database has a newer schema. Upgrade again, restore a backed-up app-data directory created by the older version, or remove processed data and re-import with the desired version.

## Settings database

Settings migrations are numbered, forward-only, and applied at startup. Settings, retained-source inventory, and import history survive health-database replacement. Before testing a downgrade, back up the complete application-data directory while the viewer is stopped.

## Parser compatibility

The parser inventories unknown top-level elements and health types rather than silently treating them as supported. New Apple export versions remain best-effort until listed in [`export-compatibility.md`](export-compatibility.md). Malformed, unsafe, or structurally excessive XML/GPX/CSV is rejected without activating the staged database.

## Rollback

Application rollback does not imply database rollback. Keep the original export or retained upload so a supported application version can rebuild its own database. Do not manually edit `health.sqlite3`, `settings.sqlite3`, activation markers, or staging files.
