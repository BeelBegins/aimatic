# Governed AI Business Intelligence

The AI Assistant is a read-only decision-support layer. The language model may interpret a
question, resolve business entities and dates, select a server-approved tool, and explain the
returned facts. It cannot provide SQL, calculate figures, choose a forecasting model, change
prices, move stock, or write business transactions.

## Request and response flow

1. A constrained analysis plan records intent, domain, metric, dimensions, entities, current and
   comparison periods, ranking, forecast horizon, confidence requirement, and route preference.
2. `routing_engine.py` scores only registered sources and exposes a narrow candidate set in this
   order: certified tool, governed analytics, approved report, then whitelisted dynamic report.
3. Each call is preserved as a separate invocation with its call ID, arguments, result, route,
   sequence, period role, and scenario. A valid zero result stops fallback routing.
4. Server code builds every KPI, variance, driver, recommendation, chart, warning, table,
   confidence score, and drill-down.
5. The model may summarize those facts but cannot introduce a number absent from tool output.

Saved reports, dashboard snapshots, refreshes, and exports retain the invocation list. The
existing snapshot field is reused, so this release requires no schema migration.

## Certified calculations

### ABC and XYZ

- ABC contribution is the selected non-negative metric divided by the full filtered total.
- A contains items encountered before the cumulative 80% boundary; B covers the next 15%; C is
  the remainder. Thresholds are configurable within server limits.
- XYZ uses zero-filled calendar months. X requires at least six periods, 75% demand frequency,
  and coefficient of variation no greater than 0.30. Y requires at least four periods, 50%
  frequency, and coefficient of variation no greater than 1.00. Other sufficiently active items
  are Z; short-history items are explicitly `Insufficient`.
- Item-level net sales reconcile to certified Sales Overview for the same dates and branch.

### Demand forecasting

Submitted POS invoice items are aggregated into complete daily, weekly, or monthly series.
Returns are netted, missing periods become zero, observed stockout zeroes are conservatively
imputed, and isolated high outliers are capped for model fitting.

Eligible models are naive, seasonal naive, moving average, exponential smoothing, TSB
intermittent demand, and seasonal average. Rolling-origin one-step backtesting selects the lowest
WAPE model; ties use MAE and a stable model-name order. Intervals use backtest residual scale.

- WAPE = `sum(abs(actual - forecast)) / sum(abs(actual))`
- MAE = `mean(abs(actual - forecast))`
- Bias = `mean(forecast - actual)`
- Safety stock = `1.65 × residual sigma × sqrt(lead-time periods)`
- Reorder point = `lead-time demand + safety stock`
- Suggested reorder = `max(0, reorder point - available stock - incoming stock)`

Short history returns an explicit fallback, low confidence, and the additional period count
required. Forecast results are calculation-versioned and cached for six hours.

### Price recommendations

The service is advisory only and always displays:

> Decision-support recommendation — not an automatic price update.

Hard constraints are applied in server code:

- Price floor = `tax-inclusive cost / (1 - minimum margin percentage)`
- Price never exceeds a positive MRP ceiling.
- Price movement is bounded by the configured maximum unless a hard floor or MRP rule must take
  precedence.
- Retail rounding cannot move a result outside the hard bounds.
- Missing certified cost causes a limitation when a minimum margin must be enforced; no scenario
  is returned.

Elasticity uses a log-log regression only with at least eight usable periods, three distinct
prices, at least 3% price variation, acceptable fit, and limited promotion/stockout distortion.
Otherwise, the three scenarios use a labeled deterministic rule method and low confidence.
No code path updates Item Price or another business document.

### Transfers and additional intelligence

- Transfers match item-level stock above target cover to the same item's deficit at another
  branch. Allocated surplus cannot be reused. No Stock Entry is created.
- Promotion effectiveness scales the pre-period daily baseline to the promotion duration and
  reports incremental units, revenue, certified Stock Ledger margin where available,
  cannibalization, post-period change, and ROI.
- RFM uses population quintiles for recency, frequency, and monetary value, followed by explicit
  segment and engagement rules.
- Basket analysis is capped at 5,000 transactions and 30 items per transaction. Pairs below the
  minimum sample, support, or confidence are excluded. Lift is
  `P(A and B) / (P(A) × P(B))`.
- Vendor reliability combines on-time delivery (40%), fill rate (35%), rejection quality (15%),
  and lead-time consistency (10%).
- Anomalies compare the latest observation with a historical mean and population standard
  deviation. The default threshold is 2.5 standard deviations. Each result includes its actual
  value, expected range, variance, severity, source, and drill-down metadata.

## Confidence and data quality

Overall answer confidence combines tool success, bounded row volume, coverage, completeness,
reconciliation, backtest accuracy, outlier rate, and route reliability. Forecast rows contribute
their own WAPE and confidence. An unbacktested forecast receives low accuracy rather than a
placeholder score.

Warnings are emitted for missing cost, short history, weak elasticity, promotions, observed
stockouts, intermittent demand, outliers, partial row caps, failed reconciliation, and tool
errors. Unknown values are returned as unavailable, never as invented zeroes.

## Example questions

- “Compare this month’s net sales with last month.”
- “Classify beverages into ABC and XYZ and show A items at stockout risk.”
- “Forecast this item for four weeks and calculate its reorder point.”
- “Compare conservative, recommended, and aggressive price scenarios.”
- “Which branch can transfer this item?”
- “Was this promotion effective after margin and cannibalization?”
- “Show dormant high-value customers.”
- “Which item pairs have lift above one?”
- “Rank vendors by reliability.”
- “Show the source transactions behind the sales anomaly.”

The 120-case evaluation corpus lives in `aimatic/ai/golden_questions.py`, with 20 simple,
comparison, diagnostic, forecasting, inventory, and pricing questions. Each case declares the
expected route, tool, required parameters, structured output, no-invention requirement, and
insufficient-data behavior.
