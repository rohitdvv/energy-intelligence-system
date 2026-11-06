# KPI Definitions

All KPIs computed in `src/kpi/metrics.py`. Surfaced in Overview tab metric cards, comparison table, and as tool outputs to AI agents.

---

## 1. Projected Production Estimate

**What:** Annual total production for a basin in the selected year.

**Units:** Mbbls/yr (oil) or MMcf/yr (natural gas)

**Calculation:**
- Historical year (≤ cutoff): sum of `y_actual` monthly values for that calendar year
- Forecast year (> cutoff): sum of `y_forecast` for that calendar year, from the model chosen for this basin and horizon (see Forecast model selection below)

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

**What:** Estimated gross monthly revenue in $M at the user's price assumption (WTI for oil, Henry Hub for gas).

**Formulas:**
- Oil: `Mbbls × 1,000 bbl/Mbbl × WTI_$/bbl ÷ 1,000,000`
- Gas: `MMcf × 1,000 Mcf/MMcf × HenryHub_$/MMBtu × 1.036 MMBtu/Mcf ÷ 1,000,000`

Gas used to be priced at WTI / 6, which is energy parity rather than a market price. At $75 WTI that is $12.50/Mcf, three to four times typical Henry Hub prices.

UI displays annualised: `$M/mo × 12 = $M/yr`

**Limitation:** Gross wellhead revenue only — no royalties, opex, transport differentials, or taxes.

**Answers:** "What dollar opportunity does this basin represent at my price assumption?"

---

## 6. Relative Performance Index (RPI)

**What:** 0–100 normalised score of projected production vs. all 7 peer basins.

**Formula:** `(basin_value - min_peer) / (max_peer - min_peer) × 100`

Highest-producing basin = 100, lowest = 0. Relative ranking only — not an absolute quality score.

It re-expresses size only: the largest basin is always 100 and the smallest 0.

**Answers:** "Where does this basin rank in the peer set this year?"

---

## 7. Well Economics KPIs (Economics page)

Computed by `src/models/well_economics.py`; the page is `src/ui/economics.py`.
Oil wells are modelled in barrels at the WTI assumption, gas wells in Mcf at Henry Hub × 1.036.

**Decline.** The input is the effective first-year decline `De` (70% means the rate after a year is 30% of the initial rate). It converts to the nominal rate the hyperbolic curve needs:

- `b > 0`: `Di = ((1 − De)^(−b) − 1) / b`
- `b = 0`: `Di = −ln(1 − De)`

`q(t) = qi / (1 + b·Di·t)^(1/b)` until the instantaneous decline `Di / (1 + b·Di·t)` falls to the terminal rate `Dlim = −ln(1 − Dmin)` (default `Dmin` = 8%/yr), then exponential at `Dlim`. Monthly volumes are integrals of the rate over each month, from the closed-form cumulative curve.

**Cash flow.** `NCF = volume × (price × (1 − royalty) × (1 − production tax) − variable cost) − fixed cost`. The well is shut in from the first month NCF is negative. CAPEX at month 0; end-of-month discounting at `(1 + r)^(1/12) − 1`.

| KPI | Formula | What it answers |
|-----|---------|-----------------|
| EUR | Sum of monthly volumes over the forecast period, up to the economic limit | Volume the well produces |
| NPV | `−capex + Σ NCF_t / (1 + r_m)^t` | Is this well worth drilling at this discount rate? |
| IRR | Bisection for NPV = 0 on [0, 10] annual | What return does this well generate? |
| Payback | First month cumulative NCF ≥ capex | How long until capital is returned? |
| Breakeven @ r | Price at which NPV at the discount rate is zero (bisection, full model) | Below what price does the well destroy value? |
| Breakeven, undiscounted | Same at r = 0 | Below what price does the well never repay its cost? |

Not modelled: secondary-product revenue (gas and NGL for oil wells, condensate for gas wells), price differentials, workovers, abandonment cost, working interest below 100%, and income tax. Basin defaults are illustrative round numbers, not sourced type curves.

---

## Data Sources

| KPI | Source | Endpoint | Unit |
|-----|--------|----------|------|
| Production | EIA Open Data API v2 | `petroleum/crd/crpdn/data` (oil), `natural-gas/prod/sum/data` (gas) | Mbbls/mo, MMcf/mo |
| WTI price | EIA spot / FRED WTISPLC | `petroleum/pri/spt/data` | $/bbl |
| Henry Hub price | User assumption (default $3.50) | N/A | $/MMBtu |
| Well inputs | User-entered / basin defaults | N/A | Various |

