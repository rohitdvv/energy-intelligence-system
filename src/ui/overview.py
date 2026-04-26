"""Overview tab: KPI metric cards, basin comparison table, RPI bar chart."""
from __future__ import annotations

from datetime import datetime
from typing import Any

_NOW_YEAR = datetime.now().year

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from agents.tools import execute_tool
from data.loader import load_production_no_cache
from kpi.metrics import basin_kpi_summary
from models.forecaster import forecast_basin as _fit


# ------------------------------------------------------------------
# Cached data fetchers
# ------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def _kpi_for_basin(
    basin: str, fuel_type: str, target_year: int, wti: float
) -> dict[str, Any] | None:
    df = load_production_no_cache(fuel_type=fuel_type, live_fetch=True)
    bdf = df[df["basin"] == basin][["ds", "y"]].dropna().copy()
    if bdf.empty:
        return None
    cutoff = int(bdf["ds"].dt.year.max())
    result = _fit(bdf, cutoff, target_year, basin=basin, fuel_type=fuel_type)
    return basin_kpi_summary(result, target_year, wti_price=wti)


@st.cache_data(ttl=3600, show_spinner=False)
def _compare_all_basins(
    fuel_type: str, target_year: int, wti: float
) -> dict[str, Any]:
    return execute_tool(
        "compare_basins",
        {"fuel_type": fuel_type, "target_year": target_year, "wti_assumption": wti},
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _fmt(val: float | None, prefix: str = "", suffix: str = "") -> str:
    if val is None:
        return "N/A"
    if abs(val) >= 1_000:
        return f"{prefix}{val:,.0f}{suffix}"
    return f"{prefix}{val:.1f}{suffix}"


# ------------------------------------------------------------------
# Render
# ------------------------------------------------------------------

_PALETTE = [
    "#4A90D9", "#FF6B35", "#50C878", "#FFD700",
    "#C084FC", "#F472B6", "#34D399",
]

_DARK_BG   = "rgba(0,0,0,0)"
_PLOT_BG   = "rgba(18,26,44,.55)"
_LAYOUT    = dict(
    template="plotly_dark",
    paper_bgcolor=_DARK_BG,
    plot_bgcolor=_PLOT_BG,
    font=dict(color="white", size=11),
    margin=dict(t=30, b=20, l=10, r=10),
)


def _normalise(values: list[float | None], invert: bool = False) -> list[float]:
    """Map a list of values to [0, 100]; None → 0.  Optionally invert."""
    clean = [v if v is not None else 0.0 for v in values]
    lo, hi = min(clean), max(clean)
    span = hi - lo if hi > lo else 1.0
    normed = [(v - lo) / span * 100 for v in clean]
    return [100 - n for n in normed] if invert else normed


def _render_radar(
    ranked: list[dict[str, Any]],
    selected: str,
    fuel_type: str,
    target_year: int,
) -> None:
    """Spider/radar chart: 5 normalised dimensions per basin."""
    dims = ["Production", "YoY Growth", "Revenue", "Stability", "RPI"]
    theta = dims + [dims[0]]   # close the polygon

    prods  = [b.get("projected_production", {}).get("value") for b in ranked]
    yoys   = [b.get("growth_rate", {}).get("yoy_pct") for b in ranked]
    revs   = [b.get("revenue_potential", {}).get("revenue_usd_millions") for b in ranked]
    vols   = [b.get("volatility", {}).get("cv_pct") for b in ranked]  # invert → stability
    rpis   = [b.get("relative_performance_index") for b in ranked]

    norm_prod  = _normalise(prods)
    norm_yoy   = _normalise(yoys)
    norm_rev   = _normalise(revs)
    norm_stab  = _normalise(vols, invert=True)   # low volatility = high stability
    norm_rpi   = _normalise(rpis)

    fig = go.Figure()
    for i, b in enumerate(ranked):
        bname  = b["basin"]
        scores = [norm_prod[i], norm_yoy[i], norm_rev[i], norm_stab[i], norm_rpi[i]]
        r      = scores + [scores[0]]
        is_sel = bname == selected
        color  = _PALETTE[i % len(_PALETTE)]

        fig.add_trace(go.Scatterpolar(
            r=r, theta=theta,
            name=bname,
            line=dict(color=color, width=3 if is_sel else 1.2),
            fill="toself",
            fillcolor=color.replace(")", ",0.18)").replace("rgb", "rgba") if is_sel else "rgba(0,0,0,0)",
            opacity=1.0 if is_sel else 0.55,
            hovertemplate=(
                f"<b>{bname}</b><br>"
                + "<br>".join(f"{d}: %{{r[{j}]:.0f}}" for j, d in enumerate(dims))
                + "<extra></extra>"
            ),
        ))

    fig.update_layout(
        **_LAYOUT,
        polar=dict(
            bgcolor="rgba(18,26,44,.4)",
            radialaxis=dict(visible=True, range=[0, 100], tickfont=dict(size=9),
                            gridcolor="rgba(255,255,255,.12)", linecolor="rgba(255,255,255,.12)"),
            angularaxis=dict(tickfont=dict(size=10), gridcolor="rgba(255,255,255,.12)"),
        ),
        showlegend=True,
        legend=dict(orientation="h", y=-0.18, font=dict(size=9)),
        height=340,
        title=dict(text=f"{fuel_type.upper()} · {target_year}", font=dict(size=11), x=0.5),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_lollipop(
    ranked: list[dict[str, Any]],
    selected: str,
    fuel_type: str,
    target_year: int,
) -> None:
    """Horizontal lollipop chart: production ranked, dot colour = RPI."""
    basins = [b["basin"] for b in ranked]
    prods  = [b.get("projected_production", {}).get("value") or 0 for b in ranked]
    rpis   = [b.get("relative_performance_index") or 0 for b in ranked]
    unit   = ranked[0].get("projected_production", {}).get("unit", "") if ranked else ""

    # Sort descending by production
    order  = sorted(range(len(prods)), key=lambda i: prods[i])
    basins = [basins[i] for i in order]
    prods  = [prods[i] for i in order]
    rpis   = [rpis[i] for i in order]

    fig = go.Figure()

    # Stems (thin horizontal bars from 0 to value)
    for i, (b, p) in enumerate(zip(basins, prods)):
        fig.add_shape(
            type="line",
            x0=0, x1=p, y0=i, y1=i,
            line=dict(color="#4A90D9" if b == selected else "rgba(255,255,255,.2)", width=2),
        )

    # Dots
    dot_colors = [
        f"rgb({int(255*(1-r/100))},{int(200*(r/100))},60)"
        for r in rpis
    ]
    fig.add_trace(go.Scatter(
        x=prods, y=list(range(len(basins))),
        mode="markers",
        marker=dict(size=14, color=dot_colors,
                    line=dict(color="white", width=1.5)),
        text=[f"RPI {r:.0f}" for r in rpis],
        customdata=basins,
        hovertemplate="<b>%{customdata}</b><br>%{x:,.0f} " + unit + "<br>%{text}<extra></extra>",
        showlegend=False,
    ))

    fig.update_layout(
        **_LAYOUT,
        height=340,
        xaxis=dict(title=unit, gridcolor="rgba(255,255,255,.08)", zeroline=False),
        yaxis=dict(
            tickvals=list(range(len(basins))),
            ticktext=[f"<b>{b}</b>" if b == selected else b for b in basins],
            tickfont=dict(size=11),
            gridcolor="rgba(0,0,0,0)",
        ),
        title=dict(text=f"{fuel_type.upper()} · {target_year}", font=dict(size=11), x=0.5),
    )
    st.plotly_chart(fig, use_container_width=True)


def _rpi_color(rpi: float) -> str:
    """Red → yellow → green based on RPI 0-100."""
    r = max(0, min(255, int(255 * (1 - rpi / 100))))
    g = max(0, min(200, int(200 * (rpi / 100))))
    return f"rgb({r},{g},60)"


def _render_revenue_bar(
    ranked: list[dict[str, Any]],
    selected: str,
    wti: float,
) -> None:
    """Simple horizontal bar: annual revenue by basin, selected highlighted."""
    basins = [b["basin"] for b in ranked]
    revs   = [
        (b.get("revenue_potential", {}).get("revenue_usd_millions") or 0) * 12
        for b in ranked
    ]
    order  = sorted(range(len(revs)), key=lambda i: revs[i])
    basins = [basins[i] for i in order]
    revs   = [revs[i] for i in order]
    colors = ["#4A90D9" if b == selected else "#1e3a5f" for b in basins]

    fig = go.Figure(go.Bar(
        x=revs, y=basins, orientation="h",
        marker_color=colors,
        text=[f"${v:,.0f}M" for v in revs],
        textposition="outside",
        textfont=dict(size=10, color="white"),
        hovertemplate="<b>%{y}</b><br>$%{x:,.0f}M/yr<extra></extra>",
    ))
    fig.update_layout(
        **{**_LAYOUT, "margin": dict(t=10, b=20, l=10, r=80)},
        height=300,
        xaxis=dict(title="$M / year", gridcolor="rgba(255,255,255,.08)", zeroline=False),
        yaxis=dict(tickfont=dict(size=11)),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_rpi_bar(
    ranked: list[dict[str, Any]],
    selected: str,
) -> None:
    """Vertical color-coded bar chart of RPI scores — easiest to read."""
    basins = [b["basin"] for b in ranked]
    rpis   = [b.get("relative_performance_index") or 0 for b in ranked]

    # Sort high → low
    order  = sorted(range(len(rpis)), key=lambda i: -rpis[i])
    basins = [basins[i] for i in order]
    rpis   = [rpis[i] for i in order]
    colors = [_rpi_color(r) for r in rpis]
    bolds  = ["<b>" + b + "</b>" if b == selected else b for b in basins]

    fig = go.Figure(go.Bar(
        x=basins, y=rpis,
        marker_color=colors,
        marker_line=dict(
            color=["white" if b == selected else "rgba(0,0,0,0)" for b in basins],
            width=2,
        ),
        text=[f"{r:.0f}" for r in rpis],
        textposition="outside",
        textfont=dict(size=11, color="white"),
        hovertemplate="<b>%{x}</b><br>RPI: %{y:.1f}<extra></extra>",
    ))
    fig.add_hline(
        y=50, line_dash="dot",
        line_color="rgba(255,255,255,0.3)",
        annotation_text="Avg",
        annotation_position="right",
        annotation_font_color="rgba(255,255,255,0.5)",
    )
    fig.update_layout(
        **{**_LAYOUT, "margin": dict(t=10, b=20, l=10, r=10)},
        height=300,
        xaxis=dict(tickfont=dict(size=10)),
        yaxis=dict(range=[0, 110], title="RPI Score", gridcolor="rgba(255,255,255,.08)"),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_bubble(
    ranked: list[dict[str, Any]],
    selected: str,
    fuel_type: str,
    target_year: int,
) -> None:
    """BCG-style bubble matrix: Revenue (X) vs YoY Growth (Y), size = production."""
    basins = [b["basin"] for b in ranked]
    revs   = [b.get("revenue_potential", {}).get("revenue_usd_millions") or 0 for b in ranked]
    yoys   = [b.get("growth_rate", {}).get("yoy_pct") or 0 for b in ranked]
    prods  = [b.get("projected_production", {}).get("value") or 0 for b in ranked]
    rpis   = [b.get("relative_performance_index") or 0 for b in ranked]
    unit   = ranked[0].get("projected_production", {}).get("unit", "") if ranked else ""

    # Bubble size: normalise production to 20–60px
    max_p  = max(prods) if max(prods) > 0 else 1
    sizes  = [20 + 40 * (p / max_p) for p in prods]
    colors = [_rpi_color(r) for r in rpis]

    fig = go.Figure()

    # Quadrant shading
    rev_mid  = np.median(revs)
    yoy_mid  = float(np.median(yoys))
    fig.add_shape(type="rect", x0=rev_mid, x1=max(revs)*1.15, y0=yoy_mid, y1=max(yoys)*1.3 + 1,
                  fillcolor="rgba(80,200,120,0.06)", line_width=0)
    fig.add_shape(type="rect", x0=0, x1=rev_mid, y0=min(yoys)*1.3 - 1, y1=yoy_mid,
                  fillcolor="rgba(255,70,70,0.06)", line_width=0)

    fig.add_annotation(x=max(revs)*1.1, y=max(yoys)*1.2,
                       text="<b>Sweet Spot</b>", showarrow=False,
                       font=dict(color="rgba(80,200,120,0.6)", size=10))
    fig.add_annotation(x=min(revs)*0.5 + rev_mid*0.05, y=min(yoys)*1.15,
                       text="<b>Avoid</b>", showarrow=False,
                       font=dict(color="rgba(255,100,100,0.6)", size=10))

    for i, b in enumerate(basins):
        is_sel = b == selected
        fig.add_trace(go.Scatter(
            x=[revs[i]], y=[yoys[i]],
            mode="markers+text",
            name=b,
            marker=dict(
                size=sizes[i],
                color=colors[i],
                line=dict(color="white" if is_sel else "rgba(255,255,255,0.3)",
                          width=3 if is_sel else 1),
                opacity=1.0 if is_sel else 0.85,
            ),
            text=[f"<b>{b}</b>" if is_sel else b],
            textposition="top center",
            textfont=dict(size=10 if not is_sel else 12,
                          color="white"),
            hovertemplate=(
                f"<b>{b}</b><br>"
                f"Revenue: ${revs[i]:,.0f}M/mo<br>"
                f"YoY Growth: {yoys[i]:+.1f}%<br>"
                f"Production: {prods[i]:,.0f} {unit}<br>"
                f"RPI: {rpis[i]:.0f}<extra></extra>"
            ),
            showlegend=False,
        ))

    fig.update_layout(
        **{**_LAYOUT, "margin": dict(t=20, b=40, l=20, r=20)},
        height=400,
        xaxis=dict(
            title="Revenue Potential ($M / month)",
            gridcolor="rgba(255,255,255,.08)",
            zeroline=False,
        ),
        yaxis=dict(
            title="YoY Production Growth (%)",
            gridcolor="rgba(255,255,255,.08)",
            zeroline=True,
            zerolinecolor="rgba(255,255,255,.2)",
        ),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_overview(basin: str, fuel_type: str, target_year: int, wti: float) -> None:
    """Render the Overview tab."""

    # First-load notice — shown once per session when no local parquet files exist
    if not st.session_state.get("_eis_fetch_banner_shown"):
        st.session_state["_eis_fetch_banner_shown"] = True
        from pathlib import Path
        _raw = Path(__file__).resolve().parents[2] / "data" / "raw"
        if not any(_raw.glob("*.parquet")):
            st.info("⏳ First-time data fetch from EIA/FRED — this takes about a minute.")

    # --- KPI cards ---
    with st.spinner("Loading KPIs…"):
        try:
            kpi = _kpi_for_basin(basin, fuel_type, target_year, wti)
        except Exception as exc:
            kpi = None
            st.warning(f"KPI computation failed: {exc}", icon="⚠️")

    c1, c2, c3, c4 = st.columns(4)
    if kpi:
        ppe = kpi["projected_production"]
        gr  = kpi["growth_rate"]
        vol = kpi["volatility"]
        rev = kpi["revenue_potential"]

        ppe_val = ppe.get("value")
        c1.metric(
            "Projected Production",
            _fmt(ppe_val, suffix=f" {ppe.get('unit','')}"),
            help=f"{target_year} annual total — source: {ppe.get('source','')}",
        )

        yoy = gr.get("yoy_pct")
        yoy_year = gr.get("year")
        yoy_help = (
            f"Year-over-year % change: {yoy_year} vs {yoy_year - 1}"
            if yoy_year is not None
            else "Year-over-year % change in annual production"
        )
        cutoff_year = int(kpi.get("projected_production", {}).get("year", target_year)) if kpi else target_year
        is_forecast_yoy = yoy_year is not None and yoy_year > _NOW_YEAR
        yoy_label = (
            f"YoY Growth ({yoy_year} vs {yoy_year - 1})"
            if yoy_year is not None
            else "YoY Growth"
        )
        yoy_help = (
            f"Forecast-based: Prophet {yoy_year} vs {yoy_year - 1} projections."
            if is_forecast_yoy
            else f"Actual EIA data: {yoy_year} vs {yoy_year - 1}."
        ) if yoy_year else "Year-over-year % change"
        c2.metric(
            yoy_label,
            f"{yoy:+.1f}%" if yoy is not None else "N/A",
            delta=f"{yoy:.1f}%" if yoy is not None else None,
            help=yoy_help,
        )

        cv = vol.get("cv_pct")
        interp = vol.get("interpretation", "—")
        c3.metric(
            "Volatility CV% (historical)",
            f"{cv:.1f}%" if cv is not None else "N/A",
            help=f"{interp} — coefficient of variation on historical actuals; not affected by target year.",
        )

        rev_m = rev.get("revenue_usd_millions")
        c4.metric(
            "Revenue Potential",
            f"${rev_m * 12:,.0f}M/yr" if rev_m is not None else "N/A",
            help=f"Gross potential at WTI ${wti}/bbl (annualised)",
        )
    else:
        for col in (c1, c2, c3, c4):
            col.metric("—", "No data")
        st.warning(
            "No production data found for this basin.  \n"
            "Run `python src/data/fetch_all.py` to ingest EIA data.",
            icon="⚠️",
        )

    st.divider()

    # --- Basin comparison ---
    st.subheader(f"Basin Comparison — {fuel_type.upper()} · {target_year}")
    st.caption("First load fits Prophet for all 7 basins — allow ~2 min. Subsequent loads are cached for 1 h.")

    with st.spinner("Fitting forecasts for all 7 basins…"):
        try:
            cmp = _compare_all_basins(fuel_type, target_year, wti)
        except Exception as exc:
            st.error(f"Comparison failed: {exc}")
            return

    if "error" in cmp:
        st.error(f"compare_basins error: {cmp['error']}")
        return

    ranked: list[dict[str, Any]] = [b for b in cmp.get("ranked_basins", []) if "error" not in b]
    if not ranked:
        all_basins = cmp.get("ranked_basins", [])
        errored = [b for b in all_basins if "error" in b]
        if errored:
            err_df = pd.DataFrame([
                {"Basin": b["basin"], "Error": b.get("error", "unknown")}
                for b in errored
            ])
            st.warning("All 7 basins failed to compute. Details below:", icon="⚠️")
            st.dataframe(err_df, use_container_width=True, hide_index=True)
        else:
            st.info("No basin data available yet.")
        return

    rows = []
    for b in ranked:
        pp = b.get("projected_production", {})
        rows.append({
            "Basin":            b["basin"],
            "Proj. Production": pp.get("value"),
            "Unit":             pp.get("unit", ""),
            "YoY %":            b.get("growth_rate", {}).get("yoy_pct"),
            "Volatility CV%":   b.get("volatility", {}).get("cv_pct"),
            "Rev $M/mo":        b.get("revenue_potential", {}).get("revenue_usd_millions"),
            "RPI (0–100)":      b.get("relative_performance_index"),
        })

    df_tbl = pd.DataFrame(rows)
    st.dataframe(
        df_tbl.style.format(
            {
                "Proj. Production": "{:,.0f}",
                "YoY %":            "{:+.1f}",
                "Volatility CV%":   "{:.1f}",
                "Rev $M/mo":        "{:,.0f}",
                "RPI (0–100)":      "{:.1f}",
            },
            na_rep="N/A",
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()

    # ── Row 1: Radar + Lollipop ───────────────────────────────────────────────
    col_radar, col_lollipop = st.columns([1, 1], gap="large")

    with col_radar:
        st.markdown("##### Multi-Dimension Scorecard")
        st.caption("5 metrics normalised 0–100 — further from centre = better")
        _render_radar(ranked, basin, fuel_type, target_year)

    with col_lollipop:
        st.markdown("##### Production Ranking")
        st.caption("Projected annual production · dot colour: green = high RPI, red = low")
        _render_lollipop(ranked, basin, fuel_type, target_year)

    # ── Row 2: Revenue bar + RPI leaderboard ─────────────────────────────────
    st.divider()
    col_rev, col_rpi = st.columns([1, 1], gap="large")

    with col_rev:
        st.markdown("##### Revenue Potential by Basin")
        st.caption("Estimated gross revenue at your WTI assumption (annualised, $M/year)")
        _render_revenue_bar(ranked, basin, wti)

    with col_rpi:
        st.markdown("##### RPI Leaderboard")
        st.caption("Relative Performance Index 0–100 — combines production, growth, revenue & stability")
        _render_rpi_bar(ranked, basin)

    # ── Row 3: Growth vs Risk matrix (beginner-friendly full-width) ───────────
    st.divider()
    st.markdown("##### Growth vs Risk Matrix")
    st.caption(
        "Each bubble is a basin.  "
        "Right = more revenue.  Up = faster growing.  "
        "Bigger bubble = more production.  "
        "Green = strong performer, Red = weaker."
    )
    _render_bubble(ranked, basin, fuel_type, target_year)
