"""Telemetry sinks — where Events go once recorded.

Three implementations, all optional and fail-open:

  MemorySink   — bounded in-process ring buffer that feeds the in-app
                 Observatory dashboard (this is "option 1", always on).
  LoggingSink  — one structured JSON line per event via the stdlib logger,
                 so events show up in Streamlit Cloud / container logs.
  OTLPSink     — OpenTelemetry OTLP exporter (the "option 2" external seam).
                 Inert unless an OTLP endpoint is configured AND the optional
                 opentelemetry packages are installed. Never a hard dependency.

A Sink must never raise into the caller: emit() swallows and logs.
"""
from __future__ import annotations

import logging
import os
import threading
from collections import deque
from typing import TYPE_CHECKING, Iterable, Protocol

if TYPE_CHECKING:
    from observability.core import Event

logger = logging.getLogger(__name__)


class Sink(Protocol):
    """Anything that can receive telemetry events."""

    def emit(self, event: "Event") -> None: ...


# ----------------------------------------------------------------------
# In-memory ring buffer (feeds the dashboard)
# ----------------------------------------------------------------------

class MemorySink:
    """Thread-safe bounded buffer of the most recent events.

    Survives Streamlit reruns because the owning Telemetry singleton lives in
    a module global (Streamlit re-executes the script but keeps sys.modules).
    """

    def __init__(self, maxlen: int = 5000) -> None:
        self._buf: deque[Event] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def emit(self, event: "Event") -> None:
        with self._lock:
            self._buf.append(event)

    def events(self) -> list["Event"]:
        with self._lock:
            return list(self._buf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()


# ----------------------------------------------------------------------
# Structured JSON logging
# ----------------------------------------------------------------------

class LoggingSink:
    """Emit each event as a single JSON log line."""

    def __init__(self, level: int = logging.INFO) -> None:
        self._log = logging.getLogger("observability.events")
        self._level = level

    def emit(self, event: "Event") -> None:
        try:
            self._log.log(self._level, event.to_json())
        except Exception as exc:  # noqa: BLE001
            logger.debug("LoggingSink failed: %s", exc)


# ----------------------------------------------------------------------
# OpenTelemetry OTLP exporter (external-ready seam — option 2)
# ----------------------------------------------------------------------

class OTLPSink:
    """Forward events to an OTLP collector as OpenTelemetry spans.

    Activates only when BOTH are true:
      * an endpoint is set (OTEL_EXPORTER_OTLP_ENDPOINT), and
      * the optional `opentelemetry-sdk` + OTLP exporter packages import.

    Otherwise it is a no-op, so shipping the code costs nothing on
    Streamlit Cloud where the extra packages are not installed.
    """

    def __init__(self) -> None:
        self._tracer = None
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        if not endpoint:
            return
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(
                resource=Resource.create({"service.name": "energy-intelligence-system"})
            )
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer("energy-intelligence.observability")
            logger.info("OTLPSink active → %s", endpoint)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "OTLP endpoint set but exporter unavailable (%s); "
                "install opentelemetry-sdk + opentelemetry-exporter-otlp to enable.",
                exc,
            )

    @property
    def active(self) -> bool:
        return self._tracer is not None

    def emit(self, event: "Event") -> None:
        if self._tracer is None:
            return
        try:
            from opentelemetry.trace import Status, StatusCode

            # Record a zero-duration span stamped with the event attributes.
            with self._tracer.start_as_current_span(f"{event.category}.{event.name}") as span:
                span.set_attribute("duration_ms", event.duration_ms)
                span.set_attribute("success", event.success)
                for k, v in event.attributes.items():
                    if isinstance(v, (str, int, float, bool)):
                        span.set_attribute(k, v)
                if not event.success:
                    span.set_status(Status(StatusCode.ERROR, event.error or ""))
        except Exception as exc:  # noqa: BLE001
            logger.debug("OTLPSink emit failed: %s", exc)


# ----------------------------------------------------------------------
# Fan-out
# ----------------------------------------------------------------------

class MultiSink:
    """Broadcast one event to several sinks; one failure never blocks others."""

    def __init__(self, sinks: Iterable[Sink]) -> None:
        self._sinks = list(sinks)

    def emit(self, event: "Event") -> None:
        for sink in self._sinks:
            try:
                sink.emit(event)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Sink %s failed: %s", type(sink).__name__, exc)
