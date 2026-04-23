"""API key access and secret validation for Streamlit deployment."""
from __future__ import annotations

import streamlit as st

_REQUIRED_SECRETS: dict[str, str] = {
    "EIA_API_KEY": "EIA Open Data API key — free at api.eia.gov",
    "FRED_API_KEY": "FRED API key — free at fred.stlouisfed.org",
    "ANTHROPIC_API_KEY": "Anthropic API key — console.anthropic.com",
}


def check_secrets() -> None:
    """Stop the app early with a clear error if any API key is absent."""
    missing = [k for k in _REQUIRED_SECRETS if k not in st.secrets]
    if missing:
        lines = "\n".join(
            f"• **{k}** — {_REQUIRED_SECRETS[k]}" for k in missing
        )
        st.error(
            f"Missing required API keys in Streamlit secrets:\n\n{lines}\n\n"
            "Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` "
            "and fill in your keys, then restart the app."
        )
        st.stop()


def get_eia_key() -> str:
    return str(st.secrets["EIA_API_KEY"])


def get_fred_key() -> str:
    return str(st.secrets["FRED_API_KEY"])


def get_anthropic_key() -> str:
    return str(st.secrets["ANTHROPIC_API_KEY"])
