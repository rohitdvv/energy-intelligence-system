"""Committee tab: run the three-agent debate and display results."""
from __future__ import annotations

import time
from typing import Any

import streamlit as st

from agents.committee import Committee
from agents.prompts import BEAR_SYSTEM_PROMPT, BULL_SYSTEM_PROMPT, PM_SYSTEM_PROMPT
from agents.tools import TOOL_SPECS
from data.eia import basin_scope
from kpi.metrics import DEFAULT_HENRY_HUB, price_assumption_text
from ui.access import has_access
from ui.usage_limits import daily_public_run_limit, remaining_public_runs, try_start_public_run


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _debate_key(basin: str, fuel_type: str, target_year: int) -> str:
    return f"debate_{basin}_{fuel_type}_{target_year}"


def _build_pm_context(
    basin: str,
    fuel_type: str,
    target_year: int,
    price: str,
    bull: dict[str, Any],
    bear: dict[str, Any],
) -> str:
    sep = "─" * 44
    return (
        f"Investment committee debate — **{basin}** {fuel_type} "
        f"(target: {target_year}, price assumption: {price}).\n\n"
        f"BULL ANALYST — Riley Chen\n{sep}\n{bull['text_response']}\n\n"
        f"BEAR ANALYST — Marcus Webb\n{sep}\n{bear['text_response']}\n\n"
        "Issue your binding investment committee verdict."
    )


def _run_debate(
    basin: str,
    fuel_type: str,
    target_year: int,
    wti: float,
    client: Any,
    key: str,
    henry_hub: float = DEFAULT_HENRY_HUB,
) -> None:
    """Execute Bull → Bear → PM sequence with live status updates."""
    committee = Committee(client)
    price = price_assumption_text(fuel_type, wti, henry_hub)
    context = (
        f"Basin: **{basin}** | Fuel: {fuel_type} | "
        f"Target year: {target_year} | Price assumption: {price} "
        f"(pass wti_assumption={wti} and henry_hub_assumption={henry_hub} to KPI tools)\n\n"
        f"Data scope: {basin_scope(basin)}.\n\n"
        "Use tools to pull live data before writing your thesis. "
        "Do not cite numbers you have not retrieved from a tool call."
    )

    t0 = time.time()
    try:
        with st.status("Investment committee in session…", expanded=True) as status:
            status.write("**Bull analyst** is gathering production data and building the case…")
            bull = committee.run_agent(BULL_SYSTEM_PROMPT, context, TOOL_SPECS, agent="bull")

            status.write(
                f"Bull analyst finished ({len(bull['tool_calls'])} tool calls).  \n"
                "**Bear analyst** is investigating anomalies and risks…"
            )
            bear = committee.run_agent(BEAR_SYSTEM_PROMPT, context, TOOL_SPECS, agent="bear")

            status.write(
                f"Bear analyst finished ({len(bear['tool_calls'])} tool calls).  \n"
                "**Portfolio manager** is deciding…"
            )
            pm = committee.run_agent(
                PM_SYSTEM_PROMPT,
                _build_pm_context(basin, fuel_type, target_year, price, bull, bear),
                [],
                agent="pm",
            )

            total = len(bull["tool_calls"]) + len(bear["tool_calls"]) + len(pm["tool_calls"])
            elapsed = round(time.time() - t0, 1)
            status.update(
                label=f"Debate complete · {total} tool calls · {elapsed}s",
                state="complete",
            )

        st.session_state[key] = {
            "bull": bull,
            "bear": bear,
            "pm": pm,
            "metadata": {
                "basin": basin,
                "fuel_type": fuel_type,
                "target_year": target_year,
                "wti_assumption": wti,
                "henry_hub_assumption": henry_hub,
                "price_assumption": price,
                "total_tool_calls": total,
                "latency_seconds": elapsed,
            },
        }

    except Exception as exc:
        st.error(f"Committee debate failed: {exc}", icon=":material/error:")


