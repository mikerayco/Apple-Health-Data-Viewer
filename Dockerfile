FROM python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TMPDIR=/data/tmp

WORKDIR /app

COPY requirements.lock pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir -r requirements.lock \
    && python -m pip install --no-cache-dir --no-deps --no-build-isolation . \
    && groupadd --system viewer \
    && useradd --system --gid viewer --home-dir /data viewer \
    && mkdir -p /data/tmp \
    && chown -R viewer:viewer /data \
    && chmod 700 /data /data/tmp

USER viewer
EXPOSE 8787

HEALTHCHECK --interval=20s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=2)" || exit 1

CMD ["apple-health-viewer", "--data-dir", "/data", "--host", "0.0.0.0", "--port", "8787", "--no-browser"]
