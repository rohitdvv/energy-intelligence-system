"""Forecast tab: the most accurate backtested model per basin, with anomaly overlay."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from agents.tools import execute_tool
from data.loader import load_drivers_no_cache, load_production_no_cache
from models.selection import MODEL_LABELS, best_forecast, compare_models_cached
from ui.theme import FLARE, MUTED, RED, STEEL, TRANSPARENT, style_figure, with_alpha

_NOW_YEAR = datetime.now().year

_METHODOLOGY = """\
**Three models are tested for every basin:**

- **Prophet trend** (Meta, 2017): a trend with changepoints times a yearly seasonal pattern. \
It sees only past production.
- **EIA well activity**: EIA splits each region's monthly change into production from new wells \
and the decline of older wells. This model holds the past 12 months of new-well production steady \
and lets the older wells keep declining at their recent rate, so production heads towards the \
level where new wells just replace the decline. Not available for Anadarko, which EIA does not \
publish as a region.
- **Repeat last year**: the last 12 months before the cutoff, repeated. This is the baseline.

**Which model is shown:** each model is refit at each of the last eight year-ends using only data \
up to that point, and its forecasts are compared with what happened. For a forecast N years past \
the cutoff, the model with the lowest average error on annual totals at N years ahead is used \
(three years is the longest tested). Ties go to the simpler model. Because the same tests choose \
the model and report its error, the error shown for the chosen model is slightly optimistic.

**80% band:** set from the chosen model's own past misses, so that about 80% of them would have \
fallen inside it. Beyond three years it widens with the square root of the horizon, which is an \
assumption.

