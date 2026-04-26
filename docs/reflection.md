# Reflection

## What Was Built

### All 7 Tabs

**Overview** — Four KPI metric cards (Projected Production, YoY Growth, Volatility CV%, Revenue Potential). 7-basin comparison table. Five charts: Multi-Dimension Radar, Production Ranking (lollipop), Revenue Potential bar, RPI Leaderboard, Growth vs Risk bubble matrix (BCG-style).

**Forecast** — Prophet chart with historical actuals, forecast line, 80% CI band, anomaly dots with callout arrows. Cutoff year slider using `@st.fragment` (only reruns the chart block, not the full page). Held-out MAPE displayed in methodology expander.

**Map** — Interactive Plotly `go.Scattergeo` map with `go.Choropleth` state shading. Click any basin marker → sidebar + all tabs update to that basin. Metric overlay options: RPI score, projected production, YoY growth. KPI panel below map.

**Chat** — Multi-turn conversational AI grounded in live EIA data. Agent calls tools before answering — responses tagged `[DATA]` (tool-backed) or `[INFERENCE]` (model estimate). Inline Plotly charts rendered next to responses when compare/forecast tools are called.

**Committee** — Three-agent investment debate: Riley Chen (Bull), Marcus Webb (Bear), Sarah Kim (PM). Each agent runs a tool-use loop before writing their thesis. PM delivers structured PURSUE/WATCH/PASS verdict. Live streaming via `st.status` updates.

**Memo** — Deal memo assembled from committee results. Professional PDF download: header bar, metadata table, colour-coded verdict badge, rationale, risk/opportunity side-by-side, full agent arguments, process notes footer.

**Economics** — Well-level profitability calculator. Arps hyperbolic decline curve with per-basin benchmark defaults. Outputs: EUR, NPV, IRR, payback period, breakeven price. Decline curve + cumulative cash flow dual chart.

### Data & ML Layer
- EIA API v2 with retry/backoff; partial month detection; VGM process filter for gas
- FRED WTI price integration
- Prophet multiplicative seasonality forecasting with 80% CI
- XGBoost recursive forecaster built (lag features, rolling stats, cyclic calendar encoding) — retained in codebase but not shown in UI to keep chart clean
- Held-out MAPE backtest for Prophet validation

---

## What Would Be Done Differently

**1. True basin-level production data**
The biggest data quality issue: EIA duoarea `STX` is all of Texas, so Permian and Eagle Ford show the same numbers. Fix: integrate Texas RRC API or Enverus basin-level datasets for proper disaggregation. This is the single highest-impact improvement.

**2. XGBoost as residual corrector, not standalone**
The XGBoost model was built as an independent forecaster which caused long-horizon drift (900%+ YoY values). Better design: XGBoost models Prophet residuals. Prophet captures global trend + seasonality; XGBoost corrects local regime-specific patterns in the residuals. This ensemble would be more stable and more accurate.

**3. Streaming committee responses**
Currently each agent shows a spinner then dumps full text. Anthropic's streaming API + `st.write_stream()` would let users read the thesis as it's generated — dramatically better UX for a 30–60s operation.

**4. Smaller model for tool dispatch**
Bull and Bear agents make 4–8 tool calls each. These dispatch turns don't require deep reasoning. Using `claude-haiku-4-5` for tool-use turns and `claude-sonnet-4-6` only for the final thesis write-up would cut latency and cost by ~60%.

**5. Price × growth scenario heat map**
A Plotly `go.Heatmap` showing NPV across a grid of (WTI price, growth rate) assumptions would be the highest-value output for an investment analyst. The KPI infrastructure is already in place — this is a presentation layer addition.

**6. Conversation persistence**
Chat history is lost on page refresh. Adding a lightweight SQLite store (or Streamlit's `st.experimental_connection`) for chat threads would make the tool genuinely useful across sessions.

---

## AI Tools Used

**Claude Code (Anthropic)** — Used throughout for scaffolding, debugging, and all feature development. Key contributions:

- Full project structure from a single spec prompt
- EIA API v2 client including list-of-tuples bracket-notation parameter convention
- `ForecastResult` dataclass + `BasinForecaster` with MS resampling fix
- All six KPI functions and `basin_kpi_summary` with detrended CV% calculation
- Tool-use loop in `committee.py` with `cache_control: ephemeral` pattern
- `parse_pm_verdict()` regex parser for structured agent output
- All three agent system prompts with citation rules and structured output format
- `_pending_basin` two-rerun pattern for map → sidebar sync
- `_chat_pending` flag pattern for multi-turn chat without widget conflicts
- XGBoost forecaster with recursive multi-step prediction and feature engineering
- Arps decline curve + NPV/IRR bisection for well economics tab
- fpdf2 PDF generator with latin-1 sanitisation
- All Plotly chart builders: Scattergeo, Choropleth, Scatterpolar, bubble matrix

**All architecture decisions, domain logic (basin mappings, energy event calendar, VGM process code, Arps parameters), and KPI definitions** were directed by the developer; Claude implemented.

---

## Biggest Technical Challenges

| Challenge | Root Cause | Fix |
|-----------|-----------|-----|
| Streamlit widget key conflict on map click | Writing to `st.session_state["basin"]` mid-render | `_pending_basin` intermediary key + two-rerun pattern |
| Plotly `add_vline` TypeError | Plotly 5.22 requires Unix-ms for datetime axis, not ISO string | `.timestamp() * 1000` |
| Multi-turn chat input disappearing | `st.session_state.pop()` unreliable; input re-rendered before state cleared | `get/del` pattern with `_chat_pending` flag |
| XGBoost 900%+ YoY on long horizons | Recursive prediction error accumulation over 5-7 year horizons | Reverted to Prophet-only; XGBoost retained for future residual-correction ensemble |
| PDF unicode crashes | fpdf2 Helvetica only supports latin-1; em-dash/bullets not supported | `_pdf()` sanitiser: replace common chars, then `encode('latin-1', errors='replace')` |
| YoY fixed after cutoff year | `min(target_year, cutoff_year)` clamped to historical data only | Changed to use `y_forecast` values for both comparison years when target > cutoff |
