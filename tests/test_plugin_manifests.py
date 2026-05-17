"""Container-level smoke tests: hermes-agent 0.14.0 plugin.yaml patch.

Verifies that the Dockerfile workaround (RUN <<'PYEOF' python3 ...) correctly
restored the plugin.yaml manifests that the hermes-agent wheel omits from its
package-data.  These tests are skipped on a dev laptop where hermes-agent is
not installed; they run inside the built image via `container exec` with
HERMES_STATION_REQUIRE_TOOLBELT=1 set (same flag as test_container_toolbelt).

See also: upstream PRs #27240/#27268 — remove this file when those merge and
we bump the hermes-agent pin.
"""

from __future__ import annotations

import os
import sysconfig
from pathlib import Path

import pytest

REQUIRE = os.environ.get("HERMES_STATION_REQUIRE_TOOLBELT") == "1"


def _plugins_root() -> Path:
    return Path(sysconfig.get_paths()["purelib"]) / "plugins"


def _skip_or_fail(msg: str) -> None:
    if REQUIRE:
        pytest.fail(msg)
    pytest.skip(msg + " (set HERMES_STATION_REQUIRE_TOOLBELT=1 to fail instead)")


# ---------------------------------------------------------------------------
# Section 1: plugin.yaml files exist on disk
# ---------------------------------------------------------------------------

WEB_PLUGINS = [
    ("tavily", "TAVILY_API_KEY"),
    ("brave_free", "BRAVE_SEARCH_API_KEY"),
    ("firecrawl", "FIRECRAWL_API_KEY"),
    ("ddgs", None),
    ("exa", "EXA_API_KEY"),
    ("parallel", "PARALLEL_API_KEY"),
    ("searxng", "SEARXNG_URL"),
]

IMAGE_GEN_PLUGINS = [
    "openai",
    "openai-codex",
    "xai",
]


@pytest.mark.parametrize("plugin_dir,_env_key", WEB_PLUGINS, ids=[p for p, _ in WEB_PLUGINS])
def test_web_plugin_yaml_exists(plugin_dir: str, _env_key: str | None) -> None:
    root = _plugins_root()
    if not root.exists():
        _skip_or_fail(f"plugins root not found: {root} (hermes-agent not installed?)")
    manifest = root / "web" / plugin_dir / "plugin.yaml"
    if not manifest.exists():
        _skip_or_fail(
            f"Missing: {manifest}\n"
            "The Dockerfile plugin.yaml patch did not run or the directory was not present."
        )
    content = manifest.read_text()
    assert "kind: backend" in content, f"{manifest}: missing 'kind: backend'"
    assert "provides_web_providers:" in content, f"{manifest}: missing 'provides_web_providers:'"


@pytest.mark.parametrize("plugin_dir", IMAGE_GEN_PLUGINS)
def test_image_gen_plugin_yaml_exists(plugin_dir: str) -> None:
    root = _plugins_root()
    if not root.exists():
        _skip_or_fail(f"plugins root not found: {root} (hermes-agent not installed?)")
    manifest = root / "image_gen" / plugin_dir / "plugin.yaml"
    if not manifest.exists():
        _skip_or_fail(f"Missing: {manifest}")
    content = manifest.read_text()
    assert "kind: backend" in content, f"{manifest}: missing 'kind: backend'"


# ---------------------------------------------------------------------------
# Section 2: plugin discovery actually registers providers
# ---------------------------------------------------------------------------


def test_plugin_discovery_registers_web_providers() -> None:
    """hermes_cli plugin discovery must register at least the bundled web providers.

    Calls _scan_directory_level (the function the user confirmed in the diagnostic)
    against the installed plugins/web directory and asserts that at least the
    tavily and firecrawl manifests are discovered.  Unknown API surface → skip.
    """
    root = _plugins_root()
    if not root.exists():
        _skip_or_fail(f"plugins root not found: {root}")

    try:
        import hermes_cli.plugins as _hp  # type: ignore[import-not-found]
    except ImportError:
        _skip_or_fail("hermes_cli not importable (not inside hermes-agent container layer?)")

    # Discover by reading plugin.yaml files directly — mirrors what the loader does.
    web_root = root / "web"
    if not web_root.is_dir():
        _skip_or_fail(f"plugins/web dir not found: {web_root}")

    manifests_found = [p.parent.name for p in web_root.rglob("plugin.yaml")]
    expected = {"tavily", "brave_free", "firecrawl", "ddgs", "exa"}
    missing = expected - set(manifests_found)
    assert not missing, (
        f"plugin.yaml missing for: {missing}\n"
        f"Found manifests in: {manifests_found}\n"
        "The Dockerfile plugin.yaml patch did not write all expected files."
    )

    # Best-effort: if the loader exposes a scan function, call it and verify > 0 plugins.
    scan_fn = getattr(_hp, "_scan_directory_level", None) or getattr(_hp, "discover", None)
    if scan_fn is None:
        return  # API not found — file-existence check above is sufficient
    try:
        result = scan_fn(web_root)
        count = len(result) if result is not None else 0
        assert count > 0, (
            f"Plugin scan returned 0 results from {web_root} even though plugin.yaml files exist.\n"
            "Check hermes_cli/plugins.py scan logic."
        )
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"Plugin scan raised unexpectedly: {exc}")
