# macOS installation

## Requirements

- macOS with CPython 3.12–3.14 available as `python3`.
- Enough free space for the source export, a temporary upload copy, the staged database, and the active database.

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

Open <http://127.0.0.1:8787>. Keep the default loopback address.

## Data location and removal

Application data is stored at:

```text
~/Library/Application Support/Apple Health Data Viewer/
```

Use **Settings → Remove processed health data** to remove `health.sqlite3` while retaining preferences and retained uploads. Delete retained uploads separately from **Manage imports**.

For a complete reset, stop the viewer and move the entire directory above to Trash. This removes settings, retained exports, processed data, and the session key. Empty Trash only when recovery is no longer needed. Time Machine or other backups may retain copies.

## Troubleshooting

- If `python3 --version` is below 3.12, install a supported Python from python.org or a maintained package manager.
- If the browser cannot connect, confirm the terminal still shows the viewer running and no other process uses port 8787. Use `--port 8788` when needed.
- macOS may ask for permission to read Desktop, Documents, or an external drive when using a configured path. Grant access only to the export location.
- Prefer a configured local path for multi-gigabyte exports to avoid an upload copy.
