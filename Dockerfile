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
         ffmpeg \
         tesseract-ocr tesseract-ocr-eng \
         ripgrep \
         fd-find \
         sqlite3 \
         poppler-utils \
         # operator-diagnostics toolbelt — required for a shareable image
         # (see test_container_toolbelt.py and HERMES_CONTAINER_REQUIREMENTS §4)
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
    curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 --retry-max-time 60 -o /tmp/yq "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_${yq_arch}"; \
    echo "${yq_sha}  /tmp/yq" | sha256sum -c -; \
    install -m 0755 /tmp/yq /usr/local/bin/yq; \
    rm /tmp/yq

WORKDIR /app

# Pinned upstream — tracked by Renovate's regex manager (see renovate.json5).
# hermes-webui is fetched at build time because it has no pyproject.toml,
# so it can't be installed via pip. The control plane reads it from /opt/hermes-webui at runtime.
ARG HERMES_WEBUI_VERSION=v0.51.92
ARG HERMES_WEBUI_SHA=71c70352c113c57bef959b751e276c38b2c6caf1
RUN git clone --depth 1 --branch "${HERMES_WEBUI_VERSION}" \
        https://github.com/nesquena/hermes-webui.git /opt/hermes-webui \
    && actual="$(git -C /opt/hermes-webui rev-parse HEAD)"; \
       if [ "$actual" != "${HERMES_WEBUI_SHA}" ]; then \
         echo "SECURITY: hermes-webui commit mismatch: expected ${HERMES_WEBUI_SHA}, got $actual" >&2; \
         exit 1; \
       fi \
    && rm -rf /opt/hermes-webui/.git

# Copy only the metadata + a stub package up front so the dependency-install
# layer below caches across code-only changes. The real source is copied last.
COPY pyproject.toml README.md LICENSE ./
COPY hermes_station/__init__.py /app/hermes_station/__init__.py

# Single resolve covering hermes-station's deps, the `hermes` extra (pulls
# hermes-agent), and hermes-webui's runtime requirements. The Docker layer
# cache handles reuse — layer is reused as long as pyproject.toml,
# __init__.py, and HERMES_WEBUI_VERSION are unchanged.
#
# No BuildKit cache mount: Railway's metal builder requires
# id=s/<service-id>-<path>, which is service-specific and cannot be
# interpolated from ARG/env vars. Dropping the mount keeps this Dockerfile
# portable across forks and fresh Railway services.
RUN uv pip install --system --link-mode=copy ".[hermes]" -r /opt/hermes-webui/requirements.txt \
    && mkdir -p /data/.hermes /data/webui /data/workspace

# Patch: restore plugin.yaml manifests omitted from the hermes-agent 0.14.0 wheel.
# setuptools package-data doesn't include *.yaml under plugins/, so the plugin
# discovery system (hermes_cli/plugins.py) skips every bundled backend and
# web_search / web_extract / image_gen all fail with "No provider configured".
# Remove this step once upstream PRs #27240 / #27268 merge and we bump the pin.
RUN <<'PYEOF' python3
import pathlib, sysconfig

root = pathlib.Path(sysconfig.get_paths()["purelib"]) / "plugins"

MANIFESTS: dict[str, str] = {
    "web/tavily/plugin.yaml": (
        "name: web-tavily\nversion: 1.0.0\n"
        "description: 'Tavily web search + content extraction + crawl. Requires TAVILY_API_KEY.'\n"
        "author: NousResearch\nkind: backend\nprovides_web_providers:\n  - tavily\n"
    ),
    "web/brave_free/plugin.yaml": (
        "name: web-brave-free\nversion: 1.0.0\n"
        "description: 'Brave Search (free tier). Requires BRAVE_SEARCH_API_KEY.'\n"
        "author: NousResearch\nkind: backend\nprovides_web_providers:\n  - brave-free\n"
    ),
    "web/firecrawl/plugin.yaml": (
        "name: web-firecrawl\nversion: 1.0.0\n"
        "description: 'Firecrawl web search + content extraction. Requires FIRECRAWL_API_KEY.'\n"
        "author: NousResearch\nkind: backend\nprovides_web_providers:\n  - firecrawl\n"
    ),
    "web/ddgs/plugin.yaml": (
        "name: web-ddgs\nversion: 1.0.0\n"
        "description: 'DuckDuckGo web search via ddgs. No API key required.'\n"
        "author: NousResearch\nkind: backend\nprovides_web_providers:\n  - ddgs\n"
    ),
    "web/exa/plugin.yaml": (
        "name: web-exa\nversion: 1.0.0\n"
        "description: 'Exa web search and content extraction. Requires EXA_API_KEY.'\n"
        "author: NousResearch\nkind: backend\nprovides_web_providers:\n  - exa\n"
    ),
    "web/parallel/plugin.yaml": (
        "name: web-parallel\nversion: 1.0.0\n"
        "description: 'Parallel.ai web search + extraction. Requires PARALLEL_API_KEY.'\n"
        "author: NousResearch\nkind: backend\nprovides_web_providers:\n  - parallel\n"
    ),
    "web/searxng/plugin.yaml": (
        "name: web-searxng\nversion: 1.0.0\n"
        "description: 'SearXNG self-hosted metasearch. Requires SEARXNG_URL.'\n"
        "author: NousResearch\nkind: backend\nprovides_web_providers:\n  - searxng\n"
    ),
    "image_gen/openai/plugin.yaml": (
        "name: openai\nversion: 1.0.0\n"
        "description: 'OpenAI image generation (gpt-image-2). Requires OPENAI_API_KEY.'\n"
        "author: NousResearch\nkind: backend\nrequires_env:\n  - OPENAI_API_KEY\n"
    ),
    "image_gen/openai-codex/plugin.yaml": (
        "name: openai-codex\nversion: 1.0.0\n"
        "description: 'OpenAI image generation via ChatGPT/Codex OAuth.'\n"
        "author: NousResearch\nkind: backend\n"
    ),
    "image_gen/xai/plugin.yaml": (
        "name: xai\nversion: 1.0.0\n"
        "description: 'xAI image generation (grok-imagine-image). Requires XAI_API_KEY.'\n"
        "author: Julien Talbot\nkind: backend\nrequires_env:\n  - XAI_API_KEY\n"
    ),
}

