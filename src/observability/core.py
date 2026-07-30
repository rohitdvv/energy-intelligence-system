"""Observability core — the telemetry buffer, event schema, and helpers.

Design goals
------------
* **Fail-open.** Instrumentation must never break the app. Every public entry
  point swallows its own exceptions.
* **Zero-config.** Importing this module gives you an in-memory buffer (for the
  Observatory tab) and JSON logs. External export (OTLP) turns on only when an
  endpoint is configured.
* **Low churn.** Instrument call sites with a decorator (`@track`), a context
  manager (`span`), or a one-liner for LLM calls (`record_llm`).

Usage
-----
    from observability import track, span, record_llm, get_telemetry

    @track("api", source="eia")
    def _get(...): ...

    with span("tool", "compare_basins", basin="Permian") as sp:
        ...
        sp["rows"] = len(df)

    record_llm(model, response, duration_ms, agent="bull", retries=1)
"""
from __future__ import annotations

import functools
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterator, TypeVar
from contextlib import contextmanager
import json

from observability.pricing import cost_usd
from observability.sinks import LoggingSink, MemorySink, MultiSink, OTLPSink

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Event categories (kept as plain strings for easy filtering/serialisation).
CATEGORY_API = "api"
CATEGORY_LLM = "llm"
CATEGORY_TOOL = "tool"
CATEGORY_MODEL = "model"


@dataclass
class Event:
    """A single instrumented operation."""

    ts: float                      # epoch seconds (start time)
    category: str                  # api | llm | tool | model
    name: str                      # e.g. "eia._get", "committee.bull"
    duration_ms: float
    success: bool = True
    error: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class Telemetry:
    """Process-wide telemetry hub. One instance per process (see singleton)."""

    def __init__(self) -> None:
        self._memory = MemorySink()
        self._otlp = OTLPSink()
        self._sink = MultiSink([self._memory, LoggingSink(), self._otlp])
        self._lock = threading.Lock()
        self._counter = 0

    # -- recording -----------------------------------------------------

    def record(self, event: Event) -> None:
        """Push an event to all sinks. Never raises."""
        try:
            with self._lock:
                self._counter += 1
            self._sink.emit(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Telemetry.record failed: %s", exc)

    # -- read (for the dashboard) --------------------------------------

    def events(self) -> list[Event]:
        return self._memory.events()

    def clear(self) -> None:
        self._memory.clear()

    @property
    def external_active(self) -> bool:
        """True when the OTLP exporter is live (external stack connected)."""
        return getattr(self._otlp, "active", False)

    @property
    def total_recorded(self) -> int:
        return self._counter


# ----------------------------------------------------------------------
# Singleton — module global so it survives Streamlit script reruns
# ----------------------------------------------------------------------

_TELEMETRY: Telemetry | None = None
_SINGLETON_LOCK = threading.Lock()


def get_telemetry() -> Telemetry:
    global _TELEMETRY
    if _TELEMETRY is None:
        with _SINGLETON_LOCK:
            if _TELEMETRY is None:
                _TELEMETRY = Telemetry()
    return _TELEMETRY


# ----------------------------------------------------------------------
# Instrumentation helpers
# ----------------------------------------------------------------------

@contextmanager
def span(category: str, name: str, **attributes: Any) -> Iterator[dict[str, Any]]:
    """Time a block and record an Event on exit.

    Yields a mutable attribute dict — mutate it to attach fields discovered
    during execution (row counts, status codes, etc.)::

        with span("api", "eia._get", route=route) as sp:
            resp = ...
            sp["status_code"] = resp.status_code
    """
    attrs: dict[str, Any] = dict(attributes)
    start = time.time()
    success = True
    error: str | None = None
    try:
        yield attrs
    except Exception as exc:  # noqa: BLE001 — record then re-raise
        success = False
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        get_telemetry().record(
            Event(
                ts=start,
                category=category,
                name=name,
                duration_ms=round((time.time() - start) * 1000, 2),
                success=success,
                error=error,
                attributes=attrs,
            )
        )


def track(
    category: str,
    name: str | None = None,
    **static_attributes: Any,
) -> Callable[[F], F]:
    """Decorator form of :func:`span`.

    ``name`` defaults to ``"<module>.<func>"``. Extra keyword args are attached
    as static attributes on every recorded event (e.g. ``source="eia"``).
    """

    def decorator(func: F) -> F:
        event_name = name or f"{func.__module__.split('.')[-1]}.{func.__name__}"

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with span(category, event_name, **static_attributes):
                return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def record_llm(
    model: str,
    response: Any = None,
    duration_ms: float = 0.0,
    *,
    agent: str | None = None,
    retries: int = 0,
    success: bool = True,
    error: str | None = None,
    **extra: Any,
) -> None:
    """Record a single Claude API call, pulling tokens + cost from usage.

    Safe to call with ``response=None`` on failure paths — tokens/cost are then
    zero and the event is still logged with success=False.
    """
    try:
        usage = getattr(response, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        cache_w = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        cache_r = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        stop_reason = getattr(response, "stop_reason", None)

        usd = cost_usd(model, in_tok, out_tok, cache_w, cache_r)

        attrs: dict[str, Any] = {
            "model": model,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cache_write_tokens": cache_w,
            "cache_read_tokens": cache_r,
            "total_tokens": in_tok + out_tok + cache_w + cache_r,
            "cost_usd": usd,
            "retries": retries,
            "stop_reason": stop_reason,
        }
        if agent:
            attrs["agent"] = agent
        attrs.update(extra)

        get_telemetry().record(
            Event(
                ts=time.time() - duration_ms / 1000.0,
                category=CATEGORY_LLM,
                name=f"llm.{agent}" if agent else "llm.call",
                duration_ms=round(duration_ms, 2),
                success=success,
                error=error,
                attributes=attrs,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_llm failed: %s", exc)
