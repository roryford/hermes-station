"""hermes-webui subprocess supervisor.

hermes-webui isn't ASGI — its `requirements.txt` is literally `pyyaml>=6.0` and
the whole thing is hand-rolled on stdlib `http.server`. We can't mount it as a
sub-app, so we run it as a child process and proxy HTTP at the boundary.

Supervised via `asyncio.create_subprocess_exec` so it lives in the same event
loop as everything else. Stdout/stderr from the child are line-pumped into our
own stdout, so Railway's log UI sees them with no extra setup.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

import httpx

from hermes_station.logs import WEBUI_LOGS

logger = logging.getLogger("hermes_station.webui")

# Keys from os.environ that hermes-webui legitimately needs.
_WEBUI_ENV_PASSTHROUGH = frozenset(
    {
        # System / runtime
        "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TERM",
        "PYTHONPATH",
        # MCP cache dirs set by the Dockerfile
        "NPM_CONFIG_CACHE", "UV_CACHE_DIR", "UV_TOOL_DIR",
    }
)


class WebUIProcess:
    INTERNAL_HOST = "127.0.0.1"
    INTERNAL_PORT = 8788
    STARTUP_GRACE_SECONDS = 30.0
    BACKOFF_BASE_SECONDS = 1.0
    BACKOFF_MAX_SECONDS = 30.0

    def __init__(
        self,
        *,
        webui_src: Path,
        hermes_home: Path,
        webui_state_dir: Path,
        workspace_dir: Path,
        config_path: Path,
    ) -> None:
        self.webui_src = webui_src
        self.hermes_home = hermes_home
        self.webui_state_dir = webui_state_dir
        self.workspace_dir = workspace_dir
        self.config_path = config_path
        self.process: asyncio.subprocess.Process | None = None
        self._supervisor_task: asyncio.Task[None] | None = None
        self._log_pump_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        # Snapshot bookkeeping — set/cleared by wait_ready and _supervise so
        # /health can give a cheap answer without doing an HTTP probe.
        self._last_healthy_at: datetime | None = None
        self._last_unhealthy_reason: str | None = None
        self._disabled: bool = False

    @property
    def health_url(self) -> str:
        return f"http://{self.INTERNAL_HOST}:{self.INTERNAL_PORT}/health"

    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def is_healthy(self) -> bool:
        if not self.is_running():
            return False
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(self.health_url)
                return 200 <= response.status_code < 300
        except (httpx.HTTPError, OSError):
            return False

    async def start(self) -> None:
        if self.is_running():
            return
        self._stopping.clear()
        await self._spawn()
        self._supervisor_task = asyncio.create_task(
            self._supervise(), name="hermes-station.webui-supervisor"
        )

    async def stop(self) -> None:
        self._stopping.set()
        if self._supervisor_task:
            self._supervisor_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._supervisor_task
            self._supervisor_task = None
        await self._terminate_process()

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def wait_ready(self, timeout: float | None = None) -> bool:
        deadline = asyncio.get_event_loop().time() + (timeout or self.STARTUP_GRACE_SECONDS)
        while asyncio.get_event_loop().time() < deadline:
            if await self.is_healthy():
                self._last_healthy_at = datetime.now(timezone.utc)
                self._last_unhealthy_reason = None
                return True
            if not self.is_running():
                self._last_unhealthy_reason = "process not running"
                return False
            await asyncio.sleep(0.5)
        self._last_unhealthy_reason = "wait_ready timed out"
        return False

    def mark_disabled(self) -> None:
        """Called when hermes-webui source is not present and we won't spawn."""
        self._disabled = True

    def snapshot(self) -> dict[str, object]:
        """Cheap state snapshot for /health.

        Reports state from the supervisor's view — does NOT do an HTTP probe
        (too expensive for the /health request path). Returns:

            {"state": "ready|starting|down|disabled",
             "pid": <int|None>,
             "internal_url": str,
             "is_running": bool}
        """
        if self._disabled:
            return {
                "state": "disabled",
                "pid": None,
                "internal_url": self.health_url,
                "is_running": False,
            }
        running = self.is_running()
        pid = self.process.pid if (self.process and running) else None
        if not running:
            state = "down"
        elif self._last_healthy_at is not None:
            state = "ready"
        else:
            state = "starting"
        return {
            "state": state,
            "pid": pid,
            "internal_url": self.health_url,
            "is_running": running,
        }

    def _build_env(self) -> dict[str, str]:
        # Pass only the minimum env vars hermes-webui needs.
        # Do NOT forward arbitrary secrets (API keys, admin password, bot tokens).
        env: dict[str, str] = {
            k: v for k, v in os.environ.items() if k in _WEBUI_ENV_PASSTHROUGH
        }
        if not env.get("HERMES_WEBUI_AGENT_DIR"):
            import sysconfig

            env["HERMES_WEBUI_AGENT_DIR"] = sysconfig.get_paths()["purelib"]
        env.update(
            {
                "HERMES_WEBUI_HOST": self.INTERNAL_HOST,
                "HERMES_WEBUI_PORT": str(self.INTERNAL_PORT),
                "HERMES_HOME": str(self.hermes_home),
                "HERMES_CONFIG_PATH": str(self.config_path),
                "HERMES_WEBUI_STATE_DIR": str(self.webui_state_dir),
                "HERMES_WEBUI_DEFAULT_WORKSPACE": str(self.workspace_dir),
                "PYTHONUNBUFFERED": "1",
            }
        )
        # Propagate the admin password as webui password so hermes-webui's own
        # auth stays in sync — but only HERMES_WEBUI_PASSWORD reaches the child,
        # not HERMES_ADMIN_PASSWORD directly.
        if not env.get("HERMES_WEBUI_PASSWORD"):
            admin_pw = os.environ.get("HERMES_ADMIN_PASSWORD", "")
            if admin_pw:
                env["HERMES_WEBUI_PASSWORD"] = admin_pw
        return env

    async def _spawn(self) -> None:
        server_py = self.webui_src / "server.py"
        cmd = [sys.executable, str(server_py)]
        logger.info("starting hermes-webui: %s", " ".join(cmd))
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=self._build_env(),
            cwd=str(self.webui_src),
        )
        self._log_pump_task = asyncio.create_task(
            self._pump_logs(), name="hermes-station.webui-log-pump"
        )

    async def _terminate_process(self) -> None:
        if not self.process or self.process.returncode is not None:
            self.process = None
            return
        try:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("hermes-webui ignored SIGTERM; sending SIGKILL")
                self.process.kill()
                await self.process.wait()
        except ProcessLookupError:
            pass
        finally:
            if self._log_pump_task:
                self._log_pump_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._log_pump_task
                self._log_pump_task = None
            self.process = None

    async def _supervise(self) -> None:
        backoff = self.BACKOFF_BASE_SECONDS
        while not self._stopping.is_set():
            assert self.process is not None
            returncode = await self.process.wait()
            if self._stopping.is_set():
                return
            logger.warning(
                "hermes-webui exited (rc=%s); restarting in %.1fs", returncode, backoff
            )
            await asyncio.sleep(backoff)
            if self._stopping.is_set():
                return
            await self._spawn()
            if await self.wait_ready(timeout=self.STARTUP_GRACE_SECONDS):
                self._last_healthy_at = datetime.now(timezone.utc)
                self._last_unhealthy_reason = None
                backoff = self.BACKOFF_BASE_SECONDS
            else:
                self._last_unhealthy_reason = f"restart wait_ready failed (rc={returncode})"
                backoff = min(backoff * 2, self.BACKOFF_MAX_SECONDS)

    async def _pump_logs(self) -> None:
        if not self.process or not self.process.stdout:
            return
        try:
            async for raw in self.process.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    # Goes to our stdout → Railway log stream
                    print(f"[webui] {line}", flush=True)
                    WEBUI_LOGS.append(line)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("hermes-webui log pump error: %s", exc)
