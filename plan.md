# Apple Health Viewer — Project Plan

**Status:** Proposed
**Date:** 2026-07-30
**Target:** Local-first v1, public repository, macOS/Linux/Windows

## 1. Product statement

Build a polished local web app that turns a user's complete Apple Health export into an understandable, filterable health dashboard. Users clone the repository, run the app on their own computer, import a ZIP or unzipped export, and keep all health data local.

The app is descriptive—not diagnostic. It should help users understand personal trends, coverage, consistency, workouts, routes, ECGs, and changes over time without making medical claims.

## 2. Confirmed decisions

- Personal Mac use is the initial priority, but the repository must be usable on macOS, Linux, and Windows.
- Native local execution is the default; Docker Compose is also supported.
- Import sources:
  1. upload an Apple Health ZIP in the web interface;
  2. upload an unzipped export folder in the web interface;
  3. point the app at an existing local export path through app configuration before launch or through local setup UI.
- Each complete re-import builds a new SQLite health database and atomically replaces the old one only after successful validation.
- Dashboard areas: activity, heart/vitals, sleep, workouts, and body measurements.
- Workouts include activity-type breakdowns and filters.
- v1 includes GPX workout-route and ECG CSV visualization.
- Route views remain offline and tile-free.
- Global date filtering includes 7/30/90-day, one-year, all-time, custom ranges, and comparison with the immediately preceding equivalent period.
- Insights are descriptive only, with a clear medical disclaimer.
- Display units are selectable between Metric and Imperial; Metric is the default.
- Runtime is fully offline: no telemetry, analytics, CDN assets, or health-data network requests.
- The server binds to `127.0.0.1` by default and has no v1 authentication.
- App-managed ZIP/folder uploads are retained by default for future reprocessing, with disk-usage visibility and an explicit deletion control. Configured-path imports are never copied.
- Clinical Records/CDA, audiograms, and vision prescriptions are inventoried but not visualized in v1; they remain possible future extensions.
- Overlapping activity sources use a transparent combined best estimate by default, with uncertainty indicators and source/device filters.
- The public repository uses the MIT License with `Copyright (c) 2026 Mike Rayco`, supplemented by medical and privacy disclaimers.

Related decisions:

- [ADR-0001: Local web architecture and minimal stack](docs/adr/0001-local-web-architecture.md)
- [ADR-0002: Transactional snapshot imports](docs/adr/0002-transactional-snapshot-imports.md)
- [ADR-0003: Offline privacy and network boundary](docs/adr/0003-offline-privacy-boundary.md)
- [ADR-0004: Source-aware activity aggregation](docs/adr/0004-source-aware-activity-aggregation.md)

## 3. Success criteria

A v1 is successful when a user can:

1. follow platform-specific setup instructions from a clean clone;
2. import a realistic multi-gigabyte Apple Health export without loading the XML into memory;
3. close and reopen the app without reparsing the export;
4. filter all dashboard views by a consistent date range;
5. understand how the selected period compares with its preceding period;
6. inspect activity, vitals, sleep, body, and workout trends with clear units and data coverage;
7. filter workouts by Apple workout activity type and open workout details;
8. inspect a workout route and elevation profile without fetching map tiles;
9. inspect ECG metadata and waveform locally;
10. replace the imported snapshot safely with a newer complete export;
11. see unsupported or missing export content rather than have it silently ignored; and
12. verify from documentation and browser tooling that no health data leaves the machine.

## 4. Scope

### 4.1 v1 functional scope

#### Setup and source selection

- First-run screen explains local-only processing and expected import time/storage implications.
- Source choices:
  - **ZIP upload:** stream the upload to an app-managed temporary file; do not buffer it in memory.
  - **Folder upload:** use a directory-capable browser input and preserve relative paths. Explain that entering an existing local path is more efficient for very large exports.
  - **Local path:** validate a path entered in local setup UI or supplied before launch.
- Configuration precedence, highest first:
  1. command-line option;
  2. environment variable;
  3. local app settings;
  4. first-run UI.
- Recognize an export root whether `export.xml` is at the selected root or one expected wrapper directory below it.
- Show import phases, progress, elapsed time, parsed counts, warnings, and final summary.
- Support cancel/retry without damaging the active database.

#### Overview dashboard

