"""Forecast models other than Prophet: EIA well activity, and repeating the last year.

Well-activity model
-------------------
EIA splits each region's monthly production change into two parts, published
with the Short-Term Energy Outlook:

  new_well       production in the month from wells that started producing
                 that month (kb/d for oil, MMcf/d for gas)
  legacy_change  the change in production from all older wells, almost always
                 negative because shale wells decline fast

So  rate[t] = rate[t-1] + new_well[t] + legacy_change[t].

The model holds the last 12 months of drilling activity steady and lets the
legacy decline scale with the size of the producing base:

  new  = mean of new_well over the 12 months to the forecast origin
  d    = mean of (-legacy_change / rate) over the same months
  rate[t+1] = rate[t] + new - d * rate[t]

That tends towards new / d, the rate at which new wells just replace the
decline. It assumes activity stays at the past year's level; it does not
forecast rig counts or prices. Monthly volumes are rate x days in month.

Repeat last year
----------------
The last 12 months before the origin, repeated. Its annual total is the last
full year's total, the "flat" baseline the other models are scored against.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from models.calibration import FitPredict

AVG_MONTHS = 12
MAX_DRIVER_LAG_MONTHS = 2  # drivers may end up to two months before the origin


def seasonal_naive_fit_predict(train: pd.DataFrame, periods: int) -> np.ndarray | None:
    """The last 12 observed months, repeated for *periods* months."""
    y = train["y"].to_numpy(dtype=float)
    if len(y) < 12:
        return None
    reps = int(np.ceil(periods / 12))
    return np.tile(y[-12:], reps)[:periods]


def well_activity_fit_predict(drivers: pd.DataFrame) -> FitPredict:
    """A FitPredict that uses EIA new-well and legacy-change data up to each origin only."""
    drv = drivers.copy()
    drv["ds"] = pd.to_datetime(drv["ds"])
    drv = drv.set_index("ds").sort_index()

    def fit_predict(train: pd.DataFrame, periods: int) -> np.ndarray | None:
        hist = train[["ds", "y"]].copy()
        hist["ds"] = pd.to_datetime(hist["ds"])
        hist = hist.set_index("ds").sort_index()
        if hist.empty:
            return None
        origin = hist.index[-1]
        rate = hist["y"] / hist.index.days_in_month  # kb/d or MMcf/d
        window = drv[drv.index <= origin].tail(AVG_MONTHS)
        if len(window) < AVG_MONTHS:
            return None
        lag = (origin.year - window.index[-1].year) * 12 + origin.month - window.index[-1].month
        if lag > MAX_DRIVER_LAG_MONTHS:
            return None
        base = rate.reindex(window.index)
        if base.isna().any() or (base <= 0).any():
            return None
        new = float(window["new_well"].mean())
        decline = float((-window["legacy_change"] / base).mean())

        months = pd.date_range(origin + pd.DateOffset(months=1), periods=periods, freq="MS")
        r = float(rate.iloc[-1])
        out = np.empty(periods)
        for i, month in enumerate(months):
            r = max(0.0, r + new - decline * r)
            out[i] = r * month.days_in_month
        return out

    return fit_predict
