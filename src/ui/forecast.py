"""Forecast tab: Prophet production forecast with anomaly overlay."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from agents.tools import execute_tool
from data.loader import load_production_no_cache
from models.calibration import apply_monthly_band, run_backtest
from models.forecaster import forecast_basin as _fit
from ui.theme import FLARE, MUTED, RED, STEEL, TRANSPARENT, style_figure, with_alpha

_NOW_YEAR = datetime.now().year

_METHODOLOGY = """\
**Model:** Prophet (Meta, 2017): a trend with changepoints times a yearly seasonal pattern \
(multiplicative seasonality, changepoint prior scale 0.05). Prices, rig counts and completions \
are not inputs, so the forecast assumes the recent trend continues.

**80% band:** taken from the model's own past misses, not from Prophet's built-in interval, \
which covered only about half of actual months when tested. The model is refit at each of the \
last eight year-ends using only data up to that point, and the band at each horizon is set so \
that about 80% of those past misses would have fallen inside it. Beyond the three tested years \
the band widens with the square root of the horizon, which is an assumption.

**Baseline:** the dotted line repeats the last 12 months before the cutoff. A forecast is only \
worth reading as a forecast where it has beaten that baseline in the backtest.

**Caveats:** backtests use today's revised data, the windows overlap in time, and basins that \
share a state share errors, so treat small differences as ties. Basin figures are state totals. \
Directional planning only; not a reserves estimate.
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



@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _cached_backtest(basin: str, fuel_type: str) -> dict[str, Any]:
    """Rolling-origin backtest for one series; the basis of the band and baseline check."""
    df = load_production_no_cache(fuel_type=fuel_type, live_fetch=True)
    bdf = df[df["basin"] == basin][["ds", "y"]].dropna().copy()
    try:
        bt = run_backtest(bdf)
    except Exception as exc:
        return {"error": str(exc)}
    bt.pop("monthly", None)
    bt.pop("annual", None)
    return bt


def _baseline(fc_df: pd.DataFrame) -> pd.DataFrame:
    """Seasonal naive: the last 12 months before the cutoff, repeated."""
    hist = fc_df[~fc_df["is_forecast"]].dropna(subset=["y_actual"])
    fut = fc_df[fc_df["is_forecast"]]
    if len(hist) < 12 or fut.empty:
        return pd.DataFrame(columns=["ds", "y"])
    last = hist.tail(12)
    by_month = dict(zip(pd.to_datetime(last["ds"]).dt.month, last["y_actual"]))
    return pd.DataFrame({
        "ds": fut["ds"],
        "y": [by_month.get(m) for m in pd.to_datetime(fut["ds"]).dt.month],
    })


# ------------------------------------------------------------------
# Chart builder
# ------------------------------------------------------------------

