"""Access codes for features that spend money or expose operational data.

The public data views stay open. Chat and Committee make paid Claude calls, and
the Observatory shows activity from every viewer, so each sits behind a code set
in Streamlit secrets. A feature whose code is unset or too short stays locked.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

import streamlit as st

from config import get_optional_secret

_MIN_CODE_LENGTH = 16
_FAILED_ATTEMPT_DELAY_SECONDS = 1.0


@dataclass(frozen=True)
class _Scope:
    secret_name: str
    feature: str


_SCOPES: dict[str, _Scope] = {
    "ai": _Scope("AI_ACCESS_CODE", "AI Analyst"),
    "observatory": _Scope("OBSERVATORY_ACCESS_CODE", "Observatory"),
}


def _configured_code(scope: _Scope) -> str | None:
    code = get_optional_secret(scope.secret_name)
    return code if code and len(code) >= _MIN_CODE_LENGTH else None


def _fingerprint(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def has_access(scope_name: str) -> bool:
    """True if this session has unlocked the scope with the current code."""
    code = _configured_code(_SCOPES[scope_name])
    return code is not None and st.session_state.get(f"_access_{scope_name}") == _fingerprint(code)


def require_access(scope_name: str, form_key: str) -> bool:
    """Return True if this session may use the feature; otherwise render a prompt.

    The session keeps a fingerprint of the code it unlocked with, so rotating the
    secret signs existing sessions out. *form_key* must be unique per call site.
    """
    scope = _SCOPES[scope_name]
    code = _configured_code(scope)
    state_key = f"_access_{scope_name}"

    if code is None:
        st.info(
            f"The {scope.feature} is locked on this deployment. The owner can turn "
            f"it on by setting `{scope.secret_name}` in the app's secrets "
            f"(at least {_MIN_CODE_LENGTH} characters).",
            icon=":material/lock:",
        )
        return False

    if st.session_state.get(state_key) == _fingerprint(code):
        return True

    st.info(f"Enter the access code to use the {scope.feature}.", icon=":material/lock:")
    with st.form(f"access_form_{form_key}", clear_on_submit=True):
        entered = st.text_input(
            "Access code", type="password", key=f"access_code_{form_key}"
        )
        submitted = st.form_submit_button("Unlock")

    if submitted:
        if hmac.compare_digest(entered.encode(), code.encode()):
            st.session_state[state_key] = _fingerprint(code)
            st.rerun()
        time.sleep(_FAILED_ATTEMPT_DELAY_SECONDS)
        st.error("That access code is not valid.")
    return False
