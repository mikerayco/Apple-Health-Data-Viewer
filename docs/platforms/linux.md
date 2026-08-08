# Linux installation

## Requirements

- CPython 3.12–3.14, including the distribution package that provides `venv`.
- Enough free space for staging and processed data.

## Install from a clean clone

```bash
git clone https://github.com/mikerayco/Apple-Health-Data-Viewer.git
cd Apple-Health-Data-Viewer
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
apple-health-viewer
```

Open <http://127.0.0.1:8787>. Do not expose the unauthenticated process through a reverse proxy.

## Data location and removal

Application data is stored at:

```text
${XDG_DATA_HOME:-~/.local/share}/apple-health-data-viewer/
```

The directory is restricted to the current account where POSIX permissions are available. Use **Settings → Remove processed health data** to remove `health.sqlite3`; remove retained uploads separately from **Manage imports**.

For a complete reset, stop the viewer and remove the application-data directory above. This removes settings, retained exports, processed data, and the session key. Snapshots or backup tools may retain copies.

## Troubleshooting

- Install your distribution's Python `venv` package if virtual-environment creation fails.
- If the browser does not open, navigate to the loopback URL manually or use `--no-browser`.
- If port 8787 is occupied, start with `apple-health-viewer --port 8788`.
- Sandboxed browsers may not support folder uploads consistently; use ZIP upload or a configured local path.
- Prefer a configured path for multi-gigabyte exports to avoid an upload copy.
