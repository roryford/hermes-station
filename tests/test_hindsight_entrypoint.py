"""Host-runnable structural tests for the Hindsight sidecar entrypoint block.

These parse scripts/hermes-entrypoint.sh to verify the guard conditions and
key configuration values are present. They run without a container.
"""

from __future__ import annotations

from pathlib import Path

ENTRYPOINT = Path(__file__).parent.parent / "scripts" / "hermes-entrypoint.sh"


def test_entrypoint_has_api_key_guard() -> None:
    src = ENTRYPOINT.read_text()
    assert "OPENROUTER_API_KEY is not set" in src
    assert "HINDSIGHT_SIDECAR" in src


def test_entrypoint_sidecar_uses_pg0_db() -> None:
    src = ENTRYPOINT.read_text()
    assert "pg0://hindsight-hermes" in src


def test_entrypoint_sidecar_binds_loopback() -> None:
    src = ENTRYPOINT.read_text()
    assert 'HINDSIGHT_API_HOST="127.0.0.1"' in src


def test_entrypoint_sidecar_default_port_8888() -> None:
    src = ENTRYPOINT.read_text()
    assert "8888" in src
