"""Choose, for each basin and horizon, the forecast model with the lowest past error.

Three candidates are backtested on the same past year-ends (see calibration.py):

  prophet        Prophet trend with yearly seasonality
  well_activity  EIA new-well production and legacy decline (models/drivers.py);
                 only for basins EIA publishes those series for
  repeat         the last 12 months repeated (the flat baseline)

For a forecast k years past the cutoff, the model with the lowest mean
absolute error on annual totals at k years ahead is used (k is capped at the
longest horizon tested, three years). Ties at one decimal place go to the
simpler model. The 80% band comes from the chosen model's own backtest.

The choice is made on the same past windows that are then reported as its
error, so the reported error of the chosen model is slightly optimistic.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from models.calibration import FitPredict, apply_monthly_band, prophet_fit_predict, run_backtest
from models.drivers import seasonal_naive_fit_predict, well_activity_fit_predict
from models.forecaster import ForecastResult, forecast_basin

logger = logging.getLogger(__name__)

MODEL_LABELS: dict[str, str] = {
    "prophet": "Prophet trend",
    "well_activity": "EIA well activity",
    "repeat": "Repeat last year",
}
# Tie-break order: simplest first.
_SIMPLEST_FIRST = ["repeat", "well_activity", "prophet"]


def candidates(drivers: pd.DataFrame | None) -> dict[str, FitPredict]:
    out: dict[str, FitPredict] = {"prophet": prophet_fit_predict, "repeat": seasonal_naive_fit_predict}
    if drivers is not None and not drivers.empty:
        out["well_activity"] = well_activity_fit_predict(drivers)
    return out


def compare_models(
    bdf: pd.DataFrame,
    drivers: pd.DataFrame | None,
    models: dict[str, FitPredict] | None = None,
) -> dict[str, Any]:
    """Backtest every candidate on the same origins.

    Returns ``backtests`` (name -> backtest summary without the raw tables),
    ``eligible`` (models scored on the same origins as the baseline) and
    ``excluded`` (name -> reason).
    """
    models = models or candidates(drivers)
    backtests: dict[str, dict[str, Any]] = {}
    for name, fit_predict in models.items():
        try:
            bt = run_backtest(bdf, fit_predict)
        except Exception as exc:  # one failing model must not stop the others
            logger.warning("backtest of %s failed: %r", name, exc)
            bt = {"error": str(exc)}
        bt.pop("monthly", None)
        bt.pop("annual", None)
        backtests[name] = bt

    base = backtests.get("repeat", {})
    if "error" in base:
        return {"error": base["error"], "backtests": backtests}
    eligible: list[str] = []
    excluded: dict[str, str] = {}
    for name, bt in backtests.items():
        if "error" in bt:
            excluded[name] = bt["error"]
        elif bt["origins"] != base["origins"]:
            excluded[name] = "could not be tested on all the same past years"
        else:
            eligible.append(name)
    return {"backtests": backtests, "eligible": eligible, "excluded": excluded}


def _row(bt: dict[str, Any], k: int) -> dict[str, Any]:
    rows = bt["by_year"]
    return next((r for r in rows if r["year"] == k), rows[-1])


def choose(comparison: dict[str, Any], years_ahead: int) -> str:
    """Name of the model with the lowest annual error at *years_ahead* (capped)."""
    ranked = _ranked(comparison, max(1, years_ahead))
    return ranked[0] if ranked else "prophet"


def comparison_rows(comparison: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per model and horizon year, for tables and the agents."""
    rows: list[dict[str, Any]] = []
    for name in comparison.get("eligible", []):
        for r in comparison["backtests"][name]["by_year"]:
            rows.append({
                "model": name,
                "label": MODEL_LABELS[name],
                "years_ahead": r["year"],
                "windows": r["windows"],
                "annual_error_pct": round(r["model_mape_annual"], 1),
                "monthly_error_pct": round(r["model_mape_monthly"], 1),
                "band_annual_pct": round(r["band_annual"] * 100, 1) if r["band_annual"] is not None else None,
            })
    return rows


# ---------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------

def _months_between(start: pd.Timestamp, end: pd.Timestamp) -> int:
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


