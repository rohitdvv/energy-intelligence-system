"""Tool definitions and executors for the Investment Committee agents.

Exports:
  TOOL_SPECS       — list[dict] Anthropic tool-use schemas (pass to messages.create)
  TOOL_EXECUTORS   — dict mapping tool name → callable
  execute_tool()   — safe dispatch; always returns a dict, never raises
  ENERGY_EVENT_CALENDAR — known market events keyed by "YYYY-MM"
"""
from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
import pandas as pd

from data.eia import BASINS
from data.loader import load_production_no_cache
from kpi.metrics import basin_kpi_summary, relative_performance_index
from models.forecaster import forecast_basin as _fit_and_forecast

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Known energy market events — used to contextualise anomalies
# ------------------------------------------------------------------

ENERGY_EVENT_CALENDAR: dict[str, str] = {
    "2008-09": "Global Financial Crisis — oil demand collapse",
    "2014-11": "OPEC declines production cut — oil price crash begins",
    "2016-02": "WTI hits 12-year low ($26/bbl)",
    "2017-08": "Hurricane Harvey — Texas Gulf Coast shutdowns",
    "2020-03": "COVID-19 lockdowns — demand collapse",
    "2020-04": "WTI futures go negative for first time in history",
    "2021-02": "Winter Storm Uri — Texas wellhead + grid freeze",
    "2021-08": "Hurricane Ida — Louisiana Gulf production shut-ins",
    "2022-02": "Russia invades Ukraine — oil/gas price spike",
    "2022-06": "WTI peaks above $120/bbl post-invasion",
    "2023-04": "OPEC+ surprise production cut",
}

_BASIN_ENUM = BASINS  # ["Permian", "Bakken", ..., "Appalachian"]


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _load_basin(fuel_type: str, basin: str) -> pd.DataFrame:
    """Return ds/y DataFrame for *basin*, empty if unavailable."""
    df = load_production_no_cache(fuel_type=fuel_type, live_fetch=True)
    return df[df["basin"] == basin][["ds", "y"]].dropna().copy()


def _annual_sum(bdf: pd.DataFrame) -> pd.Series:
    tmp = bdf.copy()
    tmp["yr"] = pd.to_datetime(tmp["ds"]).dt.year
    return tmp.groupby("yr")["y"].sum()


# ------------------------------------------------------------------
# Executor implementations
# ------------------------------------------------------------------

def _exec_get_production_history(inp: dict[str, Any]) -> dict[str, Any]:
    basin = inp["basin"]
    fuel_type = inp["fuel_type"]
    n_years = int(inp.get("n_years", 5))

    bdf = _load_basin(fuel_type, basin)
    if bdf.empty:
        return {"error": f"No {fuel_type} data found for basin '{basin}'. Run fetch_all.py first."}

    bdf = bdf.sort_values("ds")
    latest = bdf.iloc[-1]
    latest_month = latest["ds"].strftime("%Y-%m")

    cutoff = bdf["ds"].max()
    last_12 = bdf[bdf["ds"] > cutoff - pd.DateOffset(months=12)]["y"]
    prior_12 = bdf[
        (bdf["ds"] > cutoff - pd.DateOffset(months=24))
        & (bdf["ds"] <= cutoff - pd.DateOffset(months=12))
    ]["y"]

    avg_last = float(last_12.mean()) if not last_12.empty else None
    avg_prior = float(prior_12.mean()) if not prior_12.empty else None

    yoy_pct: float | None = None
    if avg_last and avg_prior and avg_prior > 0:
        yoy_pct = round((avg_last - avg_prior) / avg_prior * 100, 2)

    if yoy_pct is None:
        trend = "unknown"
    elif yoy_pct > 2:
        trend = "increasing"
    elif yoy_pct < -2:
        trend = "decreasing"
    else:
        trend = "flat"

    window = bdf[bdf["ds"] > cutoff - pd.DateOffset(years=n_years)]["y"]

    return {
        "basin": basin,
        "fuel_type": fuel_type,
        "latest_month": latest_month,
        "latest_value": round(float(latest["y"]), 2),
        "unit": "Mbbls/month" if fuel_type == "oil" else "MMcf/month",
        "avg_monthly_last_year": round(avg_last, 2) if avg_last else None,
        "yoy_change_pct": yoy_pct,
        "trend_direction": trend,
        f"{n_years}yr_high": round(float(window.max()), 2) if not window.empty else None,
        f"{n_years}yr_low": round(float(window.min()), 2) if not window.empty else None,
    }


