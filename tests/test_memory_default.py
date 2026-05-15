"""First-boot default-memory-provider seeding.

hermes-agent ships 8 memory providers but none active by default. We seed
`memory.provider: holographic` on first boot only — see CONTRACT.md §3.3
no-clobber rules.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from hermes_station.admin.htmx_dashboard import routes as htmx_routes
from hermes_station.admin.routes import admin_routes
from hermes_station.config import (
    DEFAULT_MEMORY_PROVIDER,
    Paths,
    load_yaml_config,
    seed_default_memory_provider,
    write_yaml_config,
)


# ---------------------------------------------------------------------------
# Unit: seed_default_memory_provider
# ---------------------------------------------------------------------------


def test_seed_writes_holographic_when_config_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    assert not config_path.exists()

    wrote = seed_default_memory_provider(config_path)

    assert wrote is True
    config = load_yaml_config(config_path)
    assert config["memory"]["provider"] == "holographic"
    assert DEFAULT_MEMORY_PROVIDER == "holographic"


def test_seed_writes_holographic_when_memory_block_absent(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_yaml_config(config_path, {"model": {"provider": "anthropic", "default": "claude-sonnet-4.6"}})

    wrote = seed_default_memory_provider(config_path)

    assert wrote is True
    config = load_yaml_config(config_path)
    assert config["memory"]["provider"] == "holographic"
    # Existing model block is preserved.
    assert config["model"]["provider"] == "anthropic"


def test_seed_no_clobber_when_user_picked_other_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_yaml_config(config_path, {"memory": {"provider": "honcho", "honcho": {"workspace": "x"}}})

    wrote = seed_default_memory_provider(config_path)

    assert wrote is False
    config = load_yaml_config(config_path)
    assert config["memory"]["provider"] == "honcho"
    assert config["memory"]["honcho"] == {"workspace": "x"}


def test_seed_no_clobber_when_user_picked_built_in_only(tmp_path: Path) -> None:
    """An empty `provider: ""` is the explicit "built-in only" choice — preserve it."""
    config_path = tmp_path / "config.yaml"
    write_yaml_config(config_path, {"memory": {"provider": ""}})

    wrote = seed_default_memory_provider(config_path)

    assert wrote is False
    config = load_yaml_config(config_path)
    assert config["memory"]["provider"] == ""


# ---------------------------------------------------------------------------
# Integration: dashboard surfaces memory provider
# ---------------------------------------------------------------------------


def _build_app() -> Starlette:
    base_routes: list[Route] = list(htmx_routes())
    base_routes.extend(
        route for route in admin_routes() if not (isinstance(route, Route) and route.path == "/admin")
    )
    app = Starlette(routes=base_routes)
    app.state.paths = Paths()
    app.state.webui = None
    app.state.gateway = None
    return app


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert response.status_code == 302, response.text


@pytest.fixture
def client_app(fake_data_dir: Path, admin_password: str) -> Starlette:  # noqa: ARG001
    return _build_app()


async def test_status_fragment_shows_memory_provider(
    client_app: Starlette, fake_data_dir: Path, admin_password: str
) -> None:
    write_yaml_config(
        fake_data_dir / ".hermes" / "config.yaml",
        {"memory": {"provider": "holographic"}},
    )

    transport = httpx.ASGITransport(app=client_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/_partial/status")
        assert response.status_code == 200
        body = response.text
        assert "Memory" in body
        assert "holographic" in body
