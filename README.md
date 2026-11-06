[![Review Assignment Due Date](https://classroom.github.com/assets/deadline-readme-button-22041afd0340ce965d47ae6ef1cefeee28c7c493a6346c4f15d667ab976d596c.svg)](https://classroom.github.com/a/vvE-nkdH)

# Energy Intelligence System

**Live app:** https://energy-intelligence.streamlit.app/
**Walkthrough video:** [Loom, 5 minutes](https://www.loom.com/share/67ea0809cce0461cabd9a48dbe0e7b95)

A decision-support tool for U.S. oil and gas investment. It reads public EIA production data
for six basins, forecasts each one with whichever model has the best track record for that
basin, turns the result into KPIs and well economics, and has a committee of Claude agents
argue the case for and against investing before a portfolio manager agent gives a verdict.

Built for the CDF Energy AI Hackathon. The brief is in [`problem_statement.md`](./problem_statement.md).

---

## What's in the app

| Page | What it does |
|---|---|
| **Overview** | Projected production, growth, volatility and gross revenue for the selected basin and year, USGS undiscovered resources, and a comparison of all six basins |
| **Forecast** | Monthly history and forecast with an 80% band, a model comparison table, anomaly markers with known market events, and a cutoff slider to replay past forecasts |
| **Map** | Basins on a U.S. map, coloured by projected production, growth or relative size; click a basin to select it |
| **Committee** | Bull, Bear and Portfolio Manager agents debate an investment using the app's own data tools |
| **Memo** | The committee's verdict as a deal memo, downloadable as a PDF |
| **Economics** | Single-well decline curve and discounted cash flow: NPV, IRR, payback and breakeven price |
| **AI Analyst** | Multi-turn chat with the same data tools (needs an access code) |
| **Observatory** | Live telemetry: Claude cost and tokens, latency, retries, API health, errors (needs an access code) |

A filter bar shared by the pages sets the basin, fuel (oil or gas), target year and price
assumption (WTI for oil, Henry Hub for gas).

---

## Data

| Source | Used for |
|---|---|
| EIA Short-Term Energy Outlook (`steo`), regional series | Basin oil and gas production, and new-well and legacy-decline figures for the forecast |
| EIA state production | Anadarko, which EIA does not publish as a region (shown as Oklahoma state data) |
| FRED / EIA spot prices | WTI history |
| USGS National Oil & Gas Assessment | Undiscovered resources per basin |
| BSEE | Gulf of Mexico offshore context (falls back to static figures if unavailable) |

Basins: Permian, Bakken, Eagle Ford, Haynesville, Appalachia (Marcellus and Utica), Anadarko.
EIA's forecast months are dropped, so every figure is reported history.

---

## Forecasting

Three models are backtested for every basin and fuel by refitting them at each of the last
eight year-ends using only data up to that point:

- **Prophet trend**: trend with changepoints and yearly seasonality
- **EIA well activity**: production from new wells held at the past year's level, with older
  wells declining at their recent rate
- **Repeat last year**: the baseline

For a forecast N years out, the model with the lowest past error at N years is used, and its
own past misses set the 80% band. The Forecast page shows which model was chosen and all
three models' errors; where nothing has beaten repeating last year, the page says so. Full
method and current error tables: [`docs/kpi_definitions.md`](./docs/kpi_definitions.md).

---

## Running locally

Requires Python 3.11.

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then add your keys
python src/data/fetch_all.py                                  # optional: pre-fetch data
streamlit run src/app.py
```

Free keys: [EIA](https://www.eia.gov/opendata/register.php), [FRED](https://fred.stlouisfed.org/docs/api/api_key.html), [Anthropic](https://console.anthropic.com/).
Without pre-fetching, the app downloads the data on first load and caches it in `data/raw/`.

### Access codes and limits

The data pages and the Committee are public. The Committee makes paid Claude calls, so public
runs are capped per day (20 by default, resetting at midnight UTC). Two features stay locked
until a code of at least 16 characters is set in the app's secrets:

| Secret | Effect |
|---|---|
| `AI_ACCESS_CODE` | Unlocks the AI Analyst chat, and lets that session run the Committee without the daily cap |
| `OBSERVATORY_ACCESS_CODE` | Unlocks the Observatory, which shows activity from every viewer |
| `COMMITTEE_DAILY_RUN_LIMIT` | Optional. Public Committee runs per day; `0` turns public runs off |

Changing a code signs out every session that used the old one. The daily count is kept in
memory, so it also resets when the app restarts.

### Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests
```

The tests use fake keys and a fake Claude client, so they make no paid or network calls.

---

## Observability

The Observatory tab tracks the two runtime hotspots, Claude agents and external data APIs:
per-agent cost and tokens, latency, retries, API error rates and status codes, tool usage, and
a feed of recent errors. Failed Claude calls are retried on rate limits and server errors, and
every attempt is recorded. Telemetry never breaks the app. Each event is also printed as a
JSON log line (`OBS_LOG_EVENTS=off` turns that off); `OBS_DUCKDB_PATH` keeps history in DuckDB
and `OTEL_EXPORTER_OTLP_ENDPOINT` streams to an OpenTelemetry collector. See
[`docs/observability.md`](./docs/observability.md).

---

## Repo structure

```
├── problem_statement.md     # Hackathon brief
├── planning/planning.md     # Planning document
├── src/
│   ├── app.py               # Streamlit entry point and navigation
│   ├── reload_guard.py      # Loads updated code cleanly after a deploy
│   ├── data/                # EIA, FRED, USGS and BSEE clients, parquet cache
│   ├── models/              # Forecast models, backtests and model choice, well economics
│   ├── kpi/                 # KPI calculations
│   ├── agents/              # Committee, chat agent, data tools, Claude retries
│   ├── observability/       # Telemetry feeding the Observatory
│   └── ui/                  # One module per page
├── tests/                   # pytest suite
└── docs/
    ├── walkthrough.md       # Video link and demo script
    ├── architecture.md      # Architecture and data flow
    ├── kpi_definitions.md   # KPI, forecast and band definitions
    ├── observability.md     # Telemetry design
    └── reflection.md        # What was built, tradeoffs, AI tools used
```

For research and planning only; not investment advice.

---

## Submission checklist

- [x] Live deployment URL at the top of this README
- [x] Planning document in `planning/planning.md`
- [x] Working application in `src/`
- [x] `docs/walkthrough.md`: walkthrough video link
- [x] `docs/architecture.md`: architecture overview and data flow
- [x] `docs/kpi_definitions.md`: KPIs with calculation logic
- [x] `docs/reflection.md`: reflection
