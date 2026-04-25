"""Energy Intelligence System — CDF Energy AI Hackathon.

A decision-support tool for U.S. oil & gas investment analysis powered by real
EIA / FRED data, Prophet forecasting, and a multi-agent Claude AI committee that
surfaces regional production KPIs and investment-grade deal memos.
"""
from __future__ import annotations

import anthropic
import streamlit as st

from config import check_secrets, get_anthropic_key
from data.eia import BASINS
from ui.chat import render_chat
from ui.committee import render_committee
from ui.economics import render_economics
from ui.forecast import render_forecast
from ui.map import render_map
from ui.memo import render_memo
from ui.overview import render_overview


# ------------------------------------------------------------------
# Shared resources (initialised once per Streamlit process)
# ------------------------------------------------------------------

@st.cache_resource
def _anthropic_client() -> anthropic.Anthropic:
    """Single Anthropic client reused across all agent calls."""
    return anthropic.Anthropic(api_key=get_anthropic_key())


# ------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------

def _sidebar() -> tuple[str, str, int, float]:
    # Apply any pending basin selection from the map tab.
    # This must happen BEFORE the selectbox widget is instantiated so that
    # Streamlit picks up the new value on this render pass.
    if "_pending_basin" in st.session_state:
        st.session_state["basin"] = st.session_state.pop("_pending_basin")

    with st.sidebar:
        st.markdown("## ⚡ Energy Intelligence")
        st.markdown("*U.S. Oil & Gas Investment Analysis*")
        st.divider()

        # key="basin" lets the map tab update this widget via session_state
        basin: str = st.selectbox("Basin", BASINS, key="basin")
        fuel_type: str = st.radio("Fuel type", ["oil", "gas"], horizontal=True)
        target_year: int = st.slider("Target year", 2015, 2030, 2028)
        wti: float = st.number_input(
            "WTI assumption ($/bbl)",
            min_value=40.0,
            max_value=150.0,
            value=75.0,
            step=1.0,
        )

        st.divider()
        if st.button(
            "🔄 Refresh data",
            use_container_width=True,
            help="Clears all @st.cache_data results and reruns the app.",
        ):
            st.cache_data.clear()
            st.rerun()

        st.caption(
            "Data: EIA Open Data API · FRED  \n"
            "Forecast: Facebook Prophet  \n"
            "AI: Anthropic Claude"
        )

    return basin, fuel_type, target_year, wti


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="Energy Intelligence System",
        page_icon="⚡",
        layout="wide",
    )

    check_secrets()

    basin, fuel_type, target_year, wti = _sidebar()
    client = _anthropic_client()

    st.title("Energy Intelligence System")
    st.caption(
        f"**{basin}** · {fuel_type.upper()} · "
        f"Target year **{target_year}** · WTI **${wti:.0f}/bbl**"
    )

    tab_ov, tab_fc, tab_map, tab_chat, tab_co, tab_me, tab_econ = st.tabs(
        [
            "📊 Overview",
            "📈 Forecast",
            "🗺️ Map",
            "💬 Chat",
            "🏛️ Committee",
            "📄 Memo",
            "💰 Economics",
        ]
    )

    with tab_ov:
        render_overview(basin, fuel_type, target_year, wti)

    with tab_fc:
        render_forecast(basin, fuel_type, target_year, wti)

    with tab_map:
        render_map(basin, fuel_type, target_year, wti)

    with tab_chat:
        render_chat(client)

    with tab_co:
        render_committee(basin, fuel_type, target_year, wti, client)

    with tab_me:
        render_memo(basin, fuel_type, target_year)

    with tab_econ:
        render_economics(basin, fuel_type, wti)


if __name__ == "__main__":
    main()
