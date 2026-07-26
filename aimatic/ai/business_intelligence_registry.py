"""Metadata for advanced certified decision-support tools."""

from aimatic.ai.report_registry import DataSource

BUSINESS_DATA_SOURCES = {
	"get_abc_xyz_analysis": DataSource(
		key="tool:get_abc_xyz_analysis",
		name="ABC/XYZ Inventory Analysis",
		description=(
			"Certified cumulative-contribution ABC and demand-consistency XYZ classification "
			"with stock, margin, reconciliation, confidence, and risk lists."
		),
		source_type="tool",
		supported_filters=[
			"date_from",
			"date_to",
			"branch",
			"warehouse",
			"item_group",
			"brand",
			"supplier",
			"abc_metric",
			"abc_a_threshold",
			"abc_b_threshold",
			"xyz_metric",
			"minimum_activity",
			"limit",
		],
		returned_fields=[
			"items",
			"summaries",
			"high_value_irregular_items",
			"c_items_with_excess_stock",
			"a_items_at_stockout_risk",
			"reconciliation",
		],
		supported_visualizations=["kpi", "pareto", "bar", "heatmap", "scatter", "table"],
		example_questions=[
			"Classify inventory into ABC and XYZ groups",
			"Show A-class items at stockout risk",
			"Which C items hold excessive stock?",
			"Find high-value items with irregular demand",
		],
		estimated_cost="medium",
		refresh_behavior="cached",
	),
}

from aimatic.ai.demand_forecasting_registry import FORECAST_DATA_SOURCES

BUSINESS_DATA_SOURCES.update(FORECAST_DATA_SOURCES)

from aimatic.ai.price_recommendation_registry import PRICE_DATA_SOURCES

BUSINESS_DATA_SOURCES.update(PRICE_DATA_SOURCES)
