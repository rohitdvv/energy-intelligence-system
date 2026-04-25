"""Held-out backtesting for Prophet production forecasts."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def backtest_mape(
    df: pd.DataFrame,
    basin: str,
    fuel_type: str,
    hold_months: int = 12,
) -> dict[str, Any]:
    """Fit Prophet on all-but-last *hold_months*, predict the held-out window, return MAPE.

    Parameters
    ----------
    df : DataFrame with columns ``ds`` (datetime) and ``y`` (float).
    basin, fuel_type : echoed into the result dict unchanged.
    hold_months : trailing months to hold out; default 12.

    Returns
    -------
    dict with keys:
        basin, fuel_type, mape_pct, n_predictions, train_end, test_start, test_end.

    Raises
    ------
    ValueError if fewer than hold_months + 12 rows are available.
    """
    import logging as _log
    _log.getLogger("prophet").setLevel(_log.ERROR)
    _log.getLogger("cmdstanpy").setLevel(_log.ERROR)
    from prophet import Prophet  # lazy — Prophet/Stan is slow to import

    df = df.copy()
    df["ds"] = pd.to_datetime(df["ds"])
    df = df.sort_values("ds").dropna(subset=["y"]).reset_index(drop=True)

    min_rows = hold_months + 12
    if len(df) < min_rows:
        raise ValueError(
            f"backtest_mape needs >= {min_rows} rows (got {len(df)}) "
            f"for basin={basin!r} fuel={fuel_type!r}"
        )

    train = df.iloc[:-hold_months].copy()
    test = df.iloc[-hold_months:].copy()

    model = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=False,
        daily_seasonality=False,
        seasonality_mode="multiplicative",
        changepoint_prior_scale=0.05,
    )
    model.fit(train)

    future = model.make_future_dataframe(periods=hold_months, freq="MS")
    fc = model.predict(future)
    fc_map: dict = fc.set_index("ds")["yhat"].to_dict()

    test["yhat"] = test["ds"].map(fc_map)
    test = test.dropna(subset=["yhat"])

    if test.empty:
        raise ValueError(f"No overlapping forecast dates for basin={basin!r}")

    # MAPE only over non-zero actuals to avoid division by zero
    valid = test[test["y"].abs() > 0].copy()
    if valid.empty:
        raise ValueError(f"All-zero actuals in test window for basin={basin!r}")

    ape = (valid["y"] - valid["yhat"]).abs() / valid["y"].abs()
    mape = float(ape.mean() * 100)

    logger.info(
        "backtest_mape: basin=%s fuel=%s hold=%d mape=%.2f%% n=%d",
        basin, fuel_type, hold_months, mape, len(valid),
    )

    return {
        "basin": basin,
        "fuel_type": fuel_type,
        "mape_pct": round(mape, 2),
        "n_predictions": len(valid),
        "train_end": train["ds"].iloc[-1].strftime("%Y-%m"),
        "test_start": test["ds"].iloc[0].strftime("%Y-%m"),
        "test_end": test["ds"].iloc[-1].strftime("%Y-%m"),
    }
