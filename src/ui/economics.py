"""Well Economics page: decline curve and discounted cash flow for one well.

The math lives in models.well_economics. This module only draws inputs,
results and charts. Oil wells are modelled in barrels at an oil price; gas
wells in Mcf at a gas price taken from the Henry Hub assumption.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from kpi.metrics import DEFAULT_HENRY_HUB, MMBTU_PER_MCF
from models.well_economics import WellInputs, evaluate
from ui.theme import ACCENT, AMBER, GAS, MUTED, style_figure, with_alpha

# ---------------------------------------------------------------------------
# Illustrative per-basin defaults. They are round numbers for a typical modern
# horizontal well, not sourced type curves; edit them to match a real case.
# ip: initial rate per day; de_pct: effective first-year decline; dc_cost: $MM
# ---------------------------------------------------------------------------

OIL_DEFAULTS: dict[str, dict[str, Any]] = {
    "Permian":     {"ip": 700, "de_pct": 70, "b": 1.3, "dc_cost": 9.5},
    "Bakken":      {"ip": 550, "de_pct": 65, "b": 1.2, "dc_cost": 10.0},
    "Eagle Ford":  {"ip": 500, "de_pct": 72, "b": 1.2, "dc_cost": 8.5},
    "Marcellus":   {"ip": 400, "de_pct": 60, "b": 1.1, "dc_cost": 7.0},
    "Haynesville": {"ip": 350, "de_pct": 65, "b": 1.0, "dc_cost": 8.0},
    "Anadarko":    {"ip": 300, "de_pct": 60, "b": 1.1, "dc_cost": 7.5},
    "Appalachian": {"ip": 350, "de_pct": 55, "b": 1.0, "dc_cost": 6.5},
}

GAS_DEFAULTS: dict[str, dict[str, Any]] = {
    "Permian":     {"ip": 2_000,  "de_pct": 70, "b": 1.2, "dc_cost": 9.5},
    "Bakken":      {"ip": 1_000,  "de_pct": 65, "b": 1.1, "dc_cost": 10.0},
    "Eagle Ford":  {"ip": 3_000,  "de_pct": 70, "b": 1.1, "dc_cost": 8.5},
    "Marcellus":   {"ip": 12_000, "de_pct": 60, "b": 1.0, "dc_cost": 9.0},
    "Haynesville": {"ip": 20_000, "de_pct": 75, "b": 0.9, "dc_cost": 14.0},
    "Anadarko":    {"ip": 4_000,  "de_pct": 65, "b": 1.0, "dc_cost": 8.0},
    "Appalachian": {"ip": 10_000, "de_pct": 60, "b": 1.0, "dc_cost": 8.5},
}

# Basins where wells are drilled mainly for oil, so a gas-only case is a stretch.
_OIL_WEIGHTED = {"Permian", "Bakken", "Eagle Ford", "Anadarko"}

_UNITS = {
    "oil": {"day": "bbl/d", "unit": "bbl", "k": "Mbbl", "price": "$/bbl", "var_cost": 12.0, "tax": 4.6},
    "gas": {"day": "Mcf/d", "unit": "Mcf", "k": "MMcf", "price": "$/Mcf", "var_cost": 0.50, "tax": 5.0},
}


def _money(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x) / 1e6:,.2f}MM"


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _build_chart(econ: dict[str, Any], k_unit: str) -> go.Figure:
    months = econ["months"]
    month_index = list(range(1, months + 1))
    vol_k = econ["volumes"] / 1_000.0
    cum_cf_mm = econ["cumulative_cf"] / 1_000_000.0

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=("Monthly production", "Cumulative net cash flow"),
        horizontal_spacing=0.10,
    )
    fig.add_trace(
        go.Scatter(
            x=month_index, y=vol_k, name="Monthly production",
            fill="tozeroy", fillcolor=with_alpha(ACCENT, 0.16),
            line=dict(color=ACCENT, width=2),
            hovertemplate=f"Month %{{x}}: %{{y:.2f}} {k_unit}<extra></extra>",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=month_index, y=cum_cf_mm, name="Cumulative net cash flow",
            line=dict(color=GAS, width=2),
            hovertemplate="Month %{x}: $%{y:.2f}MM<extra></extra>",
        ),
        row=1, col=2,
    )
    fig.add_hline(y=0, line_dash="dash", line_color=MUTED, row=1, col=2)

    pb = econ["payback_months"]
    if pb and pb <= months:
        fig.add_trace(
            go.Scatter(
                x=[pb], y=[float(cum_cf_mm[pb - 1])], mode="markers",
                name=f"Payback ({pb} mo)",
                marker=dict(color=AMBER, size=13, symbol="star"),
                hovertemplate=f"Payback: month {pb} ({pb / 12:.1f} yr)<extra></extra>",
            ),
            row=1, col=2,
        )

    style_figure(fig, height=380, margin=dict(t=60, b=40, l=60, r=20))
    fig.update_layout(legend=dict(y=1.12), showlegend=True)
    fig.update_yaxes(title_text=f"{k_unit} / month", row=1, col=1)
    fig.update_yaxes(title_text="$MM (cumulative)", row=1, col=2)
    fig.update_xaxes(title_text="Months on production", showgrid=False)
    return fig


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render_economics(
    basin: str, fuel_type: str, wti: float, henry_hub: float = DEFAULT_HENRY_HUB
) -> None:
    """Render the well economics calculator."""
    gas = fuel_type == "gas"
    u = _UNITS["gas" if gas else "oil"]
    table = GAS_DEFAULTS if gas else OIL_DEFAULTS
    defaults = table.get(basin, table["Permian"])
    default_price = round(henry_hub * MMBTU_PER_MCF, 2) if gas else float(wti)

    if gas and basin in _OIL_WEIGHTED:
        st.info(
            f"{basin} wells are mostly drilled for oil. This gas case charges the whole well "
            "cost to gas and leaves out the oil, so it understates the well. Switch Fuel to "
            "Oil for the main case.",
            icon=":material/info:",
        )

    col_in, col_out = st.columns([1, 1], gap="large")

    with col_in:
        st.markdown(f"#### Inputs · {basin} {fuel_type} defaults")
        ip = st.number_input(
            f"Initial rate ({u['day']})",
            min_value=50, max_value=60_000, value=int(defaults["ip"]), step=50,
            help="Average daily rate at first production.",
        )
        de_pct = st.slider(
            "First-year decline (%)",
            min_value=10, max_value=95, value=int(defaults["de_pct"]), step=1,
            help="Effective decline: the share of the initial rate lost over the first year. "
            "A 70% decline means the rate after 12 months is 30% of the initial rate.",
        )
        b_factor = st.slider(
            "Hyperbolic exponent (b)",
            min_value=0.0, max_value=2.0, value=float(defaults["b"]), step=0.05,
            help="0 is exponential, 1 is harmonic; 0.9 to 1.5 is common for shale wells.",
        )
        d_min_pct = st.slider(
            "Terminal decline (% per year)",
            min_value=2, max_value=20, value=8, step=1,
            help="Once the decline slows to this rate, the curve turns exponential. Without it, "
            "a b above 1 keeps producing without limit.",
        )
        dc_cost = st.number_input(
            "Drilling and completion cost ($MM)",
            min_value=1.0, max_value=40.0, value=float(defaults["dc_cost"]), step=0.5,
        )
        price = st.number_input(
            f"Realised price ({u['price']})",
            min_value=0.5 if gas else 20.0, max_value=30.0 if gas else 250.0,
            value=default_price, step=0.1 if gas else 1.0,
            help=(
                f"Defaults to Henry Hub ${henry_hub:.2f}/MMBtu × {MMBTU_PER_MCF} MMBtu/Mcf. "
                "Local prices can sit well below Henry Hub."
                if gas else "Defaults to the WTI assumption. Wellhead prices usually sit a few "
                "dollars below WTI."
            ),
        )
        c_roy, c_tax = st.columns(2)
        royalty_pct = c_roy.number_input(
            "Royalty (%)", min_value=0.0, max_value=40.0, value=20.0, step=0.5,
            help="Share of gross revenue paid to mineral owners.",
        )
        tax_pct = c_tax.number_input(
            "Production taxes (%)", min_value=0.0, max_value=15.0, value=u["tax"], step=0.1,
            help="Severance and ad valorem taxes on revenue after royalty.",
        )
        c_var, c_fix = st.columns(2)
        var_cost = c_var.number_input(
            f"Operating cost ({u['price']})", min_value=0.0, max_value=60.0,
            value=u["var_cost"], step=0.05 if gas else 1.0,
            help="Variable lease operating, gathering and transport cost per unit.",
        )
        fixed_k = c_fix.number_input(
            "Fixed cost ($K/month)", min_value=0.0, max_value=50.0, value=3.0, step=0.5,
            help="Fixed operating cost per producing month. The well is shut in once monthly "
            "cash flow turns negative.",
        )
        col_y, col_r = st.columns(2)
        years = col_y.slider("Forecast years", 5, 40, 20)
        disc_pct = col_r.slider("Discount rate (%)", 5, 30, 10)

    econ = evaluate(WellInputs(
        ip_per_day=float(ip),
        de_first_year=de_pct / 100.0,
        b=float(b_factor),
        d_min=d_min_pct / 100.0,
        capex=float(dc_cost) * 1e6,
        price=float(price),
        royalty=royalty_pct / 100.0,
        production_tax=tax_pct / 100.0,
        variable_cost=float(var_cost),
        fixed_cost_month=float(fixed_k) * 1_000.0,
        discount_rate=disc_pct / 100.0,
        years=int(years),
    ))

    with col_out:
        st.markdown("#### Returns")
        st.caption(
            "Inputs are illustrative round numbers, not a sourced type curve. "
            "Use them to compare cases, not as a valuation."
        )
        m1, m2 = st.columns(2)
        m1.metric(
            f"EUR ({years} yr)", f"{econ['eur'] / 1_000:,.1f} {u['k']}",
            help="Volume produced over the forecast period, up to the economic limit.",
        )
        m2.metric(
            f"NPV @ {disc_pct}%", _money(econ["npv"]),
            help="After royalty, production taxes and operating costs; before income tax.",
        )

        m3, m4 = st.columns(2)
        irr_val = econ["irr"]
        irr_str = (
            "No positive IRR" if irr_val is None
            else "Above 1,000%" if econ["irr_capped"]
            else f"{irr_val * 100:.1f}%"
        )
        m3.metric("IRR", irr_str, help="Annual internal rate of return.")
        pb = econ["payback_months"]
        m4.metric("Payback", f"{pb} mo ({pb / 12:.1f} yr)" if pb else "No payback")

        st.markdown("---")
        b1, b2 = st.columns(2)
        be_npv = econ["breakeven_npv"]
        be_raw = econ["breakeven_undiscounted"]
        fmt = (lambda p: f"${p:,.2f}/Mcf") if gas else (lambda p: f"${p:,.1f}/bbl")
        b1.metric(
            f"Breakeven @ {disc_pct}%", fmt(be_npv) if be_npv is not None else "N/A",
            help=f"Price at which NPV at {disc_pct}% is zero, with the same costs and taxes.",
        )
        b2.metric(
            "Breakeven, undiscounted", fmt(be_raw) if be_raw is not None else "N/A",
            help="Price at which total cash flow just repays the well cost, ignoring timing.",
        )
        l1, l2 = st.columns(2)
        life = econ["life_months"]
        l1.metric(
            "Producing life",
            f"{life / 12:.1f} yr" + (" (to horizon)" if life >= econ["months"] else ""),
            help="Months until cash flow turns negative, capped at the forecast period.",
        )
        l2.metric(
            f"{u['k']} per $MM", f"{econ['eur'] / 1_000 / dc_cost:,.1f}",
            help="Capital efficiency: EUR per million dollars of well cost.",
        )
        st.caption(
            f"Not modelled: {'condensate and NGL' if gas else 'gas and NGL'} revenue, price "
            "differentials, workovers, abandonment cost and income tax."
        )

    st.divider()
    st.plotly_chart(_build_chart(econ, u["k"]), use_container_width=True)

    with st.expander("Monthly production and cash flow, first 36 months"):
        n = min(36, econ["months"])
        st.dataframe(
            pd.DataFrame({
                "Month": range(1, n + 1),
                f"Production ({u['unit']})": [f"{v:,.0f}" for v in econ["volumes"][:n]],
                "Gross revenue ($K)": [f"{v / 1_000:,.1f}" for v in econ["gross_revenue"][:n]],
                "Net cash flow ($K)": [f"{v / 1_000:,.1f}" for v in econ["net_cf"][:n]],
                "Cumulative ($K)": [f"{v / 1_000:,.1f}" for v in econ["cumulative_cf"][:n]],
            }),
            use_container_width=True, hide_index=True,
        )
