"""Observatory tab — live telemetry dashboard for the LLM and API layers.

Reads the in-process event buffer (observability.get_telemetry) and renders
health, cost, latency, and error views. Zero external dependencies beyond the
libraries the app already ships (streamlit, pandas, plotly).
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from observability import get_telemetry

_ACCENT = "#4A90D9"
_ORANGE = "#e8622a"
_GREEN = "#4CAF50"
_RED = "#e05260"

_PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="rgba(255,255,255,.75)"),
    margin=dict(l=10, r=10, t=40, b=10),
    height=300,
)


def _events_df() -> pd.DataFrame:
    """Flatten the telemetry buffer into a tidy DataFrame."""
    events = get_telemetry().events()
    if not events:
        return pd.DataFrame()

    rows: list[dict] = []
    for e in events:
        row = {
            "ts": pd.to_datetime(e.ts, unit="s"),
            "category": e.category,
            "name": e.name,
            "duration_ms": e.duration_ms,
            "success": e.success,
            "error": e.error,
        }
        # Promote common attributes to columns for easy plotting.
        attrs = e.attributes or {}
        for key in (
            "source", "agent", "model", "route", "endpoint", "status_code",
            "retries", "cost_usd", "input_tokens", "output_tokens",
            "total_tokens", "cache_read_tokens", "tool_error", "basin",
            "result_source", "stop_reason",
        ):
            if key in attrs:
                row[key] = attrs[key]
        rows.append(row)

    return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)


def _empty_state() -> None:
    st.info(
        "No telemetry captured yet. Interact with the app — run a **Committee "
        "debate**, ask the **Chat** agent a question, or refresh data — and the "
        "LLM and API activity will stream in here.",
        icon="🔭",
    )


def _kpi_row(df: pd.DataFrame) -> None:
    llm = df[df["category"] == "llm"]
    api = df[df["category"] == "api"]
    tool = df[df["category"] == "tool"]

    # LLM events split into per-call records (have cost_usd) vs agent spans.
    llm_calls = llm[llm["cost_usd"].notna()] if "cost_usd" in llm else llm.iloc[0:0]

    total_cost = float(llm_calls["cost_usd"].sum()) if not llm_calls.empty else 0.0
    total_tokens = int(llm_calls["total_tokens"].sum()) if "total_tokens" in llm_calls and not llm_calls.empty else 0
    api_err = (1 - api["success"].mean()) * 100 if not api.empty else 0.0
    tool_err = (1 - tool["success"].mean()) * 100 if not tool.empty else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("LLM calls", f"{len(llm_calls):,}")
    c2.metric("Tokens used", f"{total_tokens:,}")
    c3.metric("Est. spend", f"${total_cost:,.4f}")
    c4.metric(
        "Avg LLM latency",
        f"{llm_calls['duration_ms'].mean():,.0f} ms" if not llm_calls.empty else "—",
    )

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("API calls", f"{len(api):,}")
    c6.metric("API error rate", f"{api_err:.1f}%", delta=None)
    c7.metric("Tool calls", f"{len(tool):,}")
    c8.metric("Tool error rate", f"{tool_err:.1f}%")


def _llm_section(df: pd.DataFrame) -> None:
    llm = df[(df["category"] == "llm") & (df.get("cost_usd").notna() if "cost_usd" in df else False)]
    if llm.empty:
        st.caption("No LLM calls recorded yet.")
        return

    st.markdown("#### 🤖 LLM cost & tokens")
    left, right = st.columns(2)

    with left:
        by_agent = (
            llm.groupby(llm["agent"].fillna("call"))
            .agg(cost=("cost_usd", "sum"), calls=("cost_usd", "size"))
            .reset_index()
            .rename(columns={"agent": "agent"})
            .sort_values("cost", ascending=False)
        )
        fig = px.bar(
            by_agent, x="agent", y="cost", text="calls",
            title="Spend by agent (USD) · bar label = #calls",
            color_discrete_sequence=[_ORANGE],
        )
        fig.update_layout(**_PLOTLY_LAYOUT)
        st.plotly_chart(fig, use_container_width=True)

    with right:
        tok = llm.copy()
        tok["cum_tokens"] = tok["total_tokens"].fillna(0).cumsum()
        fig = px.area(
            tok, x="ts", y="cum_tokens",
            title="Cumulative tokens",
            color_discrete_sequence=[_ACCENT],
        )
        fig.update_layout(**_PLOTLY_LAYOUT)
        st.plotly_chart(fig, use_container_width=True)

    lat_l, lat_r = st.columns(2)
    with lat_l:
        fig = px.box(
            llm, x=llm["agent"].fillna("call"), y="duration_ms",
            title="Latency distribution by agent (ms)",
            color_discrete_sequence=[_ACCENT],
        )
        fig.update_layout(**_PLOTLY_LAYOUT)
        st.plotly_chart(fig, use_container_width=True)
    with lat_r:
        if "retries" in llm and llm["retries"].fillna(0).sum() > 0:
            retr = llm.groupby(llm["agent"].fillna("call"))["retries"].sum().reset_index()
            fig = px.bar(
                retr, x="agent", y="retries",
                title="Retries by agent (rate limits / 5xx)",
                color_discrete_sequence=[_RED],
            )
            fig.update_layout(**_PLOTLY_LAYOUT)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.success("No LLM retries — all calls succeeded first try.", icon="✅")


def _api_section(df: pd.DataFrame) -> None:
    api = df[df["category"] == "api"]
    if api.empty:
        st.caption("No external API calls recorded yet.")
        return

    st.markdown("#### 🌐 External data APIs")
    left, right = st.columns(2)

    with left:
        by_src = (
            api.groupby(api["source"].fillna("unknown"))
            .agg(calls=("success", "size"), errors=("success", lambda s: (~s).sum()))
            .reset_index()
        )
        fig = px.bar(
            by_src, x="source", y="calls", text="errors",
            title="Calls by source · bar label = #errors",
            color_discrete_sequence=[_ACCENT],
        )
        fig.update_layout(**_PLOTLY_LAYOUT)
        st.plotly_chart(fig, use_container_width=True)

    with right:
        fig = px.box(
            api, x=api["source"].fillna("unknown"), y="duration_ms",
            title="Latency by source (ms)",
            color_discrete_sequence=[_GREEN],
        )
        fig.update_layout(**_PLOTLY_LAYOUT)
        st.plotly_chart(fig, use_container_width=True)


def _tool_section(df: pd.DataFrame) -> None:
    tool = df[df["category"] == "tool"]
    if tool.empty:
        return
    st.markdown("#### 🛠️ Agent tools")
    agg = (
        tool.groupby("name")
        .agg(
            calls=("success", "size"),
            avg_ms=("duration_ms", "mean"),
            errors=("success", lambda s: int((~s).sum())),
        )
        .reset_index()
        .sort_values("calls", ascending=False)
    )
    agg["avg_ms"] = agg["avg_ms"].round(0)
    st.dataframe(agg, use_container_width=True, hide_index=True)


def _recent_and_errors(df: pd.DataFrame) -> None:
    errors = df[~df["success"]]
    if not errors.empty:
        st.markdown("#### 🚨 Recent errors")
        st.dataframe(
            errors[["ts", "category", "name", "error"]].tail(20).iloc[::-1],
            use_container_width=True, hide_index=True,
        )

    with st.expander(f"📜 Event log ({len(df)} events in buffer)", expanded=False):
        cols = [c for c in ["ts", "category", "name", "duration_ms", "success",
                            "source", "agent", "cost_usd", "status_code"] if c in df]
        st.dataframe(
            df[cols].tail(200).iloc[::-1],
            use_container_width=True, hide_index=True,
        )


def render_observatory() -> None:
    """Render the Observatory telemetry dashboard."""
    tel = get_telemetry()

    head_l, head_r = st.columns([3, 1])
    with head_l:
        st.subheader("🔭 Observatory")
        st.caption(
            "Live telemetry for the LLM committee/chat and external data APIs — "
            "latency, retries, token usage and estimated spend."
        )
    with head_r:
        ext = "🟢 connected" if tel.external_active else "⚪ local only"
        st.metric("External export (OTLP)", ext)
        if st.button("Clear buffer", use_container_width=True):
            tel.clear()
            st.rerun()

    df = _events_df()
    if df.empty:
        _empty_state()
        return

    _kpi_row(df)
    st.divider()
    _llm_section(df)
    st.divider()
    _api_section(df)
    st.divider()
    _tool_section(df)
    st.divider()
    _recent_and_errors(df)

    st.caption(
        "Buffer holds the most recent 5,000 events in-process. "
        "Set `OTEL_EXPORTER_OTLP_ENDPOINT` (and install the optional "
        "`opentelemetry` extras) to also stream to Grafana / Datadog / any "
        "OTLP collector."
    )
