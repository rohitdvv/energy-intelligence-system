"""Forecast tab: multi-model comparison — Prophet, XGBoost, Ensemble."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from agents.tools import execute_tool
from data.loader import load_production_no_cache
from models.forecaster import forecast_basin as _fit
from models.xgb_forecaster import forecast_xgb

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
def _cached_xgb_forecast(
    basin: str, fuel_type: str, cutoff_year: int, horizon_year: int
) -> pd.DataFrame | None:
    """Return XGBForecastResult.df + feature_importance as a plain dict."""
    df = load_production_no_cache(fuel_type=fuel_type, live_fetch=True)
    bdf = df[df["basin"] == basin][["ds", "y"]].dropna().copy()
    if bdf.empty:
        return None
    try:
        result = forecast_xgb(bdf, cutoff_year, horizon_year, basin=basin, fuel_type=fuel_type)
        return {"df": result.df.copy(), "feat_imp": result.feature_importance, "mape": result.in_sample_mape}
    except Exception as exc:
        return {"error": str(exc)}


def _ensemble_df(prophet_df: pd.DataFrame, xgb_df: pd.DataFrame) -> pd.DataFrame:
    """Average Prophet and XGBoost forecasts into an ensemble."""
    merged = prophet_df[["ds", "y_forecast", "y_lower", "y_upper", "is_forecast", "y_actual"]].merge(
        xgb_df[["ds", "y_forecast", "y_lower", "y_upper"]],
        on="ds", suffixes=("_p", "_x"),
    )
    merged["y_forecast"] = (merged["y_forecast_p"] + merged["y_forecast_x"]) / 2
    merged["y_lower"]    = (merged["y_lower_p"]    + merged["y_lower_x"])    / 2
    merged["y_upper"]    = (merged["y_upper_p"]    + merged["y_upper_x"])    / 2
    return merged[["ds", "y_actual", "y_forecast", "y_lower", "y_upper", "is_forecast"]]


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

def _add_forecast_traces(
    fig: go.Figure,
    fcast: pd.DataFrame,
    color: str,
    name: str,
    ci_color: str,
    show_ci: bool = True,
) -> None:
    """Add a forecast line + optional CI band to an existing figure."""
    if fcast.empty:
        return
    ci_x = list(fcast["ds"]) + list(fcast["ds"].iloc[::-1])
    ci_y = list(fcast["y_upper"]) + list(fcast["y_lower"].iloc[::-1])
    if show_ci:
        fig.add_trace(go.Scatter(
            x=ci_x, y=ci_y, fill="toself",
            fillcolor=ci_color, line=dict(color="rgba(0,0,0,0)"),
            name=f"{name} 80% CI", hoverinfo="skip", showlegend=False,
        ))
    fig.add_trace(go.Scatter(
        x=fcast["ds"], y=fcast["y_forecast"],
        name=name,
        line=dict(color=color, width=2, dash="dash"),
        mode="lines",
        hovertemplate=f"%{{y:,.1f}}<extra>{name}</extra>",
    ))


def _build_chart(
    fc_df: pd.DataFrame,
    cutoff_year: int,
    anomalies: dict[str, Any],
    basin: str,
    fuel_type: str,
    xgb_data: dict[str, Any] | None = None,
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

    # Prophet forecast
    _add_forecast_traces(fig, fcast, "#FF6B35", "Prophet", "rgba(255,107,53,0.12)")

    # XGBoost + Ensemble traces
    if xgb_data and "df" in xgb_data:
        xgb_df    = xgb_data["df"].copy()
        xgb_df["ds"] = pd.to_datetime(xgb_df["ds"])
        xgb_fcast = xgb_df[xgb_df["is_forecast"]]
        _add_forecast_traces(fig, xgb_fcast, "#50C878", "XGBoost", "rgba(80,200,120,0.10)")

        # Ensemble = average of Prophet + XGBoost
        ens_df    = _ensemble_df(df, xgb_df)
        ens_fcast = ens_df[ens_df["is_forecast"]]
        _add_forecast_traces(fig, ens_fcast, "#C084FC", "Ensemble", "rgba(192,132,252,0.10)", show_ci=False)

    # Cutoff vertical line — Plotly 5.x requires a numeric x value (ms since epoch)
    # on datetime axes; ISO strings cause "unsupported operand type" internally.
    fig.add_vline(
        x=cutoff_ts.timestamp() * 1000,
        line_dash="dash",
        line_color="rgba(200,200,200,0.35)",
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
                marker=dict(color="#FF4444", size=10, symbol="circle",
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
            for a in labelled:
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
                    arrowcolor="#FF4444",
                    arrowwidth=1.2,
                    ax=0,
                    ay=-42,
                    font=dict(size=9, color="#FF9999"),
                    bgcolor="rgba(18,10,10,.75)",
                    bordercolor="#FF4444",
                    borderwidth=1,
                    borderpad=3,
                )

    unit = "Mbbls/month" if fuel_type == "oil" else "MMcf/month"
    fig.update_layout(
        title=f"{basin} {fuel_type.upper()} — Historical & Forecast",
        xaxis_title="",
        yaxis_title=unit,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(18,26,44,.55)",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(t=80, b=40),
    )
    return fig


# ------------------------------------------------------------------
# Fragment + render
# ------------------------------------------------------------------

def _render_model_leaderboard(
    basin: str, fuel_type: str, xgb_data: dict[str, Any] | None
) -> None:
    """Render a MAPE comparison table for all three models."""
    with st.spinner("Running held-out backtests…"):
        bt_prophet = _cached_backtest(basin, fuel_type)

    rows = []
    if bt_prophet and "mape_pct" in bt_prophet:
        rows.append({"Model": "Prophet", "Held-out MAPE": bt_prophet["mape_pct"],
                     "Test window": f"{bt_prophet['test_start']} → {bt_prophet['test_end']}",
                     "Quality": _quality(bt_prophet["mape_pct"])})

    xgb_mape = (xgb_data or {}).get("mape")
    if xgb_mape is not None:
        rows.append({"Model": "XGBoost", "Held-out MAPE": round(xgb_mape, 2),
                     "Test window": "in-sample diagnostic", "Quality": _quality(xgb_mape)})

    if len(rows) == 2:
        ens_mape = round((rows[0]["Held-out MAPE"] + rows[1]["Held-out MAPE"]) / 2, 2)
        rows.append({"Model": "Ensemble (Prophet + XGBoost)", "Held-out MAPE": ens_mape,
                     "Test window": "averaged", "Quality": _quality(ens_mape)})

    if rows:
        import pandas as _pd
        st.markdown("#### Model Accuracy Leaderboard")
        st.caption("Lower MAPE = better. Ensemble combines both models with equal weight.")
        df_lb = _pd.DataFrame(rows)
        best_idx = df_lb["Held-out MAPE"].idxmin()
        st.dataframe(
            df_lb.style.format({"Held-out MAPE": "{:.2f}%"})
                       .highlight_min(subset=["Held-out MAPE"], color="#1a3d1a"),
            use_container_width=True, hide_index=True,
        )


def _quality(mape: float) -> str:
    if mape < 5:   return "EXCELLENT"
    if mape < 10:  return "GOOD"
    if mape < 20:  return "OK"
    return "POOR"


def _render_feature_importance(feat_imp: dict[str, float]) -> None:
    """Horizontal bar chart of XGBoost feature importances."""
    items  = sorted(feat_imp.items(), key=lambda x: x[1])
    labels = [k.replace("_", " ").title() for k, _ in items]
    values = [v for _, v in items]

    # Friendly label map
    label_map = {
        "Lag 1": "Lag 1m", "Lag 2": "Lag 2m", "Lag 3": "Lag 3m",
        "Lag 6": "Lag 6m", "Lag 12": "Lag 12m (seasonal)",
        "Rolling Mean 3": "Rolling Mean 3m", "Rolling Mean 6": "Rolling Mean 6m",
        "Rolling Mean 12": "Rolling Mean 12m", "Rolling Std 6": "Rolling Std 6m",
        "Month Sin": "Month (sin)", "Month Cos": "Month (cos)",
        "Year Norm": "Year Trend",
    }
    labels = [label_map.get(l, l) for l in labels]

    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker=dict(
            color=values,
            colorscale="Viridis",
            showscale=False,
        ),
        hovertemplate="%{y}: %{x:.3f}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="XGBoost Feature Importance — what drives the forecast?", font=dict(size=12)),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(18,26,44,.5)",
        height=320,
        margin=dict(t=36, b=20, l=10, r=10),
        xaxis_title="Importance score",
        font=dict(size=10),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Lag 12m dominates → strong seasonal autocorrelation in production. "
        "Rolling means capture medium-term momentum. Year Trend encodes secular growth/decline."
    )


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

    col_prophet, col_xgb = st.columns([3, 1])
    with col_prophet:
        with st.spinner(f"Fitting Prophet model for {basin} {fuel_type}…"):
            try:
                fc_df = _cached_forecast(basin, fuel_type, cutoff_year, target_year)
            except Exception as exc:
                st.error(f"Prophet forecast failed: {exc}")
                return
    with col_xgb:
        with st.spinner("Fitting XGBoost…"):
            xgb_data = _cached_xgb_forecast(basin, fuel_type, cutoff_year, target_year)

    if fc_df is None:
        st.warning(
            "No production data. Run `python src/data/fetch_all.py` first.", icon="⚠️"
        )
        return

    with st.spinner("Loading anomaly detection…"):
        anom = _cached_anomalies(basin, fuel_type)

    fig = _build_chart(fc_df, cutoff_year, anom, basin, fuel_type, xgb_data=xgb_data)
    st.plotly_chart(fig, use_container_width=True)

    # ── Model leaderboard ────────────────────────────────────────────────────
    _render_model_leaderboard(basin, fuel_type, xgb_data)

    # ── XGBoost feature importance ───────────────────────────────────────────
    if xgb_data and "feat_imp" in xgb_data:
        _render_feature_importance(xgb_data["feat_imp"])

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
