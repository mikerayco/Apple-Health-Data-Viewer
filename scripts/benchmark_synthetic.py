#!/usr/bin/env python3
"""Run a privacy-safe scalable import and query benchmark with invented records."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import time
import tracemalloc

from apple_health_viewer.analytics import open_health_database, overview_summary, resolve_window
from apple_health_viewer.health_store import parse_export
from apple_health_viewer.version import HEALTH_SCHEMA_VERSION, __version__


def write_export(path: Path, records: int) -> None:
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        output.write('<?xml version="1.0" encoding="UTF-8"?>\n<HealthData locale="en_US">\n')
        output.write('<ExportDate value="2025-01-01 00:00:00 +0000"/><Me/>\n')
        for index in range(records):
            recorded = start + timedelta(minutes=index)
            timestamp = recorded.strftime("%Y-%m-%d %H:%M:%S +0000")
            output.write(
                '<Record type="HKQuantityTypeIdentifierStepCount" sourceName="Synthetic Benchmark" '
                f'unit="count" value="1" startDate="{timestamp}" endDate="{timestamp}"/>\n'
            )
        output.write("</HealthData>\n")


def benchmark(records: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ahv-synthetic-benchmark-") as directory:
        root = Path(directory)
        source = root / "synthetic.xml"
        database = root / "health.sqlite3"
        write_export(source, records)

        tracemalloc.start()
        started = time.perf_counter()
        with source.open("rb") as stream:
            result = parse_export(
                stream,
                database,
                total_bytes=source.stat().st_size,
                source_files=[("synthetic.xml", source.stat().st_size)],
                progress=lambda *_: None,
                cancelled=lambda: False,
            )
        import_seconds = time.perf_counter() - started
        _current, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        query_started = time.perf_counter()
        with closing(open_health_database(database)) as connection:
            window = resolve_window(connection, period="all")
            overview_summary(connection, window, "metric")
        query_ms = (time.perf_counter() - query_started) * 1000

        return {
            "application_version": __version__,
            "health_schema": HEALTH_SCHEMA_VERSION,
            "generated_xml_bytes": source.stat().st_size,
            "requested_records": records,
            "stored_records": result.record_count,
            "database_bytes": database.stat().st_size,
            "import_seconds": round(import_seconds, 3),
            "overview_query_ms": round(query_ms, 3),
            "peak_python_heap_bytes": peak_bytes,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=100_000)
    parser.add_argument("--max-seconds", type=float, help="optional import regression ceiling")
    parser.add_argument("--max-query-ms", type=float, default=500.0)
    args = parser.parse_args()
    if not 1 <= args.records <= 1_000_000:
        parser.error("--records must be between 1 and 1,000,000")

    result = benchmark(args.records)
    print(json.dumps(result, sort_keys=True))
    if args.max_seconds is not None and float(result["import_seconds"]) > args.max_seconds:
        return 1
    if float(result["overview_query_ms"]) > args.max_query_ms:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
