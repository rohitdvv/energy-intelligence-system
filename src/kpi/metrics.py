"""KPI calculation library for the Energy Intelligence System.

All functions take clean DataFrames (ds, y columns) and return plain Python
scalars or dicts so they are easy to display in Streamlit and pass to AI agents.

KPI definitions (Tier 1 required + Tier 2 extras):
  projected_production_estimate  – annual total for a given year (actual or forecast)
  production_growth_rate         – year-over-year % change
  production_decline_rate        – CAGR from peak to most recent year
  volatility_score               – coefficient of variation (lower = more stable)
  revenue_potential              – production × commodity price assumption
  relative_performance_index     – 0–100 score of a basin vs its peers
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from models.forecaster import ForecastResult


# ------------------------------------------------------------------
# Tier 1 — Required
# ------------------------------------------------------------------

def projected_production_estimate(
    result: ForecastResult,
    year: int,
) -> dict[str, Any]:
    """Annual production estimate for *year* — the primary required KPI.

    For historical years (year ≤ cutoff_year): sums or averages actuals.
    For future years: uses Prophet yhat.

    Returns a dict with keys:
      year, value, unit, source ('actual' | 'forecast'), basin, fuel_type.
    """
    df = result.df.copy()
    year_df = df[df["ds"].dt.year == year]

    is_future = year > result.cutoff_year
    col = "y_forecast" if is_future else "y_actual"

    if year_df.empty or year_df[col].isna().all():
        return {
            "year": year, "value": None, "unit": _unit(result.fuel_type),
            "source": "forecast" if is_future else "actual",
            "basin": result.basin, "fuel_type": result.fuel_type,
        }

    total = float(year_df[col].sum())
    return {
        "year": year,
        "value": round(total, 2),
        "unit": _unit(result.fuel_type),
        "source": "forecast" if is_future else "actual",
        "basin": result.basin,
        "fuel_type": result.fuel_type,
    }


# ------------------------------------------------------------------
# Tier 2 — Stretch
# ------------------------------------------------------------------

def production_growth_rate(df: pd.DataFrame, year: int) -> dict[str, Any]:
    """Year-over-year percentage change in total production.

    Formula: (year_total - prior_year_total) / prior_year_total × 100.
    Requires at least 2 years of data.
    """
    annual = _annual_totals(df)
    if year not in annual.index or (year - 1) not in annual.index:
        return {"year": year, "yoy_pct": None, "year_total": None, "prior_total": None}

    curr = annual.loc[year]
    prior = annual.loc[year - 1]
    pct = ((curr - prior) / prior * 100) if prior != 0 else None

    return {
        "year": year,
        "yoy_pct": round(float(pct), 2) if pct is not None else None,
        "year_total": round(float(curr), 2),
        "prior_total": round(float(prior), 2),
    }


def production_decline_rate(df: pd.DataFrame, n_years: int = 3) -> dict[str, Any]:
    """Compound annual growth/decline rate from peak to most recent year.

    Positive result = growth; negative = decline.
    Uses the last *n_years* of data so it reflects recent trajectory.
    """
    annual = _annual_totals(df)
    if len(annual) < 2:
        return {"cagr_pct": None, "peak_year": None, "latest_year": None, "n_years": n_years}

    recent = annual.iloc[-n_years:] if len(annual) >= n_years else annual
    start_val, end_val = recent.iloc[0], recent.iloc[-1]
    years = len(recent) - 1

    if start_val <= 0 or years == 0:
        return {"cagr_pct": None, "peak_year": None, "latest_year": None, "n_years": n_years}

    cagr = ((end_val / start_val) ** (1 / years) - 1) * 100
    return {
        "cagr_pct": round(float(cagr), 2),
        "start_year": int(recent.index[0]),
        "latest_year": int(recent.index[-1]),
        "n_years": years,
    }


def volatility_score(df: pd.DataFrame) -> dict[str, Any]:
    """Coefficient of variation of monthly production (std / mean × 100).

    Lower score = more consistent production.
    Score is computed on the full series after detrending by 12-month
    rolling mean to isolate noise from secular trend.
    """
    series = df["y"].dropna()
    if len(series) < 12:
        return {"cv_pct": None, "interpretation": "insufficient data"}

    trend = series.rolling(12, center=True).mean()
    residuals = series - trend
    valid = residuals.dropna()

    if valid.mean() == 0 or len(valid) < 6:
        raw_cv = (series.std() / series.mean() * 100) if series.mean() > 0 else None
        cv = raw_cv
    else:
        cv = float(valid.std() / series.mean() * 100)

    if cv is None:
        interpretation = "insufficient data"
    elif cv < 5:
        interpretation = "very stable"
    elif cv < 15:
        interpretation = "stable"
    elif cv < 30:
        interpretation = "moderate volatility"
    else:
        interpretation = "high volatility"

    return {"cv_pct": round(cv, 2) if cv is not None else None, "interpretation": interpretation}


def revenue_potential(
    production_mbbls: float,
    wti_price_per_bbl: float,
    fuel_type: str = "oil",
) -> dict[str, Any]:
    """Estimated gross revenue from a production volume + price assumption.

    For oil: production (Mbbls/month) × 1_000 bbl/Mbbl × price ($/bbl).
    For gas: production (MMcf/month) × price_per_mcf (default WTI/6 BTU parity).

    Returns value in USD millions.
    """
    if fuel_type == "oil":
        revenue_usd = production_mbbls * 1_000 * wti_price_per_bbl
        price_used = wti_price_per_bbl
        price_unit = "$/bbl"
    else:
        # Rough Henry Hub parity: 1 Mcf ≈ WTI_price / 6
        price_per_mcf = wti_price_per_bbl / 6
        revenue_usd = production_mbbls * 1_000 * price_per_mcf  # MMcf → Mcf
        price_used = price_per_mcf
        price_unit = "$/Mcf (estimated)"

    return {
        "revenue_usd_millions": round(revenue_usd / 1_000_000, 2),
        "production_input": production_mbbls,
        "price_used": round(price_used, 2),
        "price_unit": price_unit,
        "fuel_type": fuel_type,
    }


def relative_performance_index(
    basin_totals: dict[str, float],
) -> dict[str, float]:
    """Normalise basin annual production totals to a 0–100 index.

    Parameters
    ----------
    basin_totals:
        {basin_name: annual_production_total} for all basins in the peer set.

    Returns
    -------
    {basin_name: score_0_to_100}
    """
    if not basin_totals:
        return {}

    values = np.array(list(basin_totals.values()), dtype=float)
    lo, hi = values.min(), values.max()
    span = hi - lo if hi > lo else 1.0

    return {
        basin: round(float((val - lo) / span * 100), 1)
        for basin, val in basin_totals.items()
    }


def basin_kpi_summary(
    result: ForecastResult,
    target_year: int,
    wti_price: float = 75.0,
) -> dict[str, Any]:
    """Compute all KPIs for a single basin ForecastResult.

    Convenience aggregator that packages every metric into a single dict
    suitable for display in Streamlit or injection into an AI agent prompt.
    """
    historical_df = result.historical[["ds", "y_actual"]].rename(columns={"y_actual": "y"})

    ppe = projected_production_estimate(result, target_year)
    pgr = production_growth_rate(historical_df, min(target_year, result.cutoff_year))
    pdr = production_decline_rate(historical_df)
    vs = volatility_score(historical_df)

    rev: dict[str, Any] = {}
    if ppe["value"] is not None:
        rev = revenue_potential(ppe["value"] / 12, wti_price, result.fuel_type)

    return {
        "basin": result.basin,
        "fuel_type": result.fuel_type,
        "target_year": target_year,
        "projected_production": ppe,
        "growth_rate": pgr,
        "decline_rate": pdr,
        "volatility": vs,
        "revenue_potential": rev,
    }


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _annual_totals(df: pd.DataFrame) -> pd.Series:
    """Sum monthly 'y' values by year, returning a Series indexed by year."""
    tmp = df.copy()
    tmp["year"] = pd.to_datetime(tmp["ds"]).dt.year
    return tmp.groupby("year")["y"].sum()


def _unit(fuel_type: str) -> str:
    return "Mbbls/yr" if fuel_type == "oil" else "MMcf/yr" if fuel_type == "gas" else "$/bbl"