- Data-range and freshness header.
- Coverage summary: tracked days, available categories, sources/devices, missing periods, and last import date.
- Metric cards for available high-value measures only; never display invented zeroes for absent data.
- Trend charts that update with the global period.
- Prior-period absolute and percentage changes, with neutral language.
- Descriptive insight feed based on deterministic rules and visible methodology.
- Links from every summary to its source category/detail view.

#### Activity

- Steps, walking/running distance, cycling distance, active and basal energy, exercise minutes, flights climbed, stand hours, and Activity Summary values when present.
- Daily/weekly/monthly aggregation depending on selected range.
- Goal completion only when goals are present in exported Activity Summary records.
- Source/device coverage and overlap warning where cumulative samples may overlap.

#### Heart and vitals

- Heart rate, resting heart rate, walking heart rate, HRV SDNN, respiratory rate, oxygen saturation, VO2 max, blood pressure, and blood glucose when present.
- Blood glucose uses individual timestamped readings, source/device, and meal-context metadata when exported; Phase 3 supports `mg/dL` and `mmol/L` conversion without diagnostic ranges.
- Mean/median/range and latest value with timestamp.
- Scatter or range views where daily averages would hide meaningful variation.
- Explicit measurement counts and coverage to prevent sparse data from appearing continuous.

#### Sleep

- Total asleep, time in bed, awake time, and stage breakdown when available.
- Assign a sleep session to its wake-up day while retaining exact source timestamps and offsets.
- Avoid double-counting overlapping `InBed` and staged sleep records.
- Duration trend, bedtime/wake-time consistency, stage composition, and logged-night coverage.
- Label older `InBed`-only data distinctly from measured asleep duration.

#### Body measurements

- Weight/body mass, BMI, body-fat percentage, lean body mass, height, and other curated body metrics when present.
- No hard-coded plausibility thresholds. Suspected outliers are flagged, never silently deleted.
- Latest value, trend, change over selected range, measurement frequency, and source.

#### Workouts

- Searchable/filterable workout list.
- Filters for workout activity type, date range, source/device, route availability, and ECG association where applicable.
- Type summaries: session count, total/average duration, distance, energy, pace/speed, and trend.
- Detail view with all exported workout statistics and events.
- Keep Apple's raw identifier and map it to a human-readable label without losing unknown/new types.

#### Workout routes

- Parse linked GPX route files.
- Tile-free SVG/canvas plot with route polyline, start/end markers, bounds, and hoverable samples.
- Distance, duration, pace/speed, elevation profile, elevation gain/loss, and available accuracy fields.
- Downsample only for rendering; retain the original parsed points or a lossless reference.
- Clearly state that the route is shown without geographic map tiles.

#### ECG

- Parse Apple ECG CSV metadata and waveform samples defensively.
- ECG list with recorded date, duration, sample rate, classification, symptoms, and device when present.
- Detail view with zoomable waveform, time axis, amplitude unit, and metadata.
- Preserve Apple's classification wording and add no independent rhythm diagnosis.

#### Data catalog and quality

- Inventory every top-level element and health type encountered.
- Separate **supported**, **imported but not visualized**, **unknown**, and **failed** items.
- Show source names/devices, date coverage, record counts, units, exact duplicate counts, overlap warnings, and parse warnings.
- Provide a privacy-safe diagnostics export containing schema/type counts and errors, but no health values, dates of birth, coordinates, or raw records.

#### Settings and data management

- Metric/Imperial display toggle, default Metric.
- Current source and import history summary.
- Refresh/re-import, remove local processed data, and replace source actions with confirmations.
- Explain where app-managed files are stored.
- No profile DOB, biological sex, or blood type on the dashboard by default.

### 4.2 Descriptive insights

Insights must be deterministic, testable, and qualified by coverage. Initial insight types:

- selected-period versus prior-period change;
- rolling personal baseline comparison;
- streaks and consistency for measures with adequate daily coverage;
- personal highs/lows, excluding incomplete days;
- workout frequency and type mix changes;
- sleep duration and schedule consistency;
- paired-day correlations between selected metrics when sample size is sufficient.

Rules:

- Always show date window, sample count, coverage, and comparison basis.
- Use neutral wording such as “higher,” “lower,” or “associated”; never “healthy,” “dangerous,” or causal language.
- Do not produce an insight below a documented coverage/sample threshold.
- Missing data is unknown, not zero.
- Correlation does not imply causation; show this beside correlation output.
- Every insight calculation gets unit tests and a plain-language methodology note.