def _exec_forecast_basin(inp: dict[str, Any]) -> dict[str, Any]:
    basin = inp["basin"]
    fuel_type = inp["fuel_type"]
    cutoff_year = int(inp["cutoff_year"])
    horizon_year = int(inp["horizon_year"])

    bdf = _load_basin(fuel_type, basin)
    if bdf.empty:
        return {"error": f"No {fuel_type} data for '{basin}'. Run fetch_all.py first."}

    result = _fit_and_forecast(bdf, cutoff_year, horizon_year, basin=basin, fuel_type=fuel_type)

    h_rows = result.df[result.df["ds"].dt.year == horizon_year]
    annual_total = round(float(h_rows["y_forecast"].sum()), 2) if not h_rows.empty else None
    lower = round(float(h_rows["y_lower"].sum()), 2) if not h_rows.empty else None
    upper = round(float(h_rows["y_upper"].sum()), 2) if not h_rows.empty else None

    # Historical CAGR from full available history
    by_year = _annual_sum(
        result.historical[["ds", "y_actual"]].dropna().rename(columns={"y_actual": "y"})
    )
    cagr: float | None = None
    if len(by_year) >= 2:
        start_v, end_v = float(by_year.iloc[0]), float(by_year.iloc[-1])
        n = len(by_year) - 1
        if start_v > 0:
            cagr = round(((end_v / start_v) ** (1 / n) - 1) * 100, 2)

    return {
        "basin": basin,
        "fuel_type": fuel_type,
        "cutoff_year": cutoff_year,
        "horizon_year": horizon_year,
        "projected_annual_total": annual_total,
        "unit": "Mbbls/yr" if fuel_type == "oil" else "MMcf/yr",
        "confidence_interval_80pct": {"lower": lower, "upper": upper},
        "historical_cagr_pct": cagr,
    }


def _exec_get_kpi_snapshot(inp: dict[str, Any]) -> dict[str, Any]:
    basin = inp["basin"]
    fuel_type = inp["fuel_type"]
    target_year = int(inp["target_year"])
    wti = float(inp.get("wti_assumption", 75.0))

    bdf = _load_basin(fuel_type, basin)
    if bdf.empty:
        return {"error": f"No {fuel_type} data for '{basin}'. Run fetch_all.py first."}

    cutoff_year = int(bdf["ds"].dt.year.max())
    result = _fit_and_forecast(bdf, cutoff_year, target_year, basin=basin, fuel_type=fuel_type)
    return basin_kpi_summary(result, target_year, wti_price=wti)