def _simple_forecast(
    fit_predict: FitPredict,
    bdf: pd.DataFrame,
    cutoff_year: int,
    horizon_year: int,
    basin: str,
    fuel_type: str,
    method: str,
) -> ForecastResult | None:
    """Forecast frame in ForecastResult's shape for a model without fitted history."""
    cutoff = pd.Timestamp(f"{cutoff_year}-12-31")
    horizon_end = pd.Timestamp(f"{horizon_year}-12-31")
    hist = bdf[["ds", "y"]].dropna().copy()
    hist["ds"] = pd.to_datetime(hist["ds"])
    hist = hist[hist["ds"] <= cutoff].resample("MS", on="ds")["y"].mean().dropna().reset_index()
    if hist.empty:
        return None

    periods = _months_between(hist["ds"].iloc[-1], horizon_end) if horizon_year > cutoff_year else 0
    past = pd.DataFrame({
        "ds": hist["ds"], "y_actual": hist["y"], "y_forecast": hist["y"],
        "y_lower": hist["y"], "y_upper": hist["y"],
    })
    frames = [past]
    if periods:
        yhat = fit_predict(hist, periods)
        if yhat is None:
            return None
        yhat = np.clip(np.asarray(yhat, dtype=float), 0.0, None)
        months = pd.date_range(hist["ds"].iloc[-1] + pd.DateOffset(months=1), periods=periods, freq="MS")
        frames.append(pd.DataFrame({
            "ds": months, "y_actual": np.nan, "y_forecast": yhat, "y_lower": yhat, "y_upper": yhat,
        }))
    df = pd.concat(frames, ignore_index=True)
    df["is_forecast"] = df["ds"] > cutoff
    df = df[df["ds"] <= horizon_end].reset_index(drop=True)
    return ForecastResult(df=df, basin=basin, fuel_type=fuel_type, cutoff_year=cutoff_year,
                          horizon_year=horizon_year, method=method)


def forecast_with(
    method: str,
    bdf: pd.DataFrame,
    drivers: pd.DataFrame | None,
    cutoff_year: int,
    horizon_year: int,
    basin: str = "",
    fuel_type: str = "",
) -> ForecastResult | None:
    """Forecast with one named model; None if that model cannot run from this cutoff."""
    if method == "prophet":
        return forecast_basin(bdf, cutoff_year, horizon_year, basin=basin, fuel_type=fuel_type)
    fit_predict = candidates(drivers).get(method)
    if fit_predict is None:
        return None
    return _simple_forecast(fit_predict, bdf, cutoff_year, horizon_year, basin, fuel_type, method)


def best_forecast(
    bdf: pd.DataFrame,
    drivers: pd.DataFrame | None,
    cutoff_year: int,
    horizon_year: int,
    comparison: dict[str, Any],
    basin: str = "",
    fuel_type: str = "",
) -> tuple[ForecastResult, dict[str, Any]]:
    """Forecast with the model that has done best at this horizon, banded from its errors.

    Falls back to the next best model if the chosen one cannot run from this
    cutoff (for example, EIA's activity data starts after it).
    """
    k = max(1, horizon_year - cutoff_year)
    first = choose(comparison, k)
    order = [first] + [m for m in _ranked(comparison, k) if m != first]
    if "prophet" not in order:
        order.append("prophet")

    for method in order:
        result = forecast_with(method, bdf, drivers, cutoff_year, horizon_year, basin, fuel_type)
        if result is None:
            continue
        bt = comparison.get("backtests", {}).get(method, {})
        calibrated = bool(bt) and "error" not in bt and method in comparison.get("eligible", [])
        if calibrated:
            result.df = apply_monthly_band(result.df, cutoff_year, bt)
        return result, {
            "model": method,
            "label": MODEL_LABELS[method],
            "years_ahead": k,
            "calibrated": calibrated,
            "backtest": bt if calibrated else {"error": "not backtested"},
            "fallback_from": first if method != first else None,
        }
    raise ValueError(f"No forecast model could run for {basin} {fuel_type} from {cutoff_year}.")


def _ranked(comparison: dict[str, Any], k: int) -> list[str]:
    """Eligible models from lowest to highest annual error at k years ahead."""
    if "error" in comparison:
        return []
    scores = {n: round(_row(comparison["backtests"][n], k)["model_mape_annual"], 1)
              for n in comparison.get("eligible", [])}
    return sorted(scores, key=lambda n: (scores[n], _SIMPLEST_FIRST.index(n)))


# ---------------------------------------------------------------------------
# Process-wide cache, keyed by the data so a refresh recomputes
# ---------------------------------------------------------------------------

_COMPARISONS: dict[tuple, dict[str, Any]] = {}


def _fingerprint(df: pd.DataFrame | None) -> tuple:
    if df is None or df.empty:
        return ()
    last = df.iloc[-1]
    return (len(df), str(df["ds"].max()), tuple(float(v) for v in last.drop("ds").to_numpy()
                                                  if isinstance(v, (int, float, np.floating))))


def compare_models_cached(
    basin: str, fuel_type: str, bdf: pd.DataFrame, drivers: pd.DataFrame | None,
) -> dict[str, Any]:
    key = (basin, fuel_type, _fingerprint(bdf[["ds", "y"]]), _fingerprint(drivers))
    if key not in _COMPARISONS:
        _COMPARISONS[key] = compare_models(bdf, drivers)
    return _COMPARISONS[key]