### 4.3 Explicit v1 non-goals

- Hosted/SaaS deployment or remote accounts.
- Multi-user data separation.
- Authentication while bound to loopback only.
- Medical diagnosis, treatment guidance, alerts, or risk scoring.
- Writing data back to Apple Health.
- Incremental append from full exports.
- Street/topographic basemaps or runtime tile downloads.
- Automatic cloud backup or synchronization.
- Full visualization of Clinical Records/FHIR resources, CDA XML, audiograms, and vision prescriptions. They are inventoried and reported as unsupported in v1 rather than silently discarded, with visualization left as a possible future extension.
- Claiming byte-for-byte or aggregate parity with the Apple Health app where Apple source-priority behavior is not represented in the export.

## 5. Architecture

### 5.1 Proposed stack

- **Runtime:** supported CPython, minimum 3.12; CI on 3.12–3.14.
- **Web:** Flask 3.1.x with Jinja templates.
- **Storage:** Python standard-library `sqlite3`; no ORM in v1.
- **Parsing:** standard-library `xml.etree.ElementTree.iterparse`, `zipfile`, `csv`, and GPX XML parsing.
- **Frontend:** semantic HTML, tokenized CSS, focused vanilla JavaScript, and a pinned/vendored Chart.js distribution.
- **Server:** local Flask/Werkzeug process bound to `127.0.0.1`; debug mode off.
- **Packaging:** `pyproject.toml`, locked runtime dependencies, native scripts, and Docker Compose.

This deliberately avoids a SPA framework, Node runtime requirement, background queue, external database, and map library.

### 5.2 Dependency health snapshot

Validated on 2026-07-30 before recommending the stack:

- Flask 3.1.3 was released 2026-02-18 and its repository had activity in May 2026.
- Chart.js 4.5.1 is the current listed release; its repository had activity in May 2026. The built asset will be pinned, vendored, checksummed, and accompanied by its license—never loaded from a CDN.
- Python 3.12, 3.13, and 3.14 are supported according to the Python Developer's Guide.
- SQLite, XML, CSV, ZIP, hashing, and configuration needs can be met by the Python standard library.

Repeat dependency and vulnerability review immediately before implementation pinning and before each release. Do not add a dependency without recording its purpose, license, current support state, and simpler alternatives.

### 5.3 Process model

One local web process owns one active user database. A single in-process background worker performs imports so HTTP requests can report progress. Only one import may run at a time. Import job state is persisted sufficiently to identify and clean interrupted staging files on restart; no Celery/Redis is needed.

### 5.4 Storage layout

Use platform-native app-data locations without adding a platform-path dependency:

- macOS: `~/Library/Application Support/Apple Health Viewer/`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/apple-health-viewer/`
- Windows: `%LOCALAPPDATA%\Apple Health Viewer\`
- Docker: `/data`

Suggested files:

```text
app-data/
  settings.sqlite3       # settings and import history; survives health DB swaps
  health.sqlite3         # active parsed snapshot
  imports/
    staging-<job-id>/    # temporary DB/extraction; removed after success/failure
  assets/                # only if app-managed imported ECG/GPX files are retained
  logs/                  # metadata-only logs, bounded rotation