def _render_tool_calls(tool_calls: list[dict[str, Any]]) -> None:
    for tc in tool_calls:
        inputs = ", ".join(f"{k}={v!r}" for k, v in tc["input"].items())
        tool_result = tc["result"]
        result_keys = list(tool_result.keys()) if isinstance(tool_result, dict) else ["error"]
        st.code(
            f"{tc['tool']}({inputs})\n→ returned keys: {result_keys}",
            language=None,
        )


def _render_agent_section(
    agent: dict[str, Any],
    icon: str,
    label: str,
    expanded: bool = False,
) -> None:
    with st.expander(label, icon=icon, expanded=expanded):
        st.markdown(agent["text_response"] or "_No response returned._")
        calls = agent.get("tool_calls", [])
        if calls:
            with st.expander(f"Tool calls used ({len(calls)})", icon=":material/build:"):
                _render_tool_calls(calls)


def _render_results(result: dict[str, Any]) -> None:
    meta    = result["metadata"]
    pm_text = result["pm"]["text_response"]
    verdict = Committee.parse_pm_verdict(pm_text)

    # Trust panel
    v = verdict.get("verdict", "")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Recommendation", v or "N/A")
    c2.metric("Conviction", verdict.get("conviction") or "N/A")
    c3.metric("Tool Calls", meta.get("total_tool_calls", "N/A"))
    c4.metric("Latency",    f"{meta.get('latency_seconds','?')}s")

    rationale = verdict.get("rationale", "")
    if rationale:
        st.info(rationale, icon=":material/gavel:")

    if verdict.get("top_opportunity") or verdict.get("top_risk"):
        col_a, col_b = st.columns(2)
        col_a.success(f"**Opportunity:** {verdict.get('top_opportunity','')}")
        col_b.error(f"**Risk:** {verdict.get('top_risk','')}")

    st.divider()

    # Agent sections
    _render_agent_section(result["bull"], ":material/trending_up:", "Bull analyst (simulated persona)", expanded=False)
    _render_agent_section(result["bear"], ":material/trending_down:", "Bear analyst (simulated persona)", expanded=False)
    _render_agent_section(result["pm"], ":material/gavel:", "Portfolio manager recommendation", expanded=True)


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------

def render_committee(
    basin: str,
    fuel_type: str,
    target_year: int,
    wti: float,
    client: Any,
    henry_hub: float = DEFAULT_HENRY_HUB,
) -> None:
    """Render the Committee tab."""
    key = _debate_key(basin, fuel_type, target_year)
    cached = st.session_state.get(key)

    owner = has_access("ai")
    limit = daily_public_run_limit()
    remaining = None if owner else remaining_public_runs()

    col_btn, col_hint = st.columns([1, 3], vertical_alignment="center")
    with col_btn:
        run = st.button(
            "Run Investment Committee",
            icon=":material/play_arrow:",
            type="primary",
            use_container_width=True,
            disabled=remaining == 0,
        )
    with col_hint:
        if cached:
            meta = cached["metadata"]
            st.caption(
                f"Showing cached result for **{basin}** {fuel_type} {target_year} "
                f"({meta.get('total_tool_calls','?')} tool calls · "
                f"{meta.get('latency_seconds','?')}s). "
                "Re-click to run a fresh debate."
            )
        else:
            st.caption(
                "Runs the bull, bear and portfolio manager agents in turn (about 45 to 90 s). "
                "Results are kept per basin, fuel and year for this session."
            )
        if remaining is not None:
            st.caption(
                f"Public runs left today: **{remaining} of {limit}**. Resets at midnight UTC."
                if limit else "Public runs are turned off on this deployment."
            )

    if run:
        if owner or try_start_public_run():
            _run_debate(basin, fuel_type, target_year, wti, client, key, henry_hub)
        else:
            st.warning(
                "Today's public run limit has been reached. It resets at midnight UTC.",
                icon=":material/hourglass_top:",
            )

    result = st.session_state.get(key)
    if result:
        _render_results(result)
