# Walkthrough Guide

**Video Link:** <!-- Add your Loom or YouTube link here before submission -->

---

## Suggested 5-Minute Demo Script

### 0:00 — What you built (30 sec)
"This is the Energy Intelligence System — a full-stack AI platform for U.S. oil & gas basin analysis. It connects to live government data from the EIA and FRED, runs ML forecasting, and uses AI agents to produce investment-grade analysis in seconds instead of weeks."

### 0:30 — Overview Tab (60 sec)
1. Show sidebar: basin selector, fuel type, target year slider, WTI assumption
2. Point to KPI cards — "These four numbers update for any basin + year combination in real time"
3. Scroll to bubble chart: "This is the Growth vs Risk matrix — right means more revenue, up means faster growing, green means stronger overall. The selected basin is highlighted."
4. Change target year from 2026 to 2028 — watch all charts update

### 1:30 — Forecast Tab (60 sec)
1. "Blue is real historical EIA data going back to 2005. Orange is the Prophet forecast. The shaded band is the 80% confidence interval."
2. Hover over a red anomaly dot — show the event label (e.g. COVID-19, Hurricane)
3. Drag the cutoff year slider left — "This shows what the model would have predicted if we only had data up to 2019 — a validation tool for forecast credibility"

### 2:30 — Map Tab (30 sec)
1. Click a basin marker on the map — "The whole app switches to that basin"
2. Change the overlay metric — show RPI vs Production vs Growth

### 3:00 — Chat Tab (45 sec)
1. Type: "Which basin has the best oil investment case for 2027?"
2. Point out the `[DATA]` tags: "Every number it states is backed by a live EIA tool call"
3. Follow up: "Why did production drop in 2020?" — show the conversation memory

### 3:45 — Committee Tab (45 sec)
1. Click "Run Committee" — show the live status updates as agents run
2. Show Bull thesis, Bear thesis, then PM verdict badge (PURSUE / WATCH / PASS)
3. "Three AI agents debated using real production data — every claim cites a tool call"

### 4:30 — Memo Tab (20 sec)
1. Click "Generate Deal Memo"
2. Click "Download PDF" — open the PDF in preview
3. "This is what an analyst would write in three days — generated in 60 seconds"

### 4:50 — Economics Tab (10 sec)
1. Adjust IP rate slider — show NPV/IRR update
2. "Well-level profitability using the Arps decline curve, the industry standard for reserve estimation"

---

## Key Numbers to Highlight

- **7 basins** × 2 fuel types × 20+ years of data = 2,800+ monthly data points
- **5 AI tools** called per agent turn, grounding every claim in live data
- **3 AI agents** debate every investment thesis before a verdict is issued
- **80% CI** on every forecast — explicit uncertainty quantification
- **Prophet MAPE** shown in Forecast tab methodology expander — model is validated, not a black box

---

## One Investment Insight to Demo

Pick the highest-RPI basin for Oil in 2027 from the Overview table. Open the Committee tab and run a debate. When the PM issues PURSUE/WATCH/PASS with conviction level, note: "In a real fund, this analysis would have taken an analyst 2–3 days and cost $5,000–$10,000 in analyst time. This system produces it in under 2 minutes using the same underlying data."
