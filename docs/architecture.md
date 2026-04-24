# Architecture Overview

## Final Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| UI framework | Streamlit ≥ 1.37 | Four-tab layout; `@st.fragment` for slider isolation |
| Forecasting | Facebook Prophet 1.1.5 + CmdStanPy < 1.2.0 | Multiplicative seasonality, monthly "MS" resampling |
| AI agents | Anthropic Claude (`claude-sonnet-4-5-20250929`) | Tool-use loop; `cache_control: ephemeral` on system prompts |
| Data sources | EIA Open Data API v2, FRED API | Parquet cache layer; live-fetch fallback when no Parquet |
| Charts | Plotly 5.22 | Dark-mode theme; Unix-ms timestamps for `add_vline` |
| Data layer | pandas 2.2 + pyarrow 16 | Parquet persistence under `data/raw/` |
| Language | Python 3.11 | All backend logic; Streamlit as the only web layer |

No changes from `planning/planning.md` in the core stack. `duckdb` was included as a dependency for future SQL analytics but not used in the shipped version — a deliberate trade-off to keep the data layer simple.

---

## Folder Structure

```
src/
├── app.py                  # Streamlit entry point — sidebar, tabs, shared Anthropic client
├── config.py               # Dual-mode API key access (st.secrets → os.environ → .env)
├── data/
│   ├── eia.py              # EIA Open Data API v2 client (oil, gas, WTI) with retry
│   ├── fred.py             # FRED API client (WTI spot price series)
│   ├── loader.py           # Parquet cache layer; live-fetch fallback; Streamlit cache wrappers
│   └── fetch_all.py        # CLI script — pre-fetches all 7 basins × 2 fuels and saves Parquet
├── models/
│   └── forecaster.py       # ForecastResult dataclass + BasinForecaster + forecast_basin()
├── kpi/
│   └── metrics.py          # Six KPI functions + basin_kpi_summary() aggregator
├── agents/
│   ├── tools.py            # Five Anthropic tool schemas, executor functions, execute_tool()
│   ├── prompts.py          # System prompts for Bull, Bear, PM agents
│   └── committee.py        # Committee class — tool-use loop, debate(), parse_pm_verdict()
└── ui/
    ├── overview.py         # Tab 1: KPI cards, basin comparison table, RPI bar chart
    ├── forecast.py         # Tab 2: interactive Prophet chart with anomaly overlay
    ├── committee.py        # Tab 3: live multi-agent debate with st.status updates
    └── memo.py             # Tab 4: template-driven deal memo with download button
```

---

## Cross-Tab Data Flow

**1. Sidebar → all four tabs**
The sidebar (rendered once in `app.py`) collects `(basin, fuel_type, target_year, wti)` as a Python tuple. This tuple is passed as function arguments to each `render_*()` call. There is no shared Streamlit session state for these values — Streamlit's re-run model re-passes them on every interaction.

**2. EIA data → Overview KPI cards**
`render_overview()` calls `_kpi_for_basin(basin, fuel_type, target_year, wti)` which calls:
```
load_production_no_cache(fuel_type, live_fetch=True)
  → try data/raw/{fuel_type}_production.parquet
  → on miss: EIAClient().fetch_{oil,gas}_production_by_basin() for all 7 basins
  → save Parquet, return DataFrame
```
The full multi-basin DataFrame is loaded once and filtered to the selected basin inside `_kpi_for_basin`. The `@st.cache_data(ttl=3600)` decorator ensures subsequent tab-switch rerenders hit memory, not EIA.

**3. Prophet forecast → Forecast tab anomaly overlay**
`_cached_forecast(basin, fuel_type, cutoff_year, horizon_year)` and `_cached_anomalies(basin, fuel_type)` are both `@st.cache_data` functions. The forecast tab uses `@st.fragment` on `_interactive_chart()` so dragging the cutoff slider only reruns the fragment, not the full page. Anomaly detection runs a second independent Prophet fit on the full historical series; its results are merged with the chart's historical actuals using a `{YYYY-MM: y_actual}` lookup dict.

**4. Committee debate → Memo tab**
After a committee debate completes, `render_committee()` stores the result dict in `st.session_state[f"debate_{basin}_{fuel_type}_{target_year}"]`. `render_memo()` reads this same key to populate the deal memo template. If no debate result exists, the download button is disabled.

---

## AI Integration Design

### Context passed to the model

Each agent receives a structured user message containing basin, fuel type, target year, and WTI assumption. It then calls tools to fetch data rather than being pre-loaded with all data. This keeps the context window small on the first turn and lets Claude decide which tools to call based on its own reasoning.

Available tools:
- `get_production_history` — latest month, 12-month avg, YoY change, trend direction
- `forecast_basin` — Prophet forecast, annual total, 80% CI, historical CAGR
- `get_kpi_snapshot` — full KPI suite for one basin
- `compare_basins` — all 7 basins ranked by projected production + RPI scores
- `investigate_anomalies` — in-sample Prophet residual z-scores with event calendar lookup

### Boundary between AI output and verified data

The citation rule is enforced in all three system prompts: **"If you cannot cite a specific tool call output for a numeric claim, do not state that number."** This forces every quantitative claim in agent responses to trace back to a tool result. The UI renders tool-call summaries alongside agent prose so a human reviewer can cross-check.

### Prompt engineering decisions

- **Bull (Riley Chen)**: Required to call ≥ 2 tools; bias toward production upside and growth catalysts.
- **Bear (Marcus Webb)**: Required to call `investigate_anomalies` (mandatory) plus at least one other tool; bias toward risk, decline, and volatility.
- **PM (Chair)**: Reads Bull + Bear text; no tool access. Outputs five parseable fields (`VERDICT:`, `CONVICTION:`, `RATIONALE:`, `TOP_RISK:`, `TOP_OPPORTUNITY:`).
- **`cache_control: ephemeral`** on all system prompts — Anthropic caches the prompt block for ~5 minutes, reducing token cost on multi-turn tool loops.
- **`max_tokens: 800`** per turn — keeps individual responses concise and reduces runaway verbosity.

---

## What Changed From the Plan

1. **`live_fetch=True` by default** — Originally planned to require a local `fetch_all.py` run before the Streamlit app would show data. Changed to live-fetch-by-default after realising Streamlit Cloud deployments have no pre-seeded Parquet files. The Parquet files are still written on first fetch and used on subsequent loads.

2. **`@st.fragment` on forecast chart** — Not in the original plan. Added to prevent the entire page from re-running when the cutoff slider is dragged, which would re-trigger the Prophet fit and compare-basins call on every slider tick.

3. **`add_vline` Unix-ms timestamp** — Plotly 5.22 requires numeric (Unix milliseconds) for `x` on datetime axes, not ISO strings. The initial implementation used `.isoformat()` and raised a `TypeError` at runtime; fixed to `.timestamp() * 1000`.

4. **VGM process facet for natural gas** — EIA natural gas endpoint returns multiple process types (marketed, dry, gross withdrawals). Added `facets[process][]=VGM` to isolate marketed volume. Not anticipated at planning time; discovered during data validation.

5. **Partial month detection** — EIA sometimes returns the current in-progress month with a partial (low) value. Added a heuristic: drop the last row if its value is less than 50% of the 6-month prior average. This prevents Prophet from treating an incomplete month as a genuine production drop.
