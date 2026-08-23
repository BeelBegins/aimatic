"""Lightweight in-Python DataSource registry for Aimatic AI tools and Frappe reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import frappe

_ALLOWED_ROLES = frozenset({"System Manager", "Sales Manager", "Accounts Manager", "POS Supervisor"})


@dataclass(frozen=True)
class DataSource:
	key: str
	name: str
	description: str
	source_type: Literal["tool", "report", "chart", "number_card"]
	supported_filters: list[str]
	returned_fields: list[str]
	supported_visualizations: list[str]
	example_questions: list[str]
	roles: list[str] = field(default_factory=lambda: list(_ALLOWED_ROLES))
	company_scoped: bool = True
	branch_scoped: bool = True
	estimated_cost: Literal["trivial", "low", "medium", "high"] = "trivial"
	refresh_behavior: Literal["realtime", "cached"] = "realtime"


TOOL_REGISTRY: dict[str, DataSource] = {
	"run_dynamic_report": DataSource(
		key="tool:run_dynamic_report",
		name="Dynamic Report (Fallback)",
		description="Controlled, whitelist-only ad-hoc query over POS Invoice/Purchase Invoice/Purchase Receipt/Item/Customer/Supplier, used only when no purpose-built tool answers the question.",
		source_type="tool",
		supported_filters=[
			"doctype",
			"fields",
			"aggregate_field",
			"aggregate_fn",
			"filters",
			"group_by",
			"order_by",
			"date_from",
			"date_to",
			"limit",
		],
		returned_fields=["doctype", "row_count", "rows", "filters_applied"],
		supported_visualizations=["kpi", "table"],
		example_questions=[
			"List purchase invoices from a specific supplier",
			"Average purchase invoice value this quarter",
		],
		branch_scoped=True,
		estimated_cost="low",
	),
	"get_sales_overview": DataSource(
		key="tool:get_sales_overview",
		name="Sales Overview",
		description="Net/gross sales, returns, transaction count, average basket size, and per-branch breakdown for a date range.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "branch"],
		returned_fields=[
			"net_sales",
			"gross_sales",
			"returns",
			"transaction_count",
			"avg_basket_size",
			"branch_breakdown",
		],
		supported_visualizations=["kpi", "bar", "table"],
		example_questions=["What were total sales last month?", "Show sales by branch for this quarter"],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_purchase_overview": DataSource(
		key="tool:get_purchase_overview",
		name="Purchase Overview",
		description="Purchase amount, goods-received amount, and outstanding payable for a date range, optionally filtered by supplier.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "branch", "supplier"],
		returned_fields=["purchase_amount", "grn_amount", "outstanding_payable", "supplier_breakdown"],
		supported_visualizations=["kpi", "bar", "table"],
		example_questions=[
			"How much did we purchase from suppliers last month?",
			"Show outstanding payables by supplier",
		],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"rank_vendors": DataSource(
		key="tool:rank_vendors",
		name="Vendor Ranking",
		description="Rank suppliers by gross margin %, purchase volume, and outstanding payable for a date range.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "order", "limit"],
		returned_fields=["supplier", "gross_margin_pct", "purchase_volume", "outstanding_payable"],
		supported_visualizations=["kpi", "bar", "table"],
		example_questions=[
			"Which vendors have the best gross margin?",
			"Rank suppliers by purchase volume this year",
		],
		branch_scoped=False,
		estimated_cost="trivial",
	),
	"get_inventory_vs_sales": DataSource(
		key="tool:get_inventory_vs_sales",
		name="Inventory vs Sales",
		description="Compare current stock levels against recent sales velocity expressed as days-of-stock.",
		source_type="tool",
		supported_filters=["direction", "lookback_days", "branch", "limit"],
		returned_fields=["item_code", "item_name", "stock_qty", "sales_qty", "days_of_stock"],
		supported_visualizations=["kpi", "bar", "table"],
		example_questions=[
			"Which items are overstocked relative to sales?",
			"Show understocked items at risk of stockout",
		],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_branch_comparison": DataSource(
		key="tool:get_branch_comparison",
		name="Branch Comparison",
		description="Compare net sales across all branches for a date range.",
		source_type="tool",
		supported_filters=["date_from", "date_to"],
		returned_fields=["branch", "net_sales", "transaction_count"],
		supported_visualizations=["kpi", "bar", "table"],
		example_questions=[
			"Compare sales across all branches this month",
			"Which branch had highest revenue last quarter?",
		],
		branch_scoped=False,
		estimated_cost="trivial",
	),
	"get_payment_mode_split": DataSource(
		key="tool:get_payment_mode_split",
		name="Payment Mode Split",
		description="Breakdown of sales by payment mode (Cash, Card, etc.) for a date range.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "branch"],
		returned_fields=["payment_mode", "amount", "count", "percentage"],
		supported_visualizations=["kpi", "donut", "table"],
		example_questions=[
			"How did customers pay last week?",
			"Show payment mode distribution for this branch",
		],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_returns_overview": DataSource(
		key="tool:get_returns_overview",
		name="Returns Overview",
		description="Total returns/refunds amount and count for a date range with per-branch breakdown.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "branch"],
		returned_fields=["total_returns", "return_count", "branch_breakdown"],
		supported_visualizations=["kpi", "bar", "table"],
		example_questions=["What were total returns last month?", "Show returns by branch for this quarter"],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_active_shifts": DataSource(
		key="tool:get_active_shifts",
		name="Active POS Shifts",
		description="Currently open POS shifts with live running sales totals.",
		source_type="tool",
		supported_filters=[],
		returned_fields=["shift_name", "user", "opening_time", "current_sales", "expected_cash"],
		supported_visualizations=["kpi", "table"],
		example_questions=["Which shifts are currently open?", "Show live sales for active cashiers"],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_top_selling_items": DataSource(
		key="tool:get_top_selling_items",
		name="Top Selling Items",
		description="Top-selling items by revenue or quantity for a date range.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "branch", "order_by", "limit"],
		returned_fields=["item_code", "item_name", "revenue", "quantity", "margin"],
		supported_visualizations=["kpi", "bar", "table"],
		example_questions=[
			"What are the best-selling products this month?",
			"Show top 10 items by quantity sold",
		],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_outstanding_payables_overview": DataSource(
		key="tool:get_outstanding_payables_overview",
		name="Outstanding Payables",
		description="Total outstanding payable to suppliers and top suppliers by amount owed.",
		source_type="tool",
		supported_filters=["limit"],
		returned_fields=["total_outstanding", "top_suppliers"],
		supported_visualizations=["kpi", "table"],
		example_questions=[
			"How much do we owe suppliers in total?",
			"Show top 5 suppliers by outstanding amount",
		],
		branch_scoped=False,
		estimated_cost="trivial",
	),
	"get_gross_margin_overview": DataSource(
		key="tool:get_gross_margin_overview",
		name="Gross Margin Overview",
		description="Company-wide sales revenue, COGS, gross margin amount, and gross margin percentage for a date range.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "branch"],
		returned_fields=["revenue", "cogs", "gross_margin", "gross_margin_pct"],
		supported_visualizations=["kpi", "table"],
		example_questions=[
			"What is our overall gross margin this quarter?",
			"Show profitability by branch for last month",
		],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_item_price_history": DataSource(
		key="tool:get_item_price_history",
		name="Item Price History",
		description="Purchase-cost history (PR+PI) and current selling prices for a single item with cost stats and branch rates.",
		source_type="tool",
		supported_filters=["item_code", "months", "branch"],
		returned_fields=[
			"purchase_cost_history",
			"current_mrp",
			"branch_prices",
			"avg_pos_rate",
			"cost_stats",
		],
		supported_visualizations=["kpi", "bar", "table"],
		example_questions=[
			"Show cost trend for item ITEM-001 over 12 months",
			"What is the current selling price vs purchase cost for this item?",
		],
		branch_scoped=False,
		estimated_cost="low",
	),
	"get_price_increases": DataSource(
		key="tool:get_price_increases",
		name="Price Increases",
		description="Items where latest purchase cost increased vs previous cost point by at least threshold in last N months.",
		source_type="tool",
		supported_filters=["months", "min_change_pct", "branch", "limit"],
		returned_fields=["item_code", "item_name", "previous_cost", "latest_cost", "change_pct", "supplier"],
		supported_visualizations=["kpi", "bar", "table"],
		example_questions=[
			"Which items had cost increases >10% in last 6 months?",
			"Show price hikes by supplier this quarter",
		],
		branch_scoped=True,
		estimated_cost="low",
	),
	"get_dead_stock_detail": DataSource(
		key="tool:get_dead_stock_detail",
		name="Dead Stock Detail",
		description="Items with stock value > 0 that haven't sold in at least N days (or never), with total dead stock value.",
		source_type="tool",
		supported_filters=["min_days_since_last_sale", "branch", "limit"],
		returned_fields=[
			"item_code",
			"item_name",
			"stock_qty",
			"stock_value",
			"last_sale_date",
			"days_since_last_sale",
			"total_dead_stock_value",
		],
		supported_visualizations=["kpi", "bar", "table"],
		example_questions=["Show dead stock worth more than 100k", "Which items haven't sold in 90 days?"],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_top_customers": DataSource(
		key="tool:get_top_customers",
		name="Top Customers",
		description="Top customers by POS sales revenue or transaction frequency for a date range.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "branch", "order_by", "limit"],
		returned_fields=["customer", "revenue", "transaction_count", "avg_basket"],
		supported_visualizations=["kpi", "bar", "table"],
		example_questions=[
			"Who are our top 10 customers by revenue?",
			"Show most frequent buyers this month",
		],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_receivables_overview": DataSource(
		key="tool:get_receivables_overview",
		name="Outstanding Receivables",
		description="Total outstanding receivable owed by customers and top customers by amount owed.",
		source_type="tool",
		supported_filters=["limit"],
		returned_fields=["total_outstanding", "top_customers"],
		supported_visualizations=["kpi", "table"],
		example_questions=[
			"How much do customers owe us in total?",
			"Show top 5 customers by outstanding receivables",
		],
		branch_scoped=False,
		estimated_cost="trivial",
	),
	"get_payables_aging": DataSource(
		key="tool:get_payables_aging",
		name="Payables Aging",
		description="Aged payables buckets and top suppliers by outstanding.",
		source_type="tool",
		supported_filters=["limit"],
		returned_fields=["total_outstanding_amount", "buckets", "top_suppliers"],
		example_questions=[
			"What are our overdue payables?",
			"Show me payables aging buckets",
			"Which suppliers do we owe the most?",
		],
		supported_visualizations=["kpi", "table", "chart"],
	),
	"get_receivables_aging": DataSource(
		key="tool:get_receivables_aging",
		name="Receivables Aging",
		description="Aged receivables buckets and top customers by outstanding.",
		source_type="tool",
		supported_filters=["limit"],
		returned_fields=["total_outstanding_amount", "buckets", "top_customers"],
		example_questions=[
			"What are our overdue receivables?",
			"Show me receivables aging",
			"Which customers owe us the most?",
		],
		supported_visualizations=["kpi", "table", "chart"],
	),
	"get_cash_and_bank_balance": DataSource(
		key="tool:get_cash_and_bank_balance",
		name="Cash & Bank Balances",
		description="Current balances of all Cash and Bank accounts.",
		source_type="tool",
		supported_filters=[],
		returned_fields=["total_balance", "accounts"],
		example_questions=[
			"What is our cash position?",
			"Show me bank balances",
			"How much cash do we have?",
		],
		supported_visualizations=["kpi", "table", "chart"],
	),
	"get_profit_and_loss_overview": DataSource(
		key="tool:get_profit_and_loss_overview",
		name="Profit & Loss Overview",
		description="Total income, expense, net profit, and margin for a period.",
		source_type="tool",
		supported_filters=["date_from", "date_to"],
		returned_fields=["total_income", "total_expense", "net_profit", "net_margin_pct"],
		example_questions=[
			"What is our profit and loss?",
			"Show me net profit for this month",
			"What is our net margin?",
		],
		supported_visualizations=["kpi"],
	),
	"get_expense_breakdown": DataSource(
		key="tool:get_expense_breakdown",
		name="Expense Breakdown",
		description="Top expense accounts by amount for a period.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "limit"],
		returned_fields=["total_expense", "top_accounts"],
		example_questions=[
			"What are our biggest expenses?",
			"Show me expense breakdown",
			"Which accounts have the highest spending?",
		],
		supported_visualizations=["kpi", "table", "chart"],
	),
	"get_trial_balance_summary": DataSource(
		key="tool:get_trial_balance_summary",
		name="Trial Balance Summary",
		description="Cumulative Asset/Liability/Equity and period Income/Expense balances.",
		source_type="tool",
		supported_filters=["date_from", "date_to"],
		returned_fields=[
			"asset_balance",
			"liability_balance",
			"equity_balance",
			"income_balance",
			"expense_balance",
			"balance_check",
		],
		example_questions=[
			"Show me the trial balance",
			"What are our total assets and liabilities?",
			"Is our balance sheet balanced?",
		],
		supported_visualizations=["kpi", "table", "chart"],
	),
	"get_tax_liability_overview": DataSource(
		key="tool:get_tax_liability_overview",
		name="Tax Liability Overview",
		description="Tax account balances for a period.",
		source_type="tool",
		supported_filters=["date_from", "date_to"],
		returned_fields=["total_tax_balance", "tax_accounts"],
		example_questions=[
			"What is our tax liability?",
			"Show me GST payable",
			"How much tax do we owe?",
		],
		supported_visualizations=["kpi", "table", "chart"],
	),
	"get_payment_entry_summary": DataSource(
		key="tool:get_payment_entry_summary",
		name="Payment Entry Summary",
		description="Total received, paid, net cash flow, and breakdown by payment mode.",
		source_type="tool",
		supported_filters=["date_from", "date_to"],
		returned_fields=["total_received", "total_paid", "net_cash_flow", "by_mode"],
		example_questions=[
			"What is our cash flow from payments?",
			"Show me payments received vs paid",
			"Breakdown by payment mode",
		],
		supported_visualizations=["kpi", "table", "chart"],
	),
	"get_branch_profit_and_loss": DataSource(
		key="tool:get_branch_profit_and_loss",
		name="Branch Profit & Loss",
		description="Income, expense, and net profit broken down by branch.",
		source_type="tool",
		supported_filters=["date_from", "date_to"],
		returned_fields=["branches", "total_net_profit"],
		example_questions=[
			"Which branch is most profitable?",
			"Show me profit and loss by branch",
			"How does branch performance compare in net profit?",
		],
		supported_visualizations=["kpi", "table", "chart"],
	),
	"get_sales_trend": DataSource(
		key="tool:get_sales_trend",
		name="Sales Trend",
		description="Net sales bucketed by day, week, or month over a date range.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "granularity", "branch"],
		returned_fields=["trend"],
		supported_visualizations=["chart", "table"],
		example_questions=[
			"Show me the sales trend for the last 3 months",
			"How have weekly sales moved this quarter?",
		],
		branch_scoped=True,
		estimated_cost="low",
	),
	"get_hourly_sales_pattern": DataSource(
		key="tool:get_hourly_sales_pattern",
		name="Hourly Sales Pattern",
		description="Net sales broken down by hour-of-day and by weekday over a date range.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "branch"],
		returned_fields=["by_hour", "by_weekday"],
		supported_visualizations=["chart", "table"],
		example_questions=["What are our busiest hours?", "Which day of the week sells the most?"],
		branch_scoped=True,
		estimated_cost="low",
	),
	"get_discount_overview": DataSource(
		key="tool:get_discount_overview",
		name="Discount Overview",
		description="Total discount given on POS sales for a date range, with branch breakdown and effective discount %.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "branch"],
		returned_fields=["total_discount_amount", "effective_discount_pct", "branch_breakdown"],
		supported_visualizations=["kpi", "table", "chart"],
		example_questions=["How much discount are we giving away?", "Show discount leakage by branch"],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_sales_by_item_group": DataSource(
		key="tool:get_sales_by_item_group",
		name="Sales by Item Group",
		description="Revenue contribution by item group (category) for a date range.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "branch", "limit"],
		returned_fields=["item_groups"],
		supported_visualizations=["kpi", "table", "chart"],
		example_questions=["Which category sells the most?", "Show revenue contribution by department"],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_selling_below_cost": DataSource(
		key="tool:get_selling_below_cost",
		name="Selling Below Cost",
		description="Items currently priced to sell below their latest known purchase cost.",
		source_type="tool",
		supported_filters=["branch", "limit"],
		returned_fields=["items"],
		supported_visualizations=["table"],
		example_questions=["Are we selling anything at a loss?", "Show items priced below cost"],
		branch_scoped=True,
		estimated_cost="low",
	),
	"get_supplier_price_comparison": DataSource(
		key="tool:get_supplier_price_comparison",
		name="Supplier Price Comparison",
		description="For a single item, the most recent purchase rate paid to every supplier who has sold it.",
		source_type="tool",
		supported_filters=["item_code", "months", "branch"],
		returned_fields=["suppliers", "cheapest_supplier", "most_expensive_supplier"],
		supported_visualizations=["table", "chart"],
		example_questions=[
			"Which supplier gives us the best price on this item?",
			"Compare purchase rates across suppliers for item X",
		],
		branch_scoped=True,
		estimated_cost="low",
	),
	"get_po_receipt_variance": DataSource(
		key="tool:get_po_receipt_variance",
		name="PO vs Receipt Variance",
		description="Ordered vs. received quantity per Purchase Order line for a date range - surfaces short-supply.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "supplier", "branch", "limit"],
		returned_fields=["lines"],
		supported_visualizations=["table"],
		example_questions=[
			"Which suppliers short-deliver on our orders?",
			"Show ordered vs received quantity variance",
		],
		branch_scoped=True,
		estimated_cost="low",
	),
	"get_purchase_concentration": DataSource(
		key="tool:get_purchase_concentration",
		name="Purchase Concentration",
		description="Share of total purchase spend held by the top N suppliers, for a date range.",
		source_type="tool",
		supported_filters=["date_from", "date_to", "branch", "top_n"],
		returned_fields=["total_spend", "top_suppliers", "concentration_pct"],
		supported_visualizations=["kpi", "table", "chart"],
		example_questions=["How dependent are we on our top suppliers?", "Show purchase concentration risk"],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_stock_aging": DataSource(
		key="tool:get_stock_aging",
		name="Stock Aging",
		description="Current stock value per item alongside days since its last goods receipt.",
		source_type="tool",
		supported_filters=["branch", "limit"],
		returned_fields=["items"],
		supported_visualizations=["table", "chart"],
		example_questions=[
			"How old is our stock on hand?",
			"Show items that haven't been restocked recently",
		],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_reorder_recommendations": DataSource(
		key="tool:get_reorder_recommendations",
		name="Reorder Recommendations",
		description="Items below their calculated reorder point, with a suggested order quantity.",
		source_type="tool",
		supported_filters=["branch", "lookback_days", "lead_time_days", "limit"],
		returned_fields=["items"],
		supported_visualizations=["table"],
		example_questions=["What should I order today?", "Which items need restocking soon?"],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_negative_stock_check": DataSource(
		key="tool:get_negative_stock_check",
		name="Negative Stock Check",
		description="Bin rows with negative stock quantity - a data-quality issue.",
		source_type="tool",
		supported_filters=["branch", "limit"],
		returned_fields=["items"],
		supported_visualizations=["table"],
		example_questions=["Do we have any negative stock?", "Show stock discrepancies"],
		branch_scoped=True,
		estimated_cost="trivial",
	),
	"get_customer_activity_segments": DataSource(
		key="tool:get_customer_activity_segments",
		name="Customer Activity Segments",
		description="Customer counts segmented into New/Active/Lapsing/Lost based on purchase recency.",
		source_type="tool",
		supported_filters=["lookback_days", "branch"],
		returned_fields=["segments", "total_customers"],
		supported_visualizations=["kpi", "chart"],
		example_questions=["How many customers have we lost?", "Show new vs returning customer counts"],
		branch_scoped=True,
		estimated_cost="low",
	),
	"get_open_documents_overview": DataSource(
		key="tool:get_open_documents_overview",
		name="Open Documents Overview",
		description="Counts of draft/unsubmitted documents and stale POS shifts.",
		source_type="tool",
		supported_filters=["stale_days", "branch"],
		returned_fields=["documents", "stale_shifts"],
		supported_visualizations=["kpi", "table"],
		example_questions=[
			"Do we have unsubmitted documents piling up?",
			"Which shifts have been open too long?",
		],
		branch_scoped=True,
		estimated_cost="low",
	),
	"run_analytics_query": DataSource(
		key="tool:run_analytics_query",
		name="Governed Analytics Query",
		description="Semantic-layer query over sales/purchases/inventory/payables datasets with approved measures/dimensions and optional previous-period comparison.",
		source_type="tool",
		supported_filters=[
			"dataset",
			"measures",
			"dimension",
			"branch",
			"supplier",
			"date_from",
			"date_to",
			"sort",
			"limit",
			"compare_previous_period",
		],
		returned_fields=["rows", "row_count"],
		supported_visualizations=["kpi", "table", "chart"],
		example_questions=[
			"Compare net sales by branch this month vs last month",
			"Show outstanding payable by aging bucket",
		],
		branch_scoped=True,
		estimated_cost="low",
	),
	"drill_down_transactions": DataSource(
		key="tool:drill_down_transactions",
		name="Drill-Down Transactions",
		description="The actual underlying documents behind a run_analytics_query breakdown.",
		source_type="tool",
		supported_filters=[
			"dataset",
			"branch",
			"supplier",
			"customer",
			"item_code",
			"warehouse",
			"date_from",
			"date_to",
			"limit",
		],
		returned_fields=["rows"],
		supported_visualizations=["table"],
		example_questions=[
			"Show me the invoices behind this branch's sales decline",
			"Which vouchers make up this supplier's balance?",
		],
		branch_scoped=True,
		estimated_cost="low",
	),
	"list_frappe_reports": DataSource(
		key="tool:list_frappe_reports",
		name="Report Catalogue Search",
		description="Search the allowlisted catalogue of runnable ERP Query and Script Reports by keyword.",
		source_type="tool",
		supported_filters=["keyword", "limit"],
		returned_fields=["reports", "total_available"],
		supported_visualizations=["table"],
		example_questions=["Find the Stock Balance report", "Which receivables reports are available?"],
		branch_scoped=False,
		estimated_cost="trivial",
	),
	"run_frappe_report": DataSource(
		key="tool:run_frappe_report",
		name="ERP Report Runner",
		description="Execute one allowlisted ERP Query or Script Report with the report's own permission checks and forced company scope.",
		source_type="tool",
		supported_filters=["report_name", "filters", "limit"],
		returned_fields=["report_name", "columns", "rows", "row_count", "total_row_count", "filters_applied"],
		supported_visualizations=["table"],
		example_questions=[
			"Run the Stock Balance report",
			"Show Accounts Receivable from the standard report",
		],
		branch_scoped=False,
		estimated_cost="medium",
	),
}


_REPORT_CACHE: dict[str, DataSource] | None = None


def discover_frappe_reports() -> dict[str, DataSource]:
	"""Discover Query/Script Reports from relevant modules and wrap as DataSources."""
	global _REPORT_CACHE
	if _REPORT_CACHE is not None:
		return _REPORT_CACHE

	modules = ("Accounts", "Buying", "Selling", "Stock", "Aimatic")
	reports = frappe.get_all(
		"Report",
		filters={"disabled": 0, "module": ["in", modules]},
		fields=["name", "report_type", "ref_doctype", "module"],
	)

	result: dict[str, DataSource] = {}
	for r in reports:
		if r.report_type not in ("Query Report", "Script Report"):
			continue
		key = f"report:{r.name}"
		result[key] = DataSource(
			key=key,
			name=r.name,
			description=f"Frappe {r.report_type.lower()} for {r.ref_doctype or 'N/A'} (module: {r.module})",
			source_type="report",
			supported_filters=[],
			returned_fields=[],
			supported_visualizations=["table"],
			example_questions=[f"Run {r.name} report", f"Show data from {r.name}"],
			roles=[],
			company_scoped=True,
			branch_scoped=False,
			estimated_cost="medium",
			refresh_behavior="realtime",
		)

	_REPORT_CACHE = result
	return result


def get_registry() -> dict[str, DataSource]:
	"""Merge certified tools, advanced engines, and discovered reports."""
	from aimatic.ai.business_intelligence_registry import BUSINESS_DATA_SOURCES

	registry: dict[str, DataSource] = {}
	registry.update(discover_frappe_reports())
	for tool_name, ds in TOOL_REGISTRY.items():
		registry[tool_name] = ds
	registry.update(BUSINESS_DATA_SOURCES)
	return registry


def find_sources_for_question(question: str, user_roles: list[str]) -> list[DataSource]:
	"""Simple keyword-overlap heuristic: rank sources by overlapping significant words (len>=4)."""
	q_words = {w for w in question.lower().split() if len(w) >= 4}
	if not q_words:
		return []

	registry = get_registry()
	user_role_set = set(user_roles)

	scored: list[tuple[int, DataSource]] = []
	for ds in registry.values():
		if ds.roles and not (set(ds.roles) & user_role_set):
			continue
		hay = " ".join([ds.name, ds.description] + ds.example_questions).lower()
		h_words = {w for w in hay.split() if len(w) >= 4}
		overlap = len(q_words & h_words)
		if overlap:
			scored.append((overlap, ds))

	scored.sort(key=lambda x: x[0], reverse=True)
	return [ds for _, ds in scored[:5]]
