"""Tests for hermes_station.__main__ entry point."""

from __future__ import annotations

import runpy
import sys
from unittest.mock import MagicMock, patch


def test_main_calls_uvicorn_run(monkeypatch) -> None:
    """main() should call uvicorn.run with the expected default arguments."""
    monkeypatch.setattr(sys, "argv", ["hermes-station"])
    mock_run = MagicMock()
    with patch("uvicorn.run", mock_run):
        from hermes_station.__main__ import main

        main()

    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert mock_run.call_args[0][0] == "hermes_station.app:app"
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 8787
    assert kwargs["proxy_headers"] is True
    assert kwargs["access_log"] is True
    assert kwargs["log_config"] is None
    assert kwargs["forwarded_allow_ips"] == "127.0.0.1"


def test_main_respects_env_overrides(monkeypatch) -> None:
    """main() should use PORT, CONTROL_PLANE_HOST, and TRUSTED_PROXY_IPS when set."""
    monkeypatch.setattr(sys, "argv", ["hermes-station"])
    monkeypatch.setenv("PORT", "9090")
    monkeypatch.setenv("CONTROL_PLANE_HOST", "127.0.0.1")
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "10.0.0.1,10.0.0.2")

    mock_run = MagicMock()
    with patch("uvicorn.run", mock_run):
        from hermes_station.__main__ import main

        main()

    _, kwargs = mock_run.call_args
    assert kwargs["port"] == 9090
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["forwarded_allow_ips"] == "10.0.0.1,10.0.0.2"


def test_module_run_invokes_main(monkeypatch) -> None:
    """Running `python -m hermes_station` should invoke main()."""
    monkeypatch.setattr(sys, "argv", ["hermes-station"])
    mock_run = MagicMock()
    # Remove cached module so runpy executes it fresh.
    for key in list(sys.modules.keys()):
        if key == "hermes_station.__main__":
            del sys.modules[key]

    with patch("uvicorn.run", mock_run):
        runpy.run_module("hermes_station.__main__", run_name="__main__", alter_sys=False)

    mock_run.assert_called_once()
