"""Registry metadata for governed sales-price recommendations."""

from aimatic.ai.report_registry import DataSource

PRICE_DATA_SOURCES = {
	"get_price_recommendation": DataSource(
		key="tool:get_price_recommendation",
		name="Governed Sales-Price Recommendation",
		description=(
			"Read-only retail price scenarios with hard margin, floor, MRP, branch "
			"price-list, maximum-change, tax-inclusive, and rounding constraints."
		),
		source_type="tool",
		supported_filters=[
			"item_code",
			"branch",
			"customer_group",
			"history_months",
			"minimum_margin_pct",
			"maximum_price_change_pct",
			"objective",
			"include_scenarios",
		],
		returned_fields=[
			"current_price",
			"cost",
			"mrp",
			"price_floor",
			"elasticity_estimate",
			"recommendation_method",
			"scenarios",
			"constraint_warnings",
		],
		supported_visualizations=["kpi", "bar", "table"],
		example_questions=[
			"Recommend a selling price for this item",
			"Compare conservative and aggressive price scenarios",
			"Can I clear this aging stock without violating margin?",
			"Is this item's price elasticity reliable?",
		],
		estimated_cost="high",
		refresh_behavior="cached",
	),
}
