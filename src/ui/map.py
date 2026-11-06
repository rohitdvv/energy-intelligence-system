"""Geographic visualization tab — interactive U.S. basin production map.

Features:
- Plotly Scattergeo bubble map with per-basin markers (size + colour = metric)
- State-level choropleth background shaded by basin production
- Switchable overlay: RPI Score / Projected Production / YoY Growth
- Click a basin marker to update the active basin across the entire app
- KPI preview panel below the map for the selected basin
"""
from __future__ import annotations

from typing import Any

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from ui.theme import AXIS, FLARE, GRID, MUTED, TRANSPARENT, style_figure

from agents.tools import execute_tool
from data.eia import BASINS
from kpi.metrics import DEFAULT_HENRY_HUB, price_assumption_text

# ---------------------------------------------------------------------------
# Geographic constants
# ---------------------------------------------------------------------------

# Approximate centre coordinates for each basin (lat, lon)
BASIN_COORDS: dict[str, dict[str, float]] = {
    "Permian":     {"lat": 31.5,  "lon": -103.0},
    "Bakken":      {"lat": 47.8,  "lon": -103.0},
    "Eagle Ford":  {"lat": 28.5,  "lon": -98.0},
    "Marcellus":   {"lat": 41.0,  "lon": -77.5},
    "Haynesville": {"lat": 32.0,  "lon": -93.5},
    "Anadarko":    {"lat": 35.5,  "lon": -98.0},
    "Appalachian": {"lat": 39.5,  "lon": -80.0},
}

# One primary state per basin for the choropleth background layer.
# Where a basin spans multiple states the dominant production state is used.
BASIN_PRIMARY_STATE: dict[str, str] = {
    "Permian":     "TX",
    "Bakken":      "ND",
    "Eagle Ford":  "TX",
    "Marcellus":   "PA",
    "Haynesville": "LA",
    "Anadarko":    "OK",
    "Appalachian": "WV",
}

# Secondary states to also shade for context
BASIN_ALL_STATES: dict[str, list[str]] = {
    "Permian":     ["TX", "NM"],
    "Bakken":      ["ND"],
    "Eagle Ford":  ["TX"],
    "Marcellus":   ["PA", "WV"],
    "Haynesville": ["LA"],
    "Anadarko":    ["OK"],
    "Appalachian": ["PA", "WV", "OH"],
}

_OVERLAY_LABELS: dict[str, str] = {
    "rpi":        "RPI Score (0-100)",
    "production": "Projected Production",
    "yoy":        "YoY Growth (%)",
}


def _md_price(fuel_type: str, wti: float, henry_hub: float) -> str:
    """Price text with dollar signs escaped, so Streamlit markdown does not read them as math."""
    return price_assumption_text(fuel_type, wti, henry_hub).replace("$", "\\$")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def _load_comparison(
    fuel_type: str, target_year: int, wti: float, henry_hub: float = DEFAULT_HENRY_HUB
) -> dict[str, Any]:
    return execute_tool(
        "compare_basins",
        {"fuel_type": fuel_type, "target_year": target_year, "wti_assumption": wti,
         "henry_hub_assumption": henry_hub},
    )


# ---------------------------------------------------------------------------
# Chart builders
# ---------------------------------------------------------------------------

def _state_choropleth(
    ranked: list[dict[str, Any]],
    metric: str,
) -> go.Choropleth:
    """Build a state-level choropleth background layer from basin metrics."""
    state_vals: dict[str, float] = {}

    for b in ranked:
        basin = b.get("basin", "")
        states = BASIN_ALL_STATES.get(basin, [])
        if not states:
            continue

        if metric == "rpi":
            val = b.get("relative_performance_index")
        elif metric == "production":
            val = b.get("projected_production", {}).get("value")
        elif metric == "yoy":
            val = b.get("growth_rate", {}).get("yoy_pct")
        else:
            val = None

        if val is None:
            continue

        per_state = val / len(states)
        for s in states:
            # For states shared by multiple basins keep the higher value
            state_vals[s] = max(state_vals.get(s, per_state), per_state)

    if not state_vals:
        return go.Choropleth()

    locations = list(state_vals.keys())
    z_vals = [state_vals[s] for s in locations]

    return go.Choropleth(
        locations=locations,
        z=z_vals,
        locationmode="USA-states",
        colorscale=[[0.0, "rgba(232,105,42,0.10)"], [1.0, "rgba(232,105,42,0.62)"]],
        showscale=False,
        hoverinfo="skip",
        zmin=min(z_vals),
        zmax=max(z_vals),
        marker_line_color=AXIS,
        marker_line_width=0.5,
        colorbar=None,
    )


