# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini ca-certificates git curl jq file gnupg \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
         -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
         > /etc/apt/sources.list.d/github-cli.list \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
         | gpg --dearmor -o /usr/share/keyrings/nodesource.gpg \
    && chmod go+r /usr/share/keyrings/nodesource.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_24.x nodistro main" \
         > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
         gh \
         nodejs \
         ffmpeg \
         tesseract-ocr tesseract-ocr-eng \
         ripgrep \
         fd-find \
         sqlite3 \
         poppler-utils \
    && ln -sf /usr/bin/fdfind /usr/local/bin/fd \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# yq (Mike Farah's Go binary) — not in Debian repos at a recent enough version.
# Pinned upstream — bump version + both sha256s together.
ARG YQ_VERSION=v4.53.2
ARG YQ_SHA256_AMD64=d56bf5c6819e8e696340c312bd70f849dc1678a7cda9c2ad63eebd906371d56b
ARG YQ_SHA256_ARM64=03061b2a50c7a498de2bbb92d7cb078ce433011f085a4994117c2726be4106ea
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) yq_arch=amd64; yq_sha="$YQ_SHA256_AMD64" ;; \
      arm64) yq_arch=arm64; yq_sha="$YQ_SHA256_ARM64" ;; \
      *) echo "unsupported arch for yq: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/yq "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_${yq_arch}"; \
    echo "${yq_sha}  /tmp/yq" | sha256sum -c -; \
    install -m 0755 /tmp/yq /usr/local/bin/yq; \
    rm /tmp/yq

WORKDIR /app

# Pinned upstream — tracked by Renovate's regex manager (see renovate.json5).
# hermes-webui is fetched at build time because it has no pyproject.toml,
# so it can't be installed via pip. The control plane reads it from /opt/hermes-webui at runtime.
ARG HERMES_WEBUI_VERSION=v0.51.61
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

# Pre-cache the curated stdio MCP servers so first-toggle isn't a 30s npm/uv
# fetch. Versions are pinned and surfaced in hermes_station/config.py
# (MCP_SERVER_CATALOG) — bumping is one ARG change here + one literal change
# in config.py, kept in lockstep.
#
# Caches go to /opt/mcp-cache/{npm,uv} (HOME-independent) so the runtime
# (HOME=/data) finds them via NPM_CONFIG_CACHE + UV_CACHE_DIR (set below).
# Budget: ~100MB npm + ~80MB uv = ~180MB image growth.
ARG MCP_SERVER_FILESYSTEM_VERSION=2025.8.21
ARG MCP_SERVER_GITHUB_VERSION=2025.4.8
ARG MCP_SERVER_FETCH_VERSION=2025.4.7
ENV NPM_CONFIG_CACHE=/opt/mcp-cache/npm \
    UV_CACHE_DIR=/opt/mcp-cache/uv \
    UV_TOOL_DIR=/opt/mcp-cache/uv-tools
RUN set -eux; \
    mkdir -p "$NPM_CONFIG_CACHE" "$UV_CACHE_DIR" "$UV_TOOL_DIR"; \
    # `npm install -g` puts each server under /usr/lib/node_modules, but the
    # MCP config in this repo invokes them via `npx -y --package=…` which
    # checks the npm cache first. So we just prime the npm cache by running
    # npx once for each — server doesn't have to actually start, the package
    # is downloaded before the binary is invoked. Failure modes (`--help`
    # missing, args missing) are fine since the package fetch is the goal.
    npx -y --package=@modelcontextprotocol/server-filesystem@${MCP_SERVER_FILESYSTEM_VERSION} \
        -- mcp-server-filesystem /tmp >/dev/null 2>&1 & sleep 8; kill %1 2>/dev/null || true; wait || true; \
    npx -y --package=@modelcontextprotocol/server-github@${MCP_SERVER_GITHUB_VERSION} \
        -- mcp-server-github >/dev/null 2>&1 & sleep 8; kill %1 2>/dev/null || true; wait || true; \
    # `uv tool install` puts the env into UV_TOOL_DIR persistently — much
    # cleaner than `uvx` which builds a fresh env per run. The runtime can
    # then `uvx --from mcp-server-fetch==X` and uv reuses the installed env.
    uv tool install "mcp-server-fetch==${MCP_SERVER_FETCH_VERSION}"; \
    chmod -R a+rX /opt/mcp-cache; \
    echo "MCP cache warmed (filesystem=${MCP_SERVER_FILESYSTEM_VERSION}, github=${MCP_SERVER_GITHUB_VERSION}, fetch=${MCP_SERVER_FETCH_VERSION})"

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
