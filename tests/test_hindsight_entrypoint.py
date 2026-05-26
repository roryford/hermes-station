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
    assert "127.0.0.1" in src
    assert "HINDSIGHT_API_HOST" in src


def test_entrypoint_sidecar_default_port_8888() -> None:
    src = ENTRYPOINT.read_text()
    assert "8888" in src


def test_entrypoint_exports_hindsight_vars_for_agent_inheritance() -> None:
    """All HINDSIGHT_API_* vars must be exported, not just inline-assigned.

    Inline assignments (KEY=value \\ cmd) only reach the sidecar process.
    hermes-agent child processes (e.g. hindsight-embed daemon) inherit the
    environment of the main station process, so the vars must be exported
    before exec gosu hermes.
    """
    src = ENTRYPOINT.read_text()
    required = [
        "HINDSIGHT_API_LLM_API_KEY",
        "HINDSIGHT_API_LLM_PROVIDER",
        "HINDSIGHT_API_EMBEDDINGS_PROVIDER",
        "HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY",
        "HINDSIGHT_API_RERANKER_PROVIDER",
    ]
    for var in required:
        assert f"export {var}" in src, (
            f"{var} must be exported (not just inline-assigned) so hermes-agent inherits it"
        )