def _exec_compare_basins(inp: dict[str, Any]) -> dict[str, Any]:
    fuel_type = inp["fuel_type"]
    target_year = int(inp["target_year"])
    wti = float(inp.get("wti_assumption", 75.0))

    df = load_production_no_cache(fuel_type=fuel_type, live_fetch=True)
    basins_found = sorted(df["basin"].unique().tolist()) if not df.empty else []
    logger.info(
        "compare_basins: fuel=%s target_year=%d df_rows=%d basins_found=%s",
        fuel_type, target_year, len(df), basins_found,
    )

    summaries: list[dict[str, Any]] = []
    for basin in _BASIN_ENUM:
        bdf = df[df["basin"] == basin][["ds", "y"]].dropna().copy()
        if bdf.empty:
            summaries.append({"basin": basin, "error": "no data"})
            continue
        cutoff_year = int(bdf["ds"].dt.year.max())
        try:
            _t0 = time.monotonic()
            result = _fit_and_forecast(bdf, cutoff_year, target_year, basin=basin, fuel_type=fuel_type)
            _elapsed = time.monotonic() - _t0
            if _elapsed > 10:
                logger.warning(
                    "compare_basins: basin=%s Prophet fit took %.1fs (>10s threshold)",
                    basin, _elapsed,
                )
            summaries.append(basin_kpi_summary(result, target_year, wti_price=wti))
        except Exception as exc:
            logger.warning("compare_basins: basin=%s failed: %r", basin, exc)
            summaries.append({"basin": basin, "error": str(exc)})

    basin_totals: dict[str, float] = {
        s["basin"]: s["projected_production"]["value"]
        for s in summaries
        if "projected_production" in s and s["projected_production"].get("value") is not None
    }
    rpi = relative_performance_index(basin_totals)
    for s in summaries:
        s["relative_performance_index"] = rpi.get(s.get("basin", ""), None)

    summaries.sort(
        key=lambda s: s.get("projected_production", {}).get("value") or 0,
        reverse=True,
    )
    n_ok = sum(1 for s in summaries if "error" not in s)
    n_fail = len(summaries) - n_ok
    n_rpi = sum(1 for s in summaries if s.get("relative_performance_index") is not None)
    logger.info(
        "compare_basins done: %d succeeded, %d failed, %d have RPI",
        n_ok, n_fail, n_rpi,
    )
    return {
        "fuel_type": fuel_type,
        "target_year": target_year,
        "wti_assumption_per_bbl": wti,
        "ranked_basins": summaries,
    }


def _exec_investigate_anomalies(inp: dict[str, Any]) -> dict[str, Any]:
    """Detect production anomalies via Prophet in-sample residual z-scores."""
    import logging as _stdlib_log

    # Suppress Stan / cmdstanpy console noise during in-sample fit
    _stdlib_log.getLogger("prophet").setLevel(_stdlib_log.ERROR)
    _stdlib_log.getLogger("cmdstanpy").setLevel(_stdlib_log.ERROR)

    from prophet import Prophet  # local import — Prophet is slow to import

    basin = inp["basin"]
    fuel_type = inp["fuel_type"]

    bdf = _load_basin(fuel_type, basin)
    if bdf.empty:
        return {"error": f"No {fuel_type} data for '{basin}'. Run fetch_all.py first."}
    if len(bdf) < 12:
        return {"error": "Insufficient data for anomaly detection (< 12 months required)"}

    model = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=False,
        daily_seasonality=False,
        interval_width=0.95,
    )
    model.fit(bdf)

    fc = model.predict(bdf[["ds"]])
    merged = bdf.merge(fc[["ds", "yhat"]], on="ds", how="inner")
    merged["residual"] = merged["y"] - merged["yhat"]

    std_r = merged["residual"].std()
    mean_r = merged["residual"].mean()
    if std_r == 0:
        return {"basin": basin, "fuel_type": fuel_type, "anomaly_count": 0, "anomalies": []}

    merged["z_score"] = (merged["residual"] - mean_r) / std_r
    merged["dev_pct"] = np.where(
        merged["yhat"].abs() > 0.01,
        merged["residual"] / merged["yhat"] * 100,
        np.nan,
    )

    flagged = merged[merged["z_score"].abs() > 2.5].sort_values("ds")
    anomalies = []
    for _, row in flagged.iterrows():
        period = row["ds"].strftime("%Y-%m")
        dev = float(row["dev_pct"]) if not np.isnan(row["dev_pct"]) else None
        anomalies.append({
            "date": period,
            "actual": round(float(row["y"]), 2),
            "expected": round(float(row["yhat"]), 2),
            "deviation_pct": round(dev, 1) if dev is not None else None,
            "direction": "above" if row["z_score"] > 0 else "below",
            "z_score": round(float(row["z_score"]), 2),
            "known_event": ENERGY_EVENT_CALENDAR.get(period, "No catalogued event"),
        })

    return {
        "basin": basin,
        "fuel_type": fuel_type,
        "months_analyzed": len(merged),
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
    }


