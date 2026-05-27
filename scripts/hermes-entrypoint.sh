#!/bin/sh
set -e

# Ensure data dirs exist on a fresh volume (Railway mounts shadow the image layer).
mkdir -p /data/.hermes /data/webui /data/workspace /data/.hindsight

if [ "$(stat -c %u /data 2>/dev/null)" != "10000" ]; then
    chown -R 10000 /data
    echo "hermes-entrypoint: chown done, /data/.hermes ownership:"
    ls -la /data/.hermes/ 2>&1 || echo "hermes-entrypoint: ls /data/.hermes failed"
fi

# Let Railway's injected PORT win; fall back to the Dockerfile default for local runs.
export HERMES_WEBUI_PORT="${PORT:-8787}"

# Deprecation warning for users migrating from the old Python control plane.
if [ -n "${HERMES_GATEWAY_AUTOSTART}" ]; then
    echo "hermes-entrypoint: WARNING: HERMES_GATEWAY_AUTOSTART is no longer supported."
    echo "hermes-entrypoint: To start the gateway, set HERMES_GATEWAY_ENABLED=1 instead."
fi

_patched=0

if [ -n "${HERMES_PATCH_AGENT_VERSION}" ]; then
    _baked="$(python3 -c "from importlib.metadata import version; print(version('hermes-agent'))" 2>/dev/null || echo unknown)"
    echo "hermes-entrypoint: upgrading hermes-agent ${_baked} → ${HERMES_PATCH_AGENT_VERSION}"
    if uv pip install --system --link-mode=copy --quiet \
            "hermes-agent==${HERMES_PATCH_AGENT_VERSION}"; then
        _installed="$(python3 -c "from importlib.metadata import version; print(version('hermes-agent'))" 2>/dev/null || echo unknown)"
        echo "hermes-entrypoint: hermes-agent patched to ${_installed}"
        _patched=1
    else
        echo "hermes-entrypoint: WARNING: hermes-agent patch failed — continuing with baked version ${_baked}"
    fi
fi

if [ -n "${HERMES_PATCH_WEBUI_VERSION}" ]; then
    _baked="${HERMES_WEBUI_VERSION:-unknown}"
    echo "hermes-entrypoint: upgrading hermes-webui ${_baked} → ${HERMES_PATCH_WEBUI_VERSION}"
    rm -rf /opt/hermes-webui.new
    if git clone --depth 1 --branch "${HERMES_PATCH_WEBUI_VERSION}" \
               https://github.com/nesquena/hermes-webui.git /opt/hermes-webui.new; then
        _resolved="$(git -C /opt/hermes-webui.new rev-parse HEAD 2>/dev/null || echo unknown)"
        if [ -n "${HERMES_PATCH_WEBUI_SHA}" ] && [ "${_resolved}" != "${HERMES_PATCH_WEBUI_SHA}" ]; then
            echo "hermes-entrypoint: SECURITY: hermes-webui commit mismatch: expected ${HERMES_PATCH_WEBUI_SHA}, got ${_resolved}" >&2
            rm -rf /opt/hermes-webui.new
            exit 1
        fi
        rm -rf /opt/hermes-webui /opt/hermes-webui.new/.git
        mv /opt/hermes-webui.new /opt/hermes-webui
        echo "hermes-entrypoint: hermes-webui patched to tag=${HERMES_PATCH_WEBUI_VERSION} sha=${_resolved}"
        uv pip install --system --link-mode=copy --quiet -r /opt/hermes-webui/requirements.txt || true
        _patched=1
    else
        rm -rf /opt/hermes-webui.new
        echo "hermes-entrypoint: WARNING: hermes-webui patch failed — continuing with baked version ${_baked}"
    fi
fi

if [ "${_patched}" = "1" ]; then
    _site_pkgs="$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")"
    python3 -m compileall -q "${_site_pkgs}" /opt/hermes-webui 2>/dev/null || true
    chmod -R a-w "${_site_pkgs}" /opt/hermes-webui 2>/dev/null || true
fi

# Set HERMES_WEBUI_AGENT_DIR so webui can import run_agent without discovery.
HERMES_WEBUI_AGENT_DIR="$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")"
export HERMES_WEBUI_AGENT_DIR
echo "hermes-entrypoint: HERMES_WEBUI_AGENT_DIR=${HERMES_WEBUI_AGENT_DIR}"

if [ "${HINDSIGHT_SIDECAR}" = "1" ] || [ "${HINDSIGHT_SIDECAR}" = "true" ]; then
    echo "hermes-entrypoint: hindsight sidecar enabled"
    if [ -z "${OPENROUTER_API_KEY}" ]; then
        echo "hermes-entrypoint: WARNING: HINDSIGHT_SIDECAR=1 but OPENROUTER_API_KEY is not set — sidecar not started"
    else
        mkdir -p /data/.hindsight
        export HINDSIGHT_API_DATABASE_URL="${HINDSIGHT_API_DATABASE_URL:-pg0://hindsight-hermes}"
        export HINDSIGHT_API_HOST="${HINDSIGHT_API_HOST:-127.0.0.1}"
        export HINDSIGHT_API_PORT="${HINDSIGHT_API_PORT:-8888}"
        export HINDSIGHT_API_LLM_PROVIDER="${HINDSIGHT_API_LLM_PROVIDER:-openrouter}"
        export HINDSIGHT_API_LLM_MODEL="${HINDSIGHT_API_LLM_MODEL:-openai/gpt-4o-mini}"
        export HINDSIGHT_API_LLM_API_KEY="${OPENROUTER_API_KEY}"
        export HINDSIGHT_API_EMBEDDINGS_PROVIDER="${HINDSIGHT_API_EMBEDDINGS_PROVIDER:-openai}"
        export HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY="${OPENROUTER_API_KEY}"
        export HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL="${HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL:-https://openrouter.ai/api/v1}"
        export HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL="${HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL:-text-embedding-3-small}"
        export HINDSIGHT_API_RERANKER_PROVIDER="${HINDSIGHT_API_RERANKER_PROVIDER:-rrf}"
        echo "hermes-entrypoint: hindsight enabled — patching supervisord.conf"
        sed -i '/^\[program:hindsight\]/,/^\[/ s/^autostart=false$/autostart=true/' /etc/supervisord.conf
        echo "hermes-entrypoint: hindsight-api will start on port=${HINDSIGHT_API_PORT:-8888}, db=${HINDSIGHT_API_DATABASE_URL:-pg0://hindsight-hermes}"
    fi
fi

# Enable gateway if requested. Patches supervisord.conf at runtime so the
# gateway program starts with the right environment.
if [ "${HERMES_GATEWAY_ENABLED}" = "1" ] || [ "${HERMES_GATEWAY_ENABLED}" = "true" ]; then
    echo "hermes-entrypoint: gateway enabled — patching supervisord.conf"
    sed -i '/^\[program:gateway\]/,/^\[/ s/^autostart=false$/autostart=true/' /etc/supervisord.conf
fi

# supervisord must run as root to setuid per-program (user=hermes in supervisord.conf).
# All child processes drop to hermes via that directive.
exec "$@"
