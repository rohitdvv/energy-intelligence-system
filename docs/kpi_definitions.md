# KPI Definitions

All KPIs computed in `src/kpi/metrics.py`. Surfaced in Overview tab metric cards, comparison table, and as tool outputs to AI agents.

---

## 1. Projected Production Estimate

**What:** Annual total production for a basin in the selected year.

**Units:** Mbbls/yr (oil) or MMcf/yr (natural gas)

**Calculation:**
- Historical year (≤ cutoff): sum of `y_actual` monthly values for that calendar year
- Forecast year (> cutoff): sum of `y_forecast` (Prophet yhat) for that calendar year

**Answers:** "What is this basin expected to produce in my investment year?"

---

## 2. YoY Growth Rate

**What:** Year-over-year % change in annual production.

**Formula:** `(year_total - prior_year_total) / prior_year_total × 100`

- For future target years: uses forecast `y_forecast` values for both years
- For historical years: uses actual `y_actual` values

**Classification:** > +2% = "increasing", < -2% = "decreasing", otherwise "flat"

**Answers:** "Is this basin growing, plateauing, or declining?"

---

## 3. Production Decline Rate (CAGR)

**What:** Compound annual growth rate over the most recent 3 years of historical data.

**Formula:** `((end_value / start_value)^(1/3) - 1) × 100`

Negative = structural decline. A -5% CAGR means production is compounding downward — new completions are not offsetting natural well decline.

**Answers:** "Is the recent production trajectory sustainable?"

---

## 4. Volatility Score (CV%)

**What:** Coefficient of variation of monthly production after 12-month detrending.

**Formula:** `std(y - rolling_mean_12) / mean(y) × 100`

The 12-month rolling mean is subtracted first to isolate cyclical noise from secular trend.

**Interpretation:**
- < 5% — very stable
- 5–15% — stable
- 15–30% — moderate
- > 30% — high volatility

**Answers:** "How predictable is month-to-month production for cash flow planning?"

---

## 5. Revenue Potential

**What:** Estimated gross monthly revenue in $M at the user's WTI assumption.

**Formulas:**
- Oil: `Mbbls × 1,000 bbl/Mbbl × WTI_$/bbl ÷ 1,000,000`
- Gas: `MMcf × 1,000 Mcf/MMcf × (WTI/6)_$/Mcf ÷ 1,000,000` (BTU parity proxy)

UI displays annualised: `$M/mo × 12 = $M/yr`

**Limitation:** Gross wellhead revenue only — no royalties, opex, transport differentials, or taxes.

**Answers:** "What dollar opportunity does this basin represent at my price assumption?"

---

## 6. Relative Performance Index (RPI)

**What:** 0–100 normalised score of projected production vs. all 7 peer basins.

**Formula:** `(basin_value - min_peer) / (max_peer - min_peer) × 100`

Highest-producing basin = 100, lowest = 0. Relative ranking only — not an absolute quality score.

**Answers:** "Where does this basin rank in the peer set this year?"

---

## 7. Well Economics KPIs (Economics Tab)

Computed by `src/ui/economics.py` using Arps hyperbolic decline:

| KPI | Formula | What it answers |
|-----|---------|-----------------|
| EUR | `sum of monthly production over well life` | Total reserves recoverable |
| NPV | `-capex + sum(net_cf / (1+r_monthly)^t)` | Is this well worth drilling at this discount rate? |
| IRR | Bisection on NPV=0 over [0,10] annual rate | What return does this well generate? |
| Payback | Month when cumulative net CF turns positive | How long until capital is returned? |
| Breakeven price | WTI at which NPV = 0 | Below what price does this well lose money? |

**Arps hyperbolic decline:** `q(t) = q_i / (1 + b × D_i × t)^(1/b)`
Falls back to exponential when b < 0.01.

---

## Data Sources

| KPI | Source | Endpoint | Unit |
|-----|--------|----------|------|
| Production | EIA Open Data API v2 | `petroleum/crd/crpdn/data` (oil), `natural-gas/prod/sum/data` (gas) | Mbbls/mo, MMcf/mo |
| WTI price | EIA spot / FRED WTISPLC | `petroleum/pri/spt/data` | $/bbl |
| Well inputs | User-entered / basin defaults | N/A | Various |

**Texas proxy limitation:** EIA duoarea `STX` covers all Texas production. Permian and Eagle Ford both map to Texas-level aggregates — not true basin-specific volumes. This is documented in `src/data/eia.py`.
