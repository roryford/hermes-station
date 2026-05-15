"""Bounded ring-buffer log capture for the admin Logs viewer.

Three sources are surfaced:
  - station: the ``hermes_station`` logger (proxy errors, lifespan).
  - gateway: BOTH ``hermes_station.gateway`` (our supervisor's own logs) and
    the upstream ``gateway`` logger (hermes-agent emits under that name).
    Capture is attached only while the supervisor is alive.
  - webui:   raw stdout lines pumped from the hermes-webui subprocess.

In addition to the ring buffers, this module owns the process-wide
stdout logger configuration: a single JSON-per-line stream handler
attached to the root logger so that Railway/journald see structured
logs with a ``component`` field for filtering.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from collections import deque
from datetime import datetime, timezone

_BUFFER_SIZE = 500
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_GATEWAY_LOGGER_NAMES = ("gateway", "hermes_station.gateway")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# Reserved LogRecord attributes that must NOT be merged into the JSON
# output as "extras". Mirrors logging's documented record attributes
# (https://docs.python.org/3/library/logging.html#logrecord-attributes)
# plus a couple of internal ones we set ourselves.
_RESERVED_LOGRECORD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
        # fields we emit explicitly:
        "component",
        "event",
    }
)


def _infer_component(logger_name: str) -> str:
    if (
        logger_name == "gateway"
        or logger_name.startswith("gateway.")
        or logger_name.startswith("hermes_station.gateway")
    ):
        return "gateway"
    if logger_name.startswith("hermes_station.webui"):
        return "webui"
    if logger_name.startswith("hermes_station.readiness"):
        return "readiness"
    if (
        logger_name == "hermes_station"
        or logger_name.startswith("hermes_station.app")
        or logger_name == "hermes_station.app"
    ):
        return "control_plane"
    if logger_name.startswith("hermes_station."):
        # other hermes_station.* subloggers fall under control_plane
        return "control_plane"
    if logger_name.startswith("uvicorn"):
        return "http"
    return "other"


def _json_safe(value: object) -> object:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record on a single line.

    Top-level fields: ``ts``, ``level``, ``logger``, ``component``,
    optionally ``event``, ``message``, plus any user-provided ``extra``
    keys (excluding reserved LogRecord attributes). ``exc`` is included
    when ``record.exc_info`` is set.
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        # ISO-8601 UTC with millisecond precision and trailing Z.
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        ts = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(record.msecs):03d}Z"

        component = getattr(record, "component", None) or _infer_component(record.name)

        payload: dict[str, object] = {
            "ts": ts,
            "level": record.levelname.lower(),
            "logger": record.name,
            "component": component,
            "message": record.getMessage(),
        }

        event = getattr(record, "event", None)
        if event is not None:
            payload["event"] = event

        # Merge non-reserved extras.
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOGRECORD_ATTRS:
                continue
            if key.startswith("_"):
                continue
            if key in payload:
                continue
            payload[key] = _json_safe(value)

        if record.exc_info:
            try:
                payload["exc"] = self.formatException(record.exc_info)
            except Exception:  # noqa: BLE001
                payload["exc"] = "<unformattable exception>"

        try:
            return json.dumps(payload, default=str, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            # Last-ditch fallback: stringify everything.
            safe = {k: str(v) for k, v in payload.items()}
            return json.dumps(safe, ensure_ascii=False)


class LogBuffer:
    def __init__(self, maxlen: int = _BUFFER_SIZE) -> None:
        self._lines: deque[str] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        cleaned = _ANSI_ESCAPE_RE.sub("", line).replace("\r", "")
        with self._lock:
            self._lines.append(cleaned)

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
_stdout_json_handler: logging.StreamHandler | None = None
_attach_lock = threading.Lock()


def attach_stdout_json_handler() -> None:
    """Install a JSON-per-line StreamHandler on the root logger.

    Idempotent. Removes any pre-existing default ``StreamHandler``
    instances on the root logger (so uvicorn's text formatter doesn't
    double-emit) but leaves ring-buffer handlers and any other custom
    handlers in place.
    """

    global _stdout_json_handler
    with _attach_lock:
        if _stdout_json_handler is not None:
            return
        root = logging.getLogger()

        # Strip pre-existing plain StreamHandlers to avoid duplicate
        # stdout/stderr lines. Keep RingBufferHandler and any non-stream
        # handlers intact.
        for existing in list(root.handlers):
            if isinstance(existing, RingBufferHandler):
                continue
            if type(existing) is logging.StreamHandler:
                root.removeHandler(existing)

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)

        if root.level == logging.WARNING or root.level == 0:
            root.setLevel(logging.INFO)

        _stdout_json_handler = handler


class _DropWebuiStdoutFilter(logging.Filter):
    """Keep webui subprocess stdout out of the station ring buffer.

    WebUIProcess._pump_logs routes child stdout through the structured
    logger so each line becomes a JSON record on real stdout. Without
    this filter those lines would also propagate to the station handler
    on the `hermes_station` logger and pollute the admin Logs "station"
    tab — that tab is meant for control-plane messages only; the "webui"
    tab is fed separately via WEBUI_LOGS.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event", None) != "webui_stdout"


def attach_station_handler() -> None:
    global _station_handler
    attach_stdout_json_handler()
    with _attach_lock:
        if _station_handler is not None:
            return
        handler = RingBufferHandler(STATION_LOGS)
        handler.addFilter(_DropWebuiStdoutFilter())
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
