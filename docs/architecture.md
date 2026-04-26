# Architecture Overview

## Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| UI framework | Streamlit 1.56 | 7-tab layout; `@st.fragment` for slider isolation |
| Forecasting | Facebook Prophet 1.1.5 + CmdStanPy | Multiplicative seasonality, 80% CI |
| AI agents | Anthropic Claude `claude-sonnet-4-6` | Tool-use loop; multi-turn chat |
| Data sources | EIA Open Data API v2, FRED API | Parquet cache; live-fetch fallback |
| Charts | Plotly 5.22 | `go.Scattergeo`, `go.Choropleth`, `go.Scatterpolar` |
| PDF export | fpdf2 2.7+ | Latin-1 text sanitisation |
| Data layer | pandas 2.2 + pyarrow 16 | Parquet under `data/raw/` |
| Well economics | Arps hyperbolic decline | IRR bisection, NPV at monthly resolution |
| Language | Python 3.11 | |

---

## Folder Structure

```
src/
├── app.py                    # Entry point — sidebar, 7 tabs, shared Anthropic client
├── config.py                 # API key access (st.secrets → os.environ → .env)
├── data/
│   ├── eia.py                # EIA Open Data API v2 client with retry/backoff
│   ├── fred.py               # FRED API client (WTI spot price)
│   ├── loader.py             # Parquet cache + live-fetch + st.cache_data wrappers
│   └── fetch_all.py          # CLI pre-fetch script
├── models/
│   ├── forecaster.py         # ForecastResult dataclass + forecast_basin()
│   ├── backtest.py           # Held-out MAPE backtest
│   └── xgb_forecaster.py     # XGBoost recursive forecaster (retained for future use)
├── kpi/
│   └── metrics.py            # Six KPI functions + basin_kpi_summary()
├── agents/
│   ├── tools.py              # Five tool schemas + execute_tool()
│   ├── prompts.py            # System prompts for Bull, Bear, PM agents
│   ├── committee.py          # Three-agent debate loop + parse_pm_verdict()
│   └── chat_agent.py         # Multi-turn conversational agent
└── ui/
    ├── overview.py           # Tab 1: KPI cards + 5 comparison charts
    ├── forecast.py           # Tab 2: Prophet chart + anomaly overlay
    ├── map.py                # Tab 3: interactive Scattergeo + Choropleth
    ├── chat.py               # Tab 4: multi-turn AI chat with inline charts
    ├── committee.py          # Tab 5: live 3-agent investment debate
    ├── memo.py               # Tab 6: deal memo + PDF download
    └── economics.py          # Tab 7: well economics (Arps + NPV/IRR)
```

---

## Data Flow

### EIA → App
```
load_production_no_cache(fuel_type, live_fetch=True)
  → try data/raw/{fuel_type}_production.parquet
  → miss → EIAClient.fetch_*_production_by_basin() for all 7 basins
  → drop partial month (< 50% of 6-month avg)
  → save Parquet → return DataFrame
```
`@st.cache_data(ttl=3600)` — one real EIA call per basin per hour maximum.

### Map Click → Sidebar Sync
```
Click → st.session_state["_pending_basin"] = basin → st.rerun()
_sidebar() → pops _pending_basin → sets selectbox value before render
All tabs re-render with new basin
```

### Committee → Memo
```
Committee.debate() → stores dict in st.session_state["debate_{b}_{f}_{y}"]
render_memo() → reads key → _generate_memo() + _generate_pdf_bytes()
PDF regenerates on every "Generate Deal Memo" click
```

---

## AI Agent Architecture

### Tool-Use Loop
```
User prompt → Claude API (tools=TOOL_SPECS)
  → model returns tool_use blocks
  → execute_tool() dispatches to Python
  → results appended as tool_result messages
  → loop until end_turn (max 8 turns)
  → final text response
```

### Three Committee Agents
| Agent | Bias | Mandatory tools |
|-------|------|----------------|
| Riley Chen (Bull) | Upside / growth | ≥ 2 tools |
| Marcus Webb (Bear) | Risk / decline | `investigate_anomalies` + ≥ 1 other |
| Sarah Kim (PM) | Balanced verdict | None (reads Bull + Bear) |

### Five Tools
| Tool | Returns |
|------|---------|
| `get_production_history` | Latest month, 12m avg, YoY, trend |
| `forecast_basin` | Prophet yhat, annual total, 80% CI, CAGR |
| `get_kpi_snapshot` | Full KPI suite |
| `compare_basins` | All 7 basins ranked + RPI |
| `investigate_anomalies` | Z-score anomalies + event calendar |

---

## Key Engineering Decisions

| Decision | Why |
|----------|-----|
| `@st.fragment` on forecast chart | Slider reruns only the chart fragment, not full Prophet+compare |
| `_pending_basin` two-rerun pattern | Avoids Streamlit widget key conflict on map click |
| `_chat_pending` flag + get/del | Chat input stays visible; avoids session_state.pop() race |
| `live_fetch=True` default | Streamlit Cloud has no pre-seeded Parquet files |
| `cache_control: ephemeral` on system prompts | Cuts token cost on multi-turn loops |
| fpdf2 latin-1 sanitisation | Helvetica only supports latin-1; em-dash crashes without sanitisation |
| Arps bisection IRR | Bisection on [0,10] — reliable without scipy dependency |
