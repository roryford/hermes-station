#!/bin/sh
set -e

chown -R 10000 /data

_patched=0

if [ -n "${HERMES_PATCH_AGENT_VERSION}" ]; then
    _baked="$(python3 -c "from importlib.metadata import version; print(version('hermes-agent'))" 2>/dev/null || echo unknown)"
    echo "hermes-entrypoint: upgrading hermes-agent ${_baked} → ${HERMES_PATCH_AGENT_VERSION}"
    uv pip install --system --link-mode=copy --quiet \
        "hermes-agent==${HERMES_PATCH_AGENT_VERSION}"
    _patched=1
fi

if [ -n "${HERMES_PATCH_WEBUI_VERSION}" ]; then
    _baked="${HERMES_WEBUI_VERSION:-unknown}"
    echo "hermes-entrypoint: upgrading hermes-webui ${_baked} → ${HERMES_PATCH_WEBUI_VERSION}"
    rm -rf /opt/hermes-webui
    git clone --depth 1 --branch "${HERMES_PATCH_WEBUI_VERSION}" \
        https://github.com/nesquena/hermes-webui.git /opt/hermes-webui
    rm -rf /opt/hermes-webui/.git
    uv pip install --system --link-mode=copy --quiet -r /opt/hermes-webui/requirements.txt
    _patched=1
fi

if [ "${_patched}" = "1" ]; then
    _site_pkgs="$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")"
    python3 -m compileall -q "${_site_pkgs}" /opt/hermes-webui 2>/dev/null || true
    chmod -R a-w "${_site_pkgs}" /opt/hermes-webui
fi

if [ "${HINDSIGHT_SIDECAR}" = "1" ] || [ "${HINDSIGHT_SIDECAR}" = "true" ]; then
    echo "hermes-entrypoint: hindsight sidecar enabled"
    if [ -z "${OPENROUTER_API_KEY}" ]; then
        echo "hermes-entrypoint: WARNING: HINDSIGHT_SIDECAR=1 but OPENROUTER_API_KEY is not set — sidecar not started"
    else
        mkdir -p /data/.hindsight
        # Runs as hermes user; pg0 data is stored in /data/.pg0 (inside the Railway volume).
        HINDSIGHT_API_DATABASE_URL="${HINDSIGHT_API_DATABASE_URL:-pg0://hindsight-hermes}" \
        HINDSIGHT_API_HOST="${HINDSIGHT_API_HOST:-127.0.0.1}" \
        HINDSIGHT_API_PORT="${HINDSIGHT_API_PORT:-8888}" \
        HINDSIGHT_API_LLM_PROVIDER="${HINDSIGHT_API_LLM_PROVIDER:-openrouter}" \
        HINDSIGHT_API_LLM_MODEL="${HINDSIGHT_API_LLM_MODEL:-openai/gpt-4o-mini}" \
        HINDSIGHT_API_LLM_API_KEY="${OPENROUTER_API_KEY}" \
        HINDSIGHT_API_EMBEDDINGS_PROVIDER="${HINDSIGHT_API_EMBEDDINGS_PROVIDER:-openai}" \
        HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY="${OPENROUTER_API_KEY}" \
        HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL="${HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL:-https://openrouter.ai/api/v1}" \
        HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL="${HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL:-text-embedding-3-small}" \
        HINDSIGHT_API_RERANKER_PROVIDER="${HINDSIGHT_API_RERANKER_PROVIDER:-rrf}" \
            gosu hermes hindsight-api >> /data/.hindsight/api.log 2>&1 &
        echo "hermes-entrypoint: hindsight-api started (PID $!, port=${HINDSIGHT_API_PORT:-8888}, db=${HINDSIGHT_API_DATABASE_URL:-pg0://hindsight-hermes})"
    fi
fi

exec gosu hermes "$@"
