"""Deterministic rich-response builders for advanced certified engines."""

from __future__ import annotations

from frappe.utils import flt

from aimatic.ai.response_schema import (
	KPI,
	Chart,
	ChartData,
	ChartOptions,
	Pagination,
	Table,
	TableColumn,
)

_MAX_CHART_ITEMS = 15


def _kpis_abc_xyz(result: dict) -> list[KPI]:
	rows = result.get("items") or []
	summaries = result.get("summaries") or {}
	class_summary = summaries.get("class_summary") or {}
	currency = result.get("currency")
	return [
		KPI(
			key="abc_xyz_total_items",
			label="Items Classified",
			value=flt(result.get("row_count", len(rows))),
			format="number",
		),
		KPI(
			key="abc_xyz_a_net_sales",
			label="A-Class Net Sales",
			value=flt((class_summary.get("A") or {}).get("net_sales")),
			format="currency",
			currency=currency,
		),
		KPI(
			key="abc_xyz_c_stock_value",
			label="C-Class Stock Value",
			value=flt((class_summary.get("C") or {}).get("stock_value")),
			format="currency",
			currency=currency,
			severity="watch" if flt((class_summary.get("C") or {}).get("stock_value")) > 0 else None,
		),
		KPI(
			key="abc_xyz_high_value_irregular",
			label="High-Value Irregular Items",
			value=flt(len(result.get("high_value_irregular_items") or [])),
			format="number",
			severity="warning" if result.get("high_value_irregular_items") else None,
		),
	]


def _table_abc_xyz(result: dict) -> Table | None:
	rows = result.get("items") or []
	if not rows:
		return None
	currency = result.get("currency")
	return Table(
		id="table_abc_xyz_analysis",
		title="ABC/XYZ Item Analysis",
		columns=[
			TableColumn(key="item_code", label="Item", type="link", doctype="Item"),
			TableColumn(key="item_name", label="Item Name", type="text"),
			TableColumn(key="branch", label="Branch", type="link", doctype="Branch"),
			TableColumn(key="warehouse", label="Warehouse", type="link", doctype="Warehouse"),
			TableColumn(key="item_group", label="Item Group", type="text"),
			TableColumn(key="brand", label="Brand", type="text"),
			TableColumn(key="sales_quantity", label="Sales Qty", type="qty"),
			TableColumn(key="net_sales", label="Net Sales", type="currency", currency=currency),
			TableColumn(key="gross_margin", label="Gross Margin", type="currency", currency=currency),
			TableColumn(key="stock_quantity", label="Stock Qty", type="qty"),
			TableColumn(key="stock_value", label="Stock Value", type="currency", currency=currency),
			TableColumn(key="sales_contribution_pct", label="Contribution %", type="percent"),
			TableColumn(key="cumulative_contribution_pct", label="Cumulative %", type="percent"),
			TableColumn(key="abc_class", label="ABC", type="text"),
			TableColumn(key="demand_average", label="Demand Avg", type="qty"),
			TableColumn(key="demand_standard_deviation", label="Demand Std Dev", type="float"),
			TableColumn(key="coefficient_of_variation", label="CV", type="float"),
			TableColumn(key="active_selling_periods", label="Active Periods", type="int"),
			TableColumn(key="xyz_class", label="XYZ", type="text"),
			TableColumn(key="combined_class", label="Class", type="text"),
			TableColumn(key="days_of_stock", label="Days of Stock", type="float"),
			TableColumn(key="last_sale_date", label="Last Sale", type="date"),
			TableColumn(key="data_coverage", label="Coverage %", type="percent"),
			TableColumn(key="confidence", label="Confidence", type="percent"),
		],
		rows=rows,
		pagination=Pagination(
			page=1,
			page_size=len(rows),
			total=int(result.get("row_count") or len(rows)),
		),
		metadata={
			"reconciliation": result.get("reconciliation"),
			"calculation_version": result.get("calculation_version"),
		},
	)


