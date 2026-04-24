# Planning Document

## Tech Stack

**Framework / Language:** Python 3.11 + Streamlit

> Streamlit gives a rapid, interactive web UI without needing a separate frontend framework. Its `@st.cache_data`, `@st.cache_resource`, and `@st.fragment` primitives are purpose-built for data-heavy analytical apps, making it ideal for this decision-support tool.

**Key Libraries:**
- `pandas 2.2` + `pyarrow 16` — data wrangling and Parquet persistence
- `prophet 1.1.5` + `cmdstanpy <1.2.0` — time-series forecasting with seasonality and changepoints
- `plotly 5.22` — interactive charts with dark-mode themes
- `requests 2.32` — EIA and FRED API clients with retry logic
- `anthropic 0.40` — Claude Sonnet via Anthropic Messages API (tool-use loop)
- `scikit-learn 1.5` — supplemental numerical utilities
- `duckdb 0.10` — fast in-process analytics on DataFrames (reserved for future SQL queries)
- `python-dotenv` — local `.env` support for CLI runs

**AI Provider:** Anthropic Claude (claude-sonnet-4-5-20250929)

> Claude's structured tool-use API allows agents to call Python functions (EIA data fetchers, forecasting engine, KPI calculator) and receive JSON results mid-conversation. This grounds every AI claim in live data rather than training-data recall. The Anthropic SDK's `cache_control: ephemeral` feature reduces cost by caching the large system prompts across multi-turn tool loops.

---

## Phases & Priorities

| Phase | Target Dates | Goals |
|-------|-------------|-------|
| 1 | Day 1 AM | Project scaffold: repo structure, `.streamlit/config.toml`, requirements, config, .gitignore |
| 2 | Day 1 PM | Data ingestion: EIA client (oil, gas, WTI), FRED client (WTI price), `fetch_all.py` CLI |
| 3 | Day 2 AM | Data layer: `loader.py` with live-fetch fallback, Prophet forecaster, KPI metric functions |
| 4 | Day 2 PM | Multi-agent committee: `tools.py` (5 Anthropic tool schemas + executors), `prompts.py`, `committee.py` |
| 5 | Day 3     | Streamlit UI: four tabs (Overview, Forecast, Committee, Memo) wired to real data and agents |
| 6 | Day 4     | Bug fixes, deployment hardening, Streamlit Cloud secrets config, live URL verification |
| 7 | Day 5     | Documentation, walkthrough video, final commit |

---

## What I'll Cut If Time Is Short

**First to drop:** Sensitivity analysis heat map (Tier 2 stretch). It adds visual polish but doesn't change the core investment decision workflow.

**Last to drop:** The multi-agent committee. It is the primary AI integration and the main differentiator over a static dashboard. Cutting it would reduce the submission to Tier 1 only.

**Never cut:** Live data fetching from EIA, Prophet forecast with cutoff slider, the Projected Production KPI. These are the Tier 1 hard requirements.

---

## Open Questions / Risks

1. **EIA API rate limits** — The API returns up to 5,000 rows per call. With 7 basins × 2 fuels × 15 years of monthly data ≈ 1,260 rows, a single call per basin/fuel should be sufficient. Risk: if pagination is needed, retry logic must handle it. Mitigation: request `length=5000` and log if the row count equals exactly 5,000.

2. **Prophet on Streamlit Cloud** — Prophet requires a compiled Stan backend (CmdStan). Streamlit Community Cloud runs Linux x86-64; `prophet==1.1.5` with `cmdstanpy<1.2.0` is the known working pin. Risk: build failures on Cloud. Mitigation: pin both libraries in `requirements.txt`.

3. **Cold-start latency** — First load with no cached Parquet files must fetch from EIA, fit 7 Prophet models, and call Claude three times. Total time could exceed 3 minutes. Mitigation: (a) save Parquet after each fetch, (b) use `@st.cache_data(ttl=3600)` so subsequent users hit cache, (c) show a first-load info banner.

4. **Anthropic API costs** — Each committee debate calls Claude up to 24 times (3 agents × 8 max turns). Mitigation: `cache_control: ephemeral` on system prompts reduces token costs on repeated calls; `max_tokens=800` caps response length per turn.

5. **Texas state proxy ambiguity** — The EIA duoarea code `STX` covers all Texas production. Both the Permian and Eagle Ford basins are in Texas, so fetching either individually returns all-Texas volumes. This is a known limitation documented in `eia.py` and the architecture docs.
