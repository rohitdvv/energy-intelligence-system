"""The EIA well-activity model and per-basin model selection."""
from __future__ import annotations

from unittest import mock

import numpy as np
import pandas as pd
import pytest

from data.eia import steo_driver_rows
from models import selection
from models.drivers import seasonal_naive_fit_predict, well_activity_fit_predict


def _months(start: str, n: int) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="MS")


def _steady(rate: float, new: float, start: str = "2013-01-01", n: int = 160):
    """Production at a constant daily rate, with new wells exactly replacing the decline."""
    ds = _months(start, n)
    prod = pd.DataFrame({"ds": ds, "y": rate * ds.days_in_month})
    drivers = pd.DataFrame({"ds": ds, "new_well": new, "legacy_change": -new})
    return prod, drivers


# ---------------------------------------------------------------------------
# EIA rows
# ---------------------------------------------------------------------------

def test_driver_rows_are_split_by_series_and_cut_at_the_last_reported_month():
    rows = []
    for period in ["2026-04", "2026-05", "2026-06"]:
        rows.append({"period": period, "seriesId": "CONWPM", "value": "450.0"})
        rows.append({"period": period, "seriesId": "COEOPPM", "value": "-420.0"})
    df = steo_driver_rows(rows, "oil", pd.Timestamp("2026-05-01"))
    assert list(df.columns) == ["ds", "new_well", "legacy_change"]
    assert df["ds"].max() == pd.Timestamp("2026-05-01")
    assert (df["new_well"] == 450.0).all() and (df["legacy_change"] == -420.0).all()


def test_gas_driver_series_names():
    rows = [{"period": "2026-01", "seriesId": "NGNWAP", "value": 1200},
            {"period": "2026-01", "seriesId": "NGEOPAP", "value": -1190}]
    df = steo_driver_rows(rows, "gas", pd.Timestamp("2026-01-01"))
    assert df.iloc[0][["new_well", "legacy_change"]].tolist() == [1200, -1190]


# ---------------------------------------------------------------------------
# Well-activity model
# ---------------------------------------------------------------------------

def test_steady_state_stays_flat():
    prod, drivers = _steady(rate=1000.0, new=50.0)
    train = prod[prod["ds"] <= "2020-12-01"]
    out = well_activity_fit_predict(drivers)(train, 24)
    months = _months("2021-01-01", 24)
    np.testing.assert_allclose(out, 1000.0 * months.days_in_month)


def test_heads_towards_the_replacement_level():
    prod, drivers = _steady(rate=1000.0, new=50.0)
    drivers["new_well"] = 80.0  # more drilling than the decline needs: d = 0.05, level = 1600
    train = prod[prod["ds"] <= "2020-12-01"]
    out = well_activity_fit_predict(drivers)(train, 240)
    rates = out / _months("2021-01-01", 240).days_in_month
    assert rates[0] == pytest.approx(1030.0)
    assert np.all(np.diff(rates) > 0)
    assert rates[-1] == pytest.approx(1600.0, rel=1e-3)


def test_uses_no_driver_data_after_the_origin():
    prod, drivers = _steady(rate=1000.0, new=50.0)
    train = prod[prod["ds"] <= "2020-12-01"]
    changed = drivers.copy()
    changed.loc[changed["ds"] > "2020-12-01", ["new_well", "legacy_change"]] = [500.0, -5.0]
    a = well_activity_fit_predict(drivers)(train, 12)
    b = well_activity_fit_predict(changed)(train, 12)
    np.testing.assert_array_equal(a, b)


def test_declines_to_run_without_recent_or_enough_driver_data():
    prod, drivers = _steady(rate=1000.0, new=50.0)
    train = prod[prod["ds"] <= "2020-12-01"]
    stale = drivers[drivers["ds"] <= "2020-09-01"]  # three months behind the origin
    assert well_activity_fit_predict(stale)(train, 12) is None
    short = drivers[drivers["ds"].between("2020-06-01", "2020-12-01")]
    assert well_activity_fit_predict(short)(train, 12) is None


