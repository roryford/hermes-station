# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pinned upstream — tracked by Renovate's regex manager (see renovate.json5).
# hermes-webui is fetched at build time because it has no pyproject.toml,
# so it can't be installed via pip. The control plane reads it from /opt/hermes-webui at runtime.
ARG HERMES_WEBUI_VERSION=v0.51.44
RUN git clone --depth 1 --branch "${HERMES_WEBUI_VERSION}" \
        https://github.com/nesquena/hermes-webui.git /opt/hermes-webui \
    && rm -rf /opt/hermes-webui/.git

COPY pyproject.toml README.md LICENSE ./
COPY hermes_station/ /app/hermes_station/

# Install hermes-station + its dependencies (including pinned hermes-agent via git).
# Also install hermes-webui's runtime requirements into the system Python so the in-process
# mount works in Phase 1.
RUN uv pip install --system --no-cache ".[hermes]" \
    && uv pip install --system --no-cache -r /opt/hermes-webui/requirements.txt \
    && mkdir -p /data/.hermes /data/webui /data/workspace

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
