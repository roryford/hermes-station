# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini ca-certificates git curl jq file gnupg \
    && curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 --retry-max-time 60 https://cli.github.com/packages/githubcli-archive-keyring.gpg \
         -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
         > /etc/apt/sources.list.d/github-cli.list \
    && curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 --retry-max-time 60 https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
         | gpg --dearmor -o /usr/share/keyrings/nodesource.gpg \
    && chmod go+r /usr/share/keyrings/nodesource.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_24.x nodistro main" \
         > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
         gosu \
         gh \
         nodejs \
         chromium \
         ffmpeg \
         tesseract-ocr tesseract-ocr-eng \
         ripgrep \
         fd-find \
         sqlite3 \
         poppler-utils \
         xz-utils \
         # operator-diagnostics toolbelt (see test_container_toolbelt.py)
         procps \
         tmux \
         less \
         tree \
         unzip \
         zip \
         rsync \
    && ln -sf /usr/bin/fdfind /usr/local/bin/fd \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Pinned upstream binaries not in Debian repos at a useful version.
# Versions + SHA256s live in scripts/pinned-binaries.tsv (bump there).
# Copied from build-context root (CLI 0.12.3 can't read subdirs — see CLAUDE.md).
COPY pinned-binaries.tsv install_pinned_binaries.sh /tmp/
RUN chmod +x /tmp/install_pinned_binaries.sh \
    && /tmp/install_pinned_binaries.sh \
    && rm /tmp/install_pinned_binaries.sh /tmp/pinned-binaries.tsv

WORKDIR /app

# Pinned upstream — tracked by Renovate's regex manager (see renovate.json5).
# hermes-webui is fetched at build time because it has no pyproject.toml,
# so it can't be installed via pip. The control plane reads it from /opt/hermes-webui at runtime.
ARG HERMES_WEBUI_VERSION=v0.51.134
ARG HERMES_WEBUI_SHA=4ea762ae0dbbc2350cd86fe40c1a8a3c7223e605
RUN git clone --depth 1 --branch "${HERMES_WEBUI_VERSION}" \
        https://github.com/nesquena/hermes-webui.git /opt/hermes-webui \
    && actual="$(git -C /opt/hermes-webui rev-parse HEAD)"; \
       if [ "$actual" != "${HERMES_WEBUI_SHA}" ]; then \
         echo "SECURITY: hermes-webui commit mismatch: expected ${HERMES_WEBUI_SHA}, got $actual" >&2; \
         exit 1; \
       fi \
    && rm -rf /opt/hermes-webui/.git

COPY pyproject.toml README.md LICENSE ./
COPY hermes_station/__init__.py /app/hermes_station/__init__.py

# No BuildKit cache mount: Railway's metal builder requires service-specific
# cache IDs that can't be interpolated from ARG/env vars.
RUN uv pip install --system --link-mode=copy ".[hermes]" -r /opt/hermes-webui/requirements.txt \
        pandas numpy pillow openpyxl pypdf \
        pytest ruff \
        "hindsight-all-slim" "pg0-embedded" \
    && mkdir -p /data/.hermes /data/webui /data/workspace /data/.hindsight

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

ADD extension.tar /opt/hermes-station/
ADD hermes_station.tar /app/

ENV HOME=/data \
    HERMES_HOME=/data/.hermes \
    HERMES_CONFIG_PATH=/data/.hermes/config.yaml \
    HERMES_WEBUI_STATE_DIR=/data/webui \
    HERMES_WORKSPACE_DIR=/data/workspace \
    HERMES_GATEWAY_AUTOSTART=auto \
    HERMES_WEBUI_SRC=/opt/hermes-webui \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    PYTHONSAFEPATH=1 \
    PYTHONPATH=/app \
    PORT=8787

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -sf http://localhost:8787/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/hermes-entrypoint"]
CMD ["python", "-m", "hermes_station"]

# --- version metadata (kept at bottom so revision changes don't bust deps cache) ---
ARG RAILWAY_GIT_COMMIT_SHA=
ARG IMAGE_REVISION=${RAILWAY_GIT_COMMIT_SHA:-dev}
ENV HERMES_WEBUI_VERSION=${HERMES_WEBUI_VERSION}
RUN station=$(python3 -c "from importlib.metadata import version; print(version('hermes-station'))" 2>/dev/null || echo n/a) \
    && agent=$(python3 -c "from importlib.metadata import version; print(version('hermes-agent'))" 2>/dev/null || echo n/a) \
    && echo "${IMAGE_REVISION}" > /etc/hermes-station-build \
    && printf '\n=== hermes-station built ===\n  station : %s\n  revision: %s\n  webui   : %s\n  agent   : %s\n===========================\n\n' \
         "$station" "${IMAGE_REVISION}" "${HERMES_WEBUI_VERSION}" "$agent"
LABEL org.opencontainers.image.source="https://github.com/roryford/hermes-station"
LABEL org.opencontainers.image.revision="${IMAGE_REVISION}"
LABEL org.opencontainers.image.version="${HERMES_WEBUI_VERSION}"

# Harden: strip write bits from app code so the hermes user can't modify it.
# /data is NOT chowned here — entrypoint fixes ownership before dropping to hermes.
COPY hermes-entrypoint.sh /usr/local/bin/hermes-entrypoint
RUN site_pkgs="$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")" \
    && python3 -m compileall -q /app "$site_pkgs" \
    && useradd -u 10000 -d /data -s /sbin/nologin -M hermes \
    && chmod -R a-w "$site_pkgs" /opt/hermes-webui /app /opt/uv-tools \
    && chmod +x /usr/local/bin/hermes-entrypoint

# --- test stage (not shipped to prod) ---
FROM runtime AS test
COPY uv.lock ./
ADD tests.tar /app/
ADD docs.tar /app/
RUN uv export --only-group dev --frozen --no-hashes --no-header \
    | uv pip install --system --link-mode=copy -r /dev/stdin
CMD ["python", "-m", "pytest", "tests/", "-q", "--no-cov"]

# --- default stage ---
FROM runtime
