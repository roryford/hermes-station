"""Verify that hermes-agent and hermes-webui versions are readable at runtime.

Runs inside the container (test image) — not host-runnable.
"""

from importlib.metadata import version as pkg_version


def test_hermes_agent_version_readable() -> None:
    v = pkg_version("hermes-agent")
    assert v and v != "unknown", f"unexpected hermes-agent version: {v!r}"


def test_hermes_agent_version_is_semver() -> None:
    v = pkg_version("hermes-agent")
    parts = v.split(".")
    assert len(parts) >= 2, f"version does not look like semver: {v!r}"
    assert all(p.isdigit() for p in parts[:2]), f"version does not look like semver: {v!r}"