```

Original configured folders are read-only and are never copied. App-managed ZIP/folder uploads are retained by default so parser or schema upgrades can reprocess them without another upload. The UI shows retained-source and processed-database disk usage and provides an explicit **Delete retained source** action that leaves the processed database usable. Documentation warns that retained exports may be captured by system backups.

### 5.5 Database model

Keep settings/import state separate from replaceable health data.

Core health tables:

- `import_manifest`: export version/locale/date, source fingerprint, parser version, counts, warnings.
- `health_types`: raw identifier, display label, category, value kind, canonical/display units, support status.
- `sources` and `devices`: normalized source/device identities.
- `records`: type, original/canonical value, original unit, source/device, creation/start/end timestamps, local dates, dedup fingerprint.
- `record_metadata`: selected queryable keys plus raw key/value rows where safe and needed.
- `correlations`: correlation attributes and child references without duplicating top-level records.
- `activity_summaries`.
- `workouts`, `workout_statistics`, `workout_events`.
- `workout_routes` and `route_points`.
- `ecgs` and `ecg_samples`.
- `daily_metrics`: materialized daily aggregates and coverage metadata.
- `sleep_sessions` / `sleep_stages`: normalized sleep intervals and derived sessions.
- `import_issues`: unknown types, malformed values, missing files, and nonfatal warnings.

Use integer primary keys, foreign keys, batch inserts, explicit indexes created after bulk loading, and schema migrations keyed by parser/schema version. Store timestamps in a sortable normalized form while retaining original timezone offsets and local day semantics.

### 5.6 Parsing and import pipeline

1. **Acquire** source through ZIP upload, folder upload, configured path, or UI path entry.
2. **Preflight** expected files, disk space, permissions, and ZIP safety.
3. **Fingerprint** source metadata and parser version.
4. **Create staging** directory and SQLite database.
5. **Stream parse** `export.xml`; clear XML elements after processing.
6. **Parse linked assets** for GPX and ECG, reporting missing/malformed files without losing the main import.
7. **Normalize** identifiers, values, timestamps, units, and metadata while preserving originals.
8. **Deduplicate exact records** using available stable identifiers and a deterministic content fingerprint.
9. **Compute aggregates** and coverage; retain sufficient raw data for source filters and detail views.
10. **Validate** schema version, row counts, referential integrity, required indexes, date ranges, and aggregate invariants.
11. **Finalize** and atomically swap the staged health database into place.
12. **Clean up** staging/source copies according to retention settings.

Never append a full export to the active health database. If any fatal phase fails, keep the old database active.

### 5.7 ZIP and upload safety

Even though the app is local, public-repository inputs must be treated as untrusted:

- reject absolute paths, `..` traversal, device paths, and entries escaping staging;
- do not follow archive symlinks;
- stream uploads/extraction to disk;
- preflight free disk space and report compressed/uncompressed sizes;
- enforce configurable safety ceilings and file-count limits with actionable errors;
- accept only expected export files for parsing;
- use randomized staging names and restrictive file permissions where supported;
- clean interrupted staging data on the next launch;
- never echo raw health values or filesystem details into logs or browser errors.

### 5.8 Source overlap and data correctness

The Claude prototype's “highest single-source daily total” must not become an undocumented default. Apple exports can contain overlapping records from multiple devices/apps, and the export may not encode Apple's complete source-priority rules.

v1 policy:

- default to a source-aware combined best estimate;
- remove exact duplicates automatically;
- preserve source/device provenance;
- combine non-overlapping intervals and reconcile overlaps with a deterministic, metric-specific rule;
- detect and report overlapping cumulative intervals and uncertainty;
- expose source/device filters and source-specific totals;
- label combined cumulative totals with the documented aggregation method and limitations;
- validate representative totals against Apple Health screenshots supplied manually during development, without committing screenshots or data; and
- do not claim Apple-equivalent totals until demonstrated across synthetic and private fixtures.

A short implementation spike must choose and validate the precise interval-reconciliation rules before activity cards are considered complete. Correctness and transparency take priority over matching the prototype. See ADR-0004.

### 5.9 API/UI boundary

Use server-rendered page shells plus small JSON endpoints for chart/detail data:

- `/api/overview`
- `/api/metrics/<type>`
- `/api/sleep`
- `/api/workouts` and `/api/workouts/<id>`
- `/api/workouts/<id>/route`
- `/api/ecgs` and `/api/ecgs/<id>`
- `/api/data-quality`
- `/api/imports` and `/api/imports/<id>/status`
- `/api/settings`

All endpoints validate date range, granularity, pagination, type identifiers, and maximum returned points. Downsample charts server-side or with deterministic rendering decimation; never return millions of raw samples to the browser.

## 6. UX and visual direction

### 6.1 Information architecture

```text
Setup / Import
Overview
Activity
Heart & Vitals
Sleep
Body
Workouts
  Workout detail
  Route detail
ECGs
  ECG detail