def _basin_scatter(
    ranked: list[dict[str, Any]],
    metric: str,
    selected_basin: str,
    fuel_type: str,
    target_year: int,
) -> go.Scattergeo:
    """Build the basin bubble scatter layer."""
    lats, lons, labels, vals, hover_texts = [], [], [], [], []

    for b in ranked:
        basin = b.get("basin", "")
        coords = BASIN_COORDS.get(basin)
        if not coords:
            continue

        pp = b.get("projected_production", {})
        unit = pp.get("unit", "")
        prod_val = pp.get("value")
        rpi = b.get("relative_performance_index")
        yoy = b.get("growth_rate", {}).get("yoy_pct")
        rev_mm = b.get("revenue_potential", {}).get("revenue_usd_millions")

        if metric == "rpi":
            val = rpi or 0
        elif metric == "production":
            val = prod_val or 0
        elif metric == "yoy":
            val = yoy or 0
        else:
            val = 0

        hover = (
            f"<b>{basin}</b><br>"
            f"Production: {prod_val:,.0f} {unit}<br>" if prod_val else f"<b>{basin}</b><br>"
        )
        if rpi is not None:
            hover += f"RPI: {rpi:.1f}/100<br>"
        if yoy is not None:
            hover += f"YoY Growth: {yoy:+.1f}%<br>"
        if rev_mm:
            hover += f"Revenue: ${rev_mm * 12:,.0f}M/yr"

        lats.append(coords["lat"])
        lons.append(coords["lon"])
        labels.append(basin)
        vals.append(val)
        hover_texts.append(hover)

    if not vals:
        return go.Scattergeo()

    arr = np.array(vals, dtype=float)
    lo, hi = arr.min(), arr.max()
    span = hi - lo if hi > lo else 1.0
    sizes = 22 + (arr - lo) / span * 28   # 22–50 px

    border_colors = [
        FLARE if name == selected_basin else MUTED
        for name in labels
    ]
    border_widths = [3 if name == selected_basin else 1 for name in labels]

    return go.Scattergeo(
        lat=lats,
        lon=lons,
        text=labels,
        hovertext=hover_texts,
        customdata=labels,
        mode="markers+text",
        textposition="top center",
        textfont=dict(size=11),
        hovertemplate="%{hovertext}<extra></extra>",
        marker=dict(
            size=sizes,
            color=vals,
            colorscale="RdYlGn",
            cmin=float(arr.min()),
            cmax=float(arr.max()),
            line=dict(color=border_colors, width=border_widths),
            colorbar=dict(
                title=_OVERLAY_LABELS.get(metric, metric),
                thickness=14,
                len=0.55,
                x=1.01,
                titlefont=dict(size=11),
            ),
            showscale=True,
            opacity=0.92,
        ),
        name="Basins",
    )


def _build_map_figure(
    ranked: list[dict[str, Any]],
    metric: str,
    selected_basin: str,
    fuel_type: str,
    target_year: int,
) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(_state_choropleth(ranked, metric))
    fig.add_trace(_basin_scatter(ranked, metric, selected_basin, fuel_type, target_year))

    style_figure(fig, height=500, margin=dict(t=48, b=10, l=0, r=60))
    fig.update_layout(
        title=dict(
            text=(
                f"{fuel_type.title()} production, {target_year}  "
                f"<span style='font-size:12px;opacity:.65'>Overlay: "
                f"{_OVERLAY_LABELS.get(metric, metric)}</span>"
            ),
            font=dict(size=14),
            x=0.01,
        ),
        geo=dict(
            scope="usa",
            projection=dict(type="albers usa"),
            showland=True,
            landcolor=GRID,
            showlakes=True,
            lakecolor=TRANSPARENT,
            showcoastlines=True,
            coastlinecolor=AXIS,
            showsubunits=True,
            subunitcolor=AXIS,
            bgcolor=TRANSPARENT,
            showframe=False,
        ),
        showlegend=False,
    )
    return fig


