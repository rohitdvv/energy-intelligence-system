"""Energy Intelligence System — CDF Energy AI Hackathon.

A decision-support tool for U.S. oil & gas investment analysis powered by real
EIA / FRED data, backtested forecasting, and a multi-agent Claude AI committee that
surfaces regional production KPIs and investment-grade deal memos.

Each section is its own page under a top navigation bar, so only the page being
viewed runs. Analysis inputs live in a filter bar shared by the pages that use them.
"""
from __future__ import annotations

from pathlib import Path

import anthropic
import streamlit as st

from reload_guard import purge_if_changed

# Must run before any project import below, so a deploy loads the new files
# instead of modules left in memory by the previous version.
if purge_if_changed(Path(__file__).resolve().parent):
    st.cache_data.clear()
    st.cache_resource.clear()

from config import check_secrets, get_anthropic_key  # noqa: E402
from data.eia import BASIN_SOURCE, BASINS  # noqa: E402
from data.loader import load_production  # noqa: E402
from kpi.metrics import DEFAULT_HENRY_HUB, DEFAULT_WTI  # noqa: E402
from ui.access import require_access  # noqa: E402
from ui.chat import render_chat  # noqa: E402
from ui.committee import render_committee  # noqa: E402
from ui.economics import render_economics  # noqa: E402
from ui.forecast import render_forecast  # noqa: E402
from ui.map import render_map  # noqa: E402
from ui.memo import render_memo  # noqa: E402
from ui.observatory import render_observatory  # noqa: E402
from ui.overview import render_overview  # noqa: E402
from ui.theme import APP_CSS, page_header  # noqa: E402

_ASSETS = Path(__file__).resolve().parent / "assets"


def _basin_label(basin: str) -> str:
    """Basin name plus where its figures come from."""
    return f"{basin} · {BASIN_SOURCE.get(basin, '?')}"


_FILTER_DEFAULTS: dict[str, object] = {
    "basin": BASINS[0],
    "fuel_type": "oil",
    "target_year": 2028,
    "wti": DEFAULT_WTI,
    "henry_hub": DEFAULT_HENRY_HUB,
}


# ------------------------------------------------------------------
# Shared resources and state
# ------------------------------------------------------------------

@st.cache_resource
def _anthropic_client() -> anthropic.Anthropic:
    """Single Anthropic client reused across all agent calls."""
    # Retries are done in agents.llm_call so every attempt reaches telemetry;
    # the SDK's own silent retries would multiply them.
    return anthropic.Anthropic(api_key=get_anthropic_key(), max_retries=0)


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


def _henry_hub() -> float:
    return float(st.session_state["henry_hub"])


def _filter_bar() -> None:
    with st.container(border=True):
        c_basin, c_fuel, c_year, c_price, c_refresh = st.columns(
            [2.4, 1.3, 2.3, 1.5, 1.0], vertical_alignment="bottom"
        )
        c_basin.selectbox(
            "Basin", BASINS, key="basin", format_func=_basin_label,
            help="Basins are EIA's regional estimates, built from the counties over each play, so "
            "they do not overlap. Appalachia covers the Marcellus and Utica. Anadarko is the "
            "Oklahoma state total, because EIA publishes no Anadarko region.",
        )
        c_fuel.radio(
            "Fuel", ["oil", "gas"], key="fuel_type", horizontal=True, format_func=str.title
        )
        c_year.slider("Target year", 2015, 2030, key="target_year")
        if st.session_state["fuel_type"] == "gas":
            c_price.number_input(
                "Henry Hub ($/MMBtu)", min_value=1.0, max_value=20.0, step=0.25, key="henry_hub",
                help="Gas price used for gas revenue and well economics.",
            )
        else:
            c_price.number_input(
                "WTI ($/bbl)", min_value=40.0, max_value=150.0, step=1.0, key="wti",
                help="Oil price used for oil revenue and well economics.",
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
    render_overview(basin, fuel_type, target_year, wti, data_through=_data_through(fuel_type),
                    henry_hub=_henry_hub())


def _eyebrow() -> str:
    basin, fuel_type, target_year, _ = _context()
    return f"{basin} ({BASIN_SOURCE.get(basin, '?')}) · {fuel_type.title()} · {target_year}"


def _forecast_page() -> None:
    page_header(
        eyebrow=_eyebrow(),
        title="Forecast",
        description="Monthly production history and a forecast from whichever model has been "
        "most accurate for this basin in past-year tests. The 80% band comes from that model's "
        "past errors. Move the cutoff to see how the forecast changes.",
    )
    render_forecast(*_context())


def _map_page() -> None:
    page_header("Map", "Click a basin to select it on every page.", eyebrow=_eyebrow())
    render_map(*_context(), henry_hub=_henry_hub())


def _committee_page() -> None:
    basin, fuel_type, target_year, wti = _context()
    page_header(
        eyebrow=_eyebrow(),
        title="Investment Committee",
        description="A bull and a bear analyst argue the case with live data, then a portfolio "
        "manager makes a recommendation. AI output for research, not investment advice.",
    )
    render_committee(basin, fuel_type, target_year, wti, _anthropic_client(),
                     henry_hub=_henry_hub())


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
        description="Single-well decline curve and discounted cash flow after royalty, taxes "
        "and operating costs. Inputs are illustrative basin defaults; edit them to match your case.",
    )
    render_economics(basin, fuel_type, wti, henry_hub=_henry_hub())


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
        "Basin figures are EIA regional estimates (Anadarko is the Oklahoma state total). "
        "For research only, not investment advice."
    )


if __name__ == "__main__":
    main()
