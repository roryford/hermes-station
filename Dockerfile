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

# himalaya (Rust email CLI) — not in Debian repos; pinned upstream.
# Bump version + both sha256s together.
ARG HIMALAYA_VERSION=v1.2.0
ARG HIMALAYA_SHA256_AMD64=e04e6382e3e664ef34b01afa1a2216113194a2975d2859727647b22d9b36d4e4
ARG HIMALAYA_SHA256_ARM64=643020b220991fac67726f3be11310fcf806e757feadbbab3efbddd713597872
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) hm_arch=x86_64-linux; hm_sha="$HIMALAYA_SHA256_AMD64" ;; \
      arm64) hm_arch=aarch64-linux; hm_sha="$HIMALAYA_SHA256_ARM64" ;; \
      *) echo "unsupported arch for himalaya: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 --retry-max-time 60 \
         -o /tmp/himalaya.tgz "https://github.com/pimalaya/himalaya/releases/download/${HIMALAYA_VERSION}/himalaya.${hm_arch}.tgz"; \
    echo "${hm_sha}  /tmp/himalaya.tgz" | sha256sum -c -; \
    tar -xzf /tmp/himalaya.tgz -C /tmp himalaya; \
    install -m 0755 /tmp/himalaya /usr/local/bin/himalaya; \
    rm /tmp/himalaya.tgz /tmp/himalaya

# pandoc (Haskell document converter) — not in Debian repos at a useful version; pinned upstream.
# Bump version + both sha256s together.
ARG PANDOC_VERSION=3.9.0.2
ARG PANDOC_SHA256_AMD64=a69abfababda8a56969a254b09f9553a7be89ddec00d4e0fe9fd585d71a67508
ARG PANDOC_SHA256_ARM64=b6d21e8f9c3b15744f5a7ab40248019157ed7793875dbe0383d4c82ff572b528
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) pd_arch=amd64; pd_sha="$PANDOC_SHA256_AMD64" ;; \
      arm64) pd_arch=arm64; pd_sha="$PANDOC_SHA256_ARM64" ;; \
      *) echo "unsupported arch for pandoc: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 --retry-max-time 60 \
         -o /tmp/pandoc.tgz "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-linux-${pd_arch}.tar.gz"; \
    echo "${pd_sha}  /tmp/pandoc.tgz" | sha256sum -c -; \
    tar -xzf /tmp/pandoc.tgz -C /tmp "pandoc-${PANDOC_VERSION}/bin/pandoc"; \
    install -m 0755 "/tmp/pandoc-${PANDOC_VERSION}/bin/pandoc" /usr/local/bin/pandoc; \
    rm -rf /tmp/pandoc.tgz "/tmp/pandoc-${PANDOC_VERSION}"

# typst (Rust typesetting system) — not in Debian repos; pinned upstream.
# Bump version + both sha256s together.
ARG TYPST_VERSION=v0.14.2
ARG TYPST_SHA256_AMD64=a6044cbad2a954deb921167e257e120ac0a16b20339ec01121194ff9d394996d
ARG TYPST_SHA256_ARM64=491b101aa40a3a7ea82a3f8a6232cabb4e6a7e233810082e5ac812d43fdcd47a
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) ty_arch=x86_64-unknown-linux-musl ;; \
      arm64) ty_arch=aarch64-unknown-linux-musl ;; \
      *) echo "unsupported arch for typst: $arch" >&2; exit 1 ;; \
    esac; \
    case "$arch" in \
      amd64) ty_sha="$TYPST_SHA256_AMD64" ;; \
      arm64) ty_sha="$TYPST_SHA256_ARM64" ;; \
    esac; \
    curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 --retry-max-time 60 \
         -o /tmp/typst.tar.xz "https://github.com/typst/typst/releases/download/${TYPST_VERSION}/typst-${ty_arch}.tar.xz"; \
    echo "${ty_sha}  /tmp/typst.tar.xz" | sha256sum -c -; \
    tar -xJf /tmp/typst.tar.xz -C /tmp "typst-${ty_arch}/typst"; \
    install -m 0755 "/tmp/typst-${ty_arch}/typst" /usr/local/bin/typst; \
    rm -rf /tmp/typst.tar.xz "/tmp/typst-${ty_arch}"

