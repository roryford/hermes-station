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

exec gosu hermes "$@"
