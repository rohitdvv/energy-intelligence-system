"""Conversational AI energy analyst tab — true multi-turn chat.

Pattern
-------
1. User submits prompt  →  append user message, set ``_chat_pending=True``, rerun.
2. On next pass        →  for-loop renders full history, pending block runs agent
                          (with spinner), appends response, clears flag.  The
                          chat_input then appears at the bottom, ready for the
                          next question.

This guarantees the chat input is *always* visible after the first turn and
that multi-turn conversation history accumulates correctly.

Inline charts
-------------
When the agent calls ``compare_basins`` or ``forecast_basin``, the result data
is converted into a Plotly chart that appears directly inside the chat bubble —
going beyond prose as required by the hackathon spec.
"""
from __future__ import annotations

from typing import Any

import anthropic
import plotly.graph_objects as go
import streamlit as st

from agents.chat_agent import ChatAgent

_EXAMPLES: list[str] = [
    "Which region has the highest projected oil production for 2027?",
    "Summarize the Permian Basin opportunity based on current data.",
    "Compare all basins for gas production in 2026.",
    "What anomalies has the Eagle Ford seen historically?",
    "What is the YoY growth for Bakken oil?",
    "How does Haynesville's revenue potential compare to Marcellus?",
]


# ---------------------------------------------------------------------------
# Inline chart factory — turns tool results into visualisations
# ---------------------------------------------------------------------------

def _chart_for_tool_calls(tool_calls: list[dict[str, Any]]) -> go.Figure | None:
    """Build the best inline chart available from a list of tool call results."""
    for tc in tool_calls:
        fig = _try_compare_basins(tc) or _try_forecast_basin(tc)
        if fig is not None:
            return fig
    return None


def _try_compare_basins(tc: dict[str, Any]) -> go.Figure | None:
    if tc["tool"] != "compare_basins":
        return None
    ranked = [b for b in tc["result"].get("ranked_basins", []) if "error" not in b]
    if not ranked:
        return None

    basins = [b["basin"] for b in ranked]
    prods  = [b.get("projected_production", {}).get("value") or 0 for b in ranked]
    rpis   = [b.get("relative_performance_index") or 0 for b in ranked]
    unit   = ranked[0].get("projected_production", {}).get("unit", "") if ranked else ""

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=basins, y=prods,
        marker=dict(
            color=rpis,
            colorscale="RdYlGn",
            cmin=0, cmax=100,
            colorbar=dict(title="RPI", thickness=10, len=0.7),
            showscale=True,
        ),
        text=[f"RPI {r:.0f}" for r in rpis],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>Production: %{y:,.0f} " + unit + "<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="Basin Production Ranking", font=dict(size=13)),
        yaxis_title=unit,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(18,26,44,.5)",
        height=260,
        margin=dict(t=36, b=36, l=0, r=60),
        font=dict(size=11),
        showlegend=False,
    )
    return fig


def _try_forecast_basin(tc: dict[str, Any]) -> go.Figure | None:
    if tc["tool"] != "forecast_basin":
        return None
    r = tc["result"]
    if "error" in r or r.get("projected_annual_total") is None:
        return None

    basin  = r.get("basin", "")
    year   = r.get("horizon_year", "")
    total  = r["projected_annual_total"]
    lower  = r.get("confidence_interval_80pct", {}).get("lower", total * 0.85)
    upper  = r.get("confidence_interval_80pct", {}).get("upper", total * 1.15)
    unit   = r.get("unit", "")
    cagr   = r.get("historical_cagr_pct")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[f"{basin} {year}"],
        y=[total],
        error_y=dict(type="data", symmetric=False,
                     array=[upper - total], arrayminus=[total - lower]),
        marker_color="#FF6B35",
        width=0.4,
        hovertemplate=f"<b>{basin}</b><br>Projected: {{y:,.0f}} {unit}<br>"
                      f"80% CI: {lower:,.0f} – {upper:,.0f}<extra></extra>",
    ))
    cagr_note = f"  |  Historical CAGR {cagr:+.1f}%" if cagr is not None else ""
    fig.update_layout(
        title=dict(text=f"Forecast: {basin} {year}{cagr_note}", font=dict(size=13)),
        yaxis_title=unit,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(18,26,44,.5)",
        height=220,
        margin=dict(t=36, b=20),
        font=dict(size=11),
        showlegend=False,
    )
    return fig


