"""Observability layer for the Energy Intelligence System.

Lightweight, in-process telemetry for the LLM and external-API layers:
latency, errors, retries, token usage and $ cost. Feeds an in-app
Observatory dashboard and (optionally) an external OTLP collector.

Public API::

    from observability import track, span, record_llm, get_telemetry
"""
from __future__ import annotations

from observability.core import (
    CATEGORY_API,
    CATEGORY_LLM,
    CATEGORY_MODEL,
    CATEGORY_TOOL,
    Event,
    Telemetry,
    get_telemetry,
    record_llm,
    span,
    track,
)

__all__ = [
    "CATEGORY_API",
    "CATEGORY_LLM",
    "CATEGORY_MODEL",
    "CATEGORY_TOOL",
    "Event",
    "Telemetry",
    "get_telemetry",
    "record_llm",
    "span",
    "track",
]