def _charts_abc_xyz(result: dict) -> list[Chart]:
	rows = result.get("items") or []
	if not rows:
		return []
	currency = result.get("currency")
	top = rows[:_MAX_CHART_ITEMS]
	class_summary = (result.get("summaries") or {}).get("class_summary") or {}
	matrix = (result.get("summaries") or {}).get("abc_xyz_matrix") or {}
	variable = [row for row in rows if row.get("coefficient_of_variation") is not None][:_MAX_CHART_ITEMS]
	scatter_rows = sorted(rows, key=lambda row: flt(row.get("stock_value")), reverse=True)[:_MAX_CHART_ITEMS]
	return [
		Chart(
			id="chart_abc_pareto",
			title="ABC Pareto Contribution",
			type="pareto",
			data=ChartData(
				labels=[row.get("item_name") or row.get("item_code") for row in top],
				datasets=[
					{
						"label": "Contribution %",
						"data": [flt(row.get("sales_contribution_pct")) for row in top],
					},
					{
						"label": "Cumulative %",
						"data": [flt(row.get("cumulative_contribution_pct")) for row in top],
					},
				],
			),
			options=ChartOptions(yAxis={"format": "percent"}),
			auto_selected=True,
		),
		Chart(
			id="chart_abc_contribution",
			title="ABC Sales Contribution",
			type="bar",
			data=ChartData(
				labels=["A", "B", "C"],
				datasets=[
					{
						"label": "Net Sales",
						"data": [
							flt((class_summary.get(key) or {}).get("net_sales")) for key in ("A", "B", "C")
						],
					}
				],
			),
			options=ChartOptions(yAxis={"format": "currency", "currency": currency}),
			auto_selected=True,
		),
		Chart(
			id="chart_xyz_variability",
			title="XYZ Demand Variability",
			type="bar",
			data=ChartData(
				labels=[row.get("item_name") or row.get("item_code") for row in variable],
				datasets=[
					{
						"label": "Coefficient of Variation",
						"data": [flt(row.get("coefficient_of_variation")) for row in variable],
					}
				],
			),
			options=ChartOptions(yAxis={"format": "number"}),
			auto_selected=True,
		),
		Chart(
			id="chart_abc_xyz_matrix",
			title="ABC/XYZ Matrix",
			type="heatmap",
			data=ChartData(
				labels=[f"{abc}{xyz}" for abc in "ABC" for xyz in "XYZ"],
				datasets=[
					{
						"label": "Items",
						"data": [cint_value(matrix.get(f"{abc}{xyz}")) for abc in "ABC" for xyz in "XYZ"],
					}
				],
			),
			options=ChartOptions(),
			auto_selected=True,
		),
		Chart(
			id="chart_stock_vs_sales_contribution",
			title="Stock Value vs Sales Contribution",
			type="scatter",
			data=ChartData(
				labels=[row.get("item_name") or row.get("item_code") for row in scatter_rows],
				datasets=[
					{"label": "Stock Value", "data": [flt(row.get("stock_value")) for row in scatter_rows]},
					{
						"label": "Sales Contribution %",
						"data": [flt(row.get("sales_contribution_pct")) for row in scatter_rows],
					},
				],
			),
			options=ChartOptions(
				xAxis={"format": "currency", "currency": currency},
				yAxis={"format": "percent"},
			),
			auto_selected=True,
		),
	]


def cint_value(value) -> int:
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0


KPI_DISPATCH = {"get_abc_xyz_analysis": _kpis_abc_xyz}
TABLE_DISPATCH = {"get_abc_xyz_analysis": _table_abc_xyz}
CHARTS_DISPATCH = {"get_abc_xyz_analysis": _charts_abc_xyz}

from aimatic.ai.demand_forecasting_response import CHARTS_DISPATCH as _FORECAST_CHARTS
from aimatic.ai.demand_forecasting_response import KPI_DISPATCH as _FORECAST_KPIS
from aimatic.ai.demand_forecasting_response import TABLE_DISPATCH as _FORECAST_TABLES

KPI_DISPATCH.update(_FORECAST_KPIS)
TABLE_DISPATCH.update(_FORECAST_TABLES)
CHARTS_DISPATCH.update(_FORECAST_CHARTS)

from aimatic.ai.price_recommendation_response import CHARTS_DISPATCH as _PRICE_CHARTS
from aimatic.ai.price_recommendation_response import KPI_DISPATCH as _PRICE_KPIS
from aimatic.ai.price_recommendation_response import TABLE_DISPATCH as _PRICE_TABLES

KPI_DISPATCH.update(_PRICE_KPIS)
TABLE_DISPATCH.update(_PRICE_TABLES)
CHARTS_DISPATCH.update(_PRICE_CHARTS)

from aimatic.ai.advanced_intelligence_response import CHARTS_DISPATCH as _ADVANCED_CHARTS
from aimatic.ai.advanced_intelligence_response import KPI_DISPATCH as _ADVANCED_KPIS
from aimatic.ai.advanced_intelligence_response import TABLE_DISPATCH as _ADVANCED_TABLES

KPI_DISPATCH.update(_ADVANCED_KPIS)
TABLE_DISPATCH.update(_ADVANCED_TABLES)
CHARTS_DISPATCH.update(_ADVANCED_CHARTS)