# ------------------------------------------------------------------
# Anthropic tool-use schemas
# ------------------------------------------------------------------

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_production_history",
        "description": (
            "Retrieve recent production statistics for a named U.S. basin. "
            "Returns the latest month's production, 12-month average, year-over-year "
            "% change, and trend direction. Call this first to establish baseline context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "basin": {
                    "type": "string",
                    "enum": _BASIN_ENUM,
                    "description": "Basin name",
                },
                "fuel_type": {
                    "type": "string",
                    "enum": ["oil", "gas"],
                },
                "n_years": {
                    "type": "integer",
                    "description": "Look-back window for multi-year high/low. Default 5.",
                },
            },
            "required": ["basin", "fuel_type"],
        },
    },
    {
        "name": "forecast_basin",
        "description": (
            "Fit a Prophet model on historical production and forecast through horizon_year. "
            "Returns projected annual total, 80% confidence interval, and historical CAGR."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "basin": {"type": "string", "enum": _BASIN_ENUM},
                "fuel_type": {"type": "string", "enum": ["oil", "gas"]},
                "cutoff_year": {
                    "type": "integer",
                    "description": "Last year of historical data used for model fitting.",
                },
                "horizon_year": {
                    "type": "integer",
                    "description": "Year to forecast through (inclusive).",
                },
            },
            "required": ["basin", "fuel_type", "cutoff_year", "horizon_year"],
        },
    },
    {
        "name": "get_kpi_snapshot",
        "description": (
            "Full KPI suite for one basin: projected production estimate, YoY growth, "
            "decline rate, volatility score, and revenue potential at a WTI assumption."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "basin": {"type": "string", "enum": _BASIN_ENUM},
                "fuel_type": {"type": "string", "enum": ["oil", "gas"]},
                "target_year": {
                    "type": "integer",
                    "description": "Investment target year for KPI evaluation.",
                },
                "wti_assumption": {
                    "type": "number",
                    "description": "WTI price assumption $/bbl. Default 75.0.",
                },
            },
            "required": ["basin", "fuel_type", "target_year"],
        },
    },
    {
        "name": "compare_basins",
        "description": (
            "Run KPI snapshots for all 7 basins and return them ranked by projected "
            "production, with 0–100 relative performance index scores. Use to "
            "contextualise one basin against its peers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fuel_type": {"type": "string", "enum": ["oil", "gas"]},
                "target_year": {"type": "integer"},
                "wti_assumption": {
                    "type": "number",
                    "description": "WTI price assumption $/bbl. Default 75.0.",
                },
            },
            "required": ["fuel_type", "target_year"],
        },
    },
    {
        "name": "investigate_anomalies",
        "description": (
            "Detect production anomalies via Prophet in-sample residual z-scores "
            "(threshold |z| > 2.5). Each anomaly is tagged with a known energy "
            "market event where one is catalogued. "
            "Bear analysts: calling this tool is mandatory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "basin": {"type": "string", "enum": _BASIN_ENUM},
                "fuel_type": {"type": "string", "enum": ["oil", "gas"]},
            },
            "required": ["basin", "fuel_type"],
        },
    },
]

TOOL_EXECUTORS: dict[str, Any] = {
    "get_production_history": _exec_get_production_history,
    "forecast_basin": _exec_forecast_basin,
    "get_kpi_snapshot": _exec_get_kpi_snapshot,
    "compare_basins": _exec_compare_basins,
    "investigate_anomalies": _exec_investigate_anomalies,
}


def execute_tool(name: str, input_dict: dict[str, Any]) -> dict[str, Any]:
    """Safe dispatch: always returns a dict, never raises."""
    executor = TOOL_EXECUTORS.get(name)
    if executor is None:
        return {"error": f"Unknown tool: {name!r}"}
    try:
        return executor(input_dict)
    except Exception as exc:
        logger.warning("Tool '%s' raised: %s", name, exc, exc_info=True)
        return {"error": str(exc), "tool": name}
