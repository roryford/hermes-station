"""Tests for the JSON stdout logging in hermes_station.logs."""

from __future__ import annotations

import io
import json
import logging
import re

import pytest

from hermes_station import logs as logs_mod
from hermes_station.logs import (
    STATION_LOGS,
    JsonFormatter,
    attach_station_handler,
    attach_stdout_json_handler,
)


@pytest.fixture(autouse=True)
def _reset_stdout_handler():
    """Reset the module-level stdout handler flag between tests.

    Each test starts with no JSON handler attached so the next
    `attach_stdout_json_handler()` call binds the handler to the
    test's current `sys.stdout` (which capsys has redirected).
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_flag = logs_mod._stdout_json_handler
    # Detach any pre-existing JSON handler so this test gets a fresh one.
    if logs_mod._stdout_json_handler is not None:
        root.removeHandler(logs_mod._stdout_json_handler)
        logs_mod._stdout_json_handler = None
    yield
    # Tear down anything tests added.
    for h in list(root.handlers):
        if h not in saved_handlers:
            root.removeHandler(h)
    root.setLevel(saved_level)
    logs_mod._stdout_json_handler = saved_flag


def _make_record(
    name: str,
    level: int,
    msg: str,
    *,
    extra: dict | None = None,
    exc_info=None,
) -> logging.LogRecord:
    logger = logging.getLogger(name)
    record = logger.makeRecord(
        name=name,
        level=level,
        fn="test.py",
        lno=10,
        msg=msg,
        args=(),
        exc_info=exc_info,
        extra=extra,
    )
    return record


def test_jsonformatter_plain_info():
    fmt = JsonFormatter()
    rec = _make_record("hermes_station", logging.INFO, "hello world")
    out = fmt.format(rec)
    obj = json.loads(out)
    assert obj["level"] == "info"
    assert obj["logger"] == "hermes_station"
    assert obj["message"] == "hello world"
    assert obj["component"] == "control_plane"
    assert "event" not in obj
    assert "exc" not in obj
    # ts is ISO-8601 UTC with millisecond precision and Z suffix
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", obj["ts"])


def test_jsonformatter_with_extra_component_and_fields():
    fmt = JsonFormatter()
    rec = _make_record(
        "hermes_station.readiness",
        logging.INFO,
        "discord ready",
        extra={"component": "readiness", "capability": "discord", "ready": False},
    )
    out = fmt.format(rec)
    obj = json.loads(out)
    assert obj["component"] == "readiness"
    assert obj["capability"] == "discord"
    assert obj["ready"] is False
    assert obj["message"] == "discord ready"


def test_jsonformatter_event_field():
    fmt = JsonFormatter()
    rec = _make_record(
        "hermes_station",
        logging.INFO,
        "started",
        extra={"event": "startup"},
    )
    obj = json.loads(fmt.format(rec))
    assert obj["event"] == "startup"


def test_jsonformatter_exception():
    fmt = JsonFormatter()
    logger = logging.getLogger("hermes_station.app")
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        exc_info = sys.exc_info()
    rec = logger.makeRecord(
        name="hermes_station.app",
        level=logging.ERROR,
        fn="t.py",
        lno=1,
        msg="boom",
        args=(),
        exc_info=exc_info,
    )
    obj = json.loads(fmt.format(rec))
    assert obj["level"] == "error"
    assert obj["message"] == "boom"
    assert "exc" in obj
    assert "ValueError" in obj["exc"]
    assert "boom" in obj["exc"]
    assert obj["component"] == "control_plane"


def test_jsonformatter_uvicorn_access_component():
    fmt = JsonFormatter()
    rec = _make_record("uvicorn.access", logging.INFO, "GET / 200")
    obj = json.loads(fmt.format(rec))
    assert obj["component"] == "http"
    assert obj["logger"] == "uvicorn.access"


def test_jsonformatter_gateway_component():
    fmt = JsonFormatter()
    rec1 = _make_record("gateway", logging.INFO, "x")
    rec2 = _make_record("hermes_station.gateway", logging.INFO, "y")
    assert json.loads(fmt.format(rec1))["component"] == "gateway"
    assert json.loads(fmt.format(rec2))["component"] == "gateway"


def test_jsonformatter_other_component():
    fmt = JsonFormatter()
    rec = _make_record("random.thirdparty", logging.INFO, "x")
    assert json.loads(fmt.format(rec))["component"] == "other"


def test_jsonformatter_nonserializable_extra():
    fmt = JsonFormatter()

    class Weird:
        def __repr__(self) -> str:
            return "<Weird>"

    rec = _make_record("hermes_station", logging.INFO, "x", extra={"obj": Weird()})
    obj = json.loads(fmt.format(rec))
    assert obj["obj"] == "<Weird>"


def test_attach_stdout_json_handler_idempotent():
    root = logging.getLogger()
    attach_stdout_json_handler()
    after_first = len(root.handlers)
    attach_stdout_json_handler()
    after_second = len(root.handlers)
    # Second call must not add or remove handlers — idempotency is the invariant.
    assert after_second == after_first
    assert logs_mod._stdout_json_handler is not None


def test_attach_stdout_json_handler_emits_json(capsys):
    attach_stdout_json_handler()
    logging.getLogger("hermes_station").info("hello-json")
    captured = capsys.readouterr()
    # Find the json line in stdout
    lines = [ln for ln in captured.out.splitlines() if "hello-json" in ln]
    assert lines, f"no json line found in stdout: {captured.out!r}"
    obj = json.loads(lines[-1])
    assert obj["message"] == "hello-json"
    assert obj["component"] == "control_plane"


def test_attach_station_handler_preserves_ring_buffer(capsys):
    # Reset module-level flag so attach actually runs.
    logs_mod._station_handler = None
    attach_station_handler()
    msg = "ring-buffer-and-stdout-msg"
    logging.getLogger("hermes_station").info(msg)

    # Ring buffer received it (plain text)
    tail = STATION_LOGS.tail(10)
    assert any(msg in line for line in tail), tail

    # Stdout received JSON
    captured = capsys.readouterr()
    json_lines = [ln for ln in captured.out.splitlines() if msg in ln]
    assert json_lines
    obj = json.loads(json_lines[-1])
    assert obj["message"] == msg


def test_webui_stdout_event_filtered_from_station_buffer(capsys):
    """Webui subprocess stdout (event=webui_stdout) hits real stdout as
    JSON but is filtered out of STATION_LOGS so the admin Logs station
    tab stays free of subprocess output."""
    logs_mod._station_handler = None
    attach_station_handler()
    before = list(STATION_LOGS.tail(10))
    msg = "webui-subproc-stdout-line"
    logging.getLogger("hermes_station.webui").info(msg, extra={"event": "webui_stdout"})

    # Did NOT land in station ring buffer (filtered).
    after = list(STATION_LOGS.tail(10))
    new_lines = [line for line in after if line not in before]
    assert not any(msg in line for line in new_lines), new_lines

    # Did land on real stdout as JSON.
    captured = capsys.readouterr()
    json_lines = [ln for ln in captured.out.splitlines() if msg in ln]
    assert json_lines, captured.out
    obj = json.loads(json_lines[-1])
    assert obj["message"] == msg
    assert obj["component"] == "webui"
    assert obj["event"] == "webui_stdout"


def test_jsonformatter_direct_stream_handler_roundtrip():
    """Run a JsonFormatter through a real StreamHandler to confirm it
    produces valid JSON in actual stream output."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("hermes_station.test_logs_json.roundtrip")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("rt", extra={"k": 1})
    out = stream.getvalue().strip()
    obj = json.loads(out)
    assert obj["message"] == "rt"
    assert obj["k"] == 1
