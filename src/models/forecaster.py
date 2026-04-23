"""Prophet-based monthly production forecaster.

Methodology
-----------
1. Fit Facebook Prophet on monthly historical production up to *cutoff_year*.
2. Generate predictions from the first historical month through to the end
   of *horizon_year* (default: cutoff + 5 years).
3. Return a ForecastResult whose .df has both historical actuals and
   forward-looking forecast rows, clearly labeled via `is_forecast`.

Assumptions documented (per Tier 1 requirement):
  - Seasonality mode: multiplicative (production volumes scale with trend).
  - Changepoint prior scale: 0.05 (moderate flexibility; prevents overfitting
    on noisy monthly data).
  - Yearly seasonality: enabled to capture rig-count / seasonal patterns.
  - 80 % prediction interval width for uncertainty bands.
  - Data is NOT log-transformed; negative forecasts are clipped to 0.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd
from prophet import Prophet

logger = logging.getLogger(__name__)


@dataclass
class ForecastResult:
    """Output of BasinForecaster.forecast()."""

    df: pd.DataFrame
    """Combined DataFrame with columns:
       ds            – datetime (month-start)
       y_actual      – observed production (NaN for future periods)
       y_forecast    – Prophet yhat (all periods)
       y_lower       – lower confidence bound (all periods)
       y_upper       – upper confidence bound (all periods)
       is_forecast   – True for ds > cutoff date
    """
    basin: str
    fuel_type: str
    cutoff_year: int
    horizon_year: int
    model: Prophet = field(repr=False)

    @property
    def historical(self) -> pd.DataFrame:
        """Rows where is_forecast is False."""
        return self.df[~self.df["is_forecast"]]

    @property
    def forecast(self) -> pd.DataFrame:
        """Rows where is_forecast is True."""
        return self.df[self.df["is_forecast"]]


class BasinForecaster:
    """Fit Prophet on a basin production series and project forward.

    Parameters
    ----------
    seasonality_mode:
        'multiplicative' is appropriate for production volumes that scale
        with the trend level. Use 'additive' for flat/stable series.
    changepoint_prior_scale:
        Flexibility of the trend. Higher → more breakpoints detected.
    interval_width:
        Fraction for the yhat_lower / yhat_upper confidence interval.
    """

    def __init__(
        self,
        seasonality_mode: str = "multiplicative",
        changepoint_prior_scale: float = 0.05,
        interval_width: float = 0.80,
    ) -> None:
        self.seasonality_mode = seasonality_mode
        self.changepoint_prior_scale = changepoint_prior_scale
        self.interval_width = interval_width

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def forecast(
        self,
        df: pd.DataFrame,
        cutoff_year: int,
        horizon_year: int | None = None,
        basin: str = "",
        fuel_type: str = "",
    ) -> ForecastResult:
        """Fit on data up to *cutoff_year* and predict through *horizon_year*.

        Parameters
        ----------
        df:
            Must have 'ds' (datetime) and 'y' (numeric, non-negative) columns.
        cutoff_year:
            Inclusive last year of historical data used for model fitting.
        horizon_year:
            Last year to predict. Defaults to cutoff_year + 5.
        basin, fuel_type:
            Metadata labels stored on the result.

        Returns
        -------
        ForecastResult
        """
        if horizon_year is None:
            horizon_year = cutoff_year + 5

        cutoff = pd.Timestamp(f"{cutoff_year}-12-31")
        horizon_end = pd.Timestamp(f"{horizon_year}-12-31")

        historical = (
            df[df["ds"] <= cutoff][["ds", "y"]]
            .dropna(subset=["y"])
            .copy()
        )

        if len(historical) < 6:
            raise ValueError(
                f"Need at least 6 historical months to fit Prophet; "
                f"got {len(historical)} for basin={basin!r} cutoff={cutoff_year}."
            )

        # Resample to clean month-start frequency before fitting
        historical = (
            historical.resample("MS", on="ds")["y"]
            .mean()
            .reset_index()
        )

        model = Prophet(
            seasonality_mode=self.seasonality_mode,
            changepoint_prior_scale=self.changepoint_prior_scale,
            yearly_seasonality=True,
            weekly_seasonality=False,
            daily_seasonality=False,
            interval_width=self.interval_width,
        )
        # Suppress verbose Stan output
        model.fit(historical, iter=1000)

        # Number of months to project beyond the last historical point
        last_ds = historical["ds"].max()
        if horizon_year > cutoff_year:
            periods = _months_between(last_ds, horizon_end)
        else:
            periods = 0

        future = model.make_future_dataframe(periods=periods, freq="MS")
        raw_forecast = model.predict(future)

        result_df = _merge_actuals(historical, raw_forecast, cutoff)
        result_df = result_df[result_df["ds"] <= horizon_end].copy()

        return ForecastResult(
            df=result_df,
            basin=basin,
            fuel_type=fuel_type,
            cutoff_year=cutoff_year,
            horizon_year=horizon_year,
            model=model,
        )


# ------------------------------------------------------------------
# Module-level convenience
# ------------------------------------------------------------------

_default_forecaster = BasinForecaster()


def forecast_basin(
    df: pd.DataFrame,
    cutoff_year: int,
    horizon_year: int | None = None,
    basin: str = "",
    fuel_type: str = "",
) -> ForecastResult:
    """Convenience wrapper using default BasinForecaster settings."""
    return _default_forecaster.forecast(
        df, cutoff_year, horizon_year, basin, fuel_type
    )


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _months_between(start: pd.Timestamp, end: pd.Timestamp) -> int:
    """Whole months from *start* to *end* (inclusive of end month)."""
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


def _merge_actuals(
    historical: pd.DataFrame,
    raw_forecast: pd.DataFrame,
    cutoff: pd.Timestamp,
) -> pd.DataFrame:
    """Join Prophet output with actuals; label forecast rows."""
    fc = raw_forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()
    fc = fc.merge(
        historical.rename(columns={"y": "y_actual"}),
        on="ds",
        how="left",
    )
    fc["is_forecast"] = fc["ds"] > cutoff
    fc.rename(
        columns={
            "yhat": "y_forecast",
            "yhat_lower": "y_lower",
            "yhat_upper": "y_upper",
        },
        inplace=True,
    )
    # Clip negative forecasts — production cannot go below zero
    for col in ("y_forecast", "y_lower"):
        fc[col] = fc[col].clip(lower=0.0)

    return fc.sort_values("ds").reset_index(drop=True)
