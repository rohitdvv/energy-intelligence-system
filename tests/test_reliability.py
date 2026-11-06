"""Retries for Claude calls, and failures that used to be counted as successes."""
from __future__ import annotations

import json
import logging
import types
from unittest import mock

import anthropic
import httpx
import pytest

from agents import llm_call
from agents.chat_agent import ChatAgent
from observability import get_telemetry, mark_failed, span


def _reply(text: str = "ok"):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=types.SimpleNamespace(input_tokens=10, output_tokens=5,
                                    cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )


def _status_error(code: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(code, request=request)
    cls = {429: anthropic.RateLimitError, 400: anthropic.BadRequestError}.get(code, anthropic.InternalServerError)
    return cls(f"status {code}", response=response, body=None)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(llm_call, "_sleep", waits.append)
    return waits


def _llm_events():
    return [e for e in get_telemetry().events() if e.category == "llm"]


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------

def test_chat_retries_rate_limits_and_overload_then_answers(no_sleep):
    client = mock.MagicMock()
    client.messages.create.side_effect = [_status_error(429), _status_error(529), _reply("Permian is up.")]
    out = ChatAgent(client).respond([{"role": "user", "content": "How is Permian?"}])
    assert out["text"] == "Permian is up."
    assert client.messages.create.call_count == 3
    assert no_sleep == [2, 4]
    events = _llm_events()
    assert [e.success for e in events] == [False, False, True]
    assert all(e.attributes.get("agent") == "chat" for e in events)
    assert events[-1].attributes.get("retries") == 2


def test_connection_errors_are_retried():
    client = mock.MagicMock()
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    client.messages.create.side_effect = [anthropic.APIConnectionError(request=request), _reply()]
    llm_call.create_with_retry(client, agent="chat", model="m")
    assert client.messages.create.call_count == 2


def test_bad_requests_are_not_retried():
    client = mock.MagicMock()
    client.messages.create.side_effect = _status_error(400)
    with pytest.raises(anthropic.BadRequestError):
        llm_call.create_with_retry(client, agent="chat", model="m")
    assert client.messages.create.call_count == 1
    assert [e.success for e in _llm_events()] == [False]


def test_gives_up_after_the_last_attempt(no_sleep):
    client = mock.MagicMock()
    client.messages.create.side_effect = _status_error(500)
    with pytest.raises(anthropic.InternalServerError):
        llm_call.create_with_retry(client, agent="bull", model="m")
    assert client.messages.create.call_count == llm_call.MAX_ATTEMPTS
    assert len(no_sleep) == llm_call.MAX_ATTEMPTS - 1


def test_app_client_has_sdk_retries_off():
    import app

    app._anthropic_client.clear()
    assert app._anthropic_client().max_retries == 0


# ---------------------------------------------------------------------------
# Failures that are handled must still count as errors
# ---------------------------------------------------------------------------

def test_mark_failed_records_an_error_without_raising():
    with span("tool", "demo") as sp:
        mark_failed(sp, "no data")
    (event,) = get_telemetry().events()
    assert event.success is False and event.error == "no data"
    assert "__failed__" not in event.attributes


def test_tool_returning_an_error_counts_as_a_failure():
    import pandas as pd

    from agents import tools

    with mock.patch.object(tools, "_load_basin", return_value=pd.DataFrame(columns=["ds", "y"])):
        result = tools.execute_tool("forecast_basin", {"basin": "Permian", "fuel_type": "oil",
                                                       "cutoff_year": 2024, "horizon_year": 2026})
    assert "error" in result
    (event,) = [e for e in get_telemetry().events() if e.category == "tool"]
    assert event.success is False and event.error


def test_unknown_tool_is_reported_to_the_agent():
    from agents.tools import execute_tool

    assert "error" in execute_tool("no_such_tool", {})


def test_bsee_fallback_counts_as_a_failure():
    from data import bsee_loader

    with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
        out = bsee_loader.fetch_gom_production(2023)
    assert out["source"] == "bsee_static"
    (event,) = [e for e in get_telemetry().events() if e.name == "bsee.fetch_gom_production"]
    assert event.success is False and "static" in event.error


def test_json_log_lines_are_printed():
    import io

    from observability.sinks import LoggingSink

    assert LoggingSink().enabled
    (handler,) = [h for h in logging.getLogger("observability.events").handlers
                  if getattr(h, "_eis_events", False)]
    buffer = io.StringIO()
    old = handler.setStream(buffer)
    try:
        with span("api", "eia._get", source="eia"):
            pass
    finally:
        handler.setStream(old)
    line = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert line["name"] == "eia._get" and line["category"] == "api"


def test_json_log_lines_can_be_turned_off(monkeypatch):
    from observability.sinks import LoggingSink

    monkeypatch.setenv("OBS_LOG_EVENTS", "off")
    assert not LoggingSink().enabled
    logging.getLogger("observability.events")  # unchanged, no error
