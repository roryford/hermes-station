# syntax=docker/dockerfile:1.7

# The heavy, slowly-changing system layer (chromium/ffmpeg/tesseract/node +
# pinned upstream binaries) lives in a separately-published base image so it
# isn't rebuilt or re-cached on every code change — see Dockerfile.base and
# .github/workflows/base-image.yml. Bump this tag after republishing the base.
ARG BASE_IMAGE=ghcr.io/roryford/hermes-station-base:v1
FROM ${BASE_IMAGE} AS runtime

WORKDIR /app

# Pinned upstream — tracked by Renovate's regex manager (see renovate.json5).
# hermes-webui is fetched at build time; it has no pyproject.toml and is run
# directly as server.py from /opt/hermes-webui at runtime.
ARG HERMES_WEBUI_VERSION=v0.51.145
ARG HERMES_WEBUI_SHA=329debcd33969c4386a72f14d91e38c0e82d0b8e
RUN git clone --depth 1 --branch "${HERMES_WEBUI_VERSION}" \
        https://github.com/nesquena/hermes-webui.git /opt/hermes-webui \
    && actual="$(git -C /opt/hermes-webui rev-parse HEAD)"; \
       if [ "$actual" != "${HERMES_WEBUI_SHA}" ]; then \
         echo "SECURITY: hermes-webui commit mismatch: expected ${HERMES_WEBUI_SHA}, got $actual" >&2; \
         exit 1; \
       fi \
    && rm -rf /opt/hermes-webui/.git

# No BuildKit cache mount: Railway's metal builder requires service-specific
# cache IDs that can't be interpolated from ARG/env vars.
# Pinned upstream — tracked by Renovate's regex manager (see renovate.json5).
ARG HERMES_AGENT_VERSION=0.14.0
RUN uv pip install --system --link-mode=copy \
        "hermes-agent[messaging]==${HERMES_AGENT_VERSION}" \
        -r /opt/hermes-webui/requirements.txt \
        pandas numpy pillow openpyxl pypdf \
        pytest \
        "hindsight-all-slim" "pg0-embedded" \
        supervisor

# Patch: hermes-agent 0.14.0 wheel omits plugin.yaml files; restore them.
# Remove once upstream PRs #27240/#27268 merge and we bump the pin.
COPY patch_plugin_manifests.py /tmp/
RUN python3 /tmp/patch_plugin_manifests.py && rm /tmp/patch_plugin_manifests.py

# MCP servers installed as root-owned globals (not npx/uvx) so MCP subprocesses
# can't write to their own package tree. See CONTRACT.md §3.6.
ARG MCP_SERVER_FILESYSTEM_VERSION=2026.1.14
ARG MCP_SERVER_GITHUB_VERSION=2025.4.8
ARG MCP_SERVER_FETCH_VERSION=2025.4.7
ARG AGENT_BROWSER_VERSION=0.27.0
RUN set -eux; \
    npm install -g --no-audit --no-fund \
        "@modelcontextprotocol/server-filesystem@${MCP_SERVER_FILESYSTEM_VERSION}" \
        "@modelcontextprotocol/server-github@${MCP_SERVER_GITHUB_VERSION}" \
        "agent-browser@${AGENT_BROWSER_VERSION}"; \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
        uv tool install "mcp-server-fetch==${MCP_SERVER_FETCH_VERSION}"; \
    rm -rf /tmp/uv-cache /root/.cache/uv /root/.npm; \
    test -x /usr/bin/mcp-server-filesystem; \
    test -x /usr/bin/mcp-server-github; \
    test -x /usr/local/bin/mcp-server-fetch; \
    test -x /usr/bin/agent-browser; \
    echo "MCP servers installed (filesystem=${MCP_SERVER_FILESYSTEM_VERSION}, github=${MCP_SERVER_GITHUB_VERSION}, fetch=${MCP_SERVER_FETCH_VERSION})" && \
    echo "agent-browser installed (${AGENT_BROWSER_VERSION})"

COPY supervisord.conf /etc/supervisord.conf

ENV HOME=/data \
    HERMES_HOME=/data/.hermes \
    HERMES_CONFIG_PATH=/data/.hermes/config.yaml \
    HERMES_WEBUI_STATE_DIR=/data/webui \
    HERMES_WORKSPACE_DIR=/data/workspace \
    HERMES_WEBUI_SRC=/opt/hermes-webui \
    HERMES_WEBUI_HOST=0.0.0.0 \
    HERMES_WEBUI_PORT=8787 \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    PYTHONSAFEPATH=1 \
    PORT=8787

# Bake the hermes-agent site-packages path so webui can import run_agent without discovery.
# The actual mechanism is the `export HERMES_WEBUI_AGENT_DIR` in hermes-entrypoint.sh.
RUN site_pkgs="$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")" \
    && echo "HERMES_WEBUI_AGENT_DIR=${site_pkgs}" >> /etc/environment

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -sf http://localhost:8787/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/hermes-entrypoint"]
CMD ["supervisord", "-n", "-c", "/etc/supervisord.conf"]

# --- version metadata (kept at bottom so revision changes don't bust deps cache) ---
ARG RAILWAY_GIT_COMMIT_SHA=
ARG IMAGE_REVISION=${RAILWAY_GIT_COMMIT_SHA:-dev}
ENV HERMES_WEBUI_VERSION=${HERMES_WEBUI_VERSION}
RUN agent=$(python3 -c "from importlib.metadata import version; print(version('hermes-agent'))" 2>/dev/null || echo n/a) \
    && echo "${IMAGE_REVISION}" > /etc/hermes-station-build \
    && printf '\n=== hermes-station built ===\n  revision: %s\n  webui   : %s\n  agent   : %s\n===========================\n\n' \
         "${IMAGE_REVISION}" "${HERMES_WEBUI_VERSION}" "$agent"
LABEL org.opencontainers.image.source="https://github.com/roryford/hermes-station"
LABEL org.opencontainers.image.revision="${IMAGE_REVISION}"
LABEL org.opencontainers.image.version="${HERMES_WEBUI_VERSION}"

COPY hermes-entrypoint.sh /usr/local/bin/hermes-entrypoint
RUN python3 -m compileall -q /opt/hermes-webui \
    && useradd -u 10000 -d /data -s /sbin/nologin -M hermes \
    && chmod +x /usr/local/bin/hermes-entrypoint
RUN chmod -R a-w "$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")" \
    /opt/hermes-webui /opt/uv-tools 2>/dev/null || true

# --- test stage (not shipped to prod) ---
FROM runtime AS test
ADD tests.tar /app/
ADD docs.tar /app/
CMD ["python", "-m", "pytest", "tests/", "-q", "--no-cov"]

# --- default stage ---
FROM runtime
