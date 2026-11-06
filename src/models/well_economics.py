"""Single-well decline curve and discounted cash flow.

Pure NumPy, no Streamlit, so the math can be tested on its own.

Decline (modified hyperbolic Arps)
----------------------------------
The decline input is the *effective* first-year decline De: the share of the
initial rate lost over the first year, which is how industry decline figures
are usually quoted. It converts to the nominal rate the hyperbolic equation
needs (per year, then divided by 12 for months):

    hyperbolic (b > 0):  Di = ((1 - De)^(-b) - 1) / b
    exponential (b = 0): Di = -ln(1 - De)

    q(t) = qi / (1 + b * Di * t)^(1/b)                    while D(t) > Dlim
    q(t) = q(t*) * exp(-Dlim * (t - t*))                  afterwards

D(t) = Di / (1 + b * Di * t) is the instantaneous decline. Once it falls to the
terminal rate Dlim = -ln(1 - Dmin), the curve switches to exponential, so a
b > 1 curve no longer produces an unbounded volume. The switch time is
t* = (Di / Dlim - 1) / (b * Di).

Each month's volume is the integral of q(t) over the month, taken from the
closed-form cumulative curve, rather than the rate at the start of the month.

Cash flow
---------
    net revenue(m) = volume(m) * price * (1 - royalty) * (1 - production tax)
    opex(m)        = volume(m) * variable cost + fixed cost per month
    NCF(m)         = net revenue(m) - opex(m)

The well is shut in from the first month NCF turns negative (economic limit).
CAPEX is spent at month 0 and cash flows are discounted at the end of each
month with r_m = (1 + r)^(1/12) - 1.

Not modelled: NGL or secondary-product revenue, price differentials, workovers,
abandonment cost, working interest below 100%, and income tax.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

DAYS_PER_MONTH = 30.4375
_EPS = 1e-6


@dataclass(frozen=True)
class WellInputs:
    ip_per_day: float          # initial rate, bbl/d or Mcf/d
    de_first_year: float       # effective first-year decline, fraction (0.70 = 70%)
    b: float                   # hyperbolic exponent
    d_min: float               # terminal effective annual decline, fraction
    capex: float               # drilling and completion, $
    price: float               # realised price per unit, $/bbl or $/Mcf
    royalty: float             # fraction of gross revenue
    production_tax: float      # fraction of revenue after royalty
    variable_cost: float       # $ per unit produced
    fixed_cost_month: float    # $ per producing month
    discount_rate: float       # annual, fraction
    years: int


def nominal_decline(de_first_year: float, b: float) -> float:
    """Nominal initial annual decline from the effective first-year decline."""
    de = min(max(de_first_year, 0.0), 0.999)
    if b < _EPS:
        return -np.log(1.0 - de)
    return ((1.0 - de) ** (-b) - 1.0) / b


def _cum_arps(t: np.ndarray, qi: float, di: float, b: float) -> np.ndarray:
    """Cumulative volume of a pure Arps curve from 0 to t (t, di in months)."""
    if di <= 0:
        return qi * t
    if b < _EPS:
        return qi / di * (1.0 - np.exp(-di * t))
    if abs(b - 1.0) < _EPS:
        return qi / di * np.log1p(di * t)
    return qi / ((1.0 - b) * di) * (1.0 - (1.0 + b * di * t) ** ((b - 1.0) / b))


def _rate_arps(t: float, qi: float, di: float, b: float) -> float:
    if b < _EPS:
        return qi * np.exp(-di * t)
    return qi / (1.0 + b * di * t) ** (1.0 / b)


def monthly_volumes(
    ip_per_day: float, de_first_year: float, b: float, d_min: float, months: int
) -> np.ndarray:
    """Volume produced in each month 1..months, in units (bbl or Mcf)."""
    qi = ip_per_day * DAYS_PER_MONTH                 # units per month at t = 0
    di = nominal_decline(de_first_year, b) / 12.0    # nominal, per month
    d_lim = -np.log(1.0 - min(max(d_min, 0.0), 0.999)) / 12.0

    edges = np.arange(months + 1, dtype=float)
    if b < _EPS or d_lim <= 0 or di <= d_lim:
        cum = _cum_arps(edges, qi, di, b)
    else:
        t_switch = (di / d_lim - 1.0) / (b * di)
        q_switch = _rate_arps(t_switch, qi, di, b)
        cum_switch = float(_cum_arps(np.array([t_switch]), qi, di, b)[0])
        before = _cum_arps(np.minimum(edges, t_switch), qi, di, b)
        after = q_switch / d_lim * (1.0 - np.exp(-d_lim * np.maximum(edges - t_switch, 0.0)))
        cum = np.where(edges <= t_switch, before, cum_switch + after)
    return np.diff(cum)


def _discount_factors(months: int, r_annual: float) -> np.ndarray:
    r_m = (1.0 + r_annual) ** (1.0 / 12.0) - 1.0
    return (1.0 + r_m) ** -np.arange(1, months + 1, dtype=float)


def _cash_flows(inp: WellInputs, volumes: np.ndarray, price: float) -> tuple[np.ndarray, np.ndarray]:
    """Monthly NCF and volumes after applying the economic limit."""
    net_price = price * (1.0 - inp.royalty) * (1.0 - inp.production_tax)
    ncf = volumes * (net_price - inp.variable_cost) - inp.fixed_cost_month
    negative = np.nonzero(ncf < 0)[0]
    if negative.size:
        life = int(negative[0])
        ncf = ncf.copy()
        volumes = volumes.copy()
        ncf[life:] = 0.0
        volumes[life:] = 0.0
    return ncf, volumes


def npv(ncf: np.ndarray, capex: float, r_annual: float) -> float:
    return float(-capex + np.sum(ncf * _discount_factors(len(ncf), r_annual)))


def irr(ncf: np.ndarray, capex: float, hi: float = 10.0) -> float | None:
    """Annual IRR by bisection. None when the well never pays back (no positive IRR).

    Returns ``hi`` when the IRR is above the search ceiling (1,000%).
    """
    if npv(ncf, capex, 0.0) <= 0:
        return None
    if npv(ncf, capex, hi) > 0:
        return hi
    lo = 0.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if npv(ncf, capex, mid) > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-9:
            break
    return (lo + hi) / 2.0


def breakeven_price(inp: WellInputs, volumes: np.ndarray, r_annual: float) -> float | None:
    """Price at which NPV at ``r_annual`` is zero, with the economic limit applied."""
    def f(p: float) -> float:
        ncf, _ = _cash_flows(inp, volumes, p)
        return npv(ncf, inp.capex, r_annual)

    lo, hi = 0.0, 1_000.0
    if f(hi) < 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6:
            break
    return hi


def evaluate(inp: WellInputs) -> dict[str, Any]:
    """Run the full model. Arrays are per month; money in $, volumes in units."""
    months = inp.years * 12
    raw = monthly_volumes(inp.ip_per_day, inp.de_first_year, inp.b, inp.d_min, months)
    ncf, volumes = _cash_flows(inp, raw, inp.price)
    gross = volumes * inp.price
    cumulative = np.cumsum(ncf) - inp.capex

    paid = np.nonzero(cumulative >= 0)[0]
    producing = np.nonzero(volumes > 0)[0]
    irr_val = irr(ncf, inp.capex)

    return {
        "volumes": volumes,
        "gross_revenue": gross,
        "net_cf": ncf,
        "cumulative_cf": cumulative,
        "months": months,
        "eur": float(volumes.sum()),
        "npv": npv(ncf, inp.capex, inp.discount_rate),
        "irr": irr_val,
        "irr_capped": irr_val is not None and irr_val >= 10.0,
        "payback_months": int(paid[0]) + 1 if paid.size else None,
        "life_months": int(producing[-1]) + 1 if producing.size else 0,
        "breakeven_npv": breakeven_price(inp, raw, inp.discount_rate),
        "breakeven_undiscounted": breakeven_price(inp, raw, 0.0),
        "nominal_decline": nominal_decline(inp.de_first_year, inp.b),
    }
