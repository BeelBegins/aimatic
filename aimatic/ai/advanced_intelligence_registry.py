"""Registry metadata for the remaining certified intelligence engines."""

from aimatic.ai.report_registry import DataSource


def _source(name, description, filters, fields, examples, cost="medium"):
	return DataSource(
		key=f"tool:{name}",
		name=name.replace("get_", "").replace("_", " ").title(),
		description=description,
		source_type="tool",
		supported_filters=filters,
		returned_fields=fields,
		supported_visualizations=["kpi", "bar", "table"],
		example_questions=examples,
		estimated_cost=cost,
		refresh_behavior="cached",
	)


ADVANCED_DATA_SOURCES = {
	"get_branch_transfer_recommendations": _source(
		"get_branch_transfer_recommendations",
		"Read-only branch stock transfers from measured surplus to measured deficit with confidence and impact.",
		["item_code", "item_group", "brand", "history_days", "target_cover_days", "minimum_transfer_qty", "limit"],
		["recommendations", "transfer_qty", "expected_avoided_stockout_days", "expected_dead_stock_reduction"],
		["Which branch can transfer this stock?", "Recommend stock transfers between branches"],
		"high",
	),
	"get_promotion_effectiveness": _source(
		"get_promotion_effectiveness",
		"Certified baseline, promotion, and post-period effectiveness with margin, cannibalization, and ROI.",
		["item_code", "promotion_from", "promotion_to", "branch", "baseline_days", "post_days"],
		["incremental_units", "incremental_revenue", "incremental_margin", "cannibalization", "promotion_roi_pct"],
		["Was this promotion effective?", "Calculate promotion ROI and cannibalization"],
	),
	"get_customer_rfm_segments": _source(
		"get_customer_rfm_segments",
		"Certified customer RFM segments, churn risk, high-value customers, dormant customers, and engagement categories.",
		["branch", "customer_group", "history_days", "minimum_transactions", "limit"],
		["segments", "recency_days", "frequency", "monetary_value", "customer_segment", "churn_risk"],
		["Segment customers by RFM", "Show dormant high-value customers"],
	),
	"get_market_basket_analysis": _source(
		"get_market_basket_analysis",
		"Bounded market-basket pairs protected by minimum transaction, support, and confidence thresholds.",
		["branch", "history_days", "minimum_transactions", "minimum_support", "minimum_confidence", "limit"],
		["pairs", "support", "confidence", "lift", "recommended_cross_sell"],
		["What items are bought together?", "Recommend cross-sell combinations"],
		"high",
	),
	"get_vendor_reliability": _source(
		"get_vendor_reliability",
		"Certified vendor lead time, fill rate, late delivery, rejection, price trend, concentration, and reliability.",
		["supplier", "history_days", "minimum_orders", "limit"],
		["vendors", "price_trend_pct", "fill_rate_pct", "on_time_delivery_pct", "reliability_score"],
		["Rank vendors by reliability", "Which supplier delivers late or short?"],
		"high",
	),
	"get_business_anomalies": _source(
		"get_business_anomalies",
		"Deterministic sales, return, discount, transaction, branch, and negative-stock anomalies with ranges and drill-down.",
		["branch", "lookback_days", "z_threshold", "limit"],
		["anomalies", "actual_value", "expected_low", "expected_high", "variance", "severity", "data_source"],
		["Detect unusual business activity", "Why did sales drop unexpectedly?", "Show negative stock anomalies"],
	),
}