def _build_chart(
    fc_df: pd.DataFrame,
    cutoff_year: int,
    anomalies: dict[str, Any],
    basin: str,
    fuel_type: str,
    calibrated: bool = False,
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
        line=dict(color=STEEL, width=2),
        mode="lines",
        hovertemplate="%{y:,.1f}<extra>Actual</extra>",
    ))

    if not fcast.empty:
        # 80% CI band
        ci_x = list(fcast["ds"]) + list(fcast["ds"].iloc[::-1])
        ci_y = list(fcast["y_upper"]) + list(fcast["y_lower"].iloc[::-1])
        fig.add_trace(go.Scatter(
            x=ci_x, y=ci_y, fill="toself",
            fillcolor=with_alpha(FLARE, 0.16),
            line=dict(color=TRANSPARENT),
            name="80% band (from past errors)" if calibrated else "80% band (Prophet, uncalibrated)",
            hoverinfo="skip",
        ))
        # Forecast line
        fig.add_trace(go.Scatter(
            x=fcast["ds"], y=fcast["y_forecast"],
            name="Forecast (Prophet)",
            line=dict(color=FLARE, width=2.4, dash="dash"),
            mode="lines",
            hovertemplate="%{y:,.1f}<extra>Forecast</extra>",
        ))
        base = _baseline(df)
        if not base.empty:
            fig.add_trace(go.Scatter(
                x=base["ds"], y=base["y"],
                name="Baseline (last 12 months repeated)",
                line=dict(color=MUTED, width=1.4, dash="dot"),
                mode="lines",
                hovertemplate="%{y:,.1f}<extra>Baseline</extra>",
            ))

    # Cutoff vertical line — Plotly 5.x requires a numeric x value (ms since epoch)
    # on datetime axes; ISO strings cause "unsupported operand type" internally.
    fig.add_vline(
        x=cutoff_ts.timestamp() * 1000,
        line_dash="dash",
        line_color=MUTED,
        annotation_text=f"Cutoff: {cutoff_year}",
        annotation_position="top right",
    )

    # Anomaly scatter overlay + event label annotations
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
                event = a.get("known_event", "No catalogued event")
                atxt.append(
                    f"<b>{a['date']}</b><br>"
                    f"{event}<br>"
                    f"Z-score: {a['z_score']:.1f}"
                    + (f" ({dev:+.1f}% vs expected)" if dev is not None else "")
                )
        if ax:
            fig.add_trace(go.Scatter(
                x=ax, y=ay,
                mode="markers",
                name="Anomaly",
                marker=dict(color=RED, size=10, symbol="circle",
                            line=dict(color="white", width=1.5)),
                hovertemplate="<b>%{text}</b><extra></extra>",
                text=atxt,
            ))

            # Annotate the top-5 anomalies by |z_score| directly on the chart
            labelled = sorted(
                [a for a in anom_list if actual_map.get(a["date"]) is not None],
                key=lambda a: abs(a["z_score"]),
                reverse=True,
            )[:5]
            # Neighbouring months would stack their labels; stagger the heights by date.
            labelled.sort(key=lambda a: a["date"])
            for rank, a in enumerate(labelled):
                val = actual_map[a["date"]]
                event = a.get("known_event", "")
                # Truncate long event names to keep chart readable
                short = event.split("—")[0].strip()[:28] if event else a["date"]
                dev   = a.get("deviation_pct")
                dev_s = f" {dev:+.0f}%" if dev is not None else ""
                fig.add_annotation(
                    x=pd.Timestamp(a["date"]),
                    y=val,
                    text=f"<b>{short}{dev_s}</b>",
                    showarrow=True,
                    arrowhead=2,
                    arrowsize=1,
                    arrowcolor=RED,
                    arrowwidth=1.2,
                    ax=0,
                    ay=-40 - (rank % 3) * 26,
                    font=dict(size=10, color=RED),
                    bgcolor=with_alpha(RED, 0.10),
                    bordercolor=RED,
                    borderwidth=1,
                    borderpad=3,
                )

    unit = "Mbbls/month" if fuel_type == "oil" else "MMcf/month"
    style_figure(fig, height=480, margin=dict(t=72, b=40, l=8, r=8))
    fig.update_layout(
        title=f"{basin} {fuel_type.title()}: history and forecast",
        xaxis_title="",
        yaxis_title=unit,
        hovermode="x unified",
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

    with st.spinner(f"Fitting Prophet for {basin} ({fuel_type})…"):
        try:
            fc_df = _cached_forecast(basin, fuel_type, cutoff_year, target_year)
        except Exception as exc:
            st.error(f"Forecast failed: {exc}")
            return

    if fc_df is None:
        st.warning(
            "No production data. Run `python src/data/fetch_all.py` first.", icon=":material/warning:"
        )
        return

    with st.spinner("Backtesting the model on past years…"):
        bt = _cached_backtest(basin, fuel_type)
    calibrated = "error" not in bt
    if calibrated:
        fc_df = apply_monthly_band(fc_df, cutoff_year, bt)

    with st.spinner("Loading anomaly detection…"):
        anom = _cached_anomalies(basin, fuel_type)

    fig = _build_chart(fc_df, cutoff_year, anom, basin, fuel_type, calibrated)
    st.plotly_chart(fig, use_container_width=True)
    _reliability(bt, target_year - cutoff_year)

    anom_list = anom.get("anomalies", []) if "error" not in anom else []
    n = len(anom_list)
    if n:
        st.caption(
            f"**{n} anomalous month{'s' if n != 1 else ''} detected** (|z| > 2.5) — "
            "labelled arrows show the top events; hover dots for full detail."
        )
        # Collapsible anomaly event table
        with st.expander(f"View all {n} anomaly events"):
            rows = []
            for a in sorted(anom_list, key=lambda x: x["date"]):
                rows.append({
                    "Date":          a["date"],
                    "Direction":     a["direction"].title(),
                    "Deviation":     f"{a['deviation_pct']:+.1f}%" if a.get("deviation_pct") else "N/A",
                    "Z-score":       f"{a['z_score']:.2f}",
                    "Known Event":   a.get("known_event", "—"),
                })
            import pandas as _pd
            st.dataframe(_pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _reliability(bt: dict[str, Any], horizon_years: int) -> None:
    """Tell the reader how far to trust the forecast, from the rolling backtest."""
    if "error" in bt:
        st.caption(f"Reliability check unavailable: {bt['error']} The band shown is Prophet's own, "
                   "which has been too narrow in past tests.")
        return
    rows = bt["by_year"]
    k = max(1, horizon_years)
    row = next((r for r in rows if r["year"] == k), rows[-1])
    beyond = k > row["year"]
    model, flat = row["model_mape_annual"], row["flat_mape_annual"]
    years = f"{row['year']} year{'s' if row['year'] > 1 else ''}"
    if round(model, 6) < round(flat, 6):
        st.success(
            f"At {years} ahead this model has beaten the baseline: average annual error "
            f"{model:.1f}% against {flat:.1f}% for repeating the last year "
            f"({row['windows']} past tests)."
            + (f" The target is {k} years out, past what has been tested." if beyond else ""),
            icon=":material/verified:",
        )
    else:
        st.warning(
            f"At {years} ahead this model has done worse than simply repeating the last year: "
            f"average annual error {model:.1f}% against {flat:.1f}% ({row['windows']} past tests). "
            "Treat the forecast line as a scenario, and the baseline as the better guess."
            + (f" The target is {k} years out, past what has been tested." if beyond else ""),
            icon=":material/warning:",
        )

    table = pd.DataFrame([{
        "Years ahead": r["year"],
        "Past tests": r["windows"],
        "Model error, annual": f"{r['model_mape_annual']:.1f}%",
        "Baseline error, annual": f"{r['flat_mape_annual']:.1f}%",
        "Model error, monthly": f"{r['model_mape_monthly']:.1f}%",
        "Baseline error, monthly": f"{r['naive_mape_monthly']:.1f}%",
        "80% band, annual": f"±{r['band_annual'] * 100:.0f}%",
    } for r in rows])
    with st.expander("Backtest detail", icon=":material/fact_check:"):
        st.caption(
            f"The model was refit at each year-end from {bt['origins'][0]} to {bt['origins'][-1]} "
            "using only data up to that point. Errors are mean absolute percentage errors."
        )
        st.dataframe(table, use_container_width=True, hide_index=True)


def render_forecast(basin: str, fuel_type: str, target_year: int, _wti: float) -> None:
    """Render the Forecast tab."""
    _interactive_chart(basin, fuel_type, target_year)

    with st.expander("Forecast methodology", icon=":material/menu_book:"):
        st.markdown(_METHODOLOGY)
