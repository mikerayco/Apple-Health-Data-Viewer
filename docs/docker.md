# Docker

Native execution is the default. Docker Compose is an optional cross-platform alternative.

## Start

```bash
docker compose up --build
```

Open <http://127.0.0.1:8787>. Compose publishes the container only on the host loopback interface and stores settings in the `viewer-data` volume.

## Use an existing export path

Add a read-only bind mount and container path to a local Compose override:

```yaml
services:
  viewer:
    volumes:
      - viewer-data:/data
      - /absolute/path/to/apple_health_export:/health-data:ro
    environment:
      AHV_HEALTH_DATA_PATH: /health-data
```

On Windows or macOS, ensure Docker Desktop is permitted to read the selected host folder. The app must never write to this mount.

## Privacy boundary

- Host publication remains `127.0.0.1:8787:8787`.
- The container runs as an unprivileged user.
- The root filesystem is read-only; `/data` is the only persistent writable volume.
- No health source is included in the image or default build context.
- Do not use `-p 8787:8787`, which may publish the port on every host interface.

## Stop

```bash
docker compose down
```

Removing the `viewer-data` volume also removes local app settings and, in future phases, processed health data. Treat that as destructive and back up intentionally.
