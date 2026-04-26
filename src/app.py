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

_BASIN_DISPLAY: dict[str, str] = {
    "Permian":     "Permian Basin  (TX · NM)",
    "Bakken":      "Bakken  (ND)",
    "Eagle Ford":  "Eagle Ford  (TX)",
    "Marcellus":   "Marcellus  (PA · WV)",
    "Haynesville": "Haynesville  (LA)",
    "Anadarko":    "Anadarko  (OK)",
    "Appalachian": "Appalachian  (PA · WV · OH)",
}

_CSS = """
<style>
/* ── global background ───────────────────────────────────── */
.stApp { background: linear-gradient(160deg,#07090f 0%,#0d1117 60%,#0a0e1a 100%); }

/* ── sidebar ─────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg,#0c1122 0%,#070a12 100%) !important;
    border-right: 1px solid rgba(74,144,213,.12);
}

/* ── metric cards ────────────────────────────────────────── */
[data-testid="metric-container"] {
    background: linear-gradient(135deg,#131d30 0%,#0d1520 100%);
    border: 1px solid rgba(74,144,213,.18);
    border-radius: 12px;
    padding: 18px 20px;
    box-shadow: 0 4px 24px rgba(0,0,0,.35);
}
[data-testid="stMetricValue"]  { font-size: 1.45rem !important; font-weight: 700; }
[data-testid="stMetricLabel"]  { color: rgba(255,255,255,.55) !important; font-size:.8rem; }
[data-testid="stMetricDelta"]  { font-size: .85rem !important; }

/* ── tabs ────────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"]  { gap:6px; border-bottom:1px solid rgba(255,255,255,.08); }
.stTabs [data-baseweb="tab"]       { border-radius:8px 8px 0 0; color:rgba(255,255,255,.55); font-weight:600; padding:8px 16px; }
.stTabs [aria-selected="true"]     { color:#4A90D9 !important; border-bottom:2px solid #4A90D9 !important; background:rgba(74,144,213,.08) !important; }

/* ── buttons ─────────────────────────────────────────────── */
.stButton>button {
    background: linear-gradient(135deg,#1a3855,#0f2538);
    border: 1px solid rgba(74,144,213,.28);
    border-radius: 8px; color:#d4e5f7; font-weight:500;
    transition: all .2s;
}
.stButton>button:hover { border-color:rgba(74,144,213,.7); background:linear-gradient(135deg,#22476b,#163046); }
.stButton>button[kind="primary"] { background:linear-gradient(135deg,#e8622a,#c94d18); border:none; }

/* ── chat messages ───────────────────────────────────────── */
[data-testid="stChatMessage"] {
    background: rgba(18,26,44,.75);
    border: 1px solid rgba(74,144,213,.10);
    border-radius: 12px;
    margin: 6px 0;
    padding: 4px 8px;
}

/* ── dataframe ───────────────────────────────────────────── */
[data-testid="stDataFrame"] { border-radius: 10px; overflow:hidden; }

/* ── expanders ───────────────────────────────────────────── */
[data-testid="stExpander"] summary {
    color: rgba(255,255,255,.65);
    font-size: .85rem;
}

/* ── info / success boxes ────────────────────────────────── */
.stInfo    { background:rgba(74,144,213,.08); border-color:rgba(74,144,213,.3); border-radius:8px; }
.stSuccess { background:rgba(76,175,80,.08);  border-color:rgba(76,175,80,.3);  border-radius:8px; }
.stWarning { background:rgba(255,152,0,.08);  border-color:rgba(255,152,0,.3);  border-radius:8px; }

/* ── divider ─────────────────────────────────────────────── */
hr { border-color:rgba(255,255,255,.07) !important; }

/* ── chat input ──────────────────────────────────────────── */
[data-testid="stChatInput"] textarea {
    background: #111827 !important;
    border: 1px solid rgba(74,144,213,.25) !important;
    border-radius: 10px !important;
    color: #e2e8f0 !important;
}
</style>
"""
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

        basin: str = st.selectbox(
            "Basin",
            BASINS,
            key="basin",
            format_func=lambda b: _BASIN_DISPLAY.get(b, b),
        )
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

    st.markdown(_CSS, unsafe_allow_html=True)
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
