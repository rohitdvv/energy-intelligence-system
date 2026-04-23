"""API key access — supports st.secrets (Streamlit app) and env vars (CLI).

Priority: st.secrets → os.environ → .env file loaded via python-dotenv.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

# Load .env when running outside Streamlit (CLI scripts, tests).
# In Streamlit context this is harmless — st.secrets takes priority.
load_dotenv()

_REQUIRED: dict[str, str] = {
    "EIA_API_KEY": "EIA Open Data API key — free at api.eia.gov",
    "FRED_API_KEY": "FRED API key — free at fred.stlouisfed.org",
    "ANTHROPIC_API_KEY": "Anthropic API key — console.anthropic.com",
}


def _get_secret(key: str) -> str:
    """Read key from st.secrets (Streamlit) or os.environ (CLI)."""
    try:
        import streamlit as st  # local import — not available in all CLI envs

        return str(st.secrets[key])
    except Exception:
        value = os.environ.get(key, "")
        if not value:
            raise RuntimeError(
                f"'{key}' not found. Set it in .streamlit/secrets.toml "
                f"(Streamlit app) or in a .env file / environment variable (CLI)."
            )
        return value


def check_secrets() -> None:
    """Stop the Streamlit app early with a clear error if any API key is absent."""
    import streamlit as st

    missing: list[str] = []
    for k in _REQUIRED:
        try:
            _get_secret(k)
        except RuntimeError:
            missing.append(k)

    if missing:
        lines = "\n".join(f"• **{k}** — {_REQUIRED[k]}" for k in missing)
        st.error(
            f"Missing required API keys in Streamlit secrets:\n\n{lines}\n\n"
            "Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` "
            "and fill in your keys, then restart the app."
        )
        st.stop()


def get_eia_key() -> str:
    return _get_secret("EIA_API_KEY")


def get_fred_key() -> str:
    return _get_secret("FRED_API_KEY")


def get_anthropic_key() -> str:
    return _get_secret("ANTHROPIC_API_KEY")
