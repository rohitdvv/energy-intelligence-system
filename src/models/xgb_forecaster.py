"""XGBoost multi-step recursive forecaster for monthly production series.

Approach
--------
1. Engineer lag features (t-1, t-2, t-3, t-6, t-12), cyclic calendar
   encodings (month sin/cos), a normalised year trend, and rolling
   statistics (mean/std over 3-, 6-, 12-month windows).
2. Fit an XGBRegressor on all historical months up to cutoff_year.
3. Forecast forward recursively: each new prediction is appended to the
   running series and becomes a lag input for the next step.
4. 80 % confidence band estimated from in-sample residual std (±1.28 σ),
   matching Prophet's interval width for fair comparison.

Why XGBoost complements Prophet
--------------------------------
Prophet models global trend + Fourier seasonality.  XGBoost captures
local, regime-specific patterns in the lag structure (e.g. post-shale
boom autocorrelation, COVID recovery shape) that a parametric trend
curve cannot represent.  Their ensemble therefore outperforms either
model alone on held-out data.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

FEATURE_COLS: list[str] = [
    "month_sin", "month_cos", "year_norm",
    "lag_1", "lag_2", "lag_3", "lag_6", "lag_12",
    "rolling_mean_3", "rolling_mean_6", "rolling_mean_12", "rolling_std_6",
]

_Z80 = 1.2816  # z-score for 80 % two-tailed CI


@dataclass
class XGBForecastResult:
    """Output of forecast_xgb()."""

    df: pd.DataFrame
    """Columns: ds, y_actual, y_forecast, y_lower, y_upper, is_forecast"""

    basin: str
    fuel_type: str
    cutoff_year: int
    horizon_year: int
    feature_importance: dict[str, float] = field(default_factory=dict)
    in_sample_mape: float | None = None


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def _build_features(
    series: pd.DataFrame,
    yr_min: int,
    yr_max: int,
) -> pd.DataFrame:
    """Return series with all FEATURE_COLS added (NaN where insufficient history)."""
    d = series[["ds", "y"]].copy()
    d["ds"] = pd.to_datetime(d["ds"])
    d = d.sort_values("ds").reset_index(drop=True)

    d["month_sin"] = np.sin(2 * np.pi * d["ds"].dt.month / 12)
    d["month_cos"] = np.cos(2 * np.pi * d["ds"].dt.month / 12)
    d["year_norm"] = (d["ds"].dt.year - yr_min) / max(1, yr_max - yr_min)

    for lag in [1, 2, 3, 6, 12]:
        d[f"lag_{lag}"] = d["y"].shift(lag)

    d["rolling_mean_3"]  = d["y"].shift(1).rolling(3).mean()
    d["rolling_mean_6"]  = d["y"].shift(1).rolling(6).mean()
    d["rolling_mean_12"] = d["y"].shift(1).rolling(12).mean()
    d["rolling_std_6"]   = d["y"].shift(1).rolling(6).std().fillna(0.0)

    return d


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def forecast_xgb(
    df: pd.DataFrame,
    cutoff_year: int,
    horizon_year: int | None = None,
    basin: str = "",
    fuel_type: str = "",
) -> XGBForecastResult:
    """Fit XGBoost on history up to *cutoff_year* and project to *horizon_year*.

    Parameters
    ----------
    df:
        DataFrame with columns ``ds`` (datetime) and ``y`` (float ≥ 0).
    cutoff_year:
        Last year included in training. Data beyond this date is forecast.
    horizon_year:
        Last year to project; defaults to cutoff_year + 5.
    basin, fuel_type:
        Metadata echoed into the result.
    """
    import xgboost as xgb  # lazy — avoids import cost at module load

    if horizon_year is None:
        horizon_year = cutoff_year + 5

    cutoff      = pd.Timestamp(f"{cutoff_year}-12-31")
    horizon_end = pd.Timestamp(f"{horizon_year}-12-31")

    # ── Training data ────────────────────────────────────────────────────────
    hist = (
        df[df["ds"] <= cutoff][["ds", "y"]]
        .dropna()
        .copy()
    )
    hist = hist.resample("MS", on="ds")["y"].mean().reset_index()

    if len(hist) < 18:
        raise ValueError(
            f"XGBoost needs >= 18 historical months; got {len(hist)} for {basin!r}."
        )

    yr_min = int(hist["ds"].dt.year.min())
    feat_df = _build_features(hist, yr_min, horizon_year)
    train   = feat_df.dropna(subset=FEATURE_COLS)

    X_train = train[FEATURE_COLS].values
    y_train = train["y"].values

    model = xgb.XGBRegressor(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.08,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=3,
        reg_alpha=0.1,
        random_state=42,
        verbosity=0,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model.fit(X_train, y_train)

    # In-sample residuals → σ for 80 % CI
    in_sample_preds = model.predict(X_train)
    residuals       = y_train - in_sample_preds
    sigma           = float(residuals.std()) if len(residuals) > 1 else 0.0
    ci_half         = _Z80 * sigma

    # In-sample MAPE (training-set diagnostic only)
    with np.errstate(divide="ignore", invalid="ignore"):
        ape = np.abs(residuals) / np.where(y_train != 0, y_train, np.nan)
    in_sample_mape = float(np.nanmean(ape) * 100)

    feat_imp = dict(zip(FEATURE_COLS, map(float, model.feature_importances_)))

    in_sample_map: dict[pd.Timestamp, float] = {
        row["ds"]: max(0.0, float(p))
        for row, p in zip(train.to_dict("records"), in_sample_preds)
    }

    # ── Recursive multi-step forecast ────────────────────────────────────────
    running_vals = list(hist["y"].values)

    future_dates = pd.date_range(
        start=hist["ds"].max() + pd.DateOffset(months=1),
        end=horizon_end,
        freq="MS",
    )

    future_preds: list[float] = []
    for fd in future_dates:
        month_sin = float(np.sin(2 * np.pi * fd.month / 12))
        month_cos = float(np.cos(2 * np.pi * fd.month / 12))
        year_norm = (fd.year - yr_min) / max(1, horizon_year - yr_min)

        v = running_vals
        lag_vals = [
            v[-1]  if len(v) >= 1  else 0.0,
            v[-2]  if len(v) >= 2  else 0.0,
            v[-3]  if len(v) >= 3  else 0.0,
            v[-6]  if len(v) >= 6  else 0.0,
            v[-12] if len(v) >= 12 else 0.0,
        ]
        rm3  = float(np.mean(v[-3:]))  if len(v) >= 3  else float(np.mean(v))
        rm6  = float(np.mean(v[-6:]))  if len(v) >= 6  else float(np.mean(v))
        rm12 = float(np.mean(v[-12:])) if len(v) >= 12 else float(np.mean(v))
        rs6  = float(np.std(v[-6:]))   if len(v) >= 6  else 0.0

        feat = [month_sin, month_cos, year_norm, *lag_vals, rm3, rm6, rm12, rs6]
        pred = max(0.0, float(model.predict([feat])[0]))
        future_preds.append(pred)
        running_vals.append(pred)

    # ── Assemble result DataFrame ────────────────────────────────────────────
    rows: list[dict] = []

    for _, row in hist.iterrows():
        ds  = row["ds"]
        yfc = in_sample_map.get(ds, float(row["y"]))
        rows.append({
            "ds":          ds,
            "y_actual":    float(row["y"]),
            "y_forecast":  yfc,
            "y_lower":     max(0.0, yfc - ci_half),
            "y_upper":     yfc + ci_half,
            "is_forecast": False,
        })

    for ds, pred in zip(future_dates, future_preds):
        rows.append({
            "ds":          ds,
            "y_actual":    float("nan"),
            "y_forecast":  pred,
            "y_lower":     max(0.0, pred - ci_half),
            "y_upper":     pred + ci_half,
            "is_forecast": True,
        })

    result_df = (
        pd.DataFrame(rows)
        .query("ds <= @horizon_end")
        .sort_values("ds")
        .reset_index(drop=True)
    )

    return XGBForecastResult(
        df=result_df,
        basin=basin,
        fuel_type=fuel_type,
        cutoff_year=cutoff_year,
        horizon_year=horizon_year,
        feature_importance=feat_imp,
        in_sample_mape=in_sample_mape,
    )