**Caveats:** none of the models use prices or forecast rig counts. Backtests use today's revised \
data and the windows overlap in time, so treat small differences as ties. Basin figures are EIA \
regional estimates, and EIA revises them. Directional planning only; not a reserves estimate.
"""


# ------------------------------------------------------------------
# Cached loaders
# ------------------------------------------------------------------

def _series(basin: str, fuel_type: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = load_production_no_cache(fuel_type=fuel_type, live_fetch=True)
    bdf = df[df["basin"] == basin][["ds", "y"]].dropna().copy()
    return bdf, load_drivers_no_cache(fuel_type, basin, live_fetch=True)


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_forecast(
    basin: str, fuel_type: str, cutoff_year: int, horizon_year: int
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]] | None:
    """Forecast frame, the chosen model, and the comparison of all models (plain data only)."""
    bdf, drivers = _series(basin, fuel_type)
    if bdf.empty:
        return None
    comparison = compare_models_cached(basin, fuel_type, bdf, drivers)
    result, chosen = best_forecast(bdf, drivers, cutoff_year, horizon_year, comparison,
                                   basin=basin, fuel_type=fuel_type)
    return result.df.copy(), chosen, comparison


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_anomalies(basin: str, fuel_type: str) -> dict[str, Any]:
    return execute_tool("investigate_anomalies", {"basin": basin, "fuel_type": fuel_type})




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
    model_label: str = "Forecast",
    show_baseline: bool = True,
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
            name="80% band (from past errors)" if calibrated else "80% band (uncalibrated)",
            hoverinfo="skip",
        ))
        # Forecast line
        fig.add_trace(go.Scatter(
            x=fcast["ds"], y=fcast["y_forecast"],
            name=f"Forecast ({model_label})",
            line=dict(color=FLARE, width=2.4, dash="dash"),
            mode="lines",
            hovertemplate="%{y:,.1f}<extra>Forecast</extra>",
        ))
        base = _baseline(df) if show_baseline else pd.DataFrame()
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

    with st.spinner(f"Testing forecast models for {basin} ({fuel_type})…"):
        try:
            out = _cached_forecast(basin, fuel_type, cutoff_year, target_year)
        except Exception as exc:
            st.error(f"Forecast failed: {exc}")
            return

    if out is None:
        st.warning(
            "No production data. Run `python src/data/fetch_all.py` first.", icon=":material/warning:"
        )
        return
    fc_df, chosen, comparison = out

    with st.spinner("Loading anomaly detection…"):
        anom = _cached_anomalies(basin, fuel_type)

    fig = _build_chart(fc_df, cutoff_year, anom, basin, fuel_type, chosen["calibrated"],
                       model_label=chosen["label"], show_baseline=chosen["model"] != "repeat")
    st.plotly_chart(fig, use_container_width=True)
    _reliability(chosen, comparison, target_year - cutoff_year)

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


def _reliability(chosen: dict[str, Any], comparison: dict[str, Any], horizon_years: int) -> None:
    """Say which model is shown, why, and how far to trust it, from the rolling backtests."""
    bt = chosen["backtest"]
    if "error" in bt:
        st.caption(f"Reliability check unavailable ({bt['error']}). The band shown is not calibrated.")
        return
    rows = bt["by_year"]
    k = max(1, horizon_years)
    row = next((r for r in rows if r["year"] == k), rows[-1])
    tested = f"{row['year']} year{'s' if row['year'] > 1 else ''}"
    beyond = f" The target is {k} years out, past the {row['year']} years tested." if k > row["year"] else ""
    fallback = (f" {MODEL_LABELS[chosen['fallback_from']]} did better in tests but cannot run from "
                "this cutoff." if chosen.get("fallback_from") else "")
    model, flat = row["model_mape_annual"], row["flat_mape_annual"]

    if chosen["model"] == "repeat":
        st.warning(
            f"At {tested} ahead no model has beaten simply repeating the last year "
            f"(average annual error {model:.1f}% in {row['windows']} past tests), so that is the "
            "forecast shown. Read it as \"no change\", not as a prediction of change."
            + beyond + fallback,
            icon=":material/warning:",
        )
    elif round(model, 6) < round(flat, 6):
        st.success(
            f"Showing **{chosen['label']}**, the most accurate model at {tested} ahead for this basin: "
            f"average annual error {model:.1f}% against {flat:.1f}% for repeating the last year "
            f"({row['windows']} past tests)." + beyond + fallback,
            icon=":material/verified:",
        )
    else:
        st.warning(
            f"Showing **{chosen['label']}**, but at {tested} ahead it has not beaten repeating the "
            f"last year: {model:.1f}% against {flat:.1f}% ({row['windows']} past tests)."
            + beyond + fallback,
            icon=":material/warning:",
        )

    table_rows = []
    for name in comparison.get("eligible", []):
        entry: dict[str, Any] = {"Model": MODEL_LABELS[name] + ("  ✓" if name == chosen["model"] else "")}
        for r in comparison["backtests"][name]["by_year"]:
            entry[f"{r['year']} yr ahead"] = f"{r['model_mape_annual']:.1f}%"
        table_rows.append(entry)
    with st.expander("Model comparison", icon=":material/fact_check:"):
        st.caption(
            f"Average error on annual totals when each model was refit at each year-end from "
            f"{bt['origins'][0]} to {bt['origins'][-1]} using only data up to that point. "
            "Lower is better; ✓ marks the model shown."
        )
        st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
        if comparison.get("excluded"):
            st.caption("Not compared: " + "; ".join(
                f"{MODEL_LABELS.get(n, n)} ({why})" for n, why in comparison["excluded"].items()))
        band = pd.DataFrame([{
            "Years ahead": r["year"],
            "Past tests": r["windows"],
            "Monthly error": f"{r['model_mape_monthly']:.1f}%",
            "80% band, annual": f"±{r['band_annual'] * 100:.0f}%",
        } for r in rows])
        st.caption(f"{chosen['label']}: monthly error and the band used.")
        st.dataframe(band, use_container_width=True, hide_index=True)


def render_forecast(basin: str, fuel_type: str, target_year: int, _wti: float) -> None:
    """Render the Forecast tab."""
    _interactive_chart(basin, fuel_type, target_year)

    with st.expander("Forecast methodology", icon=":material/menu_book:"):
        st.markdown(_METHODOLOGY)