def test_repeat_last_year():
    y = pd.DataFrame({"ds": _months("2020-01-01", 24), "y": np.arange(24.0)})
    np.testing.assert_array_equal(seasonal_naive_fit_predict(y, 14),
                                  list(range(12, 24)) + [12.0, 13.0])


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def _comparison(errors: dict[str, list[float]], origins=(2017, 2024)) -> dict:
    backtests = {
        name: {"origins": list(range(*origins)), "by_year": [
            {"year": k + 1, "windows": 7, "model_mape_annual": e, "flat_mape_annual": errors["repeat"][k],
             "model_mape_monthly": e, "naive_mape_monthly": e, "band_monthly": 0.1, "band_annual": 0.1}
            for k, e in enumerate(errs)]}
        for name, errs in errors.items()
    }
    return {"backtests": backtests, "eligible": list(errors), "excluded": {}}


def test_choice_depends_on_the_horizon_and_ties_go_to_the_simpler_model():
    c = _comparison({"prophet": [9.0, 12.0, 20.0], "well_activity": [5.0, 12.0, 15.0],
                     "repeat": [8.0, 12.04, 11.0]})
    assert selection.choose(c, 1) == "well_activity"
    assert selection.choose(c, 2) == "repeat"  # 12.0 vs 12.0 after rounding
    assert selection.choose(c, 3) == "repeat"
    assert selection.choose(c, 7) == "repeat"  # capped at the longest tested horizon


def test_models_tested_on_fewer_years_are_not_compared():
    prod, drivers = _steady(rate=1000.0, new=50.0, start="2008-01-01", n=200)
    late = drivers[drivers["ds"] >= "2019-01-01"]  # too short for the early origins
    c = selection.compare_models(prod, late, models={
        "repeat": seasonal_naive_fit_predict,
        "well_activity": well_activity_fit_predict(late),
    })
    assert c["eligible"] == ["repeat"]
    assert "well_activity" in c["excluded"]


def test_best_forecast_falls_back_when_the_chosen_model_cannot_run():
    prod, drivers = _steady(rate=1000.0, new=50.0)
    c = _comparison({"prophet": [9.0, 9.0, 9.0], "well_activity": [1.0, 1.0, 1.0],
                     "repeat": [5.0, 5.0, 5.0]})
    stale = drivers[drivers["ds"] <= "2018-01-01"]
    result, chosen = selection.best_forecast(prod, stale, 2020, 2022, c, basin="X", fuel_type="oil")
    assert chosen["model"] == "repeat" and chosen["fallback_from"] == "well_activity"
    assert result.method == "repeat"
    fc = result.df[result.df["is_forecast"]]
    assert fc["ds"].min() == pd.Timestamp("2021-01-01") and fc["ds"].max() == pd.Timestamp("2022-12-01")
    assert (fc["y_upper"] > fc["y_forecast"]).all() and (fc["y_lower"] < fc["y_forecast"]).all()


def test_forecast_frame_has_the_shape_kpis_expect():
    from kpi.metrics import basin_kpi_summary

    prod, drivers = _steady(rate=1000.0, new=50.0)
    result = selection.forecast_with("well_activity", prod, drivers, 2020, 2023, "X", "oil")
    assert set(result.df.columns) >= {"ds", "y_actual", "y_forecast", "y_lower", "y_upper", "is_forecast"}
    kpi = basin_kpi_summary(result, 2023)
    assert kpi["projected_production"]["value"] == pytest.approx(365_000.0)
    assert kpi["growth_rate"]["yoy_pct"] == pytest.approx(0.0, abs=1e-9)


def test_forecast_tool_reports_the_model_used():
    from agents import tools

    prod, drivers = _steady(rate=1000.0, new=50.0, start="2010-01-01", n=168)
    prod["y"] *= 1 + 0.02 * np.sin(np.arange(len(prod)))  # some noise so models differ
    selection._COMPARISONS.clear()

    def bad_prophet(train, periods):
        return np.full(periods, float(train["y"].iloc[-1]) * 3)

    with mock.patch.object(tools, "_load_basin", return_value=prod), \
         mock.patch.object(tools, "load_drivers_no_cache", return_value=drivers), \
         mock.patch.object(selection, "prophet_fit_predict", bad_prophet):
        out = tools.execute_tool("forecast_basin", {"basin": "Permian", "fuel_type": "oil",
                                                     "cutoff_year": 2022, "horizon_year": 2024})
    assert out["forecast_model"] in ("EIA well activity", "Repeat last year")
    assert set(out["backtest_annual_error_pct_by_model"]) == {
        "Prophet trend", "EIA well activity", "Repeat last year"}
    assert out["interval_80pct"]["lower"] < out["projected_annual_total"] < out["interval_80pct"]["upper"]
    selection._COMPARISONS.clear()
