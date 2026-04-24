# KPI Definitions

All KPIs are computed in `src/kpi/metrics.py` and surfaced in the Overview tab. Each is also available to the AI agents as structured tool outputs.

---

## Tier 1 — Required KPI

### Projected Production Estimate

**What it measures:** Annual total production for a basin in a selected year, expressed in the commodity's native unit.

**Units:** Mbbls/yr (oil) or MMcf/yr (natural gas)

**Calculation:**
- **Historical year** (year ≤ cutoff year): Sum of all monthly `y_actual` values for that calendar year from the Prophet result DataFrame.
- **Forecast year** (year > cutoff year): Sum of all monthly `y_forecast` (Prophet `yhat`) values for that calendar year.

**Source label:** "actual" or "forecast" is shown alongside the value so the distinction is never hidden.

**Business decision support:** Answers the primary question — "What is this basin expected to produce in my investment year?" All downstream KPIs and revenue figures are anchored to this estimate.

---

## Tier 2 — Custom KPIs

### YoY Growth Rate

**What it measures:** Year-over-year percentage change in annual total production.

**Formula:** `(year_total - prior_year_total) / prior_year_total × 100`

**Business decision support:** A basin growing at 8% YoY competes differently for capital than one declining at 3%. Growth rate contextualises whether the basin is in expansion, plateau, or decline phase. A value above +2% is classified "increasing"; below -2% is "decreasing"; otherwise "flat".

---

### Production Decline Rate (CAGR)

**What it measures:** Compound annual growth rate (CAGR) computed over the most recent 3 years of historical data.

**Formula:** `((end_value / start_value)^(1 / n_years) - 1) × 100`

**Interpretation:** A negative value indicates decline. A -5% CAGR over 3 years means the basin is compounding downward — relevant for mature fields where well productivity is falling faster than new completions can offset.

**Business decision support:** Investors underwriting a 5-year hold need to know if current production levels are structurally sustained or in structural decline. This KPI exposes the recent trajectory separate from forecast assumptions.

---

### Volatility Score (CV%)

**What it measures:** Coefficient of variation of monthly production after detrending by a 12-month rolling mean, expressed as a percentage.

**Formula:** `std(residuals) / mean(series) × 100`

The 12-month rolling mean is subtracted first to remove secular trend, leaving only cyclical noise and irregular shocks. This prevents a growing basin from appearing artificially volatile simply because its mean is rising.

**Interpretation ranges:**
- < 5% — very stable
- 5–15% — stable
- 15–30% — moderate volatility
- > 30% — high volatility

**Business decision support:** Volatile basins are operationally riskier — weather events, infrastructure outages, or drilling permitting delays create wide month-to-month swings that complicate cash flow forecasting. A lower CV% basin is a more predictable underwrite.

---

### Revenue Potential

**What it measures:** Estimated gross monthly revenue in USD millions, derived from the projected monthly production and the user-selected WTI price assumption.

**Formulas:**
- **Oil:** `production_Mbbls × 1,000 bbl/Mbbl × WTI_$/bbl ÷ 1,000,000`
- **Gas:** `production_MMcf × 1,000 Mcf/MMcf × (WTI/6)_$/Mcf ÷ 1,000,000`
  - Gas price uses BTU parity: 1 Mcf ≈ WTI/6, a rough Henry Hub proxy.

The UI displays this annualised (`Rev $M/mo × 12 = Rev $M/yr`).

**Limitation:** This is gross revenue at the wellhead price assumption. It does not subtract royalties, operating expense, transportation differentials, or taxes. It is an opportunity-sizing metric, not a net present value.

**Business decision support:** Converts production volumes into a dollar-denominated figure analysts can quickly compare across basins and WTI scenarios. The interactive WTI slider in the sidebar stress-tests revenue under different price environments in real time.

---

### Relative Performance Index (RPI)

**What it measures:** A 0–100 normalised score of a basin's projected annual production relative to the full peer set of 7 basins in the same fuel type and target year.

**Formula:** `(basin_value - min_peer) / (max_peer - min_peer) × 100`

The highest-producing basin always scores 100; the lowest always scores 0.

**Interpretation:** RPI is a relative ranking tool, not an absolute value. A basin with RPI 75 is in the top quartile of this peer set for the selected year — it does not mean the basin is "75% good" in absolute terms.

**Business decision support:** Helps a business development analyst quickly identify which basins deserve further diligence vs. which lag the peer group. The RPI bar chart in the Overview tab highlights the selected basin in orange for instant visual reference.

---

## Data Provenance

| KPI | Source | Endpoint | Unit at source | Transformation |
|-----|--------|----------|----------------|---------------|
| Projected Production | EIA Open Data API v2 | `petroleum/crd/crpdn/data` (oil) or `natural-gas/prod/sum/data` (gas) | Mbbls/month (oil), MMcf/month (gas) | Aggregate state duoareas → basin; Prophet yhat for future years |
| WTI Price (Revenue) | EIA spot prices | `petroleum/pri/spt/data`, series RWTC | $/bbl | Monthly average |
| All KPIs above | Derived from production series | — | — | See formulas above |

Production data is refreshed from EIA when no local Parquet file exists (live-fetch) or when the user clicks "Refresh data" in the sidebar. The last-fetched timestamp is implicit in the Parquet file's modification time.