**Basin data:** basins are EIA's regional production estimates from the Short-Term Energy Outlook (`steo` route, series `COPR<code>` for crude oil in million b/d and `NGMP<code>` for marketed gas in Bcf/d; codes PM, BK, EF, HA, AP). EIA builds each region from the counties over the play, so regions do not overlap. Rates are converted to monthly volumes (`rate × 1,000 × days in month`), and months after the latest month of EIA's reported U.S. production are dropped because STEO also carries EIA's forecast. Appalachia covers the Marcellus and Utica. Anadarko has no STEO region, so it is still the Oklahoma state total and is labelled "OK state data" everywhere.

Until November 2026 basins were sums of whole states: Eagle Ford was all of Texas and Permian was Texas plus New Mexico, which overstated the six rows by about 60% on 2025 oil.

---

## Forecast model selection

Computed by `src/models/selection.py`. Three models are backtested for every basin and fuel:

| Model | What it does |
|---|---|
| Prophet trend | Trend with changepoints × yearly seasonality, fitted to production only |
| EIA well activity | `rate[t+1] = rate[t] + new − d × rate[t]`, where `new` is the mean of EIA's new-well production (`CONW<code>` kb/d, `NGNW<code>` MMcf/d) over the 12 months to the origin and `d` is the mean of `−legacy_change / rate` (`COEOP<code>`, `NGEOP<code>`) over the same months. Heads towards `new / d`. Not available for Anadarko |
| Repeat last year | The last 12 months repeated (the flat baseline) |

For a forecast k years past the cutoff, the model with the lowest mean absolute error on annual totals at k years ahead is used (k capped at 3). Ties at one decimal go to the simpler model. Models that could not be tested on all the same past years are not compared. If the chosen model cannot run from the user's cutoff (for example, EIA's activity data starts later), the next best is used and the page says so. The same windows choose the model and report its error, so the chosen model's reported error is slightly optimistic.

Backtest annual error (%) on EIA regional data, November 2026, eight year-end origins 2017–2024:

| Series | Prophet 1/2/3 yr | Well activity 1/2/3 yr | Repeat 1/2/3 yr |
|---|---|---|---|
| Permian oil | 8.2 / 14.7 / 16.9 | 4.8 / 10.5 / 12.3 | 10.9 / 19.1 / 26.1 |
| Bakken oil | 12.8 / 26.1 / 33.7 | 8.7 / 20.2 / 27.9 | 9.1 / 12.9 / 12.7 |
| Eagle Ford oil | 10.7 / 20.9 / 25.6 | 5.6 / 13.2 / 17.8 | 6.2 / 10.8 / 11.1 |
| Haynesville gas | 10.4 / 19.1 / 22.4 | 5.3 / 10.0 / 14.9 | 12.2 / 19.5 / 23.1 |
| Appalachia gas | 3.7 / 8.0 / 12.5 | 2.6 / 3.3 / 4.5 | 5.0 / 8.4 / 11.3 |
| Permian gas | 5.3 / 9.0 / 10.2 | 4.1 / 10.2 / 14.6 | 14.3 / 25.9 / 35.3 |
| Anadarko oil (OK state) | 18.3 / 36.6 / 49.8 | n/a | 10.2 / 16.6 / 16.2 |

The well-activity model wins at one year almost everywhere. For oil outside the Permian, repeating the last year is still the best guess at two and three years, and the app says so rather than showing a trend.

## Forecast reliability and bands

Computed by `src/models/calibration.py`. The chosen model is refit at each of the last eight year-ends using only data up to that point, and its misses over the next three years are recorded as `|actual − forecast| / forecast`.

- **80% band** at horizon year k: the split-conformal quantile of those misses, the `⌈0.8(n+1)⌉`-th smallest of n. Band = forecast × (1 ± q_k). Monthly bands pool monthly misses by horizon year; annual bands use annual-total misses. Past three years, `q_k = q_3 × √(k/3)` (an assumption). Prophet's own interval is no longer shown: it covered about half of actual months against a nominal 80%.
- **Baselines** scored on the same windows: seasonal naive (the last 12 months repeated) for monthly figures, flat (the last full year repeated) for annual totals. The Forecast page and the agents' `forecast_basin` tool say whether the model has beaten the flat baseline at that horizon.
