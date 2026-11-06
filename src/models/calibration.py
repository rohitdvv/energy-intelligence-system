"""Rolling-origin backtests and forecast bands calibrated from real errors.

Prophet's own 80% interval covered only about half of actual months in the
November 2026 review, and its forecasts lost to a flat baseline at three
years. So the app no longer shows Prophet's interval. Instead it:

1. Refits the model at several past year-ends (origins), each time using only
   data up to that origin, and records how far each forecast missed:
       e = |actual - forecast| / forecast
2. Sets the band half-width at horizon year k to the split-conformal quantile
   of those errors:
       q_k = the ceil(0.8 * (n + 1))-th smallest of the n errors
       band = forecast * (1 - q_k) .. forecast * (1 + q_k)
   Monthly bands use monthly errors pooled by horizon year; annual bands use
   annual-total errors.
3. Scores two baselines on the same windows, so a forecast is only presented
   as better than "nothing changes" when it has been:
       monthly: seasonal naive, the last 12 observed months repeated
       annual:  flat, the last full year's total repeated

Horizons past the backtested years widen with the square root of the horizon,
q_k = q_K * sqrt(k / K), which is an assumption rather than a measurement.

Caveats: errors come from today's revised data, origins overlap in time, and
basins that share a state share errors, so treat small gaps as ties.
"""
from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np
import pandas as pd

FitPredict = Callable[[pd.DataFrame, int], "np.ndarray | None"]
"""(train frame with ds, y; months to predict) -> forecasts for those months,
or None when the model cannot forecast from that origin (it is then skipped)."""

LEVEL = 0.80
MAX_YEARS = 3
N_ORIGINS = 8
MIN_TRAIN_MONTHS = 48


def prophet_fit_predict(train: pd.DataFrame, periods: int) -> np.ndarray:
    """Fit Prophet with the app's settings and return the next ``periods`` months."""
    import logging

    logging.getLogger("prophet").setLevel(logging.ERROR)
    logging.getLogger("cmdstanpy").setLevel(logging.ERROR)
    from prophet import Prophet

    model = Prophet(
        seasonality_mode="multiplicative",
        changepoint_prior_scale=0.05,
        yearly_seasonality=True,
        weekly_seasonality=False,
        daily_seasonality=False,
        uncertainty_samples=0,
    )
    model.fit(train[["ds", "y"]], iter=1000)
    future = model.make_future_dataframe(periods=periods, freq="MS")
    yhat = model.predict(future)["yhat"].to_numpy()[-periods:]
    return np.clip(yhat, 0.0, None)


def _monthly(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["ds", "y"]].dropna().copy()
    out["ds"] = pd.to_datetime(out["ds"])
    return out.resample("MS", on="ds")["y"].mean().dropna().reset_index()


def _full_years(df: pd.DataFrame) -> pd.Series:
    """Annual totals for calendar years with all 12 months present."""
    years = df["ds"].dt.year
    counts = df.groupby(years)["y"].count()
    totals = df.groupby(years)["y"].sum()
    return totals[counts == 12]


def conformal_quantile(errors: np.ndarray, level: float = LEVEL) -> float | None:
    """Split-conformal quantile; with few errors this is the largest one seen."""
    errors = np.sort(np.asarray(errors, dtype=float)[np.isfinite(errors)])
    n = len(errors)
    if n == 0:
        return None
    rank = min(n, math.ceil(level * (n + 1)))
    return float(errors[rank - 1])


