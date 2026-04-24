"""Overview tab: KPI metric cards, basin comparison table, RPI bar chart."""
from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from agents.tools import execute_tool
from data.loader import load_production_no_cache
from kpi.metrics import basin_kpi_summary
from models.forecaster import forecast_basin as _fit


# ------------------------------------------------------------------
# Cached data fetchers
# ------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def _kpi_for_basin(
    basin: str, fuel_type: str, target_year: int, wti: float
) -> dict[str, Any] | None:
    df = load_production_no_cache(fuel_type=fuel_type, live_fetch=True)
    bdf = df[df["basin"] == basin][["ds", "y"]].dropna().copy()
    if bdf.empty:
        return None
    cutoff = int(bdf["ds"].dt.year.max())
    result = _fit(bdf, cutoff, target_year, basin=basin, fuel_type=fuel_type)
    return basin_kpi_summary(result, target_year, wti_price=wti)


@st.cache_data(ttl=3600, show_spinner=False)
def _compare_all_basins(
    fuel_type: str, target_year: int, wti: float
) -> dict[str, Any]:
    return execute_tool(
        "compare_basins",
        {"fuel_type": fuel_type, "target_year": target_year, "wti_assumption": wti},
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _fmt(val: float | None, prefix: str = "", suffix: str = "") -> str:
    if val is None:
        return "N/A"
    if abs(val) >= 1_000:
        return f"{prefix}{val:,.0f}{suffix}"
    return f"{prefix}{val:.1f}{suffix}"


# ------------------------------------------------------------------
# Render
# ------------------------------------------------------------------

def render_overview(basin: str, fuel_type: str, target_year: int, wti: float) -> None:
    """Render the Overview tab."""

    # First-load notice — shown once per session when no local parquet files exist
    if not st.session_state.get("_eis_fetch_banner_shown"):
        st.session_state["_eis_fetch_banner_shown"] = True
        from pathlib import Path
        _raw = Path(__file__).resolve().parents[2] / "data" / "raw"
        if not any(_raw.glob("*.parquet")):
            st.info("⏳ First-time data fetch from EIA/FRED — this takes about a minute.")

    # --- KPI cards ---
    with st.spinner("Loading KPIs…"):
        try:
            kpi = _kpi_for_basin(basin, fuel_type, target_year, wti)
        except Exception as exc:
            kpi = None
            st.warning(f"KPI computation failed: {exc}", icon="⚠️")

    c1, c2, c3, c4 = st.columns(4)
    if kpi:
        ppe = kpi["projected_production"]
        gr  = kpi["growth_rate"]
        vol = kpi["volatility"]
        rev = kpi["revenue_potential"]

        ppe_val = ppe.get("value")
        c1.metric(
            "Projected Production",
            _fmt(ppe_val, suffix=f" {ppe.get('unit','')}"),
            help=f"{target_year} annual total — source: {ppe.get('source','')}",
        )

        yoy = gr.get("yoy_pct")
        yoy_year = gr.get("year")
        yoy_help = (
            f"Year-over-year % change: {yoy_year} vs {yoy_year - 1}"
            if yoy_year is not None
            else "Year-over-year % change in annual production"
        )
        c2.metric(
            "YoY Growth",
            f"{yoy:+.1f}%" if yoy is not None else "N/A",
            delta=f"{yoy:.1f}%" if yoy is not None else None,
            help=yoy_help,
        )

        cv = vol.get("cv_pct")
        c3.metric(
            "Volatility (CV%)",
            f"{cv:.1f}%" if cv is not None else "N/A",
            help=f"{vol.get('interpretation','—')} — coefficient of variation; lower = more stable",
        )

        rev_m = rev.get("revenue_usd_millions")
        c4.metric(
            "Revenue Potential",
            f"${rev_m * 12:,.0f}M/yr" if rev_m is not None else "N/A",
            help=f"Gross potential at WTI ${wti}/bbl (annualised)",
        )
    else:
        for col in (c1, c2, c3, c4):
            col.metric("—", "No data")
        st.warning(
            "No production data found for this basin.  \n"
            "Run `python src/data/fetch_all.py` to ingest EIA data.",
            icon="⚠️",
        )

    st.divider()

    # --- Basin comparison ---
    st.subheader(f"Basin Comparison — {fuel_type.upper()} · {target_year}")
    st.caption("First load fits Prophet for all 7 basins — allow ~2 min. Subsequent loads are cached for 1 h.")

    with st.spinner("Fitting forecasts for all 7 basins…"):
        try:
            cmp = _compare_all_basins(fuel_type, target_year, wti)
        except Exception as exc:
            st.error(f"Comparison failed: {exc}")
            return

    if "error" in cmp:
        st.error(f"compare_basins error: {cmp['error']}")
        return

    ranked: list[dict[str, Any]] = [b for b in cmp.get("ranked_basins", []) if "error" not in b]
    if not ranked:
        all_basins = cmp.get("ranked_basins", [])
        errored = [b for b in all_basins if "error" in b]
        if errored:
            err_df = pd.DataFrame([
                {"Basin": b["basin"], "Error": b.get("error", "unknown")}
                for b in errored
            ])
            st.warning("All 7 basins failed to compute. Details below:", icon="⚠️")
            st.dataframe(err_df, use_container_width=True, hide_index=True)
        else:
            st.info("No basin data available yet.")
        return

    rows = []
    for b in ranked:
        pp = b.get("projected_production", {})
        rows.append({
            "Basin":               b["basin"],
            "Proj. Production":    pp.get("value"),
            "Unit":                pp.get("unit", ""),
            "YoY %":               b.get("growth_rate", {}).get("yoy_pct"),
            "Volatility CV%":      b.get("volatility", {}).get("cv_pct"),
            "Rev $M/mo":           b.get("revenue_potential", {}).get("revenue_usd_millions"),
            "RPI (0–100)":         b.get("relative_performance_index"),
        })

    df_tbl = pd.DataFrame(rows)
    st.dataframe(
        df_tbl.style.format(
            {
                "Proj. Production": "{:,.0f}",
                "YoY %":           "{:+.1f}",
                "Volatility CV%":  "{:.1f}",
                "Rev $M/mo":       "{:,.0f}",
                "RPI (0–100)":     "{:.1f}",
            },
            na_rep="N/A",
        ),
        use_container_width=True,
        hide_index=True,
    )

    # RPI bar chart — selected basin in orange
    basin_names = [r["Basin"] for r in rows]
    rpi_vals    = [r["RPI (0–100)"] or 0 for r in rows]
    bar_colors  = ["#FF6B35" if b == basin else "#4A6FA5" for b in basin_names]

    fig = go.Figure(
        go.Bar(
            x=basin_names,
            y=rpi_vals,
            marker_color=bar_colors,
            hovertemplate="<b>%{x}</b><br>RPI: %{y:.1f}<extra></extra>",
        )
    )
    fig.update_layout(
        title=f"Relative Performance Index — {fuel_type.upper()} ({target_year})",
        xaxis_title="Basin",
        yaxis_title="RPI Score (0–100)",
        yaxis_range=[0, 108],
        template="plotly_dark",
        paper_bgcolor="#0E1117",
        plot_bgcolor="#1A1D24",
        showlegend=False,
        margin=dict(t=55, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)
