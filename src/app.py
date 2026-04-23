"""Energy Intelligence System — CDF Energy AI Hackathon.

A decision-support tool for U.S. oil & gas investment analysis powered
by real EIA/FRED data, Prophet forecasting, and a multi-agent Claude AI
committee that surfaces regional production KPIs and investment memos.
"""
from __future__ import annotations

import streamlit as st

from config import check_secrets


def render_overview() -> None:
    st.info("Coming soon — Overview")


def render_forecast() -> None:
    st.info("Coming soon — Forecast")


def render_committee() -> None:
    st.info("Coming soon — Committee")


def render_memo() -> None:
    st.info("Coming soon — Memo")


def main() -> None:
    st.set_page_config(
        page_title="Energy Intelligence System",
        page_icon="⚡",
        layout="wide",
    )

    st.title("Energy Intelligence System")
    st.caption("U.S. Oil & Gas Investment Analysis · CDF Energy AI Hackathon")

    check_secrets()

    tab_overview, tab_forecast, tab_committee, tab_memo = st.tabs(
        ["Overview", "Forecast", "Committee", "Memo"]
    )

    with tab_overview:
        render_overview()

    with tab_forecast:
        render_forecast()

    with tab_committee:
        render_committee()

    with tab_memo:
        render_memo()


if __name__ == "__main__":
    main()
