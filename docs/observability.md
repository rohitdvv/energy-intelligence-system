# Observability — the Observatory layer

A lightweight, in-process telemetry layer for the **LLM** and **external-API**
hotspots, surfaced live in the app's **🔭 Observatory** tab and optionally
exported to any OpenTelemetry (OTLP) collector.

## Why

The system's two riskiest layers at runtime are:

1. **The AI committee** — three sequential Claude agents (Bull → Bear → PM) plus
   the Chat agent, each running a tool-use loop with retry/backoff. This is the
   main latency and **cost** sink, and was previously a black box.
2. **External data APIs** — EIA, FRED, and BSEE. These fail, rate-limit, and
   fall back to static data in ways the UI didn't expose.

The Observatory makes both observable: latency, retries, errors, token usage,
and estimated USD spend.

## Design principles

- **Fail-open.** Instrumentation never breaks the app — every recording path
  swallows its own exceptions.
- **Zero-config in-app.** Importing `observability` gives you an in-memory ring
  buffer (feeds the dashboard) and structured JSON logs, no setup required.
- **External-ready seam.** Set `OTEL_EXPORTER_OTLP_ENDPOINT` and install the
  optional `opentelemetry` extras (see `requirements.txt`) to *also* stream to
  Grafana Cloud / Datadog / Honeycomb. Inert otherwise — not a hard dependency.
- **Low churn.** Call sites are instrumented with a decorator, a context
  manager, or a one-liner.

## Module map

| File | Responsibility |
|---|---|
| `src/observability/core.py` | `Event` schema, process-wide `Telemetry` singleton, `@track` / `span()` / `record_llm()` |
| `src/observability/sinks.py` | `MemorySink` (dashboard), `LoggingSink` (JSON logs), `OTLPSink` (external), `MultiSink` fan-out |
| `src/observability/pricing.py` | Claude token → USD table (incl. cache read/write), `cost_usd()` |
| `src/ui/observatory.py` | The Streamlit dashboard tab |

## Instrumented call sites

| Layer | Location | Captured |
|---|---|---|
| LLM | `agents/committee.py::_create`, agent spans in `debate` | model, tokens (in/out/cache), cost, latency, retries, per-agent (bull/bear/pm) |
| LLM | `agents/chat_agent.py::respond` | same, agent=`chat` |
| Tools | `agents/tools.py::execute_tool` | tool name, latency, error flag |
| API | `data/eia.py::_get`, `data/fred.py::_get` | source, route, status code, retries, latency |
| API | `data/bsee_loader.py::fetch_gom_production` | source, API-vs-static fallback, rows, latency |

## Instrumenting new code

```python
from observability import track, span, record_llm

# 1) decorate a whole function
@track("api", source="myapi")
def fetch(...): ...

# 2) wrap a block and attach discovered fields
with span("tool", "my_tool", basin=basin) as sp:
    result = do_work()
    sp["rows"] = len(result)

# 3) record a Claude call (pulls tokens + cost from response.usage)
record_llm(model, response, duration_ms=elapsed_ms, agent="bull", retries=n)
```

## What's next (not in this pass)

This first pass covers the LLM and API layers. Natural follow-ons:
forecast-model instrumentation (Prophet/XGBoost fit time + backtest error),
app/UX events (cache hit rate, tab usage), and a persisted DuckDB sink for
history across restarts.
