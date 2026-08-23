"""Golden evaluation corpus for governed conversational BI.

The corpus is generated from explicit business-question families so it remains
reviewable while guaranteeing 20 cases in each required category.
"""

from __future__ import annotations

_BRANCHES = ["Ghouri Town", "Blue Area", "I-8", "F-10", "Rawalpindi"]
_PERIODS = ["today", "this week", "this month", "last month"]


def _case(category, question, tool, required_parameters, output_type, insufficient_data=False):
	return {
		"category": category,
		"question": question,
		"expected_route": "certified_tool",
		"expected_tool": tool,
		"required_parameters": required_parameters,
		"no_invented_figures": True,
		"structured_output_type": output_type,
		"insufficient_data_behavior": (
			"explicit_limitation" if insufficient_data else "valid_zero_or_factual_result"
		),
	}


SIMPLE_QUESTIONS = (
	[
		_case(
			"simple", f"What were net sales {period}?", "get_sales_overview", ["date_from", "date_to"], "kpi"
		)
		for period in _PERIODS
	]
	+ [
		_case(
			"simple",
			f"Show top selling items at {branch}.",
			"get_top_selling_items",
			["branch", "date_from", "date_to"],
			"table",
		)
		for branch in _BRANCHES
	]
	+ [
		_case("simple", question, tool, parameters, output)
		for question, tool, parameters, output in [
			(
				"How much did customers return this month?",
				"get_returns_overview",
				["date_from", "date_to"],
				"kpi",
			),
			(
				"Show sales by item group this month.",
				"get_sales_by_item_group",
				["date_from", "date_to"],
				"chart",
			),
			("What is current cash and bank balance?", "get_cash_and_bank_balance", [], "kpi"),
			("Show outstanding payables.", "get_outstanding_payables_overview", [], "kpi"),
			("Show receivables aging.", "get_receivables_aging", [], "table"),
			("Which items are selling below cost?", "get_selling_below_cost", [], "table"),
			("Show negative stock now.", "get_negative_stock_check", [], "table"),
			("Show purchase totals this month.", "get_purchase_overview", ["date_from", "date_to"], "kpi"),
			(
				"Show this month's expense breakdown.",
				"get_expense_breakdown",
				["date_from", "date_to"],
				"chart",
			),
			("Show payment-mode split today.", "get_payment_mode_split", ["date_from", "date_to"], "chart"),
			("Show active POS shifts.", "get_active_shifts", [], "table"),
		]
	]
)

COMPARISON_QUESTIONS = (
	[
		_case(
			"comparison",
			f"Compare {branch} sales this month with last month.",
			"get_sales_overview",
			["branch", "date_from", "date_to", "comparison_from", "comparison_to"],
			"kpi_comparison",
		)
		for branch in _BRANCHES
	]
	+ [
		_case(
			"comparison",
			f"Compare net sales {period} with the previous period.",
			"get_sales_overview",
			["date_from", "date_to", "comparison_from", "comparison_to"],
			"kpi_comparison",
		)
		for period in _PERIODS
	]
	+ [
		_case("comparison", question, tool, parameters, "kpi_comparison")
		for question, tool, parameters in [
			(
				"Compare returns this week versus last week.",
				"get_returns_overview",
				["date_from", "date_to", "comparison_from", "comparison_to"],
			),
			(
				"Compare gross margin this month versus last month.",
				"get_gross_margin_overview",
				["date_from", "date_to", "comparison_from", "comparison_to"],
			),
			(
				"Compare purchases this quarter to the prior quarter.",
				"get_purchase_overview",
				["date_from", "date_to", "comparison_from", "comparison_to"],
			),
			(
				"Compare discounts this month with last month.",
				"get_discount_overview",
				["date_from", "date_to", "comparison_from", "comparison_to"],
			),
			(
				"Compare hourly sales for this Friday and last Friday.",
				"get_hourly_sales_pattern",
				["date_from", "date_to", "comparison_from", "comparison_to"],
			),
			(
				"Compare branch profitability this month and last month.",
				"get_branch_profit_and_loss",
				["date_from", "date_to", "comparison_from", "comparison_to"],
			),
			(
				"Compare expense categories this quarter versus last quarter.",
				"get_expense_breakdown",
				["date_from", "date_to", "comparison_from", "comparison_to"],
			),
			(
				"Compare item-group sales this month and previous month.",
				"get_sales_by_item_group",
				["date_from", "date_to", "comparison_from", "comparison_to"],
			),
			(
				"Compare supplier purchase concentration year over year.",
				"get_purchase_concentration",
				["date_from", "date_to", "comparison_from", "comparison_to"],
			),
			(
				"Compare receivables aging with last month end.",
				"get_receivables_aging",
				["date_to", "comparison_to"],
			),
			(
				"Compare transaction count this week and last week.",
				"get_sales_overview",
				["date_from", "date_to", "comparison_from", "comparison_to"],
			),
		]
	]
)

