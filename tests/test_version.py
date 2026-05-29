"""Verify that hermes-agent and hermes-webui versions are readable at runtime.

Runs inside the container (test image) — not host-runnable.
"""

from __future__ import annotations

import os
from importlib.metadata import version as pkg_version
from pathlib import Path

import pytest


def test_hermes_agent_version_readable() -> None:
    v = pkg_version("hermes-agent")
    assert v and v != "unknown", f"unexpected hermes-agent version: {v!r}"


def test_hermes_agent_version_is_semver() -> None:
    v = pkg_version("hermes-agent")
    parts = v.split(".")
    assert len(parts) >= 2, f"version does not look like semver: {v!r}"
    assert all(p.isdigit() for p in parts[:2]), f"version does not look like semver: {v!r}"


def test_hermes_agent_version_matches_baked_default() -> None:
    """When not hot-patched, installed agent version must equal HERMES_AGENT_VERSION."""
    baked = os.environ.get("HERMES_AGENT_VERSION")
    if not baked:
        pytest.skip("HERMES_AGENT_VERSION not set in environment")
    if os.environ.get("HERMES_PATCH_AGENT_VERSION"):
        pytest.skip("HERMES_PATCH_AGENT_VERSION is set — agent was hot-patched, baked check skipped")
    actual = pkg_version("hermes-agent")
    assert actual == baked, f"installed hermes-agent {actual!r} != baked HERMES_AGENT_VERSION {baked!r}"


def test_hermes_webui_version_env_set() -> None:
    """HERMES_WEBUI_VERSION must be baked into the image as a non-empty tag."""
    version = os.environ.get("HERMES_WEBUI_VERSION")
    assert version, "HERMES_WEBUI_VERSION env var is not set in the container"
    assert version.startswith("v"), f"expected a tag like 'v0.x.y', got {version!r}"


def test_hermes_webui_dir_installed() -> None:
    """hermes-webui must be present at /opt/hermes-webui with server.py."""
    webui = Path("/opt/hermes-webui")
    assert webui.is_dir(), "/opt/hermes-webui directory is missing"
    assert (webui / "server.py").is_file(), "/opt/hermes-webui/server.py not found"