# ---------------------------------------------------------------------------
# KPI panel
# ---------------------------------------------------------------------------

def _render_kpi_panel(
    basin: str,
    ranked: list[dict[str, Any]],
    fuel_type: str,
    target_year: int,
    wti: float,
    henry_hub: float = DEFAULT_HENRY_HUB,
) -> None:
    """Render KPI metric cards for the selected basin."""
    st.subheader(f"{basin} · {fuel_type.title()} · {target_year}", anchor=False)

    basin_data = next((b for b in ranked if b.get("basin") == basin), None)
    if not basin_data:
        st.warning(f"No data available for {basin}.")
        return

    c1, c2, c3, c4 = st.columns(4)

    pp = basin_data.get("projected_production", {})
    prod_val = pp.get("value")
    c1.metric(
        "Projected Production",
        f"{prod_val:,.0f} {pp.get('unit', '')}" if prod_val else "N/A",
        help=f"Source: {pp.get('source', 'forecast')} · {target_year} annual total",
    )

    yoy = basin_data.get("growth_rate", {}).get("yoy_pct")
    c2.metric(
        "YoY Growth",
        f"{yoy:+.1f}%" if yoy is not None else "N/A",
        delta=f"{yoy:.1f}%" if yoy is not None else None,
    )

    rpi = basin_data.get("relative_performance_index")
    c3.metric(
        "RPI Score",
        f"{rpi:.1f} / 100" if rpi is not None else "N/A",
        help="Relative Performance Index vs. all 7 peer basins (0=lowest, 100=highest)",
    )

    rev = basin_data.get("revenue_potential", {}).get("revenue_usd_millions")
    c4.metric(
        "Revenue Potential",
        f"${rev * 12:,.0f}M / yr" if rev else "N/A",
        help=f"Annualised gross value at {_md_price(fuel_type, wti, henry_hub)}, before royalties, taxes and costs",
    )


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render_map(
    basin: str,
    fuel_type: str,
    target_year: int,
    wti: float,
    henry_hub: float = DEFAULT_HENRY_HUB,
) -> None:
    """Render the geographic basin map tab."""
    col_overlay, col_tip = st.columns([1, 3])
    with col_overlay:
        overlay: str = st.selectbox(
            "Data overlay",
            options=list(_OVERLAY_LABELS.keys()),
            format_func=lambda x: _OVERLAY_LABELS[x],
            key="map_overlay",
        )
    with col_tip:
        st.info(
            "Bubble size and colour show the selected overlay. "
            "The orange ring marks the selected basin.",
            icon=":material/info:",
        )

    with st.spinner("Loading basin data..."):
        try:
            cmp = _load_comparison(fuel_type, target_year, wti, henry_hub)
        except Exception as exc:
            st.error(f"Failed to load map data: {exc}")
            return

    if "error" in cmp:
        st.error(f"Data error: {cmp['error']}")
        return

    ranked: list[dict[str, Any]] = [
        b for b in cmp.get("ranked_basins", []) if "error" not in b
    ]
    if not ranked:
        st.warning("No basin data available yet — run the data fetcher first.")
        return

    fig = _build_map_figure(ranked, overlay, basin, fuel_type, target_year)

    # Render with Streamlit plotly event support
    event = st.plotly_chart(
        fig,
        use_container_width=True,
        on_select="rerun",
        selection_mode=["points"],
        key="map_chart",
    )

    # Handle basin click: update session state and trigger a second rerun so the
    # sidebar selectbox (which renders before this tab) reflects the new choice.
    if event and hasattr(event, "selection") and event.selection.points:
        pt = event.selection.points[0]
        # Scattergeo returns 'text' for the marker label
        clicked = pt.get("text") or (
            pt.get("customdata") if isinstance(pt.get("customdata"), str) else None
        )
        # Fall back to point_index lookup
        if not clicked:
            idx = pt.get("point_index")
            basin_names = [b.get("basin", "") for b in ranked if b.get("basin") in BASIN_COORDS]
            if idx is not None and idx < len(basin_names):
                clicked = basin_names[idx]

        if clicked and clicked in BASINS and clicked != basin:
            # Cannot write a widget key mid-render; stage it for the next pass.
            st.session_state["_pending_basin"] = clicked
            st.rerun()

    st.divider()
    _render_kpi_panel(basin, ranked, fuel_type, target_year, wti, henry_hub)
