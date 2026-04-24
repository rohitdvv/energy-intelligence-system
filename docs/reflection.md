# Reflection

## What I Built

**Completed features:**

- **Overview tab** — Four KPI metric cards (Projected Production, YoY Growth, Volatility CV%, Revenue Potential), a 7-basin comparison table with all KPIs, and an RPI bar chart with the selected basin highlighted. All data updates dynamically when the sidebar selections change.

- **Forecast tab** — Interactive Prophet chart with historical actuals, forecast line, 80% confidence interval band, and anomaly scatter overlay. The cutoff year slider uses `@st.fragment` so dragging it reruns only the chart block, not the full page. Hovering a red anomaly dot shows the date, z-score, and a known energy market event label (where catalogued).

- **Committee tab** — Three-agent investment committee: Bull analyst (Riley Chen), Bear analyst (Marcus Webb), and Portfolio Manager (Chair). Each agent calls live data tools (production history, forecast, KPI snapshot, compare basins, anomaly detection) before writing its thesis. The PM reads both theses and issues a structured `VERDICT / CONVICTION / RATIONALE / TOP_RISK / TOP_OPPORTUNITY` output. Results are shown with a trust panel (verdict chip, conviction badge, tool-call count, latency), collapsible agent sections, and tool-call summaries.

- **Memo tab** — Template-driven investment deal memo generated from committee results. Includes basin statistics, agent summaries, PM verdict, and appendix. Downloadable as a Markdown file.

- **Data layer** — EIA API v2 client (oil, gas, WTI) and FRED client with retry, exponential backoff, and Parquet caching. Live-fetch by default so the deployed app works without pre-seeded data files.

- **Partial month detection** — Drops the latest EIA data point if it is less than 50% of the recent 6-month average, preventing Prophet from misreading an incomplete reporting month as a production crash.

- **Natural gas process filter** — Adds `facets[process][]=VGM` to EIA natural gas requests to return marketed production only, not gross withdrawals.

**Known limitations:**

- Texas state proxy: EIA duoarea `STX` covers all Texas production. Permian and Eagle Ford both map to Texas, so their fetched volumes are identical (all-Texas) rather than basin-specific. This is documented in `eia.py`.
- Revenue potential is gross at-wellhead; no deductions for royalties, opex, or differentials.
- Prophet forecasts do not incorporate rig counts, completion activity, or commodity price signals — suitable for directional planning only.

---

## What I'd Do Differently

1. **Basin-level granularity** — The largest data quality issue is the Texas proxy problem. I would add supplemental data from the Texas RRC API or NDIC (North Dakota) to get true basin-level production figures rather than state-level aggregates. This would make Permian vs. Eagle Ford a meaningful comparison instead of two identical series.

2. **Sensitivity analysis** — A price × decline-rate heat map (Tier 2 stretch) would add significant analyst value. The KPI and forecast infrastructure is already in place; the missing piece is a Streamlit `st.experimental_data_editor` or Plotly `go.Heatmap` view driven by a grid of Prophet runs.

3. **Streaming agent responses** — The committee tab currently shows a spinner during each agent run and renders the full text when done. Using Anthropic's streaming API + `st.write_stream()` would let the user read the Bull's thesis as it is typed, improving perceived responsiveness.

4. **Excel export** — The Tier 2 Excel integration (formula-driven workbook) would help bridge to downstream analyst workflows. `openpyxl` with named ranges for KPI inputs is straightforward to add but was cut to stay within the time budget.

5. **Smaller model for tool-use turns** — The Bull and Bear agents make 4–8 API calls each for data fetching. These tool-call turns don't require deep reasoning — a smaller, faster model (Claude Haiku) for tool dispatch with Sonnet only for the final thesis write-up would reduce latency and cost significantly.

---

## AI Tools Used

**Claude Code (Anthropic)** — Used throughout the build for scaffolding, debugging, and iterative development. Specific contributions:

- Scaffolded the full project structure (all directories, config, .gitignore, requirements, .streamlit config) from a single spec prompt.
- Wrote the EIA API v2 client including the list-of-tuples parameter convention required by the `requests` library for bracket-notation query params.
- Designed the `ForecastResult` dataclass and `BasinForecaster` class, including the "MS" resampling fix that prevents Prophet from misinterpreting mixed date formats.
- Wrote all six KPI functions and the `basin_kpi_summary` aggregator with the detrended CV% calculation.
- Designed the tool-use loop in `committee.py`, including the `cache_control: ephemeral` system prompt pattern and the `parse_pm_verdict` parser.
- Debugged the Plotly `add_vline` TypeError by identifying that Plotly 5.22+ requires Unix milliseconds (not ISO strings) for datetime axis annotations.
- Diagnosed and fixed the Streamlit Cloud deployment issue (empty DataFrames) by changing all `live_fetch` defaults from `False` to `True`.
- Wrote all system prompts for Bull, Bear, and PM agents including the citation rule and structured output format.

All architecture decisions, KPI definitions, and domain logic (basin mappings, energy event calendar, VGM process code) were written collaboratively — Claude generated the code, I reviewed and directed each step.
