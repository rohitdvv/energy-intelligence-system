"""Animated oilfield hero for the Overview page.

A cross-section of the ground: rock strata over a lit reservoir zone, a pumpjack
nodding at the surface, a horizontal well being drilled into the reservoir, a gas
flare, product flowing along a pipeline, and a seismic trace scrolling beneath.
Pure SVG and CSS, so it needs no JavaScript and stops moving for anyone who has
asked their system for reduced motion. The panel keeps one dark look in both themes.

Streamlit's HTML sanitizer removes inline <svg>, so the scene is embedded as an
SVG image. Browsers still run CSS animations inside SVG images.
"""
from __future__ import annotations

import base64
from html import escape

import streamlit as st

from data.eia import BASIN_SOURCE

_SEISMIC = " ".join(
    f"{x},{236 + (7 if (x // 12) % 2 else -9) * (1 if (x // 48) % 3 else 0.35):.1f}"
    for x in range(0, 2401, 12)
)

_SCENE_STYLE = """
<style>
.eis-reservoir { opacity: .16; animation: glow 5s ease-in-out infinite; }
.eis-well { stroke-dasharray: 560; stroke-dashoffset: 560; animation: drill 7s ease-in-out infinite; }
.eis-bit { opacity: 0; animation: bit 7s ease-in-out infinite; }
.eis-pipe { stroke-dasharray: 10 14; animation: flow 1.6s linear infinite; }
.eis-beam { transform-box: fill-box; transform-origin: 41.4% 14.3%; animation: nod 2.6s ease-in-out infinite alternate; }
.eis-flame { transform-box: fill-box; transform-origin: 50% 100%; animation: flicker .9s ease-in-out infinite alternate; }
.eis-seismic { animation: scroll 14s linear infinite; }
@keyframes glow { 50% { opacity: .3; } }
@keyframes drill { 0% { stroke-dashoffset: 560; } 70%, 100% { stroke-dashoffset: 0; } }
@keyframes bit { 0%, 66% { opacity: 0; } 72%, 92% { opacity: 1; } 100% { opacity: 0; } }
@keyframes flow { to { stroke-dashoffset: -24; } }
@keyframes nod { from { transform: rotate(-9deg); } to { transform: rotate(9deg); } }
@keyframes flicker { from { transform: scale(.92, .86); opacity: .85; } to { transform: scale(1.06, 1.1); opacity: 1; } }
@keyframes scroll { to { transform: translateX(-1200px); } }
@media (prefers-reduced-motion: reduce) {
  * { animation: none !important; } .eis-well { stroke-dashoffset: 0; } .eis-bit { opacity: 1; }
}
</style>
"""

_SCENE = f"""
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 260" preserveAspectRatio="xMidYMid slice">
  {_SCENE_STYLE}
  <defs>
    <linearGradient id="eisSky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1B1410"/><stop offset="1" stop-color="#120E0B"/>
    </linearGradient>
    <linearGradient id="eisFade" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#120E0B" stop-opacity=".97"/>
      <stop offset=".46" stop-color="#120E0B" stop-opacity=".82"/>
      <stop offset=".72" stop-color="#120E0B" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="eisFlame" x1="0" y1="1" x2="0" y2="0">
      <stop offset="0" stop-color="#E8692A"/><stop offset=".6" stop-color="#F2A93B"/>
      <stop offset="1" stop-color="#FFE2A8"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="260" fill="url(#eisSky)"/>
  <path d="M0 124 C220 116 420 130 640 122 S1000 114 1200 124 V260 H0Z" fill="#2B2019"/>
  <path d="M0 150 C260 142 470 160 700 150 S1010 140 1200 152 V260 H0Z" fill="#34271D"/>
  <path d="M0 178 C240 170 520 188 760 178 S1040 168 1200 180 V260 H0Z" fill="#3D2E21"/>
  <path class="eis-reservoir" d="M0 198 C260 192 560 208 800 198 S1060 190 1200 200 V226 C1000 218 800 230 560 222 S200 214 0 222Z" fill="#E8692A"/>
  <path d="M0 222 C200 214 560 226 800 222 S1060 220 1200 226 V260 H0Z" fill="#251C16"/>
  <g stroke="#F2C89A" stroke-opacity=".08" fill="none">
    <path d="M0 150 C260 142 470 160 700 150 S1010 140 1200 152"/>
    <path d="M0 178 C240 170 520 188 760 178 S1040 168 1200 180"/>
  </g>
  <path d="M520 122 V196 Q520 210 506 210 H330" stroke="#B98F66" stroke-opacity=".55" stroke-width="3" fill="none"/>
  <path class="eis-well" d="M780 120 V188 Q780 210 804 210 H1160" stroke="#F2A93B" stroke-width="3.5" fill="none" stroke-linecap="round"/>
  <circle class="eis-bit" cx="1160" cy="210" r="5" fill="#FFE2A8"/>
  <line class="eis-pipe" x1="560" y1="117" x2="1104" y2="117" stroke="#E8692A" stroke-width="2.5" stroke-opacity=".9"/>
  <g fill="#0B0908" stroke="#6B5948" stroke-width="1.2">
    <rect x="766" y="96" width="28" height="22" rx="2"/>
    <path d="M772 96 L780 52 L788 96Z"/>
  </g>
  <g class="eis-pumpjack" transform="translate(250 0)">
    <path d="M368 118 L388 70 L408 118Z" fill="#0B0908" stroke="#6B5948" stroke-width="1.2"/>
    <g class="eis-beam">
      <rect x="330" y="66" width="120" height="8" rx="3" fill="#E8692A"/>
      <path d="M450 62 Q470 70 450 84 Z" fill="#E8692A"/>
      <line x1="458" y1="84" x2="458" y2="118" stroke="#B98F66" stroke-width="1.5"/>
    </g>
    <circle cx="388" cy="70" r="4" fill="#F2A93B"/>
    <rect x="318" y="98" width="22" height="20" rx="3" fill="#0B0908" stroke="#6B5948" stroke-width="1.2"/>
  </g>
  <line x1="1110" y1="118" x2="1110" y2="64" stroke="#6B5948" stroke-width="4"/>
  <path class="eis-flame" d="M1110 64 C1096 50 1102 34 1110 22 C1118 34 1124 50 1110 64Z" fill="url(#eisFlame)"/>
  <polyline class="eis-seismic" points="{_SEISMIC}" fill="none" stroke="#F2A93B" stroke-opacity=".35" stroke-width="1.4"/>
  <rect width="1200" height="260" fill="url(#eisFade)"/>
</svg>
"""

