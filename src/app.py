"""Energy Intelligence System — CDF Energy AI Hackathon.

A decision-support tool for U.S. oil & gas investment analysis powered by real
EIA / FRED data, Prophet forecasting, and a multi-agent Claude AI committee that
surfaces regional production KPIs and investment-grade deal memos.

Each section is its own page under a top navigation bar, so only the page being
viewed runs. Analysis inputs live in a filter bar shared by the pages that use them.
"""
from __future__ import annotations

from pathlib import Path

import anthropic
import streamlit as st

from config import check_secrets, get_anthropic_key
from data.eia import BASINS
from data.loader import load_production
from ui.access import require_access
from ui.chat import render_chat
from ui.committee import render_committee
from ui.economics import render_economics
from ui.forecast import render_forecast
from ui.map import render_map
from ui.memo import render_memo
from ui.observatory import render_observatory
from ui.overview import render_overview
from ui.theme import APP_CSS, page_header

_ASSETS = Path(__file__).resolve().parent / "assets"

_BASIN_DISPLAY: dict[str, str] = {
    "Permian":     "Permian (TX · NM)",
    "Bakken":      "Bakken (ND)",
    "Eagle Ford":  "Eagle Ford (TX)",
    "Marcellus":   "Marcellus (PA · WV)",
    "Haynesville": "Haynesville (LA)",
    "Anadarko":    "Anadarko (OK)",
    "Appalachian": "Appalachian (PA · WV · OH)",
}

_FILTER_DEFAULTS: dict[str, object] = {
    "basin": BASINS[0],
    "fuel_type": "oil",
    "target_year": 2028,
    "wti": 75.0,
}


# ------------------------------------------------------------------
# Shared resources and state
# ------------------------------------------------------------------

@st.cache_resource
def _anthropic_client() -> anthropic.Anthropic:
    """Single Anthropic client reused across all agent calls."""
    return anthropic.Anthropic(api_key=get_anthropic_key())


def _init_filter_state() -> None:
    """Keep filter values when moving to a page that does not show the filter bar.

    Streamlit forgets a widget's value on any run where the widget is not drawn,
    so the values are re-stored every run. Widgets then read them by key.
    """
    for key, default in _FILTER_DEFAULTS.items():
        st.session_state[key] = st.session_state.get(key, default)
    # A basin clicked on the map is staged here, because a widget's value cannot
    # change after the widget has been drawn in the same run.
    if "_pending_basin" in st.session_state:
        st.session_state["basin"] = st.session_state.pop("_pending_basin")


def _context() -> tuple[str, str, int, float]:
    s = st.session_state
    return s["basin"], s["fuel_type"], int(s["target_year"]), float(s["wti"])


def _filter_bar() -> None:
    with st.container(border=True):
        c_basin, c_fuel, c_year, c_wti, c_refresh = st.columns(
            [2.2, 1.3, 2.4, 1.4, 1.0], vertical_alignment="bottom"
        )
        c_basin.selectbox(
            "Basin", BASINS, key="basin", format_func=lambda b: _BASIN_DISPLAY.get(b, b)
        )
        c_fuel.radio(
            "Fuel", ["oil", "gas"], key="fuel_type", horizontal=True, format_func=str.title
        )
        c_year.slider("Target year", 2015, 2030, key="target_year")
        c_wti.number_input(
            "WTI ($/bbl)", min_value=40.0, max_value=150.0, step=1.0, key="wti"
        )
        if c_refresh.button(
            "Refresh",
            icon=":material/refresh:",
            use_container_width=True,
            help="Reload source data and recompute everything.",
        ):
            st.cache_data.clear()
            st.rerun()


def _data_through(fuel_type: str) -> str | None:
    try:
        df = load_production(fuel_type=fuel_type)
    except Exception:
        return None
    return None if df.empty else df["ds"].max().strftime("%b %Y")


# ------------------------------------------------------------------
# Pages
# ------------------------------------------------------------------

def _overview_page() -> None:
    basin, fuel_type, target_year, wti = _context()
    render_overview(basin, fuel_type, target_year, wti, data_through=_data_through(fuel_type))


def _eyebrow() -> str:
    basin, fuel_type, target_year, _ = _context()
    return f"{basin} · {fuel_type.title()} · {target_year}"


def _forecast_page() -> None:
    page_header(
        eyebrow=_eyebrow(),
        title="Forecast",
        description="Monthly production history and a Prophet forecast with an 80% band. "
        "Move the cutoff to see how the forecast changes.",
    )
    render_forecast(*_context())


def _map_page() -> None:
    page_header("Map", "Click a basin to select it on every page.", eyebrow=_eyebrow())
    render_map(*_context())


def _committee_page() -> None:
    basin, fuel_type, target_year, wti = _context()
    page_header(
        eyebrow=_eyebrow(),
        title="Investment Committee",
        description="A bull and a bear analyst argue the case with live data, then a portfolio "
        "manager makes a recommendation. AI output for research, not investment advice.",
    )
    render_committee(basin, fuel_type, target_year, wti, _anthropic_client())


def _memo_page() -> None:
    basin, fuel_type, target_year, _ = _context()
    page_header("Deal Memo", "A memo built from the latest Committee run, with a PDF download.",
                eyebrow=_eyebrow())
    render_memo(basin, fuel_type, target_year)


def _economics_page() -> None:
    basin, fuel_type, _, wti = _context()
    page_header(
        eyebrow=_eyebrow(),
        title="Well Economics",
        description="Single-well decline curve and discounted cash flow. Inputs are illustrative "
        "basin defaults; edit them to match your case.",
    )
    render_economics(basin, fuel_type, wti)


def _analyst_page() -> None:
    page_header("AI Analyst", "Ask questions in plain English, answered from live EIA data.")
    if require_access("ai", form_key="chat"):
        render_chat(_anthropic_client())


def _observatory_page() -> None:
    page_header(
        "Observatory",
        "Latency, errors, token usage and spend for the AI agents and data APIs.",
    )
    if require_access("observatory", form_key="observatory"):
        render_observatory()


def main() -> None:
    st.set_page_config(
        page_title="Energy Intelligence",
        page_icon=str(_ASSETS / "mark.svg"),
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.html(APP_CSS)
    st.logo(str(_ASSETS / "logo.svg"), size="large")
    check_secrets()
    _init_filter_state()

    # The default page lives at the site root, so it takes no url_path.
    analysis_pages = [
        st.Page(_overview_page, title="Overview", icon=":material/space_dashboard:", default=True),
        st.Page(_forecast_page, title="Forecast", icon=":material/show_chart:", url_path="forecast"),
        st.Page(_map_page, title="Map", icon=":material/map:", url_path="map"),
        st.Page(_committee_page, title="Committee", icon=":material/groups:", url_path="committee"),
        st.Page(_memo_page, title="Memo", icon=":material/description:", url_path="memo"),
        st.Page(_economics_page, title="Economics", icon=":material/calculate:", url_path="economics"),
    ]
    other_pages = [
        st.Page(_analyst_page, title="AI Analyst", icon=":material/forum:", url_path="analyst"),
        st.Page(_observatory_page, title="Observatory", icon=":material/monitoring:",
                url_path="observatory"),
    ]
    page = st.navigation(analysis_pages + other_pages, position="top")

    if page.title in {p.title for p in analysis_pages}:
        _filter_bar()
    page.run()

    st.divider()
    st.caption(
        "Sources: EIA Open Data, FRED, USGS National Oil & Gas Assessment, BSEE. "
        "Basin figures add up state-level EIA data, so basins that share a state overlap. "
        "For research only, not investment advice."
    )


if __name__ == "__main__":
    main()
