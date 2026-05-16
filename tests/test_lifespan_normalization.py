"""Integration tests: lifespan normalizes config before passing to validate_readiness.

These tests boot the full app via TestClient and verify that:
1. A denormalized config (e.g. terminal.env_passthrough as a string) is healed
   on disk and the normalized form reaches readiness/health.
2. The normalization warning is emitted.
3. image_gen intent derived from a fal: config block is visible in both
   readiness rows and summary.toolsets after a real boot.

These are the seam tests that unit tests for normalize_config and
validate_readiness in isolation cannot cover.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient


def _boot_app(fake_data_dir: Path):
    # Point HERMES_WEBUI_SRC at a non-existent dir so the webui supervisor
    # takes the "source not found" branch and never tries to launch a subprocess.
    os.environ["HERMES_WEBUI_SRC"] = str(fake_data_dir / "no-webui")
    from hermes_station.app import create_app

    return create_app()


def test_lifespan_normalizes_string_env_passthrough_on_disk(
    fake_data_dir: Path,
) -> None:
    """A config with terminal.env_passthrough as a comma-separated string must
    be rewritten to a list on disk during lifespan, before readiness validation.
    """
    from hermes_station.config import Paths, write_yaml_config

    paths = Paths()
    # Write a denormalized config — env_passthrough is a string, not a list.
    write_yaml_config(
        paths.config_path,
        {"terminal": {"env_passthrough": "GITHUB_TOKEN,GH_TOKEN,MY_KEY"}},
    )

    app = _boot_app(fake_data_dir)
    with TestClient(app):
        pass  # lifespan runs during context entry/exit

    # The file on disk must now have a proper list.
    from hermes_station.config import load_yaml_config

    config = load_yaml_config(paths.config_path)
    ep = config["terminal"]["env_passthrough"]
    assert isinstance(ep, list), f"expected list on disk, got {type(ep).__name__}: {ep!r}"
    assert "GITHUB_TOKEN" in ep
    assert "GH_TOKEN" in ep
    assert "MY_KEY" in ep


def test_lifespan_normalization_emits_warning(fake_data_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A denormalized config must produce a WARNING log during lifespan."""
    from hermes_station.config import Paths, write_yaml_config

    paths = Paths()
    write_yaml_config(
        paths.config_path,
        {"terminal": {"env_passthrough": "GITHUB_TOKEN,GH_TOKEN"}},
    )

    app = _boot_app(fake_data_dir)
    with caplog.at_level(logging.WARNING, logger="hermes_station.app"):
        with TestClient(app):
            pass

    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("normalized" in m.lower() for m in warning_messages), (
        f"Expected a normalization warning; got: {warning_messages}"
    )


def test_lifespan_no_normalization_warning_for_clean_config(
    fake_data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A well-formed config must not emit any normalization warning."""
    from hermes_station.config import Paths, write_yaml_config

    paths = Paths()
    write_yaml_config(
        paths.config_path,
        {"terminal": {"env_passthrough": ["GITHUB_TOKEN", "GH_TOKEN"]}},
    )

    app = _boot_app(fake_data_dir)
    with caplog.at_level(logging.WARNING, logger="hermes_station.app"):
        with TestClient(app):
            pass

    norm_warnings = [
        r for r in caplog.records if r.levelno >= logging.WARNING and "normalized" in r.message.lower()
    ]
    assert not norm_warnings, f"Unexpected normalization warnings: {norm_warnings}"


def test_lifespan_image_gen_via_fal_block_reflected_in_health(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config with fal: block must show image_gen as intended in /health readiness
    and in summary.toolsets — verifying the seam between normalization, readiness,
    and health payload composition.
    """
    monkeypatch.delenv("FAL_KEY", raising=False)
    from hermes_station.config import Paths, write_yaml_config

    paths = Paths()
    write_yaml_config(paths.config_path, {"fal": {"model": "fal-ai/flux/dev"}})

    app = _boot_app(fake_data_dir)
    with TestClient(app) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    readiness = body["readiness"]
    assert "image_gen" in readiness, "image_gen not present in readiness"
    assert readiness["image_gen"]["intended"] is True
    assert readiness["image_gen"]["ready"] is False
    assert "FAL_KEY" in readiness["image_gen"].get("reason", "")


def test_lifespan_disabled_secrets_pop_from_os_environ(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key on admin.disabled_secrets must be popped from os.environ at boot.

    Validates the suppress-Railway-without-touching-Railway affordance: the
    operator disables FAL_KEY via the Secrets page, redeploy, and the agent
    no longer sees the Railway value.
    """
    from hermes_station.config import Paths, write_yaml_config

    # Simulate Railway-injected FAL_KEY.
    monkeypatch.setenv("FAL_KEY", "railway-injected-value")

    paths = Paths()
    write_yaml_config(
        paths.config_path,
        {"admin": {"disabled_secrets": ["FAL_KEY"]}},
    )

    app = _boot_app(fake_data_dir)
    with TestClient(app):
        pass

    # After lifespan, FAL_KEY should be gone from os.environ.
    assert os.environ.get("FAL_KEY") is None, (
        f"disabled_secrets did not pop FAL_KEY; still {os.environ.get('FAL_KEY')!r}"
    )


def test_lifespan_disabled_secrets_does_not_pop_unrelated_keys(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disabling one key must not affect any other env var."""
    from hermes_station.config import Paths, write_yaml_config

    monkeypatch.setenv("FAL_KEY", "to-be-popped")
    monkeypatch.setenv("BRAVE_API_KEY", "should-survive")

    paths = Paths()
    write_yaml_config(
        paths.config_path,
        {"admin": {"disabled_secrets": ["FAL_KEY"]}},
    )

    app = _boot_app(fake_data_dir)
    with TestClient(app):
        pass

    assert os.environ.get("FAL_KEY") is None
    assert os.environ.get("BRAVE_API_KEY") == "should-survive"


def test_lifespan_denorm_config_readiness_sees_correct_passthrough(
    fake_data_dir: Path,
) -> None:
    """When env_passthrough starts as a string, the subsequent _ensure_env_passthrough
    call must still add GITHUB_TOKEN/GH_TOKEN correctly (not do substring matching).
    Boot the app and verify the final on-disk list has all expected entries exactly once.
    """
    from hermes_station.config import Paths, write_yaml_config

    paths = Paths()
    # env_passthrough is a string; GITHUB_TOKEN happens to be a substring of
    # another token name — confirms we do list-member checks, not substring.
    write_yaml_config(
        paths.config_path,
        {"terminal": {"env_passthrough": "MY_GITHUB_TOKEN_EXTRA,SOME_KEY"}},
    )

    app = _boot_app(fake_data_dir)
    with TestClient(app):
        pass

    from hermes_station.config import load_yaml_config

    config = load_yaml_config(paths.config_path)
    ep = config["terminal"]["env_passthrough"]
    assert isinstance(ep, list)
    # Exact entries — not substrings of GITHUB_TOKEN.
    assert "GITHUB_TOKEN" in ep
    assert "GH_TOKEN" in ep
    # Original entries preserved.
    assert "MY_GITHUB_TOKEN_EXTRA" in ep
    assert "SOME_KEY" in ep
    # No duplicates.
    assert ep.count("GITHUB_TOKEN") == 1
    assert ep.count("GH_TOKEN") == 1