_SCENE_URI = "data:image/svg+xml;base64," + base64.b64encode(_SCENE.encode()).decode()

_CSS = """
<style>
.eis-hero { position: relative; overflow: hidden; border-radius: 14px; min-height: 236px;
  background: #120E0B; color: #F5EEE6; isolation: isolate; margin: 0.25rem 0 0.75rem; }
.eis-hero-scene { position: absolute; inset: 0; z-index: -1;
  background: #120E0B center / cover no-repeat; }
.eis-hero-copy { position: relative; padding: 1.6rem 1.75rem 1.5rem; max-width: 640px;
  display: grid; gap: 0.6rem; }
.eis-hero-eyebrow { font: 600 0.72rem/1 Inter, sans-serif; letter-spacing: 0.14em;
  text-transform: uppercase; color: #F2A93B; display: flex; align-items: center; gap: 0.5rem; }
.eis-live { width: 7px; height: 7px; border-radius: 50%; background: #3DDC97;
  box-shadow: 0 0 0 0 rgba(61,220,151,.6); animation: eis-pulse 2.4s ease-out infinite; }
.eis-hero h2 { margin: 0; color: #FFF8F1; font-size: clamp(1.45rem, 3.2vw, 2.15rem);
  line-height: 1.12; letter-spacing: -0.02em; font-weight: 700; text-wrap: balance; }
.eis-hero h2 em { font-style: normal; color: #F2A93B; }
.eis-hero p { margin: 0; color: rgba(245,238,230,.74); font-size: 0.92rem; max-width: 52ch; }
.eis-chips { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.35rem; }
.eis-chip { border: 1px solid rgba(255,255,255,.14); background: rgba(255,255,255,.06);
  border-radius: 999px; padding: 0.32rem 0.75rem; font-size: 0.8rem; color: #F5EEE6;
  font-variant-numeric: tabular-nums; backdrop-filter: blur(4px); }
.eis-chip b { font-weight: 600; color: #FFE2A8; }
.eis-chip.up b { color: #6FE3A7; } .eis-chip.down b { color: #FF8A7A; }
.eis-hero-copy > * { animation: eis-rise .7s cubic-bezier(.2,.7,.2,1) both; }
.eis-hero-copy > *:nth-child(2) { animation-delay: .08s; }
.eis-hero-copy > *:nth-child(3) { animation-delay: .16s; }
.eis-hero-copy > *:nth-child(4) { animation-delay: .24s; }
@keyframes eis-rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
@keyframes eis-pulse { 70% { box-shadow: 0 0 0 8px rgba(61,220,151,0); } 100% { box-shadow: 0 0 0 0 rgba(61,220,151,0); } }
@media (prefers-reduced-motion: reduce) {
  .eis-hero *, .eis-hero-copy > * { animation: none !important; }
}
@media (max-width: 640px) { .eis-hero-copy { padding: 1.25rem 1.1rem; } }
</style>
"""


def render_hero(
    basin: str,
    fuel_type: str,
    target_year: int,
    production: str | None,
    yoy_pct: float | None,
    data_through: str | None,
) -> None:
    """Render the animated hero. All text is escaped before it reaches the page."""
    fuel = "oil" if fuel_type == "oil" else "natural gas"
    if production:
        headline = (
            f"{escape(basin)} {fuel}: <em>{escape(production)}</em> projected for {target_year}"
        )
    else:
        headline = f"{escape(basin)} {fuel} outlook for {target_year}"

    chips = []
    if yoy_pct is not None:
        trend = "up" if yoy_pct >= 0 else "down"
        chips.append(f'<span class="eis-chip {trend}">Year over year <b>{yoy_pct:+.1f}%</b></span>')
    if data_through:
        chips.append(f'<span class="eis-chip">EIA data through <b>{escape(data_through)}</b></span>')
    chips.append(f'<span class="eis-chip">Source <b>{escape(BASIN_SOURCE.get(basin, "EIA"))}</b></span>')

    st.html(
        _CSS
        + f"""
<section class="eis-hero" aria-label="{escape(basin)} {fuel} outlook">
  <div class="eis-hero-scene" style="background-image: url('{_SCENE_URI}')"></div>
  <div class="eis-hero-copy">
    <div class="eis-hero-eyebrow"><span class="eis-live"></span>U.S. oil &amp; gas intelligence</div>
    <h2>{headline}</h2>
    <p>Public EIA production, a Prophet forecast and an AI investment committee,
       side by side for every basin.</p>
    <div class="eis-chips">{''.join(chips)}</div>
  </div>
</section>
"""
    )
