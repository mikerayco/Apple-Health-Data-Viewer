# Windows installation

## Requirements

- 64-bit CPython 3.12–3.14 with the `py` launcher.
- PowerShell and enough free space for staging and processed data.

## Install from a clean clone

```powershell
git clone https://github.com/mikerayco/Apple-Health-Data-Viewer.git
cd Apple-Health-Data-Viewer
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
apple-health-viewer
```

Open <http://127.0.0.1:8787>. If PowerShell blocks activation, either use a process-scoped execution policy approved for your machine or call `.venv\Scripts\python.exe` directly. Do not disable security policy system-wide for this app.

## Data location and removal

Application data is stored at:

```text
%LOCALAPPDATA%\Apple Health Data Viewer\
```

Use **Settings → Remove processed health data** to remove the processed database while retaining preferences and retained uploads. Delete retained uploads separately from **Manage imports**.

For a complete reset, stop the viewer, open `%LOCALAPPDATA%`, and delete the `Apple Health Data Viewer` directory. This removes settings, retained exports, processed data, and the session key. Recycle Bin, File History, or backup software may retain copies.

## Troubleshooting

- Verify Python with `py -3.12 --version`.
- If port 8787 is occupied, start with `apple-health-viewer --port 8788`.
- Close other viewer windows before replacing or removing a database if Windows reports that a file is in use.
- Long or protected source paths can be avoided by placing the export in a user-owned directory.
- Prefer a configured path for very large exports to avoid an upload copy.