DIAGNOSTIC_QUESTIONS = [
	_case(
		"diagnostic",
		f"Why did sales change at {branch}?",
		"get_business_anomalies",
		["branch", "lookback_days"],
		"warning_table",
	)
	for branch in _BRANCHES
] + [
	_case("diagnostic", question, tool, parameters, output)
	for question, tool, parameters, output in [
		("Detect unexpected sales drops.", "get_business_anomalies", ["lookback_days"], "warning_table"),
		("Find abnormal returns.", "get_business_anomalies", ["lookback_days"], "warning_table"),
		("Find unusual discount spikes.", "get_business_anomalies", ["lookback_days"], "warning_table"),
		("Show margin-collapse anomalies.", "get_business_anomalies", ["lookback_days"], "warning_table"),
		("Find unusual expense increases.", "get_business_anomalies", ["lookback_days"], "warning_table"),
		(
			"Find sudden supplier-price increases.",
			"get_business_anomalies",
			["lookback_days"],
			"warning_table",
		),
		(
			"Show suspicious transaction-count patterns.",
			"get_business_anomalies",
			["lookback_days"],
			"warning_table",
		),
		(
			"Why are returns high this month?",
			"get_returns_overview",
			["date_from", "date_to"],
			"driver_table",
		),
		(
			"Which branches caused the margin decline?",
			"get_branch_profit_and_loss",
			["date_from", "date_to"],
			"driver_table",
		),
		("Which suppliers deliver late or short?", "get_vendor_reliability", ["history_days"], "table"),
		("Which customers are at churn risk?", "get_customer_rfm_segments", ["history_days"], "table"),
		(
			"Which items are frequently bought together?",
			"get_market_basket_analysis",
			["history_days", "minimum_transactions"],
			"table",
		),
		(
			"Was the selected promotion effective?",
			"get_promotion_effectiveness",
			["item_code", "promotion_from", "promotion_to"],
			"kpi",
		),
		("Show all negative-stock anomalies.", "get_business_anomalies", ["lookback_days"], "warning_table"),
		("Which vendor concentration is risky?", "get_vendor_reliability", ["history_days"], "table"),
	]
]

FORECASTING_QUESTIONS = [
	_case(
		"forecasting",
		f"Forecast ITEM-{index:03d} for the next four weeks.",
		"get_demand_forecast",
		["item_code", "granularity", "history_months", "forecast_horizon"],
		"forecast",
		index in {1, 2, 3},
	)
	for index in range(1, 11)
] + [
	_case("forecasting", question, "get_demand_forecast", parameters, "forecast", insufficient)
	for question, parameters, insufficient in [
		(
			"Forecast beverages by week for eight weeks.",
			["item_group", "granularity", "forecast_horizon"],
			False,
		),
		("Forecast this brand monthly for six months.", ["brand", "granularity", "forecast_horizon"], False),
		("Forecast demand separately for Ghouri Town.", ["branch", "granularity", "forecast_horizon"], False),
		("Forecast this warehouse's top ten items.", ["warehouse", "limit", "forecast_horizon"], False),
		("Calculate safety stock for this item.", ["item_code", "include_stock_plan"], False),
		("Calculate reorder point and quantity.", ["item_code", "include_stock_plan"], False),
		("When will this item stock out?", ["item_code", "include_stock_plan"], False),
		("Forecast a brand-new item.", ["item_code", "forecast_horizon"], True),
		("Forecast an intermittent-demand item.", ["item_code", "forecast_horizon"], False),
		("Show low-confidence forecasts.", ["limit", "forecast_horizon"], True),
	]
]

