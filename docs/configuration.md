# Configuration

Configuration precedence is highest first:

1. command-line options;
2. `AHV_` environment variables;
3. values stored through the local settings interface;
4. platform defaults and first-run setup.

## Command line

```text
apple-health-viewer \
  [--data-dir PATH] \
  [--health-data PATH] \
  [--host ADDRESS] \
  [--port PORT] \
  [--no-browser]
```

The default address is `127.0.0.1:8787`. The app opens the system browser unless `--no-browser` is present.

## Environment

| Variable | Purpose | Default |
|---|---|---|
| `AHV_DATA_DIR` | Settings and processed-data directory | Platform-native path |
| `AHV_HEALTH_DATA_PATH` | Existing export ZIP or folder | Not configured |
| `AHV_HOST` | Bind address | `127.0.0.1` |
| `AHV_PORT` | TCP port | `8787` |
| `AHV_OPEN_BROWSER` | Open browser (`1`/`0`) | `1` |

`.env.example` documents the names, but the app deliberately does not load `.env` files or add a dotenv dependency. Export variables through the shell or operating system.

## Platform data directories

- macOS: `~/Library/Application Support/Apple Health Data Viewer/`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/apple-health-data-viewer/`
- Windows: `%LOCALAPPDATA%\Apple Health Data Viewer\`
- Docker: `/data`

Phase 1 stores `settings.sqlite3` and a generated `session.key`. The key protects the local session/CSRF cookie and should not be committed or shared.

## Network warning

The supported default is loopback only. Binding to another interface can expose the unauthenticated application and future health data to the network. Do not change `AHV_HOST` unless you understand that risk. Remote access is outside the supported v1 security model.