# ---------------------------------------------------------------------------
# Agent execution (pure session-state update, no inline rendering)
# ---------------------------------------------------------------------------

def _run_agent(client: anthropic.Anthropic) -> None:
    """Call the agent for the latest user message and append the response."""
    agent = ChatAgent(client)
    api_msgs = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.chat_messages
    ]
    result = agent.respond(api_msgs)
    chart  = _chart_for_tool_calls(result["tool_calls"])
    st.session_state.chat_messages.append({
        "role":       "assistant",
        "content":    result["text"],
        "tool_calls": result["tool_calls"],
        "chart":      chart,
    })


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _render_msg(msg: dict[str, Any]) -> None:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("chart") is not None:
            st.plotly_chart(msg["chart"], use_container_width=True)
        if msg["role"] == "assistant" and msg.get("tool_calls"):
            _render_tools(msg["tool_calls"])


def _render_tools(tool_calls: list[dict]) -> None:
    n = len(tool_calls)
    names = ", ".join(tc["tool"] for tc in tool_calls)
    with st.expander(f"Data sources — {n} tool call{'s' if n > 1 else ''}: {names}"):
        for tc in tool_calls:
            args = ", ".join(f"{k}={v!r}" for k, v in tc["input"].items())
            st.code(f"{tc['tool']}({args})", language="python")


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render_chat(client: anthropic.Anthropic) -> None:
    """Render the conversational AI analyst tab (always multi-turn)."""
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []

    st.subheader("AI Energy Analyst")
    st.caption(
        "Ask anything about U.S. basin production, forecasts, and KPIs. "
        "The analyst fetches **live EIA data** before responding.  \n"
        "**[DATA]** = verified from live source &nbsp;·&nbsp; "
        "**[INFERENCE]** = model-generated estimate"
    )

    # ── Example prompts (only when conversation is empty) ──────────────────
    if not st.session_state.chat_messages:
        st.markdown("##### Try asking:")
        cols = st.columns(2)
        for i, ex in enumerate(_EXAMPLES):
            if cols[i % 2].button(ex, key=f"chat_ex_{i}", use_container_width=True):
                st.session_state.chat_messages.append({"role": "user", "content": ex})
                st.session_state["_chat_pending"] = True
                st.rerun()

    # ── Render full conversation history ───────────────────────────────────
    for msg in st.session_state.chat_messages:
        _render_msg(msg)

    # ── Execute pending agent call (shows spinner inline) ──────────────────
    _pending = st.session_state.get("_chat_pending", False)
    if "_chat_pending" in st.session_state:
        del st.session_state["_chat_pending"]
    if _pending:
        with st.chat_message("assistant"):
            with st.spinner("Fetching live data and analyzing..."):
                _run_agent(client)
            # Render the response we just appended
            last = st.session_state.chat_messages[-1]
            st.markdown(last["content"])
            if last.get("chart") is not None:
                st.plotly_chart(last["chart"], use_container_width=True)
            if last.get("tool_calls"):
                _render_tools(last["tool_calls"])

    # ── Chat input — always visible after the first turn ───────────────────
    if prompt := st.chat_input("Ask about basins, forecasts, or production data..."):
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        st.session_state["_chat_pending"] = True
        st.rerun()

    # ── Clear ──────────────────────────────────────────────────────────────
    if st.session_state.chat_messages:
        st.divider()
        if st.button("Clear conversation", key="clear_chat"):
            st.session_state.chat_messages = []
            st.rerun()
