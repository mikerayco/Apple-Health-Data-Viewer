# Metrics and Aggregation

Phase 3 calculations are deterministic, local, and descriptive. They do not provide diagnosis, target ranges, risk scores, or treatment guidance. Original values, units, timestamps, source, and device provenance remain in the active snapshot alongside normalized fields.

## Date semantics

Apple timestamps retain their original UTC offset. Each record stores:

- the original timestamp text;
- a normalized UTC timestamp for ordering and interval overlap;
- the calendar date represented by the original offset.

Point measurements are assigned to their original local start date. Cumulative records crossing midnight are divided proportionally at midnight in the start timestamp's offset. Sleep sessions are assigned to the local date on which the session ends (the wake-up day).

Date presets end on the latest supported date in the imported snapshot. This avoids implying that an older export contains current-day coverage. A comparison uses the immediately preceding range of equal length. Missing dates are unknown, not zero.

## Supported registry

| Category | Metrics | Canonical unit |
|---|---|---|
| Activity | Steps, walking/running distance, cycling distance, active energy, basal energy, exercise time, flights climbed, stand time | count, m, kcal, min |
| Vitals & Metabolic | Heart rate, resting heart rate, walking heart rate, HRV SDNN, respiratory rate, oxygen saturation, VO₂ max, systolic/diastolic blood pressure, blood glucose | count/min, ms, %, mL/min·kg, mmHg, mg/dL |
| Body | Body mass, BMI, body fat, lean body mass, height | kg, count, %, m |
| Sleep | In Bed, Awake, generic Asleep, Core, Deep, REM, and Unspecified intervals | seconds |

Unknown units are preserved in raw records but excluded from aggregates and reported as import issues. The app does not guess an unfamiliar unit.

## Unit conversion

Canonical values are converted only for display:

- metres to kilometres or miles;
- kilograms to kilograms or pounds;
- metres to centimetres or decimal feet for height;
- blood glucose from `mg/dL` to `mmol/L` by dividing by `18.0182`;
- imported blood glucose in `mmol/L` is multiplied by `18.0182` for canonical storage.

Energy remains in kilocalories in both display systems. Heart, HRV, oxygen saturation, respiratory, VO₂ max, and blood-pressure units do not change between Metric and Imperial.

Blood-glucose pages show readings, counts, range, median, source/device filters, and exported meal context. They intentionally show no diagnostic ranges or interpretation.

## Cumulative activity

Exact duplicates are removed before aggregation. Each positive-duration record is represented as a constant rate over its exported interval. For every segment formed by record start/end boundaries:

1. if one record is active, use its rate;
2. if multiple records overlap, use the highest active rate rather than summing them;
3. combine all non-overlapping segments;
4. for zero-duration cumulative samples at the same timestamp, use the highest value.

This produces the default **source-aware combined estimate**. It is deterministic and reduces obvious multi-device double counting, but it does not claim to reproduce Apple's private source-priority algorithm. Days containing overlap are labeled as estimates, and overlap duration remains visible. Source-only and device-only views retain their own exported totals.

Example: a Watch reports 1,000 steps and a Phone reports 800 over the same hour, followed by 500 Phone-only steps in the next hour. The combined estimate is `max(1,000, 800) + 500 = 1,500`, not 2,300. Source totals remain Watch 1,000 and Phone 1,300.

## Measurements

Vitals and body records remain individual measurements. Selected-period summaries include:

- sample-weighted mean;
- exact median;
- minimum and maximum;
- latest value and timestamp;
- measurement count and tracked-day coverage.

No absent day is interpolated. Body values are never silently removed. With at least eight selected measurements, the UI flags values outside three interquartile ranges as statistical outliers while retaining them in every calculation. This is a distribution flag, not a plausibility or medical judgment.

## Sleep

Sleep records separated by more than four hours form distinct sessions. Within a session:

- overlapping intervals of the same semantic group are unioned;
- staged/generic asleep duration is calculated separately from `InBed`;
- `InBed` is never added to staged asleep duration;
- Awake, Core, Deep, REM, and Unspecified durations remain separately visible;
- a session with `InBed` but no asleep interval is labeled **In Bed only**;
- the session is assigned to its wake-up day.

These rules avoid the common error of adding a full `InBed` interval to the stages nested inside it.

## Coverage and point limits

Tracked-day coverage is `days with at least one supported aggregate / calendar days in the selected range`. Sleep coverage uses distinct logged wake-up days. A missing day remains missing.

Headline calculations use every selected record. Trend responses are deterministically limited to 240 points while preserving the first and last point, preventing multi-year responses from returning thousands of chart samples.

## Current limits

- Combined activity is an explainable estimate and may differ from Apple Health totals.
- Source/device filters apply to metric endpoints; sleep is currently combined through interval union.
- Correlations and narrative insight rules are Phase 4 work.
- Workouts, route details, and ECG visualization are Phase 5 work.
