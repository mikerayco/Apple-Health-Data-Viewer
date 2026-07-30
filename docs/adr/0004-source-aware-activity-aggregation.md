# ADR-0004: Source-aware activity aggregation

- **Status:** Accepted
- **Date:** 2026-07-30

## Context

A complete Apple Health export can contain measurements for the same activity and time interval from an iPhone, Apple Watch, and third-party apps. Summing every exported cumulative record can double-count activity. Selecting only one source can omit periods when that device was not recording.

Apple Health applies source-priority behavior internally, but a complete and reliable representation of that priority is not guaranteed in the exported data. The app must provide useful default totals without hiding this uncertainty or claiming exact parity with Apple's dashboard.

## Decision

The dashboard will default to a **source-aware combined best estimate** for cumulative activity metrics.

The aggregation pipeline will:

1. preserve every record's source and device provenance;
2. remove exact duplicates using stable identifiers when available and a deterministic content fingerprint otherwise;
3. combine non-overlapping intervals;
4. reconcile overlapping intervals through a documented, deterministic, metric-specific rule;
5. expose source/device filters and source-specific totals;
6. report overlap, deduplication, coverage, and uncertainty in the data-quality interface; and
7. label combined values as estimates where exact Apple-equivalent reconciliation cannot be established.

The precise interval-reconciliation rule is an implementation decision that must be validated with hand-auditable synthetic fixtures and manually compared with private Apple Health summaries before release. It may differ by metric, but it must never silently sum known-overlapping cumulative samples.

## Consequences

### Positive

- More useful default totals than requiring a user to choose one device.
- Lower double-counting risk than summing all sources.
- Retains periods recorded by only one source.
- Users can inspect and override the combined view through source filters.
- Methodology and uncertainty remain visible.

### Negative

- Some totals may differ from Apple's Health app.
- Interval reconciliation is more complex than daily summation.
- Different metric types may require different tested rules.
- The app must maintain explanatory UI and methodology documentation.

### Required validation

- Synthetic fixtures cover exact duplicates, partial overlaps, complete overlaps, gaps, changing devices, and third-party sources.
- Aggregate tests are hand-calculable.
- Private local comparisons record only differences/counts—not health values or screenshots in the repository.
- Unknown or ambiguous overlap produces a visible warning rather than false precision.

## Alternatives considered

### Sum every source

Rejected because simultaneous devices can substantially double-count cumulative activity.

### Use the highest single-source daily total

Rejected because it can discard legitimate periods captured only by another device and ignores interval-level overlap.

### Require one source for all dashboard totals

Rejected as the default because users commonly change or intermittently wear devices. Source-only views remain available as filters.

### Claim exact replication of Apple's source priority

Rejected unless future evidence demonstrates that the required priority information is consistently present in exports.
