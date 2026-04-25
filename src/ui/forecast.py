"""Forecast tab: interactive Prophet chart with anomaly overlay and methodology notes."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from agents.tools import execute_tool
from data.loader import load_production_no_cache
from models.forecaster import forecast_basin as _fit

_NOW_YEAR = datetime.now().year

_METHODOLOGY = """\
**Model:** Facebook Prophet (Meta Research, 2017)

**Why Prophet?** Monthly production series have clear yearly seasonality (seasonal drilling \
cycles, maintenance windows) and irregular changepoints (price shocks, regulation). Prophet \
handles both without manual feature engineering, and its uncertainty intervals are legible \
to a non-statistician.

**Seasonality mode:** Multiplicative — production volumes scale proportionally with the trend \
level. A 5 % seasonal amplitude means more absolute barrels when the basin is growing vs. \
contracting. Additive would understate seasonal amplitude in high-growth phases.

**Changepoint prior scale:** 0.05 — moderate flexibility. Higher → overfits noise as \
structural breaks; lower → misses genuine inflection points (e.g. shale boom, COVID).

**Confidence interval:** 80 % posterior predictive interval from Monte Carlo sampling of \
the Prophet parameter posterior. Does **not** capture exogenous risk (geopolitical, \
regulatory, capital allocation).