# tirith (Rust terminal-security binary) — not in Debian repos; pinned upstream.
# Bump version + both sha256s together.
ARG TIRITH_VERSION=v0.3.1
ARG TIRITH_SHA256_AMD64=571e6a300e4c444293476537a322666069e561c7f05283d6650f5b8ef83db3ac
ARG TIRITH_SHA256_ARM64=0462fe5083b4c72c45a8de918d5413e21d17aa8077aa7dbe53c0876b112847bb
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) tr_arch=x86_64-unknown-linux-gnu; tr_sha="$TIRITH_SHA256_AMD64" ;; \
      arm64) tr_arch=aarch64-unknown-linux-gnu; tr_sha="$TIRITH_SHA256_ARM64" ;; \
      *) echo "unsupported arch for tirith: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 --retry-max-time 60 \
         -o /tmp/tirith.tgz "https://github.com/sheeki03/tirith/releases/download/${TIRITH_VERSION}/tirith-${tr_arch}.tar.gz"; \
    echo "${tr_sha}  /tmp/tirith.tgz" | sha256sum -c -; \
    tar -xzf /tmp/tirith.tgz -C /tmp tirith; \
    install -m 0755 /tmp/tirith /usr/local/bin/tirith; \
    rm /tmp/tirith.tgz /tmp/tirith

WORKDIR /app

# Pinned upstream — tracked by Renovate's regex manager (see renovate.json5).
# hermes-webui is fetched at build time because it has no pyproject.toml,
# so it can't be installed via pip. The control plane reads it from /opt/hermes-webui at runtime.
ARG HERMES_WEBUI_VERSION=v0.51.118
ARG HERMES_WEBUI_SHA=e091e65d56fba42f350a95f3308d6d43b3627a87
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
    && mkdir -p /data/.hermes /data/webui /data/workspace

# Patch: hermes-agent 0.14.0 wheel omits plugin.yaml files; restore them.
# Remove once upstream PRs #27240/#27268 merge and we bump the pin.
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

# MCP servers installed as root-owned globals (not npx/uvx) so MCP subprocesses
# can't write to their own package tree. See CONTRACT.md §3.6.
ARG MCP_SERVER_FILESYSTEM_VERSION=2026.1.14
ARG MCP_SERVER_GITHUB_VERSION=2025.4.8
ARG MCP_SERVER_FETCH_VERSION=2025.4.7
RUN set -eux; \
    npm install -g --no-audit --no-fund \
        "@modelcontextprotocol/server-filesystem@${MCP_SERVER_FILESYSTEM_VERSION}" \
        "@modelcontextprotocol/server-github@${MCP_SERVER_GITHUB_VERSION}"; \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
        uv tool install "mcp-server-fetch==${MCP_SERVER_FETCH_VERSION}"; \
    rm -rf /tmp/uv-cache /root/.cache/uv /root/.npm; \
    test -x /usr/bin/mcp-server-filesystem; \
    test -x /usr/bin/mcp-server-github; \
    test -x /usr/local/bin/mcp-server-fetch; \
    echo "MCP servers installed (filesystem=${MCP_SERVER_FILESYSTEM_VERSION}, github=${MCP_SERVER_GITHUB_VERSION}, fetch=${MCP_SERVER_FETCH_VERSION})"

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
RUN site_pkgs="$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")" \
    && python3 -m compileall -q /app "$site_pkgs" \
    && useradd -u 10000 -d /data -s /sbin/nologin -M hermes \
    && chmod -R a-w "$site_pkgs" /opt/hermes-webui /app /opt/uv-tools \
    && printf '#!/bin/sh\nset -e\nchown -R 10000 /data\nexec gosu hermes "$@"\n' \
         > /usr/local/bin/hermes-entrypoint \
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
