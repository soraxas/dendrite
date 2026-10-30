# The dendrite server: `docker build -t dendrite-server .`, then run it with a server.toml mounted
# at /etc/dendrite/server.toml (see examples/team). The image never runs pipelines; clients do.

# -- the web UI ----------------------------------------------------------------------------
FROM node:22.20.0-alpine AS ui
WORKDIR /src/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build   # writes ../src/dendrite/ui/dist

# -- the server ----------------------------------------------------------------------------
FROM python:3.12.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml README.md hatch_build.py ./
COPY src/ src/
COPY --from=ui /src/src/dendrite/ui/dist src/dendrite/ui/dist
# Development builds Prefect from a ../prefect checkout ([tool.uv.sources]); the image uses a release.
RUN uv pip install --system --no-cache --no-sources ".[server,s3]" "prefect==3.8.8" \
    && rm -rf /src

RUN useradd --create-home --uid 10001 dendrite && mkdir -p /var/lib/dendrite && chown dendrite /var/lib/dendrite
USER dendrite
ENV DO_NOT_TRACK=1 PREFECT_HOME=/var/lib/dendrite/prefect
EXPOSE 8455
HEALTHCHECK --interval=15s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8455/api/v1/info', timeout=4)"
ENTRYPOINT ["dendrite", "server", "run", "--config", "/etc/dendrite/server.toml", "--host", "0.0.0.0", "--port", "8455"]
