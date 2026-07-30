# ADR-0001: Local web architecture and minimal stack

- **Status:** Accepted
- **Date:** 2026-07-30

## Context

The application must run locally on macOS, Linux, and Windows, present a polished interactive dashboard, process multi-gigabyte Apple Health exports, work fully offline, and remain approachable for a public repository. Native local execution is primary; Docker is optional.

A desktop framework or JavaScript SPA would add packaging, build, and dependency complexity without being necessary for a single-user loopback application. The reference prototype already demonstrates that Python can stream the export format, but its one-off scripts and static HTML are not maintainable application architecture.

## Decision

Use:

- supported CPython (minimum 3.12);
- Flask 3.1.x and Jinja for the local web application;
- standard-library SQLite through `sqlite3` with explicit SQL and migrations;
- standard-library streaming XML, ZIP, CSV, and GPX parsing;
- semantic server-rendered HTML, tokenized CSS, and focused vanilla JavaScript;
- a pinned and vendored Chart.js build for charts;
- an in-process single import worker; and
- loopback-only Flask/Werkzeug serving with debug mode disabled.

Do not introduce an ORM, SPA framework, Node runtime requirement, task queue, external database, CDN, or map-tile service in v1. Provide Docker Compose as an additional launch method, not the default.

## Consequences

### Positive

- Small dependency and operational footprint.
- One language for parsing, storage, and server logic.
- Cross-platform native and container execution.
- No database/service installation.
- Straightforward offline asset control.
- Server-rendered baseline remains usable and accessible if JavaScript features fail.

### Negative

- Complex frontend interactions require disciplined vanilla JavaScript modules.
- Explicit SQL and migrations require care.
- In-process imports do not scale to multi-user/server deployment.
- Flask's local server model is intentionally unsuitable for exposed production hosting.

### Constraints

- The server must remain loopback-only by default.
- Chart.js and all fonts/assets must be vendored with versions, checksums, and licenses.
- Dependency health and vulnerabilities are reviewed before pinning and each release.
- Reconsider the architecture if remote multi-user hosting becomes a requirement; do not stretch this design into SaaS.

## Alternatives considered

### React/Next.js or another SPA stack

Rejected for v1 because it introduces Node, a build pipeline, duplicated client/server concerns, and more dependencies without a demonstrated product need.

### Electron/Tauri desktop shell

Deferred because native packaging and platform signing increase scope. The browser is an adequate local UI shell.

### FastAPI plus a SPA

Rejected because async APIs and generated schemas do not materially help a loopback, SQLite-backed dashboard, while Flask is smaller and sufficient.

### Static HTML generated after each import

Rejected because interactive filtering, import management, detail views, and large data require a queryable local application.