INVENTORY_QUESTIONS = [
	_case(
		"inventory",
		f"Classify {group} inventory into ABC and XYZ.",
		"get_abc_xyz_analysis",
		["item_group", "date_from", "date_to"],
		"matrix",
	)
	for group in ["Beverages", "Grocery", "Household", "Personal Care", "Frozen"]
] + [
	_case("inventory", question, tool, parameters, output)
	for question, tool, parameters, output in [
		("Show A-class items at stockout risk.", "get_abc_xyz_analysis", ["date_from", "date_to"], "table"),
		(
			"Show C-class items holding excessive stock.",
			"get_abc_xyz_analysis",
			["date_from", "date_to"],
			"table",
		),
		(
			"Show high-value irregular-demand items.",
			"get_abc_xyz_analysis",
			["date_from", "date_to"],
			"table",
		),
		(
			"Classify by gross-margin contribution.",
			"get_abc_xyz_analysis",
			["abc_metric", "date_from", "date_to"],
			"matrix",
		),
		(
			"Classify by quantity sold.",
			"get_abc_xyz_analysis",
			["abc_metric", "date_from", "date_to"],
			"matrix",
		),
		(
			"Classify stock value with 70/90 thresholds.",
			"get_abc_xyz_analysis",
			["abc_metric", "abc_a_threshold", "abc_b_threshold"],
			"matrix",
		),
		(
			"Recommend branch transfers.",
			"get_branch_transfer_recommendations",
			["history_days", "target_cover_days"],
			"table",
		),
		(
			"Which branch can transfer ITEM-001?",
			"get_branch_transfer_recommendations",
			["item_code"],
			"table",
		),
		(
			"Transfer excess beverage stock between branches.",
			"get_branch_transfer_recommendations",
			["item_group"],
			"table",
		),
		(
			"Show transfer recommendations above ten units.",
			"get_branch_transfer_recommendations",
			["minimum_transfer_qty"],
			"table",
		),
		("Show dead stock by ABC class.", "get_abc_xyz_analysis", ["date_from", "date_to"], "table"),
		("Show the ABC Pareto chart.", "get_abc_xyz_analysis", ["date_from", "date_to"], "pareto"),
		(
			"Show the stock-value versus contribution scatter.",
			"get_abc_xyz_analysis",
			["date_from", "date_to"],
			"scatter",
		),
		(
			"Find items with insufficient XYZ history.",
			"get_abc_xyz_analysis",
			["date_from", "date_to"],
			"table",
		),
		(
			"Analyze one warehouse's ABC/XYZ mix.",
			"get_abc_xyz_analysis",
			["warehouse", "date_from", "date_to"],
			"matrix",
		),
	]
]

PRICING_QUESTIONS = [
	_case(
		"pricing",
		f"Recommend a price for ITEM-{index:03d} to maximize gross margin.",
		"get_price_recommendation",
		["item_code", "objective", "minimum_margin_pct", "maximum_price_change_pct"],
		"scenario_table",
		index in {1, 2, 3},
	)
	for index in range(1, 11)
] + [
	_case("pricing", question, "get_price_recommendation", parameters, "scenario_table", insufficient)
	for question, parameters, insufficient in [
		("Recommend a conservative price to improve sell-through.", ["item_code", "objective"], False),
		("Recommend a price to clear aging stock.", ["item_code", "objective"], False),
		(
			"Protect market share without violating margin.",
			["item_code", "objective", "minimum_margin_pct"],
			False,
		),
		(
			"Maintain volume with no more than 5% price movement.",
			["item_code", "objective", "maximum_price_change_pct"],
			False,
		),
		(
			"Compare conservative, recommended, and aggressive price scenarios.",
			["item_code", "include_scenarios"],
			False,
		),
		("Is elasticity reliable for this item?", ["item_code", "history_months"], True),
		("Recommend a branch-specific selling price.", ["item_code", "branch"], False),
		("Recommend a price for this customer group.", ["item_code", "customer_group"], False),
		("Enforce a 20% minimum margin.", ["item_code", "minimum_margin_pct"], False),
		("Recommend a price without exceeding MRP.", ["item_code"], False),
	]
]

GOLDEN_QUESTIONS = (
	SIMPLE_QUESTIONS
	+ COMPARISON_QUESTIONS
	+ DIAGNOSTIC_QUESTIONS
	+ FORECASTING_QUESTIONS
	+ INVENTORY_QUESTIONS
	+ PRICING_QUESTIONS
)
