"""The public Committee is capped per day; owners unlocked with the AI code skip the cap."""
from __future__ import annotations

from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

from ui import usage_limits

DAY = date(2026, 11, 5)

COMMITTEE_SCRIPT = """
import types
from unittest import mock
import streamlit as st
from ui.access import require_access
from ui.committee import render_committee

reply = types.SimpleNamespace(
    content=[types.SimpleNamespace(type="text", text="VERDICT: WATCH")],
    stop_reason="end_turn",
    usage=types.SimpleNamespace(input_tokens=10, output_tokens=5,
                                cache_creation_input_tokens=0, cache_read_input_tokens=0),
)
client = mock.MagicMock()
client.messages.create.return_value = reply
st.session_state["claude_calls"] = st.session_state.get("claude_calls", 0)
if st.session_state.get("owner_login"):
    require_access("ai", form_key="owner")
render_committee("Permian", "oil", 2028, 75.0, client)
st.session_state["claude_calls"] += client.messages.create.call_count
"""


@pytest.fixture(autouse=True)
def fresh_counter():
    usage_limits.reset()
    yield
    usage_limits.reset()


def _run_button(app):
    return next(b for b in app.button if b.label == "Run Investment Committee")


def test_counter_allows_up_to_the_limit_then_stops(monkeypatch):
    monkeypatch.setenv("COMMITTEE_DAILY_RUN_LIMIT", "2")
    assert usage_limits.try_start_public_run(DAY)
    assert usage_limits.try_start_public_run(DAY)
    assert not usage_limits.try_start_public_run(DAY)
    assert usage_limits.remaining_public_runs(DAY) == 0


def test_counter_resets_on_a_new_day(monkeypatch):
    monkeypatch.setenv("COMMITTEE_DAILY_RUN_LIMIT", "1")
    assert usage_limits.try_start_public_run(DAY)
    assert not usage_limits.try_start_public_run(DAY)
    assert usage_limits.try_start_public_run(date(2026, 11, 6))


@pytest.mark.parametrize("raw,expected", [(None, 20), ("5", 5), ("0", 0), ("-3", 0), ("lots", 20)])
def test_limit_setting(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("COMMITTEE_DAILY_RUN_LIMIT", raising=False)
    else:
        monkeypatch.setenv("COMMITTEE_DAILY_RUN_LIMIT", raw)
    assert usage_limits.daily_public_run_limit() == expected


def test_public_visitor_can_run_without_a_code(monkeypatch):
    monkeypatch.delenv("AI_ACCESS_CODE", raising=False)
    monkeypatch.setenv("COMMITTEE_DAILY_RUN_LIMIT", "3")
    app = AppTest.from_string(COMMITTEE_SCRIPT, default_timeout=60).run()
    assert not app.exception
    _run_button(app).click().run()
    assert not app.exception
    assert app.session_state["claude_calls"] == 3
    assert usage_limits.remaining_public_runs() == 2


def test_button_is_disabled_once_the_cap_is_used(monkeypatch):
    monkeypatch.setenv("COMMITTEE_DAILY_RUN_LIMIT", "1")
    app = AppTest.from_string(COMMITTEE_SCRIPT, default_timeout=60).run()
    _run_button(app).click().run()
    assert app.session_state["claude_calls"] == 3
    app.run()  # the button is drawn before the click is handled, so check the next render
    assert _run_button(app).disabled
    assert any("0 of 1" in c.value for c in app.caption)


def test_cap_reached_between_render_and_click_makes_no_calls(monkeypatch):
    monkeypatch.setenv("COMMITTEE_DAILY_RUN_LIMIT", "1")
    app = AppTest.from_string(COMMITTEE_SCRIPT, default_timeout=60).run()
    assert usage_limits.try_start_public_run()  # another visitor takes the last run
    _run_button(app).click().run()
    assert app.session_state["claude_calls"] == 0
    assert _run_button(app).disabled


def test_last_run_cannot_be_taken_twice(monkeypatch):
    monkeypatch.setenv("COMMITTEE_DAILY_RUN_LIMIT", "1")
    import threading

    results: list[bool] = []
    threads = [
        threading.Thread(target=lambda: results.append(usage_limits.try_start_public_run(DAY)))
        for _ in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1


def test_owner_skips_the_cap(monkeypatch):
    code = "owner-code-long-enough-123"
    monkeypatch.setenv("AI_ACCESS_CODE", code)
    monkeypatch.setenv("COMMITTEE_DAILY_RUN_LIMIT", "0")
    app = AppTest.from_string(COMMITTEE_SCRIPT, default_timeout=60)
    app.session_state["owner_login"] = True
    app.run()
    app.text_input(key="access_code_owner").set_value(code)
    next(b for b in app.button if b.label == "Unlock").click().run()
    assert not _run_button(app).disabled
    _run_button(app).click().run()
    assert app.session_state["claude_calls"] == 3
