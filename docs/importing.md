# Importing Apple Health Data

## Export from Apple Health

On iPhone, open Health, open the profile menu, and choose **Export All Health Data**. Apple creates a complete ZIP snapshot. Keep the original export private.

## Supported source methods

### Existing local path — recommended for large exports

Enter a path to the ZIP or unzipped export folder. The app reads it in place and never copies or modifies the source. The path can also be supplied with `--health-data` or `AHV_HEALTH_DATA_PATH`.

### ZIP upload

Choose the original ZIP in the local web interface. The browser transfers it to the loopback app, which retains one app-managed local copy for future reprocessing. ZIP members are streamed; the app does not extract the entire archive.

### Folder upload

Choose the unzipped folder in a browser that supports directory upload. Relative paths are preserved under an app-managed source directory. For multi-gigabyte exports, an existing local path is more efficient.

## Transactional replacement

Every Apple export is treated as a complete snapshot:

1. validate the source and available disk space;
2. stream `export.xml` into a new staging SQLite database;
3. inventory linked files and unsupported types;
4. remove exact duplicate records by deterministic fingerprint;
5. create indexes and run SQLite integrity validation; and
6. atomically replace the active health database only after success.

A failure or cancellation removes only staging data. The previous active database remains unchanged. Full exports are never appended together.

## Progress and cancellation

The import page reports phase, percentage of XML bytes consumed, and stored record/workout count. Cancellation is cooperative: the parser stops at a safe XML boundary and discards the staged database.

Only one import can run at a time. If the app stops during an import, the job is marked interrupted and stale staging data is cleaned on the next start.

## Retained uploads

Uploaded ZIPs/folders are retained by default. The import page shows their labels and sizes and supports re-import. **Delete source** removes only that retained copy; the processed health database remains available.

Configured local paths are never copied or deleted by the app.

## Data imported in Phase 2

- raw quantity/category records and metadata;
- source and device provenance;
- workouts, statistics, events, and route references;
- activity summaries;
- generic inventory for correlations and unsupported top-level content; and
- file inventory for GPX, ECG CSV, CDA, and other linked files.

Phase 2 does not calculate medical metrics or source-overlap estimates. Blood glucose records are retained with their original units and meal metadata; conversion and trend calculations arrive in Phase 3.

## Safety limits

Inputs are treated as untrusted even though processing is local. The importer rejects traversal paths, absolute archive paths, symbolic links, encrypted ZIPs, duplicate archive names, custom/external XML entities, excessive file counts, and configured size/compression limits.

The Data Quality page reports imported, inventory-only, and unknown types plus exact duplicates, sources, linked files, and warnings.
