# Release checklist

Complete this checklist for each public release. Record the release commit, date, operator, operating systems, Python versions, and any accepted exceptions without health values or private paths.

## Automated gates

```bash
python scripts/generate_synthetic_fixtures.py --check
python -m compileall -q src scripts tests
python -m unittest discover -s tests -v
python scripts/check_repository_privacy.py --history
python scripts/benchmark_synthetic.py --records 100000 --max-seconds 120 --max-query-ms 500
```

- [ ] All local checks pass from a clean checkout.
- [ ] GitHub Actions passes on Linux/Python 3.12–3.14, macOS/Python 3.12, Windows/Python 3.12, and Docker.
- [ ] The package version, health endpoint version, and release tag agree.
- [ ] A built wheel installs into a clean virtual environment and starts without the source tree.
- [ ] Docker builds, publishes only to host loopback, persists `/data`, and handles an upload larger than 64 MiB.

## Privacy and security

- [ ] Review [`threat-model.md`](threat-model.md) against the final diff.
- [ ] Inspect `git diff --cached` for secrets, health data, paths, screenshots, logs, databases, and generated exports.
- [ ] Run the full-history privacy scan from a full clone; inspect large Git objects manually.
- [ ] Confirm `.gitignore` and `.dockerignore` exclude private workspace and application-data files.
- [ ] Exercise malicious ZIP traversal, symlink, duplicate path, high compression ratio, delayed XML declaration, deep XML, oversized CSV row, CSRF, cross-origin POST, and invalid Host tests.
- [ ] Verify native and Docker processes have debug/reloader disabled and no unexpected logs contain source paths or values.
- [ ] Verify browser developer tools show no non-loopback/third-party runtime request while opening dashboards, a route, and an ECG.
- [ ] Exercise processed-data removal and retained-source deletion separately; verify the documented backup caveat.

## Dependencies and licensing

- [ ] Confirm every exact Python dependency and build dependency is current, supported or explicitly risk-accepted, and compatible with supported Python versions.
- [ ] Query OSV and GitHub advisories for exact locked versions; record date and results in [`dependencies.md`](dependencies.md).
- [ ] Review dependency licenses and update [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).
- [ ] Verify GitHub Actions remain pinned to reviewed full commit SHAs.
- [ ] Review the base image digest and upstream security status.
- [ ] Review Dependabot alerts and document any accepted finding.

## Performance

- [ ] Run a generated large-export import and record size, count, elapsed time, peak memory, and database size only.
- [ ] Run the private local reference benchmark without retaining health values, dates, names, paths, coordinates, or raw output.
- [ ] Confirm startup with an existing database does not reparse.
- [ ] Confirm overview and filter API requests stay below the 500 ms target on the reference dataset.
- [ ] Confirm route and ECG API point limits keep browser interactions responsive.
- [ ] Confirm the previous dashboard remains available during replacement import.

## Accessibility and interaction

- [ ] Complete the checks in [`accessibility.md`](accessibility.md) at 200% zoom and narrow/mobile widths.
- [ ] Verify keyboard-only navigation, visible focus, menu operation, forms, date controls, chart summaries, route/ECG alternatives, errors, and destructive confirmations.
- [ ] Run an automated WCAG audit and manually verify contrast, headings, landmarks, labels, live regions, reduced motion, and one screen-reader flow.

## Platform documentation

- [ ] Follow each clean-clone guide on its named platform: [macOS](platforms/macos.md), [Windows](platforms/windows.md), and [Linux](platforms/linux.md).
- [ ] Verify configured path, ZIP upload, folder upload, re-import, restart, and full reset on supported platform/browser combinations.
- [ ] Recheck troubleshooting and data-directory paths.

## Release

- [ ] Update `README.md`, `plan.md`, compatibility notes, and changelog/release notes.
- [ ] Confirm support and medical/privacy disclaimers are visible.
- [ ] Tag the reviewed commit and publish artifacts from that commit only.
- [ ] After publication, verify repository files, release artifacts, and installation instructions from a fresh machine or account.
