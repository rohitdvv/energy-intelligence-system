"""Design system shared by every page: chart palette, figure styling, page chrome.

Colours here are mid-tone on purpose so the same chart reads in the light and
dark themes defined in .streamlit/config.toml. Text and background colours are
left to the active Streamlit theme; never hardcode white or near-black.
"""
from __future__ import annotations

from html import escape

import plotly.graph_objects as go
import streamlit as st

FONT_FAMILY = "Inter, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

# Crude and flare: flare orange leads, gas is flame blue, history is steel.
FLARE = "#E8692A"
AMBER = "#E9A23B"
GAS = "#3A7BD5"
STEEL = "#6E8295"
GREEN = "#1F9D6A"
RED = "#D64545"
VIOLET = "#8A63D2"
TEAL = "#159AA8"

ACCENT = FLARE
ORANGE = AMBER

SERIES = [FLARE, GAS, AMBER, TEAL, VIOLET, GREEN, STEEL]

TRANSPARENT = "rgba(0,0,0,0)"
GRID = "rgba(128,128,128,0.18)"
AXIS = "rgba(128,128,128,0.35)"
MUTED = "rgba(128,128,128,0.6)"
ACCENT_SOFT = "rgba(232,105,42,0.30)"

# Red at 0, amber at 50, green at 100. Used for 0-100 scores such as RPI.
SCORE_COLORSCALE = [[0.0, RED], [0.5, AMBER], [1.0, GREEN]]


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def score_color(score: float) -> str:
    """Colour for a 0-100 score on the red, amber, green scale."""
    score = max(0.0, min(100.0, float(score)))
    low, high, t = (RED, AMBER, score / 50) if score <= 50 else (AMBER, GREEN, (score - 50) / 50)
    a, b = _hex_to_rgb(low), _hex_to_rgb(high)
    r, g, bl = (round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return f"rgb({r},{g},{bl})"


def with_alpha(color: str, alpha: float) -> str:
    r, g, b = _hex_to_rgb(color)
    return f"rgba({r},{g},{b},{alpha})"


def style_figure(
    fig: go.Figure,
    *,
    height: int | None = None,
    margin: dict | None = None,
) -> go.Figure:
    """Apply the shared chart look. Call before any chart-specific overrides."""
    layout: dict = dict(
        paper_bgcolor=TRANSPARENT,
        plot_bgcolor=TRANSPARENT,
        font=dict(family=FONT_FAMILY, size=12),
        hoverlabel=dict(font_family=FONT_FAMILY),
        colorway=SERIES,
        margin=margin or dict(t=28, b=28, l=8, r=8),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            bgcolor=TRANSPARENT, title_text="",
        ),
    )
    if height:
        layout["height"] = height
    fig.update_layout(**layout)
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=AXIS, linecolor=AXIS)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=AXIS, linecolor=AXIS)
    return fig


def page_header(title: str, description: str | None = None, eyebrow: str | None = None) -> None:
    if eyebrow:
        st.html(f'<div class="eis-eyebrow">{escape(eyebrow)}</div>')
    st.title(title, anchor=False)
    if description:
        st.caption(description)


APP_CSS = """
<style>
[data-testid="stMainBlockContainer"], .block-container {
  padding-top: 4.75rem; padding-bottom: 4rem; max-width: 1400px;
}
h1, h2, h3 { letter-spacing: -0.015em; }
[data-testid="stMetric"] {
  border: 1px solid rgba(128,128,128,0.22);
  border-radius: 0.625rem;
  padding: 0.85rem 1rem;
  background: rgba(128,128,128,0.035);
}
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
[data-testid="stMetricLabel"] p { font-size: 0.8rem; opacity: 0.78; }
[data-testid="stDataFrame"] { font-variant-numeric: tabular-nums; }

/* Product flowing along the top bar, like a pipeline. */
header[data-testid="stHeader"]::after {
  content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 2px;
  background: linear-gradient(90deg, transparent 0%, #E8692A 20%, #F2A93B 40%, transparent 60%) 0 0 / 200% 100%;
  animation: eis-flowbar 5s linear infinite; opacity: .9;
}
.eis-eyebrow { font: 600 0.72rem/1.2 Inter, sans-serif; letter-spacing: 0.14em;
  text-transform: uppercase; color: #D9581C; margin-bottom: -0.6rem; }
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"],
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"] {
  animation: eis-enter .55s cubic-bezier(.2,.7,.2,1) both;
}
[data-testid="stMetric"] { transition: transform .2s ease, border-color .2s ease, box-shadow .2s ease; }
[data-testid="stMetric"]:hover {
  transform: translateY(-2px); border-color: rgba(232,105,42,.55);
  box-shadow: 0 8px 24px -14px rgba(232,105,42,.55);
}
.stButton button[kind="primary"] { transition: box-shadow .2s ease, transform .2s ease; }
.stButton button[kind="primary"]:hover { box-shadow: 0 6px 22px -8px rgba(232,105,42,.75); transform: translateY(-1px); }
hr { border: 0 !important; height: 1px !important;
  background: linear-gradient(90deg, rgba(232,105,42,.45), rgba(128,128,128,.18) 35%, rgba(128,128,128,.08)) !important; }
@keyframes eis-flowbar { to { background-position: -200% 0; } }
@keyframes eis-enter { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) {
  header[data-testid="stHeader"]::after, [data-testid="stMainBlockContainer"] * { animation: none !important; transition: none !important; }
}
</style>
"""