Data Quality
Settings & Privacy
```

Desktop navigation uses a restrained sidebar or rail; narrow screens use an accessible compact menu. The global date control remains visible across analytical pages. Import/setup is visually distinct from the dashboard.

### 6.2 Design system

**Direction:** calm clinical editorial—precise, humane, and data-dense without resembling a hospital system or cloning Apple's Health UI.

- Use generous negative space, strong typographic hierarchy, quiet neutrals, and category accents used consistently.
- Avoid a generic wall of identical KPI cards; lead each page with one dominant story, followed by supporting metrics and details.
- Bundle a license-verified open font for consistent offline typography, with strong platform fallbacks.
- Define CSS tokens for color, typography, spacing, radii, borders, chart colors, focus states, and light/dark themes.
- Use color plus text/icon/shape; never encode meaning by color alone.
- Motion is limited to import progress and chart transitions and honors `prefers-reduced-motion`.
- Charts include text summaries, keyboard-accessible controls, and tabular alternatives where practical.
- Target WCAG 2.2 AA contrast, visible focus, semantic landmarks/headings, and 44px touch targets.

### 6.3 Empty, loading, and error states

- Explain why a metric is absent: not exported, no records in range, filtered source, unsupported type, or parse failure.
- Keep the prior dashboard usable during a replacement import.
- Show recoverable warnings separately from fatal import errors.
- Never expose stack traces in the UI outside an explicit developer mode.

## 7. Privacy and security requirements

- Bind only to IPv4/IPv6 loopback by default; changing the bind address requires an explicit configuration change and prominent warning.
- Debug mode and interactive debugger are always off in normal execution.
- Restrict trusted hosts to loopback hostnames/addresses.
- Set restrictive Content Security Policy: self-only scripts/styles/assets; no remote connections.
- No telemetry, analytics, update checks, remote fonts, CDNs, or external map tiles.
- Avoid persistent browser storage for health data; server-side SQLite is authoritative.
- Use POST plus CSRF protection for state-changing actions, even locally.
- Never log health values, ECG samples, coordinates, DOB, or full source paths.
- App data and uploaded extracts must be excluded from version control and Docker build contexts.
- Public issue templates must warn users not to upload exports, screenshots, logs with private paths, or databases.
- README must state that the app is not medical software and users are responsible for local machine security and backups.

## 8. Repository safety before public release

The current workspace contains real Apple Health exports and Claude-generated derivatives with personal details. Before initializing/publishing the repository:

1. create and verify `.gitignore` and `.dockerignore` rules for `apple-health-data/`, `from_claude/` generated data, ZIP/XML/CDA/GPX/ECG imports, SQLite databases, app-data directories, logs, and OS/editor files;
2. move private reference data outside the future repository or retain it only in ignored paths;
3. replace all tests with small synthetic fixtures containing invented people, dates, coordinates, and values;
4. scan the full Git index and history for health data, names, DOBs, absolute paths, tokens, and large files before the first push;
5. ensure Docker build context cannot include private exports; and
6. document a private-fixture test workflow that never records raw values in snapshots or CI artifacts.

Do not commit anything currently under `apple-health-data/` or generated JSON/text/log artifacts under `from_claude/`.

## 9. Testing strategy

### 9.1 Synthetic fixtures

Create small, hand-auditable fixtures for:

- embedded export DTD versions and unknown future elements;
- quantity/category records, metadata, correlations, activity summaries, and workouts;
- overlapping sources, exact duplicates, missing days, timezone changes, and DST;
- staged and `InBed`-only sleep;
- Metric/Imperial conversion;
- malformed records and missing linked files;
- safe and malicious ZIP paths;
- GPX routes with elevation/accuracy and ECG CSV variants;
- interrupted imports and atomic rollback.

Fixtures contain no copied private values, coordinates, identifiers, or timestamps.

### 9.2 Test layers

- Parser and unit-registry unit tests.
- Golden aggregation tests with calculations small enough to verify manually.
- SQLite migration/integrity tests.
- Import job and atomic-swap integration tests.
- Flask route/API tests including invalid filters and upload constraints.
- Frontend interaction tests for date ranges, units, workout types, pagination, and empty/error states.
- Accessibility checks plus keyboard/manual screen-reader smoke tests.
- Offline test that blocks network access and asserts all runtime assets still work.
- Cross-platform CI on Windows, macOS, and Linux for supported Python versions.
- Docker build/start/health-check test.

### 9.3 Performance tests

Use generated XML rather than private data in CI. Separately benchmark the private multi-gigabyte export locally without preserving health values in output.

Targets:

- parsing memory remains bounded and does not scale linearly with XML size;
- import progress updates throughout long imports;
- active database remains usable during replacement import;
- overview/filter API requests return in under 500 ms at realistic scale on the reference machine after import;
- page interactions remain responsive with chart point limits/downsampling;
- startup with an existing database does not reparse and reaches the dashboard in a few seconds.

Record hardware, fixture size, record count, parser version, elapsed time, peak memory, and database size—never health values.

## 10. Delivery phases

### Phase 0 — Repository and privacy foundation

- Establish repository structure, standard MIT `LICENSE` (`Copyright (c) 2026 Mike Rayco`), third-party notices, contribution policy, privacy notice, `.gitignore`, and `.dockerignore`.
- Quarantine private exports and Claude-derived artifacts from version control/build context.
- Create synthetic fixture generator and secret/PII scanning checks.
- Add architecture records and a minimal CI matrix.

**Exit:** a public-safe empty application skeleton can be pushed without personal data.

### Phase 1 — Local application shell

- Flask app factory, configuration precedence, platform app-data paths, loopback launch command.
- Settings database and schema migration mechanism.
- First-run/import UI shell, navigation, design tokens, responsive/accessibility baseline.
- Dockerfile/Compose with loopback port and read-only source volume examples.

**Exit:** native and Docker app shells run offline on all three OS families.

### Phase 2 — Transactional import foundation

- ZIP/folder/path acquisition and validation.
- Streaming XML parser with generic type inventory.
- Staging database, progress reporting, cancellation, validation, and atomic replacement.
- Exact dedup fingerprints, source/device preservation, import warnings, and safe cleanup.
- Data-quality inventory UI.

**Exit:** a large export can be imported/re-imported safely and reopening is fast.

### Phase 3 — Metrics and aggregation correctness

- Metric registry, unit normalization/conversion, timezone/date semantics.
- Daily aggregates and coverage calculations.
- Activity, vitals (including blood glucose), sleep, and body measurement logic.
- Source-overlap spike and validated interval-reconciliation rules for the accepted combined-estimate policy.
- Golden tests against synthetic fixtures and private manual cross-checks.

**Exit:** supported metrics are correct, explainable, source-aware, and unit-switchable.

### Phase 4 — Dashboard and insights

- Overview and category pages.
- Global date/custom range and prior-period comparison.
- Chart endpoints, point limits, accessible summaries/tables.
- Deterministic descriptive insight rules with coverage gates.
- Empty/error/loading states and visual polish.

**Exit:** the primary dashboard is responsive, insightful, accessible, and fully offline.

### Phase 5 — Workouts, routes, and ECG

- Workout type labels, filters, summaries, list, and detail.
- GPX linking/parsing, tile-free route rendering, and elevation profile.
- ECG CSV variants, list, metadata, and waveform viewer.
- Unknown type/file handling and diagnostics.

**Exit:** workout activity types, supported routes, and ECGs are navigable and tested.

### Phase 6 — Hardening and public release

- Threat-model review: uploads, ZIP extraction, local server, logs, CSP, and Docker mounts.
- Performance profile on synthetic large data and private local reference data.
- Windows/macOS/Linux installation documentation and troubleshooting.
- Privacy-safe issue template, support boundaries, medical disclaimer, and data-removal guide.
- Dependency/license/vulnerability review and pinned lockfiles/checksums.
- Release checklist and versioned schema/parser compatibility policy.

**Exit:** all acceptance criteria pass and repository scan confirms no personal data.

## 11. v1 acceptance checklist

- [ ] Native setup documented and tested on macOS, Windows, and Linux.
- [x] Docker Compose is optional and documented; native remains primary.
- [x] ZIP upload, folder upload, configured path, and UI local-path import work.
- [x] Multi-gigabyte XML import is streaming and bounded-memory.
- [x] Failed/cancelled import leaves the active database untouched.
- [x] Successful complete re-import atomically replaces health data while retaining settings.
- [x] App-managed uploads are retained by default, disk usage is visible, and deleting a retained source leaves processed data usable.
- [x] Configured-path imports do not duplicate the source.
- [ ] Dashboard covers all confirmed categories and hides unavailable metrics cleanly.
- [ ] Workout activity-type filtering and summaries work with unknown future types.
- [ ] GPX route and ECG waveform views work without network access.
- [x] Date presets, custom range, and immediately preceding period comparison are consistent across implemented analytical pages.
- [x] Metric default and Imperial alternative produce tested conversions.
- [x] Missing-data, sparse-data, overlap, and support-status labels are visible.
- [ ] Insights meet sample/coverage gates and make no diagnostic/causal claims.
- [ ] No runtime request targets a non-loopback or third-party origin.
- [ ] Server defaults to loopback, debug off, trusted hosts restricted, and CSP self-only.
- [ ] Accessibility review meets WCAG 2.2 AA targets.
- [ ] Synthetic test suite and cross-platform CI pass.
- [ ] Dependency, license, vulnerability, and repository privacy scans pass.
- [ ] No private Apple Health data or Claude-generated personal derivative is tracked.

## 12. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Export schema changes | Import failures or silently missed data | Read embedded DTD/version, tolerate unknown elements, inventory all types, preserve fixtures by version |
| Multi-gigabyte files | Long import, memory/disk exhaustion | Streaming parse/upload, batch inserts, disk preflight, progress/cancel, staging cleanup |
| Source overlap | Misleading cumulative totals | Exact-only automatic dedup, source filters, overlap detection, documented methodology and limitations |
| Sleep overlap/semantic changes | Inflated duration | Interval-aware stage logic, wake-day convention, staged-vs-in-bed labeling, golden tests |
| Unit inconsistency | Incorrect charts | Preserve original unit, canonical registry, explicit conversion tests, unknown-unit warnings |
| Timezone/DST errors | Wrong dates and sleep nights | Preserve offsets, define local-day rules, DST fixtures |
| ZIP traversal/bombs | Filesystem or disk damage | Safe extraction, limits, disk checks, staging sandbox |
| Local server exposed to LAN | Health-data disclosure | Loopback default, trusted hosts, warning on override, no debug mode |
| Public repo leaks current private data | Severe privacy breach | Phase 0 quarantine, ignore/build-context rules, full index/history scan before push |
| ECG/route rendering overload | Frozen browser | Server pagination/downsampling and bounded API responses |
| Dashboard implies medical advice | User harm/liability | Neutral descriptive language, methodology, coverage labels, disclaimer |
| Browser folder-upload differences | Cross-browser friction | Also support ZIP upload and efficient local-path configuration; document supported browsers |

## 13. Reference prototype assessment

The `from_claude/` folder is useful only as a private discovery artifact. Reusable ideas include streaming XML parsing, a curated initial metric list, daily/monthly aggregates, period selectors, and workout summaries.

Do not carry forward its hard-coded source/output paths, fixed dates, fixed profile fields, fixed sex, pounds-only display, silent body-mass threshold, highest-source daily dedup assumption, CDN Chart.js, generated personal JSON, or static one-off HTML build. Re-derive every calculation under tests.

## 14. Documentation deliverables

- `README.md`: purpose, screenshots using synthetic data, privacy guarantee, quick start, MIT license summary, medical disclaimer.
- `docs/importing.md`: Apple export steps, supported layouts, ZIP/folder/path choices, storage needs.
- `docs/privacy.md`: local data flow, files retained, logs, removal, network boundary.
- `docs/metrics.md`: formulas, date semantics, units, source overlap, coverage thresholds.
- `docs/platforms/{macos,windows,linux}.md`: native setup and troubleshooting.
- `docs/docker.md`: volume mounts, loopback binding, permissions, source read-only guidance.
- `docs/export-compatibility.md`: tested export versions and unsupported top-level content.
- `docs/development.md`: synthetic fixtures, private local testing rules, CI, dependency policy.
- `docs/adr/`: durable architecture decisions.

## 15. Research references

- Apple Support, “Share your data in Health on iPhone” (export flow and XML format): <https://support.apple.com/guide/iphone/share-your-health-data-iph5ede58c3d/26/ios/26>
- Flask changelog (3.1.3 release and security history): <https://flask.palletsprojects.com/en/stable/changes/>
- Python Developer's Guide, supported versions: <https://devguide.python.org/versions/>
- Chart.js releases and installation: <https://github.com/chartjs/Chart.js/releases> and <https://www.chartjs.org/docs/latest/getting-started/installation>
- The private reference export embeds **HealthKit Export Version 14** and includes `Record`, `Correlation`, `Workout`, `ActivitySummary`, `ClinicalRecord`, `Audiogram`, and `VisionPrescription` declarations. This observation guides tolerant parsing but is not a substitute for compatibility tests.
