"""Conversational AI energy analyst tab.

A chat interface backed by ChatAgent, which fetches live EIA data via tool calls
before every response. Messages are stored in session state across reruns.
All data-backed numbers are labeled [DATA]; model inferences are labeled [INFERENCE].
"""
from __future__ import annotations

import anthropic
import streamlit as st

from agents.chat_agent import ChatAgent

_EXAMPLES: list[str] = [
    "Which region has the highest projected oil production for 2027?",
    "Summarize the opportunity in the Permian Basin based on current data.",
    "What is the YoY growth rate for Bakken oil production?",
    "Compare all basins for gas production in 2026.",
    "What anomalies has the Eagle Ford seen historically?",
    "What is the revenue potential of the Haynesville at $3/Mcf gas pricing?",
]


def render_chat(client: anthropic.Anthropic) -> None:
    """Render the conversational AI analyst tab."""
    st.subheader("AI Energy Analyst")
    st.caption(
        "Ask questions about regional production, forecasts, and KPIs. "
        "The analyst fetches live EIA data before responding.  \n"
        "**[DATA]** = verified from live data source &nbsp;·&nbsp; "
        "**[INFERENCE]** = model-generated estimate"
    )

    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []

    # Suggested prompts shown only until the first message is sent
    if not st.session_state.chat_messages:
        st.markdown("**Try asking:**")
        cols = st.columns(2)
        for i, ex in enumerate(_EXAMPLES):
            if cols[i % 2].button(ex, key=f"chat_ex_{i}", use_container_width=True):
                _process_message(ex, client)
                return

    # Render conversation history
    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and msg.get("tool_calls"):
                _render_tool_calls(msg["tool_calls"])

    # Live chat input
    if prompt := st.chat_input("Ask about basins, forecasts, or production data..."):
        _process_message(prompt, client)

    # Clear button
    if st.session_state.chat_messages:
        st.divider()
        if st.button("Clear conversation", key="clear_chat"):
            st.session_state.chat_messages = []
            st.rerun()


def _process_message(prompt: str, client: anthropic.Anthropic) -> None:
    """Append user message, run agent, append reply, then rerun."""
    st.session_state.chat_messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Fetching live data and analyzing..."):
            agent = ChatAgent(client)
            # Build API-format message list (text-only; no prior tool blocks needed)
            api_msgs = [
                {"role": m["role"], "content": m["content"]}
                for m in st.session_state.chat_messages
            ]
            result = agent.respond(api_msgs)

        st.markdown(result["text"])
        if result["tool_calls"]:
            _render_tool_calls(result["tool_calls"])

    st.session_state.chat_messages.append(
        {
            "role": "assistant",
            "content": result["text"],
            "tool_calls": result["tool_calls"],
        }
    )


def _render_tool_calls(tool_calls: list[dict]) -> None:
    """Show collapsible tool call details for transparency."""
    n = len(tool_calls)
    names = ", ".join(tc["tool"] for tc in tool_calls)
    with st.expander(
        f"Data sources: {n} tool call{'s' if n > 1 else ''} ({names})",
        expanded=False,
    ):
        for tc in tool_calls:
            st.caption(f"**{tc['tool']}**")
            inp_str = ", ".join(f"{k}={v!r}" for k, v in tc["input"].items())
            st.code(f"{tc['tool']}({inp_str})", language="python")
