"""Compat test against a sanitized snapshot of a real long-running `/data` volume.

Auto-skipped when `tests/fixtures/data-realistic/` is empty (the default —
the fixture is gitignored). Populated locally by running:

    ./scripts/sanitize-data-snapshot.sh <snapshot.tgz>

after pulling a snapshot of the live Railway volume. See
`tests/fixtures/README.md` for the full workflow.

When the fixture is present, this suite re-runs the structural contract
assertions from `test_compat.py` against the real-world directory layout —
catches issues that the synthetic fresh-boot fixture can't surface, like
unexpected files in `state.db`, populated `memories/`, etc.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest

from hermes_station.config import extract_model_config, load_env_file, load_yaml_config

_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "data-realistic"
_HAS_FIXTURE = (_FIXTURE_ROOT / ".hermes").exists()

pytestmark = pytest.mark.skipif(
    not _HAS_FIXTURE,
    reason="tests/fixtures/data-realistic/ not populated — see tests/fixtures/README.md",
)


@pytest.fixture
def realistic_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy the sanitized realistic fixture to a tmp dir per-test.

    Per-test copy avoids cross-test contamination if a test mutates the fixture.
    """
    data = tmp_path / "data"
    shutil.copytree(_FIXTURE_ROOT, data)

    hermes_home = data / ".hermes"
    monkeypatch.setenv("HOME", str(data))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(hermes_home / "config.yaml"))
    monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(data / "webui"))
    monkeypatch.setenv("HERMES_WORKSPACE_DIR", str(data / "workspace"))
    return data


@pytest.fixture
def admin_password(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "realistic-test-pw")
    return "realistic-test-pw"


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert response.status_code == 302, response.text


def test_fixture_has_expected_skeleton(realistic_data_dir: Path) -> None:
    """The sanitization preserved the core directory layout."""
    home = realistic_data_dir / ".hermes"
    assert home.exists()
    assert (realistic_data_dir / "webui").exists()
    assert (realistic_data_dir / "webui" / ".signing_key").exists(), (
        "webui/.signing_key must survive sanitization for session continuity"
    )


def test_sanitized_env_loads_cleanly(realistic_data_dir: Path) -> None:
    """The .env was sanitized to PLACEHOLDER_<KEY> values but stays parseable."""
    env_path = realistic_data_dir / ".hermes" / ".env"
    if not env_path.exists():
        pytest.skip("realistic fixture has no .env")
    values = load_env_file(env_path)
    # Sanitization keys-stay-real, values-are-PLACEHOLDER_<KEY>
    for key, value in values.items():
        assert value == f"PLACEHOLDER_{key}", (
            f"expected PLACEHOLDER_{key} (sanitization), got {value!r} — re-run sanitize script"
        )


def test_yaml_config_loads_cleanly(realistic_data_dir: Path) -> None:
    """config.yaml roundtrips unchanged — it's non-sensitive, kept as-is."""
    config_path = realistic_data_dir / ".hermes" / "config.yaml"
    if not config_path.exists():
        pytest.skip("realistic fixture has no config.yaml")
    config = load_yaml_config(config_path)
    model = extract_model_config(config)
    assert model.provider, "realistic fixture should have a configured provider"


async def test_app_boots_against_realistic_data(realistic_data_dir: Path, admin_password: str) -> None:
    """hermes-station's ASGI app constructs cleanly with a real /data mounted.

    This is the in-process equivalent of the container smoke test — proves the
    config reader, paths resolution, and admin status endpoint all hold up
    against real-world directory state.
    """
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/api/status")
        assert response.status_code == 200, response.text
        body = response.json()

    assert body["paths"]["hermes_home"] == str(realistic_data_dir / ".hermes")
    assert body["paths"]["webui_state_dir"] == str(realistic_data_dir / "webui")
    assert body["model"]["provider"], "provider should round-trip from fixture's config.yaml"


async def test_admin_dashboard_renders_against_realistic_data(
    realistic_data_dir: Path, admin_password: str
) -> None:
    """The HTMX dashboard renders when pointed at real-world /data — catches any
    template lookups that assumed the synthetic fresh-boot fixture's shape."""
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin")
        assert response.status_code == 200
        assert "Dashboard" in response.text
        # The status fragment renders too
        fragment = await client.get("/admin/_partial/status")
        assert fragment.status_code == 200
        assert "WebUI" in fragment.text