def run_backtest(
    df: pd.DataFrame,
    fit_predict: FitPredict = prophet_fit_predict,
    n_origins: int = N_ORIGINS,
    max_years: int = MAX_YEARS,
) -> dict[str, Any]:
    """Refit at past year-ends and measure monthly and annual errors.

    Returns a dict with ``monthly`` and ``annual`` error tables, per-horizon
    summaries and the calibrated band half-widths.
    """
    data = _monthly(df)
    totals = _full_years(data)
    if totals.empty:
        return {"error": "No complete calendar year of data."}
    last_full = int(totals.index.max())
    horizon = 12 * max_years

    monthly_rows: list[dict[str, Any]] = []
    annual_rows: list[dict[str, Any]] = []
    for origin_year in range(last_full - n_origins, last_full):
        origin = pd.Timestamp(f"{origin_year}-12-01")
        train = data[data["ds"] <= origin]
        if len(train) < MIN_TRAIN_MONTHS or origin_year not in totals.index:
            continue
        yhat = fit_predict(train, horizon)
        if yhat is None:
            continue
        months = pd.date_range(origin + pd.DateOffset(months=1), periods=horizon, freq="MS")
        naive = np.tile(train["y"].to_numpy()[-12:], max_years)
        actual = data.set_index("ds")["y"].reindex(months).to_numpy()

        for h in range(horizon):
            if np.isnan(actual[h]) or yhat[h] <= 0:
                continue
            monthly_rows.append({
                "origin": origin_year, "h": h + 1, "year": h // 12 + 1,
                "actual": actual[h], "model": yhat[h], "naive": naive[h],
            })
        for k in range(1, max_years + 1):
            if origin_year + k not in totals.index:
                continue
            model_total = float(yhat[(k - 1) * 12:k * 12].sum())
            if model_total <= 0:
                continue
            annual_rows.append({
                "origin": origin_year, "year": k,
                "actual": float(totals[origin_year + k]),
                "model": model_total,
                "flat": float(totals[origin_year]),
            })

    monthly = pd.DataFrame(monthly_rows)
    annual = pd.DataFrame(annual_rows)
    if monthly.empty or annual.empty:
        return {"error": "Not enough history for a rolling backtest."}

    monthly["err"] = (monthly["actual"] - monthly["model"]).abs() / monthly["model"]
    annual["err"] = (annual["actual"] - annual["model"]).abs() / annual["model"]

    by_year: list[dict[str, Any]] = []
    for k in range(1, max_years + 1):
        a = annual[annual["year"] == k]
        m = monthly[monthly["year"] == k]
        if a.empty or m.empty:
            continue
        by_year.append({
            "year": k,
            "windows": int(len(a)),
            "model_mape_annual": float((a["actual"] - a["model"]).abs().div(a["actual"]).mean() * 100),
            "flat_mape_annual": float((a["actual"] - a["flat"]).abs().div(a["actual"]).mean() * 100),
            "model_mape_monthly": float((m["actual"] - m["model"]).abs().div(m["actual"]).mean() * 100),
            "naive_mape_monthly": float((m["actual"] - m["naive"]).abs().div(m["actual"]).mean() * 100),
            "band_monthly": conformal_quantile(m["err"].to_numpy()),
            "band_annual": conformal_quantile(a["err"].to_numpy()),
        })

    return {
        "origins": sorted(monthly["origin"].unique().tolist()),
        "last_full_year": last_full,
        "by_year": by_year,
        "monthly": monthly,
        "annual": annual,
    }


def _band_for_year(by_year: list[dict[str, Any]], k: int, key: str) -> tuple[float | None, bool]:
    """Half-width at horizon year k, and whether it was extrapolated."""
    known = {row["year"]: row[key] for row in by_year if row.get(key) is not None}
    if not known:
        return None, False
    k = max(1, k)
    if k in known:
        return known[k], False
    last = max(known)
    return known[last] * math.sqrt(k / last), True


def band_half_width(backtest: dict[str, Any], k: int, annual: bool = False) -> tuple[float | None, bool]:
    return _band_for_year(backtest.get("by_year", []), k, "band_annual" if annual else "band_monthly")


def apply_monthly_band(fc_df: pd.DataFrame, cutoff_year: int, backtest: dict[str, Any]) -> pd.DataFrame:
    """Replace y_lower / y_upper on forecast rows with the calibrated band."""
    out = fc_df.copy()
    ds = pd.to_datetime(out["ds"])
    h = (ds.dt.year - cutoff_year - 1) * 12 + ds.dt.month  # Jan after the cutoff is 1
    for k in sorted(set(((h[out["is_forecast"]] - 1) // 12 + 1).astype(int))):
        q, _ = band_half_width(backtest, k)
        if q is None:
            continue
        rows = out["is_forecast"] & (((h - 1) // 12 + 1) == k)
        out.loc[rows, "y_lower"] = (out.loc[rows, "y_forecast"] * (1 - q)).clip(lower=0.0)
        out.loc[rows, "y_upper"] = out.loc[rows, "y_forecast"] * (1 + q)
    return out


def annual_band(total: float, k: int, backtest: dict[str, Any]) -> dict[str, Any] | None:
    """Calibrated 80% band for an annual total forecast k years past the cutoff."""
    q, extrapolated = band_half_width(backtest, k, annual=True)
    if q is None:
        return None
    return {
        "lower": round(max(0.0, total * (1 - q)), 2),
        "upper": round(total * (1 + q), 2),
        "half_width_pct": round(q * 100, 1),
        "extrapolated": extrapolated,
    }


def baseline_verdict(backtest: dict[str, Any], k: int) -> dict[str, Any] | None:
    """How the model did against the flat baseline at horizon year k (capped at the last tested)."""
    rows = backtest.get("by_year", [])
    if not rows:
        return None
    row = next((r for r in rows if r["year"] == k), rows[-1])
    return {
        "horizon_years_tested": row["year"],
        "windows": row["windows"],
        "model_error_pct": round(row["model_mape_annual"], 1),
        "flat_baseline_error_pct": round(row["flat_mape_annual"], 1),
        "beats_baseline": round(row["model_mape_annual"], 6) < round(row["flat_mape_annual"], 6),
    }
