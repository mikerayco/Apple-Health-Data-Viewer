"""Native command-line launcher."""

from __future__ import annotations

import argparse
from pathlib import Path
import threading
import webbrowser

from . import create_app
from .config import DEFAULT_HOST, DEFAULT_PORT


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Run Apple Health Data Viewer locally.")
    command.add_argument("--data-dir", type=Path, help="directory for local settings and processed data")
    command.add_argument("--health-data", type=Path, help="existing Apple Health ZIP or unzipped folder")
    command.add_argument("--host", help=f"bind address (default: {DEFAULT_HOST})")
    command.add_argument("--port", type=int, help=f"local port (default: {DEFAULT_PORT})")
    command.add_argument("--no-browser", action="store_true", help="do not open the dashboard automatically")
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    overrides = {}
    if args.data_dir:
        overrides["DATA_DIR"] = args.data_dir
    if args.health_data:
        overrides["HEALTH_DATA_PATH"] = args.health_data
    if args.host:
        overrides["HOST"] = args.host
    if args.port is not None:
        if not 1 <= args.port <= 65535:
            parser().error("--port must be between 1 and 65535")
        overrides["PORT"] = args.port
    if args.no_browser:
        overrides["OPEN_BROWSER"] = False

    app = create_app(overrides)
    host = str(app.config["HOST"])
    port = int(app.config["PORT"])
    url_host = "localhost" if host in {"0.0.0.0", "::"} else host.strip("[]")
    url = f"http://{url_host}:{port}"

    if host not in {"127.0.0.1", "localhost", "::1", "[::1]"}:
        print("WARNING: non-loopback binding can expose private health data. Authentication is not available.")
    print(f"Apple Health Data Viewer is available at {url}")
    print("Press Ctrl+C to stop.")

    if app.config["OPEN_BROWSER"]:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()

    app.run(host=host, port=port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
