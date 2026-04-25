"""Well Economics Calculator tab — Arps decline curve + DCF model.

All calculations run client-side in Python/NumPy and update instantly when any
input changes (Streamlit re-executes on every widget interaction).

Model
-----
Hyperbolic Arps decline (reduces to exponential when b -> 0):
    q(t) = qi / (1 + b * Di * t)^(1/b)

where:
    qi  = initial monthly production (bbl/month)
    Di  = nominal initial monthly decline rate
    b   = hyperbolic exponent (0 = exponential, 1 = harmonic, 1.5 typical shale)
    t   = time in months from first production

Financial model:
    Revenue(t)    = q(t) * oil_price
    LOE(t)        = q(t) * loe_per_bbl
    Net CF(t)     = Revenue(t) - LOE(t)
    NPV@r         = -CAPEX + sum[ Net CF(t) / (1+r_monthly)^t ]  for t=1..N
    IRR           = r_annual such that NPV = 0  (bisection method)
    Payback       = first month where cumulative Net CF >= CAPEX
    EUR           = sum of q(t) over full forecast period
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# ---------------------------------------------------------------------------
# Per-basin default inputs (rough industry benchmarks for illustration)
# ---------------------------------------------------------------------------

BASIN_DEFAULTS: dict[str, dict[str, Any]] = {
    "Permian":     {"ip": 700, "di_pct": 70, "b": 1.3, "dc_cost": 9.5},
    "Bakken":      {"ip": 550, "di_pct": 65, "b": 1.2, "dc_cost": 10.0},
    "Eagle Ford":  {"ip": 500, "di_pct": 72, "b": 1.2, "dc_cost": 8.5},
    "Marcellus":   {"ip": 400, "di_pct": 60, "b": 1.1, "dc_cost": 7.0},
    "Haynesville": {"ip": 350, "di_pct": 65, "b": 1.0, "dc_cost": 8.0},
    "Anadarko":    {"ip": 300, "di_pct": 60, "b": 1.1, "dc_cost": 7.5},
    "Appalachian": {"ip": 350, "di_pct": 55, "b": 1.0, "dc_cost": 6.5},
}


# ---------------------------------------------------------------------------
# Core calculations
# ---------------------------------------------------------------------------

def _arps_monthly(
    ip_bopd: float,
    di_annual: float,
    b: float,
    months: int,
) -> np.ndarray:
    """Monthly production (bbl/month) via hyperbolic Arps.

    Parameters
    ----------
    ip_bopd   : initial rate (BOPD)
    di_annual : initial nominal annual decline rate (fraction, e.g. 0.70)
    b         : hyperbolic exponent; b=0 collapses to exponential
    months    : number of months to compute
    """
    qi = ip_bopd * 30.4375  # avg bbl/month at IP
    t = np.arange(months, dtype=float)

    if b < 0.01:
        # Exponential: convert annual rate to monthly
        di_m = -np.log(1.0 - min(di_annual, 0.999)) / 12.0
        return qi * np.exp(-di_m * t)

    # Hyperbolic: convert to equivalent monthly nominal decline
    di_m = (1.0 - (1.0 - min(di_annual, 0.999)) ** (1.0 / 12.0))
    return qi / (1.0 + b * di_m * t) ** (1.0 / b)


def _npv(cash_flows: np.ndarray, capex: float, r_annual: float) -> float:
    """NPV given monthly net cash flows and up-front CAPEX."""
    r_m = (1.0 + r_annual) ** (1.0 / 12.0) - 1.0
    t = np.arange(1, len(cash_flows) + 1, dtype=float)
    return float(-capex + np.sum(cash_flows / (1.0 + r_m) ** t))


def _irr_annual(cash_flows: np.ndarray, capex: float) -> float | None:
    """Annual IRR via bisection; returns None if no positive root in [0, 10]."""
    if _npv(cash_flows, capex, 0.0) <= 0:
        return None  # not even profitable at 0% — no positive IRR

    lo, hi = 0.0, 10.0  # search 0% to 1000% annual
    for _ in range(120):
        mid = (lo + hi) / 2.0
        if _npv(cash_flows, capex, mid) > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-7:
            break

    result = (lo + hi) / 2.0
    return result if result < 9.99 else None


def _calc_economics(
    ip_bopd: float,
    di_annual: float,
    b: float,
    dc_cost_mm: float,
    loe_per_bbl: float,
    oil_price: float,
    discount_rate: float,
    years: int,
) -> dict[str, Any]:
    """Full economics model. Returns a dict of arrays and summary metrics."""
    months = years * 12
    production = _arps_monthly(ip_bopd, di_annual, b, months)

    revenue = production * oil_price
    loe = production * loe_per_bbl
    net_cf = revenue - loe
    capex = dc_cost_mm * 1_000_000.0

    cumulative_cf = np.cumsum(net_cf) - capex

    # Payback period
    positive = np.where(cumulative_cf >= 0)[0]
    payback_months: int | None = int(positive[0]) + 1 if len(positive) > 0 else None

    eur_bbl = float(np.sum(production))

    npv_val = _npv(net_cf, capex, discount_rate)
    irr_val = _irr_annual(net_cf, capex)

    # Breakeven oil price ($/bbl): price where NPV = 0
    # Approx: (CAPEX/EUR) + LOE
    breakeven = (capex / eur_bbl + loe_per_bbl) if eur_bbl > 0 else None

    return {
        "production": production,         # bbl/month array
        "net_cf": net_cf,                 # $/month array
        "cumulative_cf": cumulative_cf,   # $ cumulative (after capex)
        "eur_mbbls": eur_bbl / 1_000.0,
        "npv_mm": npv_val / 1_000_000.0,
        "irr_pct": irr_val * 100 if irr_val is not None else None,
        "payback_months": payback_months,
        "breakeven_price": round(breakeven, 1) if breakeven else None,
        "months": months,
    }


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _build_chart(econ: dict[str, Any], years: int) -> go.Figure:
    months = econ["months"]
    month_labels = pd.date_range("2025-01", periods=months, freq="MS")
    prod_mbbls = econ["production"] / 1_000.0
    cum_cf_mm = econ["cumulative_cf"] / 1_000_000.0

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=("Production Decline Curve", "Cumulative Net Cash Flow"),
        horizontal_spacing=0.10,
    )

    # --- Production decline curve ---
    fig.add_trace(
        go.Scatter(
            x=month_labels,
            y=prod_mbbls,
            name="Monthly Production",
            fill="tozeroy",
            fillcolor="rgba(74,144,213,0.18)",
            line=dict(color="#4A90D9", width=2),
            hovertemplate="%{y:.2f} Mbbls<extra></extra>",
        ),
        row=1, col=1,
    )

    # --- Cumulative cash flow ---
    fig.add_trace(
        go.Scatter(
            x=month_labels,
            y=cum_cf_mm,
            name="Cumul. Net CF",
            line=dict(color="#FF6B35", width=2),
            hovertemplate="$%{y:.1f}MM<extra></extra>",
        ),
        row=1, col=2,
    )

    # Zero line
    fig.add_hline(
        y=0,
        line_dash="dash",
        line_color="rgba(255,255,255,0.35)",
        row=1, col=2,
    )

    # Payback star marker
    pb = econ["payback_months"]
    if pb and pb <= months:
        pb_x = month_labels[pb - 1]
        pb_y = float(cum_cf_mm[pb - 1])
        fig.add_trace(
            go.Scatter(
                x=[pb_x],
                y=[pb_y],
                mode="markers",
                name=f"Payback ({pb} mo)",
                marker=dict(color="gold", size=12, symbol="star"),
                hovertemplate=f"Payback: {pb} months ({pb/12:.1f} yr)<extra></extra>",
            ),
            row=1, col=2,
        )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0E1117",
        plot_bgcolor="#1A1D24",
        height=360,
        margin=dict(t=45, b=40, l=60, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.08, x=0),
        showlegend=True,
        font=dict(color="white"),
    )
    fig.update_yaxes(title_text="Mbbls / month", row=1, col=1)
    fig.update_yaxes(title_text="$MM (cumulative)", row=1, col=2)
    fig.update_xaxes(showgrid=False)

    return fig


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render_economics(basin: str, fuel_type: str, wti: float) -> None:
    """Render the well economics calculator tab."""
    st.subheader("Well Economics Calculator")
    st.caption(
        "Hyperbolic Arps decline curve + DCF model for a single horizontal well. "
        "Inputs pre-filled with basin-level benchmarks. All calculations update instantly."
    )

    defaults = BASIN_DEFAULTS.get(basin, BASIN_DEFAULTS["Permian"])

    col_in, col_out = st.columns([1, 1], gap="large")

    # ---- Inputs ----
    with col_in:
        st.markdown(f"#### Inputs — {basin} defaults")

        ip_bopd: float = st.number_input(
            "Initial production rate (BOPD)",
            min_value=50, max_value=5_000,
            value=int(defaults["ip"]), step=50,
            help="Barrels of oil per day at first production",
        )
        di_pct: int = st.slider(
            "Initial decline rate (% / year)",
            min_value=10, max_value=95,
            value=int(defaults["di_pct"]), step=5,
            help="Nominal initial annual decline rate",
        )
        b_factor: float = st.slider(
            "Hyperbolic exponent (b)",
            min_value=0.0, max_value=2.0,
            value=float(defaults["b"]), step=0.05,
            help="b=0 = exponential, b=1 = harmonic, b=1.2-1.5 typical shale",
        )
        dc_cost: float = st.number_input(
            "D&C cost ($MM)",
            min_value=1.0, max_value=40.0,
            value=float(defaults["dc_cost"]), step=0.5,
            help="Drilling and completion capital expenditure",
        )
        loe: float = st.number_input(
            "Lease operating expense ($/bbl)",
            min_value=0.0, max_value=60.0,
            value=12.0, step=1.0,
            help="Variable operating cost per barrel produced",
        )
        oil_px: float = st.number_input(
            "Oil price assumption ($/bbl)",
            min_value=20.0, max_value=250.0,
            value=float(wti), step=5.0,
            help="Flat price deck for entire forecast horizon",
        )

        col_y, col_r = st.columns(2)
        years: int = col_y.slider("Forecast years", 5, 30, 20)
        disc_pct: int = col_r.slider("Discount rate (%)", 5, 30, 10)

    # ---- Calculations ----
    econ = _calc_economics(
        ip_bopd=float(ip_bopd),
        di_annual=di_pct / 100.0,
        b=float(b_factor),
        dc_cost_mm=float(dc_cost),
        loe_per_bbl=float(loe),
        oil_price=float(oil_px),
        discount_rate=disc_pct / 100.0,
        years=years,
    )

    # ---- Output metrics ----
    with col_out:
        st.markdown("#### Return Metrics")

        m1, m2 = st.columns(2)
        m1.metric(
            "EUR",
            f"{econ['eur_mbbls']:,.1f} Mbbls",
            help="Estimated Ultimate Recovery over the forecast period",
        )
        npv_sign = "+" if econ["npv_mm"] >= 0 else ""
        m2.metric(
            f"NPV @ {disc_pct}%",
            f"${npv_sign}{econ['npv_mm']:,.1f}MM",
            delta="positive" if econ["npv_mm"] >= 0 else "negative",
            delta_color="normal",
        )

        m3, m4 = st.columns(2)
        irr_str = (
            f"{econ['irr_pct']:.1f}%"
            if econ["irr_pct"] is not None
            else "< 0%"
        )
        m3.metric("IRR", irr_str, help="Internal Rate of Return (annualised)")

        pb = econ["payback_months"]
        pb_str = (
            f"{pb} mo  ({pb / 12:.1f} yr)"
            if pb
            else "No payback"
        )
        m4.metric("Payback", pb_str)

        st.markdown("---")
        st.markdown("**Breakeven & efficiency**")
        be = econ["breakeven_price"]
        eur_per_mm = econ["eur_mbbls"] / dc_cost if dc_cost > 0 else 0

        col_be, col_eff = st.columns(2)
        col_be.metric(
            "Breakeven price",
            f"${be:.0f}/bbl" if be else "N/A",
            help="Oil price where undiscounted revenue = LOE + D&C",
        )
        col_eff.metric(
            "EUR / D&C $",
            f"{eur_per_mm:.0f} Mbbls/$MM",
            help="Capital efficiency measure",
        )

        if fuel_type == "gas":
            st.info(
                "Gas wells: inputs use oil-equivalent pricing. "
                "Actual gas economics require Henry Hub price and BTU conversion.",
                icon="ℹ️",
            )

    st.divider()

    # ---- Charts ----
    fig = _build_chart(econ, years)
    st.plotly_chart(fig, use_container_width=True)

    # ---- Monthly detail table ----
    with st.expander("Monthly production & cash flow — first 36 months"):
        month_labels = pd.date_range("2025-01", periods=min(36, econ["months"]), freq="MS")
        df_tbl = pd.DataFrame({
            "Period":           [d.strftime("%Y-%m") for d in month_labels],
            "Production (bbl)": [f"{v:,.0f}" for v in econ["production"][:36]],
            "Revenue ($K)":     [f"{v/1_000:,.1f}" for v in econ["production"][:36] * oil_px],
            "LOE ($K)":         [f"{v/1_000:,.1f}" for v in econ["production"][:36] * loe],
            "Net CF ($K)":      [f"{v/1_000:,.1f}" for v in econ["net_cf"][:36]],
            "Cumul. CF ($K)":   [f"{v/1_000:,.1f}" for v in econ["cumulative_cf"][:36]],
        })
        st.dataframe(df_tbl, use_container_width=True, hide_index=True)
