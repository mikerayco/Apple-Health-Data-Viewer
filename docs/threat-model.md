# v1 threat model

**Reviewed:** 2026-08-08

**Boundary:** single-user, unauthenticated viewer on the local machine

## Protected assets

- Apple Health exports and retained uploads.
- Processed SQLite databases, routes, and ECG samples.
- Source paths, device/source identifiers, and import metadata.
- Session secret and CSRF state.

## Trust boundaries

1. The browser and Flask process communicate over loopback HTTP.
2. Uploaded ZIPs, folder uploads, configured exports, GPX, CSV, and XML are untrusted.
3. The application-data directory is trusted only after path containment checks.
4. Git history, Docker build context, CI output, issues, and diagnostics are public boundaries and must contain synthetic metadata only.

Binding beyond loopback, reverse proxies, remote users, shared hosting, and internet deployment are outside v1 support. A persistent UI warning appears when a non-loopback host is configured.

## Threats and controls

| Threat | Primary controls | Residual risk |
|---|---|---|
| Network disclosure | Loopback default and Compose publication; trusted-host checks; no authentication; persistent non-loopback warning | A user can intentionally override the bind address; host validation is not a firewall |
| Cross-site state change | Strict SameSite/HttpOnly session cookie, CSRF tokens, Origin and Fetch Metadata checks before body parsing | Requests without browser provenance headers still rely on CSRF |
| Browser data exfiltration | Self-only CSP/connect policy, no CDN/telemetry, local static assets, no persistent health-data browser storage | Browser extensions and a compromised local browser are outside the boundary |
| ZIP traversal, symlinks, bombs | Path normalization, duplicate/symlink/encryption rejection, file/count/ratio/size ceilings, scoped asset access | Limits are configurable only in code for v1; large accepted inputs still consume disk and CPU |
| XML/CSV resource exhaustion | Streaming parse, entity/declaration rejection, depth/child/row/sample/point limits, cancellation | Valid exports near safety ceilings can still require substantial time and storage |
| Partial or malicious import | Staging database, integrity validation, exact deduplication, atomic activation and rollback | Windows file-sharing behavior may require closing concurrent viewer windows before retry |
| Upload temporary-disk exhaustion | Multipart files spool inside the private application-data filesystem; Content-Length free-space preflight; maximum request size | Chunked requests without a declared length reach parser limits without preflight |
| Local-account disclosure | POSIX app directories `0700`, databases/session key `0600`, Docker unprivileged user | Windows ACLs inherit from `%LOCALAPPDATA%`; local administrators and backups remain trusted |
| Privacy leakage through logs/UI | Sanitized public errors and type-only unexpected-error logs; source paths omitted from import APIs | User screenshots and operating-system logs remain outside application control |
| Repository/history leak | Ignore/build-context rules, exact synthetic-fixture allowlist, candidate scan, full-history blob scan in CI | Pattern scanning cannot prove absence of every possible identifier; final human diff review remains required |
| Destructive removal | CSRF-protected explicit confirmation; import-busy guard; retained-source deletion is separate | Backups, snapshots, and recycle/trash facilities may retain copies |
| Incompatible database | Explicit supported health-schema upper/lower bounds and re-import policy | Settings migrations are forward-only; downgrade requires a backup or reset |

## Security invariants

- Normal runtime never enables Flask debug mode or the reloader.
- No state-changing route is intentionally exposed as GET.
- Health values, ECG samples, coordinates, DOB, and full source paths are not logged.
- Failed or cancelled imports do not replace the active database.
- Configured source paths are read-only; only app-managed retained sources can be deleted by the app.
- Runtime assets and requests do not require a third-party origin.

## Release review

Before release, run the gates in [`release-checklist.md`](release-checklist.md), inspect staged changes and reachable Git history, exercise upload/re-import/removal with synthetic data, and manually verify loopback publication in native and Docker runs.
