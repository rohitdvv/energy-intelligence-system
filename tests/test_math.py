"""Well economics, gas pricing and forecast calibration."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from kpi.metrics import MMBTU_PER_MCF, price_assumption_text, revenue_potential
from models import calibration as cal
from models import well_economics as we

PERMIAN = dict(
    ip_per_day=700, de_first_year=0.70, b=1.3, d_min=0.08, capex=9.5e6, price=75.0,
    royalty=0.20, production_tax=0.046, variable_cost=12.0, fixed_cost_month=3_000.0,
    discount_rate=0.10, years=20,
)


# ---------------------------------------------------------------------------
# Decline curve
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("b", [0.0, 0.5, 1.0, 1.3, 2.0])
def test_first_year_decline_matches_the_input(b):
    qi = 1_000 * we.DAYS_PER_MONTH
    di = we.nominal_decline(0.70, b) / 12.0
    q12 = qi * np.exp(-di * 12) if b == 0 else qi / (1 + b * di * 12) ** (1 / b)
    assert q12 / qi == pytest.approx(0.30, rel=1e-9)


def test_monthly_volume_is_the_integral_of_the_rate():
    # Fine numerical integral of the rate over month one, no terminal switch yet.
    qi = 700 * we.DAYS_PER_MONTH
    di = we.nominal_decline(0.70, 1.3) / 12.0
    t = np.linspace(0, 1, 20_001)
    rate = qi / (1 + 1.3 * di * t) ** (1 / 1.3)
    month_one = float(np.sum((rate[1:] + rate[:-1]) / 2 * np.diff(t)))
    assert we.monthly_volumes(700, 0.70, 1.3, 0.08, 1)[0] == pytest.approx(month_one, rel=1e-6)


def test_terminal_decline_bounds_volume_when_b_above_one():
    no_floor = we.monthly_volumes(700, 0.70, 1.3, 0.0, 1_200).sum()
    floored = we.monthly_volumes(700, 0.70, 1.3, 0.08, 1_200).sum()
    floored_double = we.monthly_volumes(700, 0.70, 1.3, 0.08, 2_400).sum()
    assert floored < no_floor
    assert floored_double - floored < 0.01 * floored  # converges


def test_curve_is_continuous_at_the_terminal_switch():
    v = we.monthly_volumes(700, 0.70, 1.3, 0.08, 240)
    ratios = v[1:] / v[:-1]
    assert np.all(ratios < 1.0)
    # The switch is near month 108; month-on-month decline changes smoothly through it.
    assert np.max(np.abs(np.diff(ratios[95:125]))) < 1e-3
    assert ratios[-1] == pytest.approx(0.92 ** (1 / 12), rel=1e-9)


# ---------------------------------------------------------------------------
# Cash flow
# ---------------------------------------------------------------------------

def test_npv_is_zero_at_the_irr_and_at_the_breakeven_price():
    econ = we.evaluate(we.WellInputs(**PERMIAN))
    assert econ["irr"] is not None and 0 < econ["irr"] < 10
    assert we.npv(econ["net_cf"], PERMIAN["capex"], econ["irr"]) == pytest.approx(0, abs=1.0)

    at_breakeven = we.evaluate(we.WellInputs(**{**PERMIAN, "price": econ["breakeven_npv"]}))
    assert at_breakeven["npv"] == pytest.approx(0, abs=50.0)
    assert econ["breakeven_undiscounted"] < econ["breakeven_npv"]


def test_royalty_and_taxes_reduce_value():
    gross = we.evaluate(we.WellInputs(**{**PERMIAN, "royalty": 0.0, "production_tax": 0.0}))
    net = we.evaluate(we.WellInputs(**PERMIAN))
    assert net["npv"] < gross["npv"]


def test_well_shuts_in_when_cash_flow_turns_negative():
    econ = we.evaluate(we.WellInputs(**{**PERMIAN, "fixed_cost_month": 40_000.0, "years": 40}))
    life = econ["life_months"]
    assert 0 < life < 480
    assert np.all(econ["volumes"][life:] == 0)
    assert np.all(econ["net_cf"][:life] >= 0)


def test_uneconomic_well_reports_no_irr():
    econ = we.evaluate(we.WellInputs(**{**PERMIAN, "price": 25.0}))
    assert econ["irr"] is None
    assert econ["npv"] < 0


# ---------------------------------------------------------------------------
# Gas pricing
# ---------------------------------------------------------------------------

def test_gas_revenue_uses_henry_hub_not_oil_parity():
    rev = revenue_potential(1_000.0, 75.0, "gas", henry_hub_per_mmbtu=3.50)
    assert rev["revenue_usd_millions"] == pytest.approx(1_000 * 1_000 * 3.50 * MMBTU_PER_MCF / 1e6, abs=0.01)
    assert rev["revenue_usd_millions"] < 1_000 * 1_000 * 75 / 6 / 1e6 / 3  # old WTI/6 was 3x+ higher


def test_oil_revenue_is_unchanged():
    assert revenue_potential(1_000.0, 80.0, "oil")["revenue_usd_millions"] == 80.0


def test_price_text():
    assert price_assumption_text("gas", 75, 3.5) == "Henry Hub $3.50/MMBtu"
    assert price_assumption_text("oil", 75, 3.5) == "WTI $75.00/bbl"


# ---------------------------------------------------------------------------
# Forecast calibration
# ---------------------------------------------------------------------------

def _series(years: int = 12, growth: float = 0.0, noise: float = 0.0, seed: int = 1) -> pd.DataFrame:
    ds = pd.date_range("2013-01-01", periods=12 * years, freq="MS")
    rng = np.random.default_rng(seed)
    y = 1_000 * (1 + growth) ** (np.arange(len(ds)) / 12) * (1 + noise * rng.standard_normal(len(ds)))
    return pd.DataFrame({"ds": ds, "y": y})


def _flat_forecaster(train: pd.DataFrame, periods: int) -> np.ndarray:
    return np.full(periods, float(train["y"].iloc[-12:].mean()))


def test_conformal_quantile():
    assert cal.conformal_quantile(np.arange(1, 11) / 100) == pytest.approx(0.09)  # ceil(0.8*11)=9th
    assert cal.conformal_quantile(np.array([0.1, 0.2])) == pytest.approx(0.2)
    assert cal.conformal_quantile(np.array([])) is None


def test_backtest_measures_a_perfect_model_as_zero_error():
    df = _series()
    bt = cal.run_backtest(df, fit_predict=_flat_forecaster)
    assert bt["by_year"] and all(r["band_annual"] == pytest.approx(0, abs=1e-9) for r in bt["by_year"])


def _stale_forecaster(train: pd.DataFrame, periods: int) -> np.ndarray:
    return np.full(periods, float(train["y"].iloc[-24:-12].mean()))


def test_growing_series_widens_the_band_with_horizon():
    df = _series(growth=0.10)
    bt = cal.run_backtest(df, fit_predict=_flat_forecaster)
    bands = [r["band_annual"] for r in bt["by_year"]]
    assert bands == sorted(bands) and bands[0] > 0.05
    assert cal.baseline_verdict(bt, 3)["beats_baseline"] is False  # a tie is not a win


def test_model_worse_than_the_baseline_is_flagged():
    bt = cal.run_backtest(_series(growth=0.10), fit_predict=_stale_forecaster)
    verdict = cal.baseline_verdict(bt, 1)
    assert verdict["model_error_pct"] > verdict["flat_baseline_error_pct"]
    assert verdict["beats_baseline"] is False


def test_backtest_never_uses_data_after_the_origin():
    df = _series(noise=0.02)
    seen: list[pd.Timestamp] = []

    def spy(train, periods):
        seen.append(train["ds"].max())
        return _flat_forecaster(train, periods)

    bt = cal.run_backtest(df, fit_predict=spy)
    assert [ts.year for ts in seen] == bt["origins"]
    assert all(ts.month == 12 for ts in seen)


def test_monthly_band_is_applied_by_horizon_year():
    bt = {"by_year": [
        {"year": 1, "band_monthly": 0.10, "band_annual": 0.05},
        {"year": 2, "band_monthly": 0.20, "band_annual": 0.10},
    ]}
    ds = pd.date_range("2025-01-01", "2028-12-01", freq="MS")
    fc = pd.DataFrame({"ds": ds, "y_forecast": 100.0, "y_lower": 99.0, "y_upper": 101.0,
                       "is_forecast": ds > pd.Timestamp("2025-12-31")})
    out = cal.apply_monthly_band(fc, 2025, bt)
    assert out.loc[out["ds"] == "2025-06-01", "y_upper"].item() == 101.0  # history untouched
    assert out.loc[out["ds"] == "2026-12-01", "y_upper"].item() == pytest.approx(110.0)
    assert out.loc[out["ds"] == "2027-01-01", "y_upper"].item() == pytest.approx(120.0)
    assert out.loc[out["ds"] == "2028-01-01", "y_upper"].item() == pytest.approx(100 * (1 + 0.2 * np.sqrt(1.5)))
    band = cal.annual_band(1_000.0, 4, bt)
    assert band["extrapolated"] and band["upper"] == pytest.approx(1_000 * (1 + 0.10 * np.sqrt(2)), abs=0.01)


# ---------------------------------------------------------------------------
# Economics page renders for oil and gas
# ---------------------------------------------------------------------------

ECON_SCRIPT = """
from ui.economics import render_economics
render_economics("{basin}", "{fuel}", 75.0, henry_hub=3.5)
"""


@pytest.mark.parametrize("basin,fuel", [("Permian", "oil"), ("Haynesville", "gas"), ("Bakken", "gas")])
def test_economics_page_renders(basin, fuel):
    app = AppTest.from_string(ECON_SCRIPT.format(basin=basin, fuel=fuel), default_timeout=60).run()
    assert not app.exception
    labels = [m.label for m in app.metric]
    assert "IRR" in labels and any(lbl.startswith("Breakeven @") for lbl in labels)
    if fuel == "gas":
        assert any("$/Mcf" in n.label for n in app.number_input)


# ---------------------------------------------------------------------------
# Basin data: EIA STEO regions
# ---------------------------------------------------------------------------

def test_steo_rates_become_monthly_volumes_and_forecast_months_are_dropped():
    from data.eia import steo_rows_to_monthly

    rows = [
        {"period": "2026-02", "value": "6.5"},   # million b/d, 28 days
        {"period": "2026-03", "value": 6.6},
        {"period": "2026-09", "value": 6.9},     # after the last reported month
    ]
    df = steo_rows_to_monthly(rows, pd.Timestamp("2026-08-01"))
    assert df["ds"].dt.strftime("%Y-%m").tolist() == ["2026-02", "2026-03"]
    assert df["y"].tolist() == pytest.approx([6.5 * 1_000 * 28, 6.6 * 1_000 * 31])


@pytest.mark.parametrize("fuel,series", [("oil", "COPRPM"), ("gas", "NGMPPM")])
def test_region_basins_read_their_steo_series(fuel, series):
    from unittest import mock

    from data import eia

    client = eia.EIAClient()
    calls = []

    def fake_get(route, params):
        calls.append((route, dict(params)))
        if route == "steo/data":
            return {"response": {"data": [{"period": "2026-01", "value": 6.0},
                                          {"period": "2026-12", "value": 7.0}]}}
        return {"response": {"data": [{"period": "2026-08"}]}}

    with mock.patch.object(client, "_get", side_effect=fake_get):
        fetch = client.fetch_oil_production_by_basin if fuel == "oil" else client.fetch_gas_production_by_basin
        df = fetch("Permian")
    steo = [p for r, p in calls if r == "steo/data"]
    assert steo and steo[0]["facets[seriesId][]"] == series
    assert df["ds"].max() == pd.Timestamp("2026-01-01")  # 2026-12 is forecast
    assert set(df["basin"]) == {"Permian"} and set(df["fuel_type"]) == {fuel}


def test_regions_do_not_share_series_and_anadarko_is_labelled_as_state_data():
    from data.eia import BASIN_SOURCE, BASINS, STEO_REGIONS, basin_scope, is_state_proxy

    assert len(set(STEO_REGIONS.values())) == len(STEO_REGIONS)
    assert set(BASINS) == set(STEO_REGIONS) | {"Anadarko"}
    assert is_state_proxy("Anadarko") and "Oklahoma" in basin_scope("Anadarko")
    assert BASIN_SOURCE["Anadarko"] == "OK state data"
    assert all(BASIN_SOURCE[b] == "EIA region" for b in STEO_REGIONS)


def test_every_basin_has_map_and_economics_entries():
    from data.eia import BASINS
    from ui.economics import GAS_DEFAULTS, OIL_DEFAULTS
    from ui.map import BASIN_ALL_STATES, BASIN_COORDS, BASIN_PRIMARY_STATE

    for table in (OIL_DEFAULTS, GAS_DEFAULTS, BASIN_COORDS, BASIN_PRIMARY_STATE, BASIN_ALL_STATES):
        assert set(BASINS) <= set(table)


def test_old_state_proxy_cache_files_are_not_reused():
    from data.loader import production_path

    assert production_path("oil", "Permian").name == "eia_oil_permian_v2.parquet"