for rel, content in MANIFESTS.items():
    dest = root / rel
    if dest.parent.is_dir() and not dest.exists():
        dest.write_text(content)
        print(f"restored: {dest}")
PYEOF

# Install the curated stdio MCP servers as global, root-owned binaries on
# PATH. Versions are pinned and surfaced in hermes_station/config.py
# (MCP_SERVER_CATALOG) — bumping is one ARG change here + one literal change
# in config.py, kept in lockstep.
#
# Why globals (not npx/uvx)? Both launchers stage their package tree into
# writable cache dirs (npx: $NPM_CONFIG_CACHE/_npx/<hash>/, defaulting to
# $HOME/.npm/_npx/ which is /data/.npm/_npx/ under HERMES_HOME). The MCP
# subprocess then loads JS/Python from a path the runtime user can write
# to — code-execution from writable state. `npm install -g` puts binaries
# under /usr/lib/node_modules with symlinks at /usr/bin/, and uv with
# UV_TOOL_BIN_DIR=/usr/local/bin does the same for fetch. All bins land
# root-owned, not chmod'd writable, and PATH-resolves to a non-writable
# location at runtime. See CONTRACT.md §4.4.
ARG MCP_SERVER_FILESYSTEM_VERSION=2025.8.21
ARG MCP_SERVER_GITHUB_VERSION=2025.4.8
ARG MCP_SERVER_FETCH_VERSION=2025.4.7
RUN set -eux; \
    npm install -g --no-audit --no-fund \
        "@modelcontextprotocol/server-filesystem@${MCP_SERVER_FILESYSTEM_VERSION}" \
        "@modelcontextprotocol/server-github@${MCP_SERVER_GITHUB_VERSION}"; \
    # uv tool install puts the env in UV_TOOL_DIR and links bins into
    # UV_TOOL_BIN_DIR. /opt/uv-tools is root-owned and not chowned to
    # hermes later in this Dockerfile, so the installed env (and the
    # symlinked entrypoint) stay read-only to the runtime user.
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
        uv tool install "mcp-server-fetch==${MCP_SERVER_FETCH_VERSION}"; \
    rm -rf /tmp/uv-cache /root/.cache/uv /root/.npm; \
    # Sanity: bins must resolve to a non-writable location.
    test -x /usr/bin/mcp-server-filesystem; \
    test -x /usr/bin/mcp-server-github; \
    test -x /usr/local/bin/mcp-server-fetch; \
    echo "MCP servers installed (filesystem=${MCP_SERVER_FILESYSTEM_VERSION}, github=${MCP_SERVER_GITHUB_VERSION}, fetch=${MCP_SERVER_FETCH_VERSION})"

# Pilot admin extension bundle. Copied above the source layer so an edit to
# hermes_station/ doesn't invalidate the extension layer, and so an extension
# edit doesn't invalidate the (much heavier) `uv pip install` layer above.
COPY extension/ /opt/hermes-station/extension/

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
    PYTHONNOUSERSITE=1 \
    PYTHONSAFEPATH=1 \
    PYTHONPATH=/app \
    PORT=8787

EXPOSE 8787

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

# Harden: prevent the agent from modifying its own application code at runtime.
# Pre-compile /app so Python doesn't need __pycache__ write access, then strip
# write bits from site-packages, the webui source, the app source tree, and
# the uv-tools MCP env. /data (all agent state) stays writable. Running as a
# non-root user is what makes the chmod effective — root has DAC_OVERRIDE
# and ignores file permission bits.
#
# /data is NOT chowned here: bind-mounted volumes ignore image-layer ownership,
# so the entrypoint script fixes /data ownership at container start before
# dropping to the hermes user via gosu.
RUN site_pkgs="$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")" \
    && python3 -m compileall -q /app \
    && useradd -u 10000 -d /data -s /sbin/nologin -M hermes \
    && chmod -R a-w "$site_pkgs" /opt/hermes-webui /app /opt/uv-tools \
    && printf '#!/bin/sh\nset -e\nchown -R 10000 /data\nexec gosu hermes "$@"\n' \
         > /usr/local/bin/hermes-entrypoint \
    && chmod +x /usr/local/bin/hermes-entrypoint

# --- test stage (not shipped to prod) ---
# Extends runtime with dev deps + test suite so the full test suite
# (unit + toolbelt + e2e) can run inside the image via `exec`.
FROM runtime AS test
COPY uv.lock ./
COPY tests/ /app/tests/
COPY docs/ /app/docs/
# Install pinned dev deps from the lockfile — faster than resolution and
# deterministic. uv export reads uv.lock directly, no network needed.
RUN uv export --only-group dev --frozen --no-hashes --no-header \
    | uv pip install --system --link-mode=copy -r /dev/stdin
CMD ["python", "-m", "pytest", "tests/", "-q", "--no-cov"]

# --- default stage ---
# Plain `docker build .` (no --target) must produce the runtime image.
# Railway has no dockerTarget config — it always builds the final stage.
FROM runtime
