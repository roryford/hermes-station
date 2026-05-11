"""Bounded ring-buffer log capture for the admin Logs viewer.

Three sources are surfaced:
  - station: the ``hermes_station`` logger (proxy errors, lifespan).
  - gateway: BOTH ``hermes_station.gateway`` (our supervisor's own logs) and
    the upstream ``gateway`` logger (hermes-agent emits under that name).
    Capture is attached only while the supervisor is alive.
  - webui:   raw stdout lines pumped from the hermes-webui subprocess.
"""

from __future__ import annotations

import logging
import threading
from collections import deque

_BUFFER_SIZE = 500
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_GATEWAY_LOGGER_NAMES = ("gateway", "hermes_station.gateway")


class LogBuffer:
    def __init__(self, maxlen: int = _BUFFER_SIZE) -> None:
        self._lines: deque[str] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)

    def tail(self, n: int) -> list[str]:
        with self._lock:
            if n <= 0:
                return []
            if n >= len(self._lines):
                return list(self._lines)
            return list(self._lines)[-n:]

    def __len__(self) -> int:
        with self._lock:
            return len(self._lines)


class RingBufferHandler(logging.Handler):
    def __init__(self, buffer: LogBuffer) -> None:
        super().__init__()
        self.buffer = buffer
        self.setFormatter(logging.Formatter(_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append(self.format(record))
        except Exception:  # noqa: BLE001
            self.handleError(record)


STATION_LOGS = LogBuffer()
GATEWAY_LOGS = LogBuffer()
WEBUI_LOGS = LogBuffer()

BUFFERS: dict[str, LogBuffer] = {
    "station": STATION_LOGS,
    "gateway": GATEWAY_LOGS,
    "webui": WEBUI_LOGS,
}

_station_handler: RingBufferHandler | None = None
_gateway_handler: RingBufferHandler | None = None
_attach_lock = threading.Lock()


def attach_station_handler() -> None:
    global _station_handler
    with _attach_lock:
        if _station_handler is not None:
            return
        handler = RingBufferHandler(STATION_LOGS)
        logging.getLogger("hermes_station").addHandler(handler)
        _station_handler = handler


def attach_gateway_handler() -> None:
    global _gateway_handler
    with _attach_lock:
        if _gateway_handler is not None:
            return
        handler = RingBufferHandler(GATEWAY_LOGS)
        for name in _GATEWAY_LOGGER_NAMES:
            logging.getLogger(name).addHandler(handler)
        _gateway_handler = handler


def detach_gateway_handler() -> None:
    global _gateway_handler
    with _attach_lock:
        if _gateway_handler is None:
            return
        for name in _GATEWAY_LOGGER_NAMES:
            logging.getLogger(name).removeHandler(_gateway_handler)
        _gateway_handler = None
