# ADR-0003: Offline privacy and network boundary

- **Status:** Accepted
- **Date:** 2026-07-30

## Context

Apple Health exports can contain highly sensitive health records, dates of birth, device/source details, precise workout coordinates, ECG samples, and clinical metadata. The application is intended to run independently on each user's computer. The user requires fully offline runtime operation and no telemetry.

The app has no v1 authentication, which is acceptable only while its network boundary is the local machine.

## Decision

The v1 application will:

- bind to `127.0.0.1` (and explicitly controlled loopback equivalents) by default;
- disable debug mode and the interactive debugger;
- restrict trusted hosts to loopback names/addresses;
- make no runtime third-party requests;
- vendor scripts, styles, fonts, icons, and chart assets;
- use a self-only Content Security Policy;
- include no telemetry, analytics, update checks, remote fonts, CDN links, or external map tiles;
- render workout routes on a tile-free local coordinate plot;
- keep health data in server-side local storage rather than persistent browser storage;
- avoid sensitive values, coordinates, DOB, and full paths in logs/errors; and
- require an explicit configuration change plus warning before binding beyond loopback.

No authentication is implemented in v1. If LAN/remote access becomes an official feature, authentication, transport security, session security, authorization, and a revised threat model are mandatory before exposure.

## Consequences

### Positive

- Health data remains on the user's machine during normal operation.
- The app continues to work without internet access.
- Runtime behavior is easier to audit.
- No external map or analytics provider receives health-related metadata or coordinates.

### Negative

- No street/topographic route basemap in v1.
- Assets must be updated and licensed within the repository.
- Users cannot access the dashboard from another device by default.
- The project cannot rely on hosted error reporting or analytics.

### Verification

- Automated browser tests run with outbound network blocked.
- Content Security Policy tests reject non-self origins.
- Source scans reject CDN/remote asset URLs.
- Docker publishes the port to loopback only by default.
- Release checks inspect logs, diagnostics, repository contents, and container build context for private data.

## Alternatives considered

### CDN-hosted charting and fonts

Rejected because it breaks offline use and leaks runtime metadata to third parties.

### External street-map tiles

Rejected because tile requests can reveal workout locations and require network access.

### Bind to all interfaces for convenience

Rejected because no-auth health data would be exposed to the local network.

### Add authentication immediately

Deferred because loopback-only single-user operation does not require account complexity. This decision must be revisited before any supported remote access.
