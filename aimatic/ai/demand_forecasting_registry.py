"""Registry metadata for the certified demand-forecast service."""

from aimatic.ai.report_registry import DataSource

FORECAST_DATA_SOURCES = {
	"get_demand_forecast": DataSource(
		key="tool:get_demand_forecast",
		name="Demand Forecast and Stock Plan",
		description=(
			"Certified POS demand forecast selected through rolling backtesting, "
			"with intervals, confidence, stockout observations, and optional stock planning."
		),
		source_type="tool",
		supported_filters=[
			"item_code",
			"item_group",
			"brand",
			"branch",
			"warehouse",
			"granularity",
			"history_months",
			"forecast_horizon",
			"include_stock_plan",
			"limit",
		],
		returned_fields=[
			"forecasts",
			"selected_model",
			"forecast_quantity",
			"lower_confidence_bound",
			"upper_confidence_bound",
			"wape",
			"mae",
			"bias",
			"forecast_confidence",
			"stock_plan",
		],
		supported_visualizations=["kpi", "line", "table"],
		example_questions=[
			"Forecast this item's demand for the next four weeks",
			"Which items may stock out next month?",
			"Calculate safety stock and reorder quantities",
			"Show low-confidence demand forecasts",
		],
		estimated_cost="high",
		refresh_behavior="cached",
	),
}
