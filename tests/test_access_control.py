"""Paid features and the Observatory stay locked without the right code."""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

GOOD_CODE = "correct-horse-battery-staple"

SCRIPT = """
import streamlit as st
from ui.access import require_access

if require_access("ai", form_key="test"):
    st.title("PROTECTED CONTENT")
"""


def _app():
    return AppTest.from_string(SCRIPT, default_timeout=30).run()


def _shows_protected(app) -> bool:
    return any(item.value == "PROTECTED CONTENT" for item in app.title)


def test_locked_when_code_is_unset(monkeypatch):
    monkeypatch.delenv("AI_ACCESS_CODE", raising=False)
    app = _app()
    assert not app.exception
    assert not _shows_protected(app)
    assert not app.text_input, "no prompt, because no code can ever match"


def test_short_code_counts_as_unset(monkeypatch):
    monkeypatch.setenv("AI_ACCESS_CODE", "short")
    app = _app()
    assert not _shows_protected(app)
    assert not app.text_input


def test_wrong_code_is_rejected(monkeypatch):
    monkeypatch.setenv("AI_ACCESS_CODE", GOOD_CODE)
    app = _app()
    app.text_input[0].set_value("not-the-right-code-at-all")
    app.button[0].click().run()
    assert not _shows_protected(app)
    assert app.error


def test_empty_submission_is_rejected(monkeypatch):
    monkeypatch.setenv("AI_ACCESS_CODE", GOOD_CODE)
    app = _app()
    app.button[0].click().run()
    assert not _shows_protected(app)


def test_correct_code_unlocks_and_persists(monkeypatch):
    monkeypatch.setenv("AI_ACCESS_CODE", GOOD_CODE)
    app = _app()
    app.text_input[0].set_value(GOOD_CODE)
    app.button[0].click().run()
    assert not app.exception
    assert _shows_protected(app)
    app.run()
    assert _shows_protected(app), "a later rerun stays unlocked"


def test_rotating_the_code_signs_sessions_out(monkeypatch):
    monkeypatch.setenv("AI_ACCESS_CODE", GOOD_CODE)
    app = _app()
    app.text_input[0].set_value(GOOD_CODE)
    app.button[0].click().run()
    assert _shows_protected(app)

    monkeypatch.setenv("AI_ACCESS_CODE", "a-brand-new-rotated-access-code")
    app.run()
    assert not _shows_protected(app)


@pytest.mark.parametrize("scope", ["ai", "observatory"])
def test_scopes_do_not_share_access(monkeypatch, scope):
    other = "observatory" if scope == "ai" else "ai"
    names = {"ai": "AI_ACCESS_CODE", "observatory": "OBSERVATORY_ACCESS_CODE"}
    monkeypatch.setenv(names[scope], GOOD_CODE)
    monkeypatch.setenv(names[other], "a-different-long-access-code")
    app = AppTest.from_string(
        f"""
import streamlit as st
from ui.access import require_access
if require_access("{scope}", form_key="one"):
    st.title("FIRST")
if require_access("{other}", form_key="two"):
    st.title("SECOND")
""",
        default_timeout=30,
    ).run()
    app.text_input[0].set_value(GOOD_CODE)
    app.button[0].click().run()
    assert [item.value for item in app.title] == ["FIRST"]
