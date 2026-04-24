"""Committee tab: run the three-agent debate and display results."""
from __future__ import annotations

import time
from typing import Any

import streamlit as st

from agents.committee import Committee
from agents.prompts import BEAR_SYSTEM_PROMPT, BULL_SYSTEM_PROMPT, PM_SYSTEM_PROMPT
from agents.tools import TOOL_SPECS


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _debate_key(basin: str, fuel_type: str, target_year: int) -> str:
    return f"debate_{basin}_{fuel_type}_{target_year}"


def _build_pm_context(
    basin: str,
    fuel_type: str,
    target_year: int,
    wti: float,
    bull: dict[str, Any],
    bear: dict[str, Any],
) -> str:
    sep = "─" * 44
    return (
        f"Investment committee debate — **{basin}** {fuel_type} "
        f"(target: {target_year}, WTI: ${wti}/bbl).\n\n"
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
) -> None:
    """Execute Bull → Bear → PM sequence with live status updates."""
    committee = Committee(client)
    context = (
        f"Basin: **{basin}** | Fuel: {fuel_type} | "
        f"Target year: {target_year} | WTI assumption: ${wti}/bbl\n\n"
        "Use tools to pull live data before writing your thesis. "
        "Do not cite numbers you have not retrieved from a tool call."
    )

    t0 = time.time()
    try:
        with st.status("🏛️ Investment committee in session…", expanded=True) as status:
            status.write("🐂 **Bull analyst** gathering production data and building thesis…")
            bull = committee.run_agent(BULL_SYSTEM_PROMPT, context, TOOL_SPECS)

            status.write(
                f"🐂 Bull complete ({len(bull['tool_calls'])} tool calls).  \n"
                "🐻 **Bear analyst** investigating anomalies and risks…"
            )
            bear = committee.run_agent(BEAR_SYSTEM_PROMPT, context, TOOL_SPECS)

            status.write(
                f"🐻 Bear complete ({len(bear['tool_calls'])} tool calls).  \n"
                "👔 **Portfolio Manager** deliberating…"
            )
            pm = committee.run_agent(
                PM_SYSTEM_PROMPT,
                _build_pm_context(basin, fuel_type, target_year, wti, bull, bear),
                [],
            )

            total = len(bull["tool_calls"]) + len(bear["tool_calls"]) + len(pm["tool_calls"])
            elapsed = round(time.time() - t0, 1)
            status.update(
                label=f"✅ Debate complete — {total} tool calls · {elapsed}s",
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
                "total_tool_calls": total,
                "latency_seconds": elapsed,
            },
        }

    except Exception as exc:
        st.error(f"Committee debate failed: {exc}", icon="🚨")


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
    emoji: str,
    label: str,
    expanded: bool = False,
) -> None:
    with st.expander(f"{emoji} {label}", expanded=expanded):
        st.markdown(agent["text_response"] or "_No response returned._")
        calls = agent.get("tool_calls", [])
        if calls:
            with st.expander(f"🔧 Tool calls used ({len(calls)})"):
                _render_tool_calls(calls)


def _render_results(result: dict[str, Any]) -> None:
    meta    = result["metadata"]
    pm_text = result["pm"]["text_response"]
    verdict = Committee.parse_pm_verdict(pm_text)

    # Trust panel
    _VERDICT_ICON = {"PURSUE": "🟢", "PASS": "🔴", "WATCH": "🟡"}
    v = verdict.get("verdict", "")
    icon = _VERDICT_ICON.get(v, "⚪")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Verdict",    f"{icon} {v or 'N/A'}")
    c2.metric("Conviction", verdict.get("conviction") or "N/A")
    c3.metric("Tool Calls", meta.get("total_tool_calls", "N/A"))
    c4.metric("Latency",    f"{meta.get('latency_seconds','?')}s")

    rationale = verdict.get("rationale", "")
    if rationale:
        st.info(rationale, icon="👔")

    if verdict.get("top_opportunity") or verdict.get("top_risk"):
        col_a, col_b = st.columns(2)
        col_a.success(f"**Opportunity:** {verdict.get('top_opportunity','')}")
        col_b.error(f"**Risk:** {verdict.get('top_risk','')}")

    st.divider()

    # Agent sections
    _render_agent_section(result["bull"], "🐂", "Bull Analyst — Riley Chen", expanded=False)
    _render_agent_section(result["bear"], "🐻", "Bear Analyst — Marcus Webb", expanded=False)
    _render_agent_section(result["pm"],   "👔", "Portfolio Manager Verdict",  expanded=True)


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------

def render_committee(
    basin: str,
    fuel_type: str,
    target_year: int,
    wti: float,
    client: Any,
) -> None:
    """Render the Committee tab."""
    key = _debate_key(basin, fuel_type, target_year)
    cached = st.session_state.get(key)

    col_btn, col_hint = st.columns([1, 3])
    with col_btn:
        run = st.button(
            "🏛️ Run Investment Committee",
            type="primary",
            use_container_width=True,
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
                "Runs Bull, Bear, and PM agents sequentially (~45–90 s). "
                "Results are cached per basin / fuel / year combination."
            )

    if run:
        _run_debate(basin, fuel_type, target_year, wti, client, key)

    result = st.session_state.get(key)
    if result:
        _render_results(result)