**Limitations:** Assumes trend continuity beyond the cutoff. Does not incorporate commodity \
prices, rig counts, or completion activity. Suitable for directional planning only; not a \
reserve study or formal reserves estimate.
"""


# ------------------------------------------------------------------
# Cached loaders
# ------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def _cached_forecast(
    basin: str, fuel_type: str, cutoff_year: int, horizon_year: int
) -> pd.DataFrame | None:
    """Return result.df as a plain DataFrame (Prophet model excluded for cache safety)."""
    df = load_production_no_cache(fuel_type=fuel_type, live_fetch=True)
    bdf = df[df["basin"] == basin][["ds", "y"]].dropna().copy()
    if bdf.empty:
        return None
    result = _fit(bdf, cutoff_year, horizon_year, basin=basin, fuel_type=fuel_type)
    return result.df.copy()


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_anomalies(basin: str, fuel_type: str) -> dict[str, Any]:
    return execute_tool("investigate_anomalies", {"basin": basin, "fuel_type": fuel_type})


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_backtest(basin: str, fuel_type: str) -> dict[str, Any] | None:
    from data.loader import load_production_no_cache
    from models.backtest import backtest_mape
    df = load_production_no_cache(fuel_type=fuel_type, live_fetch=True)
    bdf = df[df["basin"] == basin][["ds", "y"]].dropna().copy()
    if len(bdf) < 24:
        return None
    try:
        return backtest_mape(bdf, basin, fuel_type)
    except Exception as exc:
        return {"error": str(exc)}


# ------------------------------------------------------------------
# Chart builder
# ------------------------------------------------------------------

def _build_chart(
    fc_df: pd.DataFrame,
    cutoff_year: int,
    anomalies: dict[str, Any],
    basin: str,
    fuel_type: str,
) -> go.Figure:
    df = fc_df.copy()
    df["ds"] = pd.to_datetime(df["ds"])
    cutoff_ts = pd.Timestamp(f"{cutoff_year}-12-31")

    hist  = df[~df["is_forecast"]]
    fcast = df[df["is_forecast"]]

    fig = go.Figure()

    # Historical actuals
    fig.add_trace(go.Scatter(
        x=hist["ds"],
        y=hist["y_actual"],
        name="Historical",
        line=dict(color="#4A90D9", width=2),
        mode="lines",
        hovertemplate="%{y:,.1f}<extra>Actual</extra>",
    ))

    if not fcast.empty:
        # CI band
        ci_x = list(fcast["ds"]) + list(fcast["ds"].iloc[::-1])
        ci_y = list(fcast["y_upper"]) + list(fcast["y_lower"].iloc[::-1])
        fig.add_trace(go.Scatter(
            x=ci_x, y=ci_y,
            fill="toself",
            fillcolor="rgba(255,107,53,0.15)",
            line=dict(color="rgba(0,0,0,0)"),
            name="80% CI",
            hoverinfo="skip",
        ))

        # Forecast line
        fig.add_trace(go.Scatter(
            x=fcast["ds"],
            y=fcast["y_forecast"],
            name="Forecast",
            line=dict(color="#FF6B35", width=2, dash="dash"),
            mode="lines",
            hovertemplate="%{y:,.1f}<extra>Forecast</extra>",
        ))

    # Cutoff vertical line — Plotly 5.x requires a numeric x value (ms since epoch)
    # on datetime axes; ISO strings cause "unsupported operand type" internally.
    fig.add_vline(
        x=cutoff_ts.timestamp() * 1000,
        line_dash="dash",
        line_color="rgba(200,200,200,0.35)",
        annotation_text=f"Cutoff: {cutoff_year}",
        annotation_position="top right",
    )

    # Anomaly scatter overlay
    anom_list: list[dict[str, Any]] = (
        anomalies.get("anomalies", []) if "error" not in anomalies else []
    )
    if anom_list:
        actual_map = {
            row["ds"].strftime("%Y-%m"): row["y_actual"]
            for _, row in hist.iterrows()
            if pd.notna(row.get("y_actual"))
        }
        ax, ay, atxt = [], [], []
        for a in anom_list:
            val = actual_map.get(a["date"])
            if val is not None:
                ax.append(pd.Timestamp(a["date"]))
                ay.append(val)
                dev = a.get("deviation_pct")
                atxt.append(
                    f"{a['date']}<br>{a.get('known_event','')}<br>"
                    f"Z={a['z_score']:.1f}"
                    + (f" ({dev:+.1f}%)" if dev is not None else "")
                )
        if ax:
            fig.add_trace(go.Scatter(
                x=ax, y=ay,
                mode="markers",
                name="Anomaly",
                marker=dict(color="red", size=9, symbol="circle",
                            line=dict(color="white", width=1)),
                hovertemplate="<b>%{text}</b><extra></extra>",
                text=atxt,
            ))

    unit = "Mbbls/month" if fuel_type == "oil" else "MMcf/month"
    fig.update_layout(
        title=f"{basin} {fuel_type.upper()} — Historical & Forecast",
        xaxis_title="",
        yaxis_title=unit,
        template="plotly_dark",
        paper_bgcolor="#0E1117",
        plot_bgcolor="#1A1D24",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(t=80, b=40),
    )
    return fig


# ------------------------------------------------------------------
# Fragment + render
# ------------------------------------------------------------------

@st.fragment
def _interactive_chart(basin: str, fuel_type: str, target_year: int) -> None:
    """Fragment: slider reruns only this block, not the whole page."""
    max_cutoff = max(2016, min(_NOW_YEAR - 1, target_year - 1))
    default_cutoff = min(2023, max_cutoff)

    cutoff_year: int = st.slider(
        "Historical cutoff year — drag to shift the forecast origin",
        min_value=2015,
        max_value=max_cutoff,
        value=default_cutoff,
        key="forecast_cutoff_slider",
    )

    with st.spinner(f"Fitting Prophet model for {basin} {fuel_type}…"):
        try:
            fc_df = _cached_forecast(basin, fuel_type, cutoff_year, target_year)
        except Exception as exc:
            st.error(f"Forecast failed: {exc}")
            return

    if fc_df is None:
        st.warning(
            "No production data. Run `python src/data/fetch_all.py` first.", icon="⚠️"
        )
        return

    with st.spinner("Loading anomaly detection…"):
        anom = _cached_anomalies(basin, fuel_type)

    fig = _build_chart(fc_df, cutoff_year, anom, basin, fuel_type)
    st.plotly_chart(fig, use_container_width=True)

    n = len(anom.get("anomalies", [])) if "error" not in anom else 0
    if n:
        st.caption(
            f"🔴 {n} anomalous month{'s' if n != 1 else ''} detected (|z| > 2.5) — "
            "hover red dots for event context."
        )


def render_forecast(basin: str, fuel_type: str, target_year: int, _wti: float) -> None:
    """Render the Forecast tab."""
    _interactive_chart(basin, fuel_type, target_year)

    with st.expander("📖 Forecast methodology"):
        st.markdown(_METHODOLOGY)

        st.markdown("---")
        st.markdown("**Backtest reliability** (12-month held-out MAPE):")
        with st.spinner("Running backtest…"):
            bt = _cached_backtest(basin, fuel_type)
        if bt is None:
            st.caption("Insufficient data for backtest (< 24 months).")
        elif "error" in bt:
            st.caption(f"Backtest unavailable: {bt['error']}")
        else:
            mape = bt["mape_pct"]
            quality = (
                "EXCELLENT" if mape < 5 else
                "GOOD"      if mape < 10 else
                "OK"        if mape < 20 else
                "POOR"
            )
            st.metric(
                f"{basin} {fuel_type.upper()} MAPE",
                f"{mape:.1f}%",
                help=(
                    f"{quality} — mean absolute % error on {bt['n_predictions']} "
                    f"held-out months ({bt['test_start']} → {bt['test_end']})"
                ),
            )
