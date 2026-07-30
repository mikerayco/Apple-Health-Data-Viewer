from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
import sqlite3
import tempfile
import unittest

from apple_health_viewer.analytics import (
    AnalyticsError,
    category_summary,
    insights_summary,
    metric_summary,
    open_health_database,
    overview_summary,
    resolve_window,
    sleep_summary,
)
from apple_health_viewer.health_store import create_health_database, parse_export
from apple_health_viewer.metrics import (
    METRICS,
    AggregationCancelled,
    build_aggregates,
    display_value,
    normalize_record,
)
from apple_health_viewer.sources import SourceSpec, open_export

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "synthetic" / "apple_health_export"


class MetricRegistryTests(unittest.TestCase):
    def test_unit_normalization_and_display_conversions(self) -> None:
        mass = normalize_record(
            "HKQuantityTypeIdentifierBodyMass",
            "lb",
            "220.462262",
            "2024-01-01 08:00:00 -0500",
            "2024-01-01 08:00:00 -0500",
        )
        glucose = normalize_record(
            "HKQuantityTypeIdentifierBloodGlucose",
            "mmol/L",
            "5",
            "2024-01-01 08:00:00 +0000",
            "2024-01-01 08:00:00 +0000",
        )
        distance = normalize_record(
            "HKQuantityTypeIdentifierDistanceWalkingRunning",
            "mi",
            "1",
            "2024-01-01 08:00:00 +0000",
            "2024-01-01 09:00:00 +0000",
        )

        self.assertAlmostEqual(mass.numeric_value, 100, places=5)
        self.assertAlmostEqual(glucose.numeric_value, 90.091, places=3)
        self.assertAlmostEqual(distance.numeric_value, 1609.344, places=3)
        self.assertEqual(mass.local_start_date, "2024-01-01")
        self.assertEqual(mass.start_utc, "2024-01-01T13:00:00Z")
        self.assertAlmostEqual(display_value(METRICS["body_mass"], 70, "imperial")[0], 154.324, places=3)
        self.assertAlmostEqual(display_value(METRICS["blood_glucose"], 90, "metric")[0], 4.995, places=3)

    def test_aggregation_cancellation_is_cooperative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = create_health_database(Path(directory) / "health.sqlite3")
            with closing(connection):
                with self.assertRaises(AggregationCancelled):
                    build_aggregates(connection, lambda: True)

    def test_phase_two_snapshot_requires_transactional_reimport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "health.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE manifest(id INTEGER PRIMARY KEY, schema_version INTEGER)")
                connection.execute("INSERT INTO manifest VALUES (1, 1)")
                connection.commit()
            with self.assertRaisesRegex(AnalyticsError, "Re-import"):
                open_health_database(database)

    def test_invalid_value_unit_and_timezone_are_explicit(self) -> None:
        invalid_value = normalize_record(
            "HKQuantityTypeIdentifierBodyMass",
            "kg",
            "not-a-number",
            "2024-01-01 08:00:00 +0000",
            "2024-01-01 08:00:00 +0000",
        )
        invalid_unit = normalize_record(
            "HKQuantityTypeIdentifierBodyMass",
            "synthetic-unit",
            "70",
            "2024-01-01 08:00:00 +0000",
            "2024-01-01 08:00:00 +0000",
        )
        invalid_time = normalize_record(
            "HKQuantityTypeIdentifierBodyMass", "kg", "70", "2024-01-01", "2024-01-01"
        )
        self.assertEqual(invalid_value.issue, "invalid_metric_value")
        self.assertEqual(invalid_unit.issue, "unsupported_metric_unit")
        self.assertEqual(invalid_time.issue, "invalid_metric_timestamp")


class GoldenAggregationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "health.sqlite3"
        with open_export(SourceSpec("configured_path", FIXTURE, "Synthetic export")) as opened:
            parse_export(
                opened.stream,
                self.database,
                total_bytes=opened.total_bytes,
                source_files=opened.source_files,
                progress=lambda *_: None,
                cancelled=lambda: False,
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_overlapping_activity_uses_max_rate_and_preserves_sources(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            combined = connection.execute(
                """
                SELECT value_sum, sample_count, covered_seconds, overlap_seconds, is_estimate
                FROM daily_metrics
                WHERE metric_key = 'steps' AND local_date = '2024-01-02' AND scope = 'combined'
                """
            ).fetchone()
            sources = connection.execute(
                """
                SELECT s.name, d.value_sum
                FROM daily_metrics d JOIN sources s ON s.id = d.source_key
                WHERE d.metric_key = 'steps' AND d.scope = 'source'
                ORDER BY s.name
                """
            ).fetchall()

        self.assertEqual(combined, (1500.0, 3, 7200.0, 3600.0, 1))
        self.assertEqual(sources, [("Synthetic Phone", 1300.0), ("Synthetic Watch", 1000.0)])

    def test_partial_overlap_cross_midnight_and_in_bed_only_session(self) -> None:
        xml = b'''<?xml version="1.0"?>
<HealthData locale="en_US">
 <ExportDate value="2024-01-03 12:00:00 +0000"/><Me/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="Synthetic A" unit="count" value="100" startDate="2024-01-01 23:30:00 +0000" endDate="2024-01-02 00:30:00 +0000"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="Synthetic B" unit="count" value="80" startDate="2024-01-02 00:00:00 +0000" endDate="2024-01-02 01:00:00 +0000"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Synthetic A" value="HKCategoryValueSleepAnalysisInBed" startDate="2024-01-02 12:00:00 +0000" endDate="2024-01-02 13:00:00 +0000"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Synthetic A" value="HKCategoryValueSleepAnalysisAwake" startDate="2024-01-02 20:00:00 +0000" endDate="2024-01-02 21:00:00 +0000"/>
</HealthData>'''
        database = Path(self.temporary.name) / "intervals.sqlite3"
        parse_export(
            BytesIO(xml),
            database,
            total_bytes=len(xml),
            source_files=[],
            progress=lambda *_: None,
            cancelled=lambda: False,
        )
        with closing(sqlite3.connect(database)) as connection:
            days = connection.execute(
                """
                SELECT local_date, value_sum, overlap_seconds FROM daily_metrics
                WHERE metric_key = 'steps' AND scope = 'combined' ORDER BY local_date
                """
            ).fetchall()
            in_bed_only = connection.execute(
                "SELECT in_bed_seconds, asleep_seconds, in_bed_only FROM sleep_sessions"
            ).fetchone()
            session_count = connection.execute("SELECT COUNT(*) FROM sleep_sessions").fetchone()[0]

        self.assertEqual(days, [("2024-01-01", 50.0, 0.0), ("2024-01-02", 90.0, 1800.0)])
        self.assertEqual(in_bed_only, (3600.0, 0.0, 1))
        self.assertEqual(session_count, 1)

    def test_missing_in_bed_records_remain_unknown_not_zero(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE sleep_sessions SET in_bed_seconds = 0, in_bed_only = 0")
            connection.commit()
        with closing(open_health_database(self.database)) as connection:
            window = resolve_window(
                connection, period="custom", start="2024-01-02", end="2024-01-02"
            )
            sleep = sleep_summary(connection, window)
        self.assertEqual(sleep["in_bed_nights"], 0)
        self.assertEqual(sleep["in_bed_coverage_percent"], 0.0)
        self.assertIsNone(sleep["total_in_bed_hours"])
        self.assertIsNone(sleep["trend"][0]["in_bed_hours"])

    def test_sleep_unions_stages_and_assigns_wake_day(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            session = connection.execute(
                """
                SELECT wake_date, asleep_seconds, in_bed_seconds, core_seconds,
                       deep_seconds, rem_seconds, in_bed_only
                FROM sleep_sessions
                """
            ).fetchone()

        self.assertEqual(session, ("2024-01-02", 7 * 3600, 8 * 3600, 4 * 3600, 1.5 * 3600, 1.5 * 3600, 0))

    def test_metric_and_sleep_summaries_are_unit_switchable(self) -> None:
        with closing(open_health_database(self.database)) as connection:
            window = resolve_window(connection, period="custom", start="2024-01-02", end="2024-01-02")
            steps = metric_summary(connection, "steps", window, "metric")
            phone_id = next(
                item["id"] for item in steps["filters"]["sources"] if item["name"] == "Synthetic Phone"
            )
            phone_steps = metric_summary(connection, "steps", window, "metric", source_id=phone_id)
            phone_device_id = steps["filters"]["devices"][0]["id"]
            device_steps = metric_summary(
                connection, "steps", window, "metric", device_id=phone_device_id
            )
            glucose_metric = metric_summary(connection, "blood_glucose", window, "metric")
            glucose_imperial = metric_summary(connection, "blood_glucose", window, "imperial")
            body = metric_summary(connection, "body_mass", window, "imperial")
            sleep = sleep_summary(connection, window)

        self.assertEqual(steps["value"], 1500.0)
        self.assertEqual(steps["estimated_days"], 1)
        self.assertEqual(steps["overlap_seconds"], 3600.0)
        self.assertEqual(phone_steps["value"], 1300.0)
        self.assertEqual(phone_steps["scope"], "source")
        self.assertEqual(device_steps["value"], 1300.0)
        self.assertEqual(device_steps["scope"], "device")
        self.assertEqual(glucose_metric["formatted_value"], "5.0 mmol/L")
        self.assertEqual(glucose_metric["median"], 4.995)
        self.assertEqual(glucose_metric["meal_context"], "Before meal")
        self.assertEqual(glucose_metric["readings"][0]["value"], 4.995)
        self.assertEqual(glucose_metric["readings"][0]["meal_context"], "Before meal")
        self.assertEqual(glucose_imperial["formatted_value"], "90 mg/dL")
        self.assertEqual(body["formatted_value"], "154.3 lb")
        self.assertEqual(sleep["average_asleep_hours"], 7.0)
        self.assertEqual(sleep["total_in_bed_hours"], 8.0)

    def test_overview_and_category_hide_unavailable_metrics(self) -> None:
        with closing(open_health_database(self.database)) as connection:
            window = resolve_window(connection, period="all")
            overview = overview_summary(connection, window, "metric")
            activity = category_summary(connection, "activity", window, "metric")
            heart = category_summary(connection, "heart", window, "metric")

        self.assertGreaterEqual(len(overview["cards"]), 4)
        self.assertEqual(overview["coverage"]["tracked_days"], 1)
        self.assertEqual([item["key"] for item in activity["metrics"]], ["steps"])
        self.assertIn("active_energy", activity["unavailable"])
        self.assertEqual(activity["goals"]["days_with_goals"], 1)
        self.assertEqual(activity["goals"]["exercise_met_days"], 1)
        self.assertEqual(activity["goals"]["active_energy_met_days"], 0)
        heart_keys = {item["key"] for item in heart["metrics"]}
        self.assertIn("blood_pressure_systolic", heart_keys)
        self.assertIn("blood_pressure_diastolic", heart_keys)

    def test_insights_are_coverage_gated_and_non_causal(self) -> None:
        start = date(2023, 12, 20)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM daily_metrics WHERE metric_key IN ('steps', 'resting_heart_rate')")
            for index in range(14):
                day = (start + timedelta(days=index)).isoformat()
                connection.execute(
                    """
                    INSERT INTO daily_metrics(
                      metric_key, local_date, scope, source_key, value_sum, sample_count,
                      covered_seconds, overlap_seconds, is_estimate
                    ) VALUES ('steps', ?, 'combined', 0, ?, 1, 3600, 0, 0)
                    """,
                    (day, 1000 + index * 100),
                )
                connection.execute(
                    """
                    INSERT INTO daily_metrics(
                      metric_key, local_date, scope, source_key, value_mean, value_min,
                      value_max, latest_value, latest_at, sample_count, covered_seconds,
                      overlap_seconds, is_estimate
                    ) VALUES ('resting_heart_rate', ?, 'combined', 0, ?, ?, ?, ?, ?, 1, 0, 0, 0)
                    """,
                    (day, 55 + index, 55 + index, 55 + index, 55 + index, f"{day}T08:00:00Z"),
                )
            connection.commit()

        with closing(open_health_database(self.database)) as connection:
            window = resolve_window(
                connection, period="custom", start="2023-12-27", end="2024-01-02"
            )
            insights = insights_summary(connection, window, "metric")

        by_key = {item["key"]: item for item in insights}
        self.assertIn("period-steps", by_key)
        self.assertIn("streak-steps", by_key)
        correlation = by_key["correlation-steps-resting_heart_rate"]
        self.assertEqual(correlation["sample_count"], 7)
        self.assertEqual(correlation["coverage_percent"], 100.0)
        self.assertEqual(correlation["sample_label"], "paired days")
        self.assertEqual(correlation["note"], "Correlation does not imply causation.")
        self.assertNotRegex(" ".join(item["text"] for item in insights), r"healthy|dangerous|caused")

    def test_cumulative_insight_requires_matching_tracked_day_coverage(self) -> None:
        start = date(2023, 12, 20)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM daily_metrics WHERE metric_key = 'steps'")
            connection.executemany(
                """
                INSERT INTO daily_metrics(
                  metric_key, local_date, scope, source_key, value_sum, sample_count,
                  covered_seconds, overlap_seconds, is_estimate
                ) VALUES ('steps', ?, 'combined', 0, 1000, 1, 3600, 0, 0)
                """,
                (((start + timedelta(days=index)).isoformat(),) for index in range(14) if index != 2),
            )
            connection.commit()
        with closing(open_health_database(self.database)) as connection:
            window = resolve_window(
                connection, period="custom", start="2023-12-27", end="2024-01-02"
            )
            insights = insights_summary(connection, window, "metric", category="activity")
        self.assertNotIn("period-steps", {item["key"] for item in insights})

    def test_sleep_average_uses_logged_nights_not_session_count(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """
                INSERT INTO sleep_sessions(
                  wake_date, start_utc, end_utc, asleep_seconds, in_bed_seconds,
                  awake_seconds, core_seconds, deep_seconds, rem_seconds,
                  unspecified_seconds, sample_count, source_count, in_bed_only
                ) VALUES ('2024-01-02', '2024-01-02T13:00:00Z', '2024-01-02T14:00:00Z',
                          3600, 3600, 0, 3600, 0, 0, 0, 1, 1, 0)
                """
            )
            connection.execute(
                """
                INSERT INTO sleep_sessions(
                  wake_date, start_utc, end_utc, asleep_seconds, in_bed_seconds,
                  awake_seconds, core_seconds, deep_seconds, rem_seconds,
                  unspecified_seconds, sample_count, source_count, in_bed_only
                ) VALUES ('2024-01-03', '2024-01-02T23:00:00Z', '2024-01-03T07:00:00Z',
                          0, 28800, 0, 0, 0, 0, 0, 1, 1, 1)
                """
            )
            connection.commit()
        with closing(open_health_database(self.database)) as connection:
            window = resolve_window(
                connection, period="custom", start="2024-01-02", end="2024-01-03"
            )
            sleep = sleep_summary(connection, window)
        self.assertEqual(sleep["sessions"], 3)
        self.assertEqual(sleep["logged_nights"], 2)
        self.assertEqual(sleep["measured_nights"], 1)
        self.assertEqual(sleep["average_asleep_hours"], 8.0)
        self.assertEqual(sleep["trend"][0]["asleep_hours"], 8.0)
        self.assertIsNone(sleep["trend"][1]["asleep_hours"])

    def test_sparse_ranges_do_not_generate_insights(self) -> None:
        with closing(open_health_database(self.database)) as connection:
            window = resolve_window(
                connection, period="custom", start="2024-01-02", end="2024-01-02"
            )
            self.assertEqual(insights_summary(connection, window, "metric"), [])

    def test_immediately_preceding_period_comparison(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """
                INSERT INTO daily_metrics(
                  metric_key, local_date, scope, source_key, value_sum, sample_count,
                  covered_seconds, overlap_seconds, is_estimate
                ) VALUES ('steps', '2024-01-01', 'combined', 0, 1000, 1, 3600, 0, 0)
                """
            )
            connection.commit()
        with closing(open_health_database(self.database)) as connection:
            window = resolve_window(
                connection, period="custom", start="2024-01-02", end="2024-01-02"
            )
            summary = metric_summary(connection, "steps", window, "metric")

        self.assertEqual(summary["comparison"]["prior_value"], 1000.0)
        self.assertEqual(summary["comparison"]["absolute"], 500.0)
        self.assertEqual(summary["comparison"]["percent"], 50.0)
        self.assertEqual(summary["comparison"]["basis"], "2024-01-01 to 2024-01-01")

    def test_grouped_trends_use_calendar_bucket_boundaries(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM daily_metrics WHERE metric_key = 'steps'")
            connection.executemany(
                """
                INSERT INTO daily_metrics(
                  metric_key, local_date, scope, source_key, value_sum, sample_count,
                  covered_seconds, overlap_seconds, is_estimate
                ) VALUES ('steps', ?, 'combined', 0, 1000, 1, 3600, 0, 0)
                """,
                (("2024-01-02",), ("2024-01-22",)),
            )
            connection.commit()
        with closing(open_health_database(self.database)) as connection:
            window = resolve_window(
                connection, period="custom", start="2024-01-02", end="2024-01-22"
            )
            summary = metric_summary(connection, "steps", window, "metric", granularity="week")
        self.assertEqual(
            [(point["date"], point["end_date"]) for point in summary["trend"]],
            [("2024-01-01", "2024-01-07"), ("2024-01-22", "2024-01-28")],
        )

    def test_in_bed_only_nights_do_not_qualify_sleep_correlations(self) -> None:
        start = date(2024, 1, 1)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM daily_metrics WHERE metric_key = 'steps'")
            connection.execute("DELETE FROM sleep_stages")
            connection.execute("DELETE FROM sleep_sessions")
            for index in range(7):
                day = (start + timedelta(days=index)).isoformat()
                connection.execute(
                    """
                    INSERT INTO daily_metrics(
                      metric_key, local_date, scope, source_key, value_sum, sample_count,
                      covered_seconds, overlap_seconds, is_estimate
                    ) VALUES ('steps', ?, 'combined', 0, ?, 1, 3600, 0, 0)
                    """,
                    (day, 1000 + index * 100),
                )
                asleep = 0 if index == 0 else (6 + index / 10) * 3600
                connection.execute(
                    """
                    INSERT INTO sleep_sessions(
                      wake_date, start_utc, end_utc, asleep_seconds, in_bed_seconds,
                      awake_seconds, core_seconds, deep_seconds, rem_seconds,
                      unspecified_seconds, sample_count, source_count, in_bed_only
                    ) VALUES (?, ?, ?, ?, 28800, 0, 0, 0, 0, 0, 1, 1, ?)
                    """,
                    (day, f"{day}T00:00:00Z", f"{day}T08:00:00Z", asleep, int(asleep == 0)),
                )
            connection.commit()
        with closing(open_health_database(self.database)) as connection:
            window = resolve_window(
                connection, period="custom", start="2024-01-01", end="2024-01-07"
            )
            insights = insights_summary(connection, window, "metric", category="sleep")
        self.assertNotIn("correlation-steps-sleep", {item["key"] for item in insights})

    def test_decimation_preserves_real_gaps_without_creating_artificial_ones(self) -> None:
        start = date(2023, 1, 1)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM daily_metrics WHERE metric_key = 'steps'")
            connection.executemany(
                """
                INSERT INTO daily_metrics(
                  metric_key, local_date, scope, source_key, value_sum, sample_count,
                  covered_seconds, overlap_seconds, is_estimate
                ) VALUES ('steps', ?, 'combined', 0, 1000, 1, 3600, 0, 0)
                """,
                (((start + timedelta(days=index)).isoformat(),) for index in range(300)),
            )
            connection.commit()
        with closing(open_health_database(self.database)) as connection:
            window = resolve_window(connection, period="all")
            continuous = metric_summary(connection, "steps", window, "metric", granularity="day")
        self.assertEqual(len(continuous["trend"]), 240)
        self.assertFalse(any(point["gap_before"] for point in continuous["trend"]))

        missing = (start + timedelta(days=150)).isoformat()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "DELETE FROM daily_metrics WHERE metric_key = 'steps' AND local_date = ?",
                (missing,),
            )
            connection.commit()
        with closing(open_health_database(self.database)) as connection:
            window = resolve_window(connection, period="all")
            interrupted = metric_summary(connection, "steps", window, "metric", granularity="day")
        self.assertTrue(any(point["gap_before"] for point in interrupted["trend"]))

    def test_trends_are_deterministically_limited(self) -> None:
        start = date(2023, 1, 1)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """
                INSERT INTO daily_metrics(
                  metric_key, local_date, scope, source_key, value_sum, sample_count,
                  covered_seconds, overlap_seconds, is_estimate
                ) VALUES ('steps', ?, 'combined', 0, ?, 1, 3600, 0, 0)
                """,
                (
                    ((start + timedelta(days=index)).isoformat(), float(index + 1))
                    for index in range(300)
                ),
            )
            connection.commit()
        with closing(open_health_database(self.database)) as connection:
            window = resolve_window(connection, period="all")
            summary = metric_summary(connection, "steps", window, "metric")
            daily = metric_summary(connection, "steps", window, "metric", granularity="day")

        self.assertEqual(summary["granularity"], "week")
        self.assertLess(len(summary["trend"]), 240)
        self.assertEqual(len(daily["trend"]), 240)
        self.assertEqual(daily["trend"][0]["date"], "2023-01-01")
        self.assertEqual(daily["trend"][-1]["date"], "2024-01-02")

    def test_invalid_ranges_and_unknown_metrics_are_rejected(self) -> None:
        with closing(open_health_database(self.database)) as connection:
            with self.assertRaisesRegex(AnalyticsError, "start date"):
                resolve_window(connection, period="custom", start="2024-02-01", end="2024-01-01")
            window = resolve_window(connection, period="all")
            with self.assertRaisesRegex(AnalyticsError, "not supported"):
                metric_summary(connection, "synthetic_unknown", window, "metric")
            with self.assertRaisesRegex(AnalyticsError, "automatic chart grouping"):
                metric_summary(connection, "steps", window, "metric", granularity="hour")
