# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pinned upstream — tracked by Renovate's regex manager (see renovate.json5).
# hermes-webui is fetched at build time because it has no pyproject.toml,
# so it can't be installed via pip. The control plane reads it from /opt/hermes-webui at runtime.
ARG HERMES_WEBUI_VERSION=v0.51.50
RUN git clone --depth 1 --branch "${HERMES_WEBUI_VERSION}" \
        https://github.com/nesquena/hermes-webui.git /opt/hermes-webui \
    && rm -rf /opt/hermes-webui/.git

# Copy only the metadata + a stub package up front so the dependency-install
# layer below caches across code-only changes. The real source is copied last.
COPY pyproject.toml README.md LICENSE ./
COPY hermes_station/__init__.py /app/hermes_station/__init__.py

# Single resolve covering hermes-station's deps, the `hermes` extra (pulls
# hermes-agent), and hermes-webui's runtime requirements. The BuildKit cache
# mount keeps the uv wheel cache around between builds without bloating the
# image; the layer itself caches as long as pyproject.toml, __init__.py, and
# HERMES_WEBUI_VERSION are unchanged.
#
# The cache mount `id` is in Railway's required `s/<service-id>-<path>` format
# so the Railway builder accepts it. Other BuildKit instances (GHA, local
# Docker) ignore the prefix and treat the id as opaque, so this works
# everywhere. Service ID is for the `hermes-all-in-one` service in the
# `perpetual-courtesy` project; change if redeploying under a new service.
RUN --mount=type=cache,target=/root/.cache/uv,id=s/fc796d07-dc86-467e-8269-1b6a6472ce3b-/root/.cache/uv \
    uv pip install --system ".[hermes]" -r /opt/hermes-webui/requirements.txt \
    && mkdir -p /data/.hermes /data/webui /data/workspace

# Copy the real source last. At runtime `python -m hermes_station` runs from
# WORKDIR=/app, so /app/hermes_station/ shadows the stub installed above.
COPY hermes_station/ /app/hermes_station/

# HERMES_WEBUI_AGENT_DIR is intentionally not set here — hermes_station/webui.py
# defaults it to the Python site-packages dir at process start, where pip installs
# the hermes-agent source tree (including run_agent.py).
ENV HOME=/data \
    HERMES_HOME=/data/.hermes \
    HERMES_CONFIG_PATH=/data/.hermes/config.yaml \
    HERMES_WEBUI_STATE_DIR=/data/webui \
    HERMES_WORKSPACE_DIR=/data/workspace \
    HERMES_GATEWAY_AUTOSTART=auto \
    HERMES_WEBUI_SRC=/opt/hermes-webui \
    PYTHONUNBUFFERED=1 \
    PORT=8787

EXPOSE 8787

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "hermes_station"]
