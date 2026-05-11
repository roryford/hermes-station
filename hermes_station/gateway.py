"""hermes-agent gateway supervisor.

The gateway is an async-native coroutine (`gateway.run.start_gateway`), so we
run it as a supervised `asyncio.Task` in the same event loop as uvicorn. This
eliminates the subprocess + threading pattern from hermes-all-in-one.

**Signal handling gotcha:** `gateway.run.start_gateway` registers its own
SIGINT/SIGTERM/SIGUSR1 handlers via `loop.add_signal_handler`, which would
clobber uvicorn's signal handling and break graceful shutdown of the host
process. We temporarily no-op `loop.add_signal_handler` while the gateway is
running and rely on explicit task cancellation from the lifespan handler
instead. This should be replaced with an upstream opt-out flag if/when one
lands.

**Health:** read from `$HERMES_HOME/gateway_state.json` which the gateway
writes itself. `gateway_state == "running"` is the real health signal,
unlike hermes-all-in-one's "process alive for ≥3s" heuristic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from hermes_station.logs import attach_gateway_handler, detach_gateway_handler

logger = logging.getLogger("hermes_station.gateway")

GatewayState = Literal["unknown", "starting", "running", "startup_failed", "stopping", "stopped"]
_TERMINAL_STATES = {"unknown", "startup_failed", "stopping", "stopped"}


class Gateway:
    BACKOFF_BASE_SECONDS = 5.0
    BACKOFF_MAX_SECONDS = 60.0
    SHUTDOWN_TIMEOUT_SECONDS = 30.0
    # Refresh interval for updated_at in gateway_state.json. The hermes-webui
    # considers the gateway stale after 120 s (two cron ticks); 30 s keeps us
    # well inside that window even if a tick is delayed.
    HEARTBEAT_INTERVAL_SECONDS = 30.0

    def __init__(self, *, hermes_home: Path) -> None:
        self.hermes_home = hermes_home
        self.task: asyncio.Task[Any] | None = None
        self._supervisor_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    @property
    def state_path(self) -> Path:
        return self.hermes_home / "gateway_state.json"

    def read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"gateway_state": "unknown"}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"gateway_state": "unknown"}

    @property
    def gateway_state(self) -> GatewayState:
        return self.read_state().get("gateway_state", "unknown")  # type: ignore[return-value]

    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    def is_healthy(self) -> bool:
        return self.is_running() and self.gateway_state == "running"

    async def start(self) -> None:
        if self.is_running() or (self._supervisor_task and not self._supervisor_task.done()):
            return
        self._stopping.clear()
        try:
            attach_gateway_handler()
            self._supervisor_task = asyncio.create_task(
                self._supervise(), name="hermes-station.gateway-supervisor"
            )
            self._heartbeat_task = asyncio.create_task(
                self._refresh_updated_at(), name="hermes-station.gateway-heartbeat"
            )
        except BaseException:
            detach_gateway_handler()
            raise

    async def stop(self) -> None:
        self._stopping.set()
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        if self._supervisor_task and not self._supervisor_task.done():
            self._supervisor_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._supervisor_task
            self._supervisor_task = None
        await self._cancel_task()
        detach_gateway_handler()

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _cancel_task(self) -> None:
        if not self.task or self.task.done():
            self.task = None
            return
        self.task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(self.task, timeout=self.SHUTDOWN_TIMEOUT_SECONDS)
        self.task = None

    async def _run_once(self) -> bool:
        # Imported lazily — hermes-agent is heavy and only available when
        # the [hermes] extra is installed.
        from gateway.run import start_gateway  # type: ignore[import-not-found]

        loop = asyncio.get_running_loop()
        original_add_signal_handler = loop.add_signal_handler
        loop.add_signal_handler = lambda *_args, **_kwargs: None  # type: ignore[assignment]
        try:
            self.task = asyncio.create_task(
                start_gateway(config=None, replace=False, verbosity=1),
                name="hermes-station.gateway-task",
            )
            try:
                result = await self.task
            except asyncio.CancelledError:
                logger.info("gateway task cancelled (clean shutdown)")
                raise
            return bool(result)
        finally:
            loop.add_signal_handler = original_add_signal_handler  # type: ignore[assignment]
            self.task = None

    async def _refresh_updated_at(self) -> None:
        """Keep gateway_state.json's updated_at fresh for the WebUI heartbeat.

        hermes-webui (agent_health.py) treats a gateway_state.json with
        gateway_state=="running" AND updated_at < 120 s old as proof the
        gateway is alive (#1879 cross-container fallback). The gateway task
        writes this field on state transitions but not on every tick in the
        in-process setup, so the PID file from the previous container is stale
        after a Railway restart and the freshness check fails. We patch the
        timestamp here every HEARTBEAT_INTERVAL_SECONDS while the gateway is
        in the running state.
        """
        while not self._stopping.is_set():
            try:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                return
            if self._stopping.is_set():
                return
            if self.gateway_state != "running":
                continue
            try:
                state = self.read_state()
                if state.get("gateway_state") != "running" or self._stopping.is_set():
                    continue
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
                tmp.write_text(
                    json.dumps(state, separators=(",", ":")), encoding="utf-8"
                )
                tmp.replace(self.state_path)
            except (OSError, json.JSONDecodeError):
                pass

    async def _supervise(self) -> None:
        backoff = self.BACKOFF_BASE_SECONDS
        while not self._stopping.is_set():
            try:
                ok = await self._run_once()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("gateway crashed: %s", exc)
                ok = False
            if self._stopping.is_set():
                return
            if ok:
                logger.info("gateway exited cleanly; not restarting")
                return
            logger.warning(
                "gateway failed/exited unexpectedly; retrying in %.1fs", backoff
            )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            backoff = min(backoff * 2, self.BACKOFF_MAX_SECONDS)


def should_autostart(*, mode: str, config: dict[str, Any], env_values: dict[str, str]) -> bool:
    """Determine if the gateway should auto-start per CONTRACT.md §8.

    Returns True iff:
      - mode is truthy (1/true/on/yes), OR
      - mode is "auto" AND a valid provider is configured AND the provider's
        API key is set AND at least one channel has its primary key set.
    """
    normalized = (mode or "auto").strip().lower()
    if normalized in {"1", "true", "on", "yes"}:
        return True
    if normalized in {"0", "false", "off", "no"}:
        return False

    from hermes_station.admin.channels import CHANNEL_ENV_KEYS
    from hermes_station.admin.provider import PROVIDER_CATALOG

    provider = ((config.get("model") or {}).get("provider") or "").strip().lower()
    if not provider:
        return False
    provider_meta = PROVIDER_CATALOG.get(provider)
    if not provider_meta:
        return False
    env_var = provider_meta["env_var"]
    if not (env_values.get(env_var) or os.environ.get(env_var)):
        return False
    return any(env_values.get(k) or os.environ.get(k) for k in CHANNEL_ENV_KEYS)
