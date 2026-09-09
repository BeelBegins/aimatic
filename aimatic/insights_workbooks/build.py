"""Generate Insights workbook.json files for SZL retail KPIs.

Run from the bench:  python apps/aimatic/aimatic/insights_workbooks/build.py
Do not hand-edit the emitted workbook.json files.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent

SITE_DB = "Site DB"


def _dump(folder: str, payload: dict) -> None:
	path = HERE / folder / "workbook.json"
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(payload, indent="\t") + "\n", encoding="utf-8")


def _source(table: str) -> dict:
	return {
		"type": "source",
		"table": {"type": "table", "data_source": SITE_DB, "table_name": table},
	}


def _eq(column: str, value) -> dict:
	return {
		"column": {"type": "column", "column_name": column},
		"operator": "=",
		"value": value,
	}


def _filters(*rules: dict) -> dict:
	return {"type": "filter_group", "logical_operator": "And", "filters": list(rules)}


def _join(table: str, left: str, right: str, columns: list[str], join_type: str = "inner") -> dict:
	return {
		"type": "join",
		"join_type": join_type,
		"table": {"type": "table", "data_source": SITE_DB, "table_name": table},
		"select_columns": [{"type": "column", "column_name": col} for col in columns],
		"join_condition": {
			"left_column": {"type": "column", "column_name": left},
			"right_column": {"type": "column", "column_name": right},
		},
	}


def _mutate(name: str, data_type: str, expression: str) -> dict:
	return {
		"type": "mutate",
		"new_name": name,
		"data_type": data_type,
		"expression": {"type": "expression", "expression": expression},
	}


def _query(name: str, title: str, workbook: str, operations: list, sort_order: int) -> dict:
	return {
		"name": name,
		"title": title,
		"workbook": workbook,
		"folder": None,
		"sort_order": sort_order,
		"use_live_connection": 1,
		"is_script_query": 0,
		"is_builder_query": 1,
		"is_native_query": 0,
		"operations": operations,
	}


def _native_query(name: str, title: str, workbook: str, sql: str, sort_order: int) -> dict:
	return {
		"name": name,
		"title": title,
		"workbook": workbook,
		"folder": None,
		"sort_order": sort_order,
		"use_live_connection": 1,
		"is_script_query": 0,
		"is_builder_query": 0,
		"is_native_query": 1,
		"operations": [{"type": "sql", "data_source": SITE_DB, "raw_sql": sql.strip()}],
	}


def _cur(name: str, column: str) -> dict:
	return {
		"measure_name": name,
		"column_name": column,
		"data_type": "Decimal",
		"aggregation": "sum",
		"format": "currency",
	}


def _cnt(name: str, column: str = "name") -> dict:
	return {
		"measure_name": name,
		"column_name": column,
		"data_type": "Integer",
		"aggregation": "count",
	}


def _table(
	name: str,
	title: str,
	workbook: str,
	query: str,
	rows: list[dict],
	values: list[dict],
	sort_order: int,
	limit: int = 25,
	order_col: str | None = None,
	order_dir: str = "desc",
) -> dict:
	config = {
		"rows": rows,
		"columns": [],
		"values": values,
		"order_by": [],
		"limit": limit,
		"filters": {"logical_operator": "And", "filters": []},
	}
	if order_col:
		config["order_by"] = [
			{"column": {"type": "column", "column_name": order_col}, "direction": order_dir}
		]
	return {
		"name": name,
		"title": title,
		"workbook": workbook,
		"folder": None,
		"sort_order": sort_order,
		"query": query,
		"chart_type": "Table",
		"config": config,
	}


def _dim(label: str, column: str, data_type: str = "String") -> dict:
	return {"dimension_name": label, "column_name": column, "data_type": data_type}


def _number_chart(name: str, title: str, workbook: str, query: str, measures: list[dict], date_col: str, sort_order: int) -> dict:
	opts = []
	for m in measures:
		if m.get("format") == "currency" or m.get("aggregation") == "avg":
			opts.append({"decimal": 2, "shorten_numbers": True})
		else:
			opts.append({"shorten_numbers": False})
	return {
		"name": name,
		"title": title,
		"workbook": workbook,
		"folder": None,
		"sort_order": sort_order,
		"query": query,
		"chart_type": "Number",
		"config": {
			"number_columns": measures,
			"number_column_options": opts,
			"comparison": True,
			"sparkline": True,
			"date_column": {
				"dimension_name": date_col,
				"column_name": date_col,
				"data_type": "Date",
				"granularity": "month",
			},
			"order_by": [{"column": {"type": "column", "column_name": date_col}, "direction": "asc"}],
			"limit": 100,
			"filters": {"logical_operator": "And", "filters": []},
		},
	}


def _snapshot_chart(name: str, title: str, workbook: str, query: str, measures: list[dict], sort_order: int) -> dict:
	"""A Number card over a query with no date column — Bin-style snapshots.
	Comparison and sparkline are off because there is no period to compare to."""
	opts = []
	for m in measures:
		if m.get("format") in ("currency", "percent") or m.get("aggregation") == "avg":
			opts.append({"decimal": 2, "shorten_numbers": True})
		else:
			opts.append({"shorten_numbers": False})
	return {
		"name": name,
		"title": title,
		"workbook": workbook,
		"folder": None,
		"sort_order": sort_order,
		"query": query,
		"chart_type": "Number",
		"config": {
			"number_columns": measures,
			"number_column_options": opts,
			"comparison": False,
			"sparkline": False,
			"order_by": [],
			"limit": 100,
			"filters": {"logical_operator": "And", "filters": []},
		},
	}


def _line(name: str, title: str, workbook: str, query: str, date_col: str, measure: dict, sort_order: int) -> dict:
	return {
		"name": name,
		"title": title,
		"workbook": workbook,
		"folder": None,
		"sort_order": sort_order,
		"query": query,
		"chart_type": "Line",
		"config": {
			"x_axis": {
				"dimension": {
					"dimension_name": date_col,
					"column_name": date_col,
					"data_type": "Date",
					"granularity": "month",
				}
			},
			"y_axis": {"series": [{"measure": measure}], "show_data_labels": False},
			"order_by": [{"column": {"type": "column", "column_name": date_col}, "direction": "asc"}],
			"limit": 100,
			"filters": {"logical_operator": "And", "filters": []},
		},
	}


def _donut(name: str, title: str, workbook: str, query: str, dim: str, measure: dict, sort_order: int) -> dict:
	return {
		"name": name,
		"title": title,
		"workbook": workbook,
		"folder": None,
		"sort_order": sort_order,
		"query": query,
		"chart_type": "Donut",
		"config": {
			"label_column": {
				"dimension_name": dim,
				"column_name": dim,
				"data_type": "String",
			},
			"value_column": measure,
			"legend_position": "bottom",
			"order_by": [],
			"limit": 100,
			"filters": {"logical_operator": "And", "filters": []},
		},
	}


def _row(name: str, title: str, workbook: str, query: str, dim: str, measure: dict, sort_order: int, limit: int = 10) -> dict:
	return {
		"name": name,
		"title": title,
		"workbook": workbook,
		"folder": None,
		"sort_order": sort_order,
		"query": query,
		"chart_type": "Row",
		"config": {
			"x_axis": {
				"dimension": {"dimension_name": dim, "column_name": dim, "data_type": "String"}
			},
			"y_axis": {"series": [{"measure": measure}]},
			"order_by": [
				{"column": {"type": "column", "column_name": measure["measure_name"]}, "direction": "desc"}
			],
			"limit": limit,
			"filters": {"logical_operator": "And", "filters": []},
		},
	}


def _filter_item(name: str, ftype: str, icon: str, links: dict, x: int, default: dict | None = None) -> dict:
	item = {
		"type": "filter",
		"filter_name": name,
		"filter_type": ftype,
		"icon": icon,
		"links": links,
		"layout": {"i": f"filter-{name.lower().replace(' ', '-')}", "x": x, "y": 0, "w": 4, "h": 1},
	}
	if default:
		item.update(default)
	return item


def _chart_item(chart: str, i: str, x: int, y: int, w: int, h: int) -> dict:
	return {"type": "chart", "chart": chart, "layout": {"i": i, "x": x, "y": y, "w": w, "h": h}}


def _workbook(name: str, title: str, queries: dict, charts: dict, dashboards: dict) -> dict:
	payload = {
		"version": "1.0",
		"type": "Workbook",
		"name": name,
		"doc": {"name": name, "title": title},
		"dependencies": {
			"folders": [],
			"queries": queries,
			"charts": charts,
			"dashboards": dashboards,
		},
	}
	return _split_number_dashboard_cards(payload)


def _chart_slug(value: str) -> str:
	"""Return a stable, workbook-safe suffix for a KPI measure name."""
	slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
	return slug or "metric"


def _split_number_dashboard_cards(payload: dict) -> dict:
	"""Make multi-metric Number charts safe at the Insights mobile breakpoint.

	The Insights Number chart renders one bordered card per measure. On a phone,
	the dashboard grid collapses to one column, but the dashboard item's stored
	height does not grow with the number of measures. A nine-measure chart in a
	four-row item therefore clips its own cards and the next item paints over it.

	Keep the original chart for workbook compatibility, and add one-measure
	variants for dashboard use. The grid can then compact these small tiles on
	desktop and reflow them one-per-row on mobile without changing query logic.
	"""
	charts = payload["dependencies"]["charts"]
	split_map: dict[str, list[str]] = {}

	for chart_name, chart in list(charts.items()):
		if chart.get("chart_type") != "Number":
			continue
		config = chart.get("config") or {}
		measures = config.get("number_columns") or []
		if len(measures) <= 1:
			continue

		options = config.get("number_column_options") or []
		split_names: list[str] = []
		for index, measure in enumerate(measures):
			suffix = _chart_slug(measure.get("measure_name") or f"metric-{index + 1}")
			candidate = f"{chart_name}-{suffix}"
			if candidate in charts:
				candidate = f"{candidate}-{index + 1}"
			while candidate in split_names:
				candidate = f"{candidate}-{index + 1}"

			split_chart = deepcopy(chart)
			split_chart["name"] = candidate
			split_chart["title"] = measure.get("measure_name") or chart.get("title")
			split_chart["sort_order"] = int(chart.get("sort_order", 0)) * 100 + index
			split_chart["config"]["number_columns"] = [deepcopy(measure)]
			split_chart["config"]["number_column_options"] = [
				deepcopy(options[index]) if index < len(options) else {}
			]
			charts[candidate] = split_chart
			split_names.append(candidate)

		split_map[chart_name] = split_names

	if not split_map:
		return payload

	for dashboard in payload["dependencies"]["dashboards"].values():
		items = dashboard.get("items") or []
		new_items: list[dict] = []
		for item in items:
			if item.get("type") == "filter":
				links = item.get("links") or {}
				new_links: dict[str, str] = {}
				for chart_name, value in links.items():
					for split_name in split_map.get(chart_name, [chart_name]):
						new_links[split_name] = value
				item["links"] = new_links

			if item.get("type") != "chart" or item.get("chart") not in split_map:
				new_items.append(item)
				continue

			layout = item.get("layout") or {}
			split_names = split_map[item["chart"]]
			columns = min(len(split_names), 4)
			width = max(1, 20 // columns)
			for index, split_name in enumerate(split_names):
				new_items.append(
					{
						"type": "chart",
						"chart": split_name,
						"layout": {
							"i": f"{layout.get('i', item['chart'])}-{index + 1}",
							"x": (index % columns) * width,
							"y": layout.get("y", 0) + (index // columns) * 3,
							"w": width,
							"h": 3,
						},
					}
				)
		dashboard["items"] = new_items

	return payload


def build_pos_retail() -> dict:
	wb = "aimatic-pos-retail"
	pos = _query(
		"tq-pos",
		"POS Invoices",
		wb,
		[_source("tabPOS Invoice"), _filters(_eq("docstatus", 1))],
		0,
	)
	items = _query(
		"tq-pos-items",
		"POS Invoice Items",
		wb,
		[
			_source("tabPOS Invoice Item"),
			_filters(_eq("docstatus", 1)),
			_join(
				"tabPOS Invoice",
				"parent",
				"name",
				["posting_date", "company", "branch", "customer", "customer_name"],
			),
		],
		1,
	)
	pays = _query(
		"tq-pos-payments",
		"POS Payments",
		wb,
		[
			_source("tabSales Invoice Payment"),
			_filters(_eq("parenttype", "POS Invoice")),
			_join(
				"tabPOS Invoice",
				"parent",
				"name",
				["posting_date", "company", "branch"],
			),
		],
		2,
	)
	rev = {
		"measure_name": "Revenue",
		"column_name": "base_net_total",
		"data_type": "Decimal",
		"aggregation": "sum",
		"format": "currency",
	}
	item_rev = {
		"measure_name": "Revenue",
		"column_name": "base_net_amount",
		"data_type": "Decimal",
		"aggregation": "sum",
		"format": "currency",
	}
	pay_amt = {
		"measure_name": "Collected",
		"column_name": "base_amount",
		"data_type": "Decimal",
		"aggregation": "sum",
		"format": "currency",
	}
	charts = {
		"tc-kpis": _number_chart(
			"tc-kpis",
			"Sales and customer KPIs",
			wb,
			"tq-pos",
			[
				rev,
				{
					"measure_name": "Tickets",
					"column_name": "name",
					"data_type": "Integer",
					"aggregation": "count",
				},
				{
					"measure_name": "Avg ticket",
					"column_name": "base_net_total",
					"data_type": "Decimal",
					"aggregation": "avg",
					"format": "currency",
				},
				{
					"measure_name": "Active customers",
					"column_name": "customer",
					"data_type": "Integer",
					"aggregation": "count_distinct",
				},
			],
			"posting_date",
			0,
		),
		"tc-trend": _line("tc-trend", "Revenue trend", wb, "tq-pos", "posting_date", rev, 1),
		"tc-branch": _donut("tc-branch", "Revenue by branch", wb, "tq-pos", "branch", rev, 2),
		"tc-customers": _row("tc-customers", "Top customers", wb, "tq-pos", "customer_name", rev, 3),
		"tc-items": _row("tc-items", "Top items", wb, "tq-pos-items", "item_name", item_rev, 4),
		"tc-groups": _donut(
			"tc-groups", "Revenue by item group", wb, "tq-pos-items", "item_group", item_rev, 5
		),
		"tc-pay": _donut(
			"tc-pay", "Collections by payment mode", wb, "tq-pos-payments", "mode_of_payment", pay_amt, 6
		),
	}
	date_links = {
		"tc-kpis": "`tq-pos`.`posting_date`",
		"tc-trend": "`tq-pos`.`posting_date`",
		"tc-branch": "`tq-pos`.`posting_date`",
		"tc-customers": "`tq-pos`.`posting_date`",
		"tc-items": "`tq-pos-items`.`posting_date`",
		"tc-groups": "`tq-pos-items`.`posting_date`",
		"tc-pay": "`tq-pos-payments`.`posting_date`",
	}
	co_links = {k: v.replace("posting_date", "company") for k, v in date_links.items()}
	br_links = {k: v.replace("posting_date", "branch") for k, v in date_links.items()}
	dash = {
		"td-pos-retail": {
			"name": "td-pos-retail",
			"title": "POS Sales and Customers",
			"workbook": wb,
			"items": [
				_filter_item(
					"Date Range",
					"Date",
					"calendar",
					date_links,
					0,
					{"default_operator": "within", "default_value": "Last 12 months"},
				),
				_filter_item("Company", "String", "building-2", co_links, 4),
				_filter_item("Branch", "String", "store", br_links, 8),
				_chart_item("tc-kpis", "item-kpis", 0, 1, 20, 3),
				_chart_item("tc-trend", "item-trend", 0, 4, 12, 8),
				_chart_item("tc-branch", "item-branch", 12, 4, 8, 8),
				_chart_item("tc-customers", "item-cust", 0, 12, 10, 8),
				_chart_item("tc-items", "item-items", 10, 12, 10, 8),
				_chart_item("tc-groups", "item-groups", 0, 20, 10, 8),
				_chart_item("tc-pay", "item-pay", 10, 20, 10, 8),
			],
		}
	}
	return _workbook(
		wb,
		"POS Sales and Customers",
		{"tq-pos": pos, "tq-pos-items": items, "tq-pos-payments": pays},
		charts,
		dash,
	)


def build_tax_compliance() -> dict:
	wb = "aimatic-tax-compliance"
	pos = _query(
		"tq-tax-pos",
		"POS Invoices",
		wb,
		[
			_source("tabPOS Invoice"),
			_filters(_eq("docstatus", 1)),
			_mutate(
				"submission_status",
				"String",
				"cases((custom_fbr_status == 'Accepted', 'Accepted'), (custom_fbr_status == 'Failed', 'Failed'), (custom_fbr_status == 'Not Sent', 'Not sent'), else_='Other')",
			),
			_mutate(
				"is_return_flag",
				"String",
				"if_else(is_return == 1, 'Return', 'Sale')",
			),
		],
		0,
	)
	taxes = _query(
		"tq-tax-rows",
		"POS Tax Rows",
		wb,
		[
			_source("tabSales Taxes and Charges"),
			_filters(_eq("docstatus", 1), _eq("parenttype", "POS Invoice")),
			_join(
				"tabPOS Invoice",
				"parent",
				"name",
				["posting_date", "company", "branch"],
			),
			_mutate(
				"output_tax_amount",
				"Decimal",
				"if_else(description == 'FBR Sales Tax', base_tax_amount, 0)",
			),
			_mutate(
				"pos_fee_amount",
				"Decimal",
				"if_else(description == 'FBR POS Service Fee', base_tax_amount, 0)",
			),
			_mutate(
				"charge_kind",
				"String",
				"cases((description == 'FBR POS Service Fee', 'POS service fee'), (description == 'FBR Sales Tax', 'Output tax'), else_='Other charge')",
			),
		],
		1,
	)
	net = {
		"measure_name": "Net sales",
		"column_name": "base_net_total",
		"data_type": "Decimal",
		"aggregation": "sum",
		"format": "currency",
	}
	grand = {
		"measure_name": "Gross sales",
		"column_name": "base_grand_total",
		"data_type": "Decimal",
		"aggregation": "sum",
		"format": "currency",
	}
	output_tax = _cur("Output tax", "output_tax_amount")
	pos_fee = _cur("POS service fee", "pos_fee_amount")
	count = _cnt("Invoices")
	charts = {
		"tc-tax-kpis": _number_chart(
			"tc-tax-kpis",
			"Merchandise vs ticket",
			wb,
			"tq-tax-pos",
			[net, grand, count],
			"posting_date",
			0,
		),
		"tc-tax-collected": _number_chart(
			"tc-tax-collected",
			"Output tax vs POS service fee",
			wb,
			"tq-tax-rows",
			[output_tax, pos_fee],
			"posting_date",
			1,
		),
		"tc-submit": _donut(
			"tc-submit",
			"Tax invoice submission",
			wb,
			"tq-tax-pos",
			"submission_status",
			count,
			2,
		),
		"tc-charge-kind": _donut(
			"tc-charge-kind", "Charges: tax vs POS fee", wb, "tq-tax-rows", "charge_kind", _cur("Amount", "base_tax_amount"), 3
		),
		"tc-return-mix": _donut(
			"tc-return-mix", "Sales vs returns", wb, "tq-tax-pos", "is_return_flag", net, 4
		),
	}
	date_links = {
		"tc-tax-kpis": "`tq-tax-pos`.`posting_date`",
		"tc-tax-collected": "`tq-tax-rows`.`posting_date`",
		"tc-submit": "`tq-tax-pos`.`posting_date`",
		"tc-charge-kind": "`tq-tax-rows`.`posting_date`",
		"tc-return-mix": "`tq-tax-pos`.`posting_date`",
	}
	co_links = {k: v.replace("posting_date", "company") for k, v in date_links.items()}
	br_links = {k: v.replace("posting_date", "branch") for k, v in date_links.items()}
	dash = {
		"td-tax": {
			"name": "td-tax",
			"title": "Tax Compliance",
			"workbook": wb,
			"items": [
				_filter_item(
					"Date Range",
					"Date",
					"calendar",
					date_links,
					0,
					{"default_operator": "within", "default_value": "Last 12 months"},
				),
				_filter_item("Company", "String", "building-2", co_links, 4),
				_filter_item("Branch", "String", "store", br_links, 8),
				_chart_item("tc-tax-kpis", "item-kpis", 0, 1, 14, 3),
				_chart_item("tc-tax-collected", "item-collected", 14, 1, 6, 3),
				_chart_item("tc-submit", "item-submit", 0, 4, 10, 8),
				_chart_item("tc-charge-kind", "item-head", 10, 4, 10, 8),
				_chart_item("tc-return-mix", "item-returns", 0, 12, 20, 8),
			],
		}
	}
	return _workbook(wb, "Tax Compliance", {"tq-tax-pos": pos, "tq-tax-rows": taxes}, charts, dash)


def build_accounts_pnl() -> dict:
	wb = "aimatic-accounts-pnl"
	gl = _query(
		"tq-gl",
		"GL Entries",
		wb,
		[
			_source("tabGL Entry"),
			_filters(_eq("is_cancelled", 0)),
			_join("tabAccount", "account", "name", ["root_type", "account_type", "account_name"]),
			_mutate(
				"merchandise_income",
				"Decimal",
				"if_else((root_type == 'Income') & (account_type != 'Tax'), credit - debit, 0)",
			),
			_mutate(
				"expense_amount",
				"Decimal",
				"if_else(root_type == 'Expense', debit - credit, 0)",
			),
			_mutate(
				"net_amount",
				"Decimal",
				"merchandise_income - expense_amount",
			),
		],
		0,
	)
	income = _cur("Merchandise income", "merchandise_income")
	expense = _cur("Expense", "expense_amount")
	net = _cur("Merchandise net", "net_amount")
	charts = {
		"tc-pnl-kpis": _number_chart(
			"tc-pnl-kpis",
			"Merchandise P&L (excludes tax and POS service fee)",
			wb,
			"tq-gl",
			[income, expense, net],
			"posting_date",
			0,
		),
		"tc-income-trend": _line(
			"tc-income-trend", "Merchandise income trend", wb, "tq-gl", "posting_date", income, 1
		),
		"tc-expense-trend": _line(
			"tc-expense-trend", "Expense trend", wb, "tq-gl", "posting_date", expense, 2
		),
		"tc-income-acct": _row(
			"tc-income-acct", "Merchandise income by account", wb, "tq-gl", "account_name", income, 3, 15
		),
		"tc-expense-acct": _row(
			"tc-expense-acct", "Expense by account", wb, "tq-gl", "account_name", expense, 4, 15
		),
	}
	date_links = {name: "`tq-gl`.`posting_date`" for name in charts}
	co_links = {name: "`tq-gl`.`company`" for name in charts}
	br_links = {name: "`tq-gl`.`branch`" for name in charts}
	dash = {
		"td-pnl": {
			"name": "td-pnl",
			"title": "Merchandise P&L",
			"workbook": wb,
			"items": [
				_filter_item(
					"Date Range",
					"Date",
					"calendar",
					date_links,
					0,
					{"default_operator": "within", "default_value": "Last 12 months"},
				),
				_filter_item("Company", "String", "building-2", co_links, 4),
				_filter_item("Branch", "String", "store", br_links, 8),
				_chart_item("tc-pnl-kpis", "item-kpis", 0, 1, 20, 3),
				_chart_item("tc-income-trend", "item-inc-trend", 0, 4, 10, 8),
				_chart_item("tc-expense-trend", "item-exp-trend", 10, 4, 10, 8),
				_chart_item("tc-income-acct", "item-inc-acct", 0, 12, 10, 9),
				_chart_item("tc-expense-acct", "item-exp-acct", 10, 12, 10, 9),
			],
		}
	}
	return _workbook(wb, "Merchandise P&L", {"tq-gl": gl}, charts, dash)


SALES_ABC_SQL = """
WITH s AS (
	SELECT
		i.item_code,
		i.item_name,
		i.item_group,
		SUM(i.base_net_amount) AS revenue,
		SUM(i.qty) AS qty
	FROM `tabPOS Invoice Item` i
	INNER JOIN `tabPOS Invoice` p ON p.name = i.parent
	WHERE i.docstatus = 1
		AND p.docstatus = 1
		AND p.posting_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
	GROUP BY i.item_code, i.item_name, i.item_group
),
r AS (
	SELECT
		s.*,
		SUM(revenue) OVER (ORDER BY revenue DESC) AS cum_rev,
		SUM(revenue) OVER () AS tot_rev
	FROM s
)
SELECT
	item_code,
	item_name,
	item_group,
	revenue,
	qty,
	ROUND(100 * revenue / NULLIF(tot_rev, 0), 2) AS pct,
	ROUND(100 * cum_rev / NULLIF(tot_rev, 0), 2) AS cum_pct,
	CASE
		WHEN IFNULL(tot_rev, 0) = 0 THEN 'C'
		WHEN cum_rev / tot_rev <= 0.80 THEN 'A'
		WHEN cum_rev / tot_rev <= 0.95 THEN 'B'
		ELSE 'C'
	END AS abc_class
FROM r
"""

STOCK_ABC_SQL = """
WITH s AS (
	SELECT
		b.item_code,
		i.item_name,
		i.item_group,
		SUM(b.stock_value) AS stock_value,
		SUM(b.actual_qty) AS qty
	FROM `tabBin` b
	INNER JOIN `tabItem` i ON i.name = b.item_code
	WHERE b.stock_value > 0
	GROUP BY b.item_code, i.item_name, i.item_group
),
r AS (
	SELECT
		s.*,
		SUM(stock_value) OVER (ORDER BY stock_value DESC) AS cum_val,
		SUM(stock_value) OVER () AS tot_val
	FROM s
)
SELECT
	item_code,
	item_name,
	item_group,
	stock_value,
	qty,
	ROUND(100 * stock_value / NULLIF(tot_val, 0), 2) AS pct,
	ROUND(100 * cum_val / NULLIF(tot_val, 0), 2) AS cum_pct,
	CASE
		WHEN IFNULL(tot_val, 0) = 0 THEN 'C'
		WHEN cum_val / tot_val <= 0.80 THEN 'A'
		WHEN cum_val / tot_val <= 0.95 THEN 'B'
		ELSE 'C'
	END AS abc_class
FROM r
"""

DEAD_STOCK_SQL = """
WITH last_out AS (
	SELECT item_code, warehouse, MAX(posting_date) AS last_out
	FROM `tabStock Ledger Entry`
	WHERE IFNULL(is_cancelled, 0) = 0 AND actual_qty < 0
	GROUP BY item_code, warehouse
)
SELECT
	b.item_code,
	i.item_name,
	i.item_group,
	b.warehouse,
	b.actual_qty,
	b.stock_value,
	lo.last_out,
	DATEDIFF(CURDATE(), lo.last_out) AS days_since_out
FROM `tabBin` b
INNER JOIN `tabItem` i ON i.name = b.item_code
LEFT JOIN last_out lo ON lo.item_code = b.item_code AND lo.warehouse = b.warehouse
WHERE b.actual_qty > 0
	AND (lo.last_out IS NULL OR lo.last_out < DATE_SUB(CURDATE(), INTERVAL 90 DAY))
"""

WAREHOUSE_BRANCH_GST_SQL = """
SELECT
	w.company,
	b.warehouse,
	w.custom_branch AS branch,
	SUM(b.actual_qty) AS qty,
	COUNT(DISTINCT b.item_code) AS skus,
	SUM(b.stock_value) AS stock_value_excl_gst,
	SUM(b.stock_value * IFNULL(ftc.tax_rate, 0) / 100) AS gst_value,
	SUM(b.stock_value * (100 + IFNULL(ftc.tax_rate, 0)) / 100) AS stock_value_incl_gst
FROM `tabBin` b
INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
INNER JOIN `tabItem` i ON i.name = b.item_code
LEFT JOIN `tabFBR Tax Category` ftc ON ftc.name = i.custom_fbr_tax_category
GROUP BY w.company, b.warehouse, w.custom_branch
"""
# Bin.stock_value / valuation_rate is tax-exclusive site-wide (import.md's
# "tax-exclusive valuation rate" rule: opening stock and every Purchase
# Receipt post at exclusive cost, never inclusive) -- never a direct GST
# figure. GST is reconstructed per warehouse from each item's own FBR Tax
# Category rate, mirroring the exact inverse of the exclusive_rate() formula
# every S-branch stock import uses (exclusive = inclusive * 100/(100+rate)).
# Items with no mapped FBR Tax Category get rate=0 (IFNULL), never an assumed
# rate -- their exclusive value passes through unchanged rather than being
# silently overstated.

LIABILITY_SNAPSHOT_SQL = """
SELECT
	a.account_name,
	a.account_type,
	a.root_type,
	gl.company,
	SUM(gl.credit - gl.debit) AS outstanding
FROM `tabGL Entry` gl
INNER JOIN `tabAccount` a ON a.name = gl.account
WHERE IFNULL(gl.is_cancelled, 0) = 0
	AND a.root_type = 'Liability'
GROUP BY a.account_name, a.account_type, a.root_type, gl.company
"""

PENDING_PI_SQL = """
SELECT
	posting_date,
	due_date,
	company,
	supplier_name,
	name AS invoice,
	bill_no,
	status,
	outstanding_amount,
	base_grand_total,
	DATEDIFF(CURDATE(), due_date) AS days_overdue
FROM `tabPurchase Invoice`
WHERE docstatus = 1 AND outstanding_amount > 0.005
"""

DRAFT_PI_SQL = """
SELECT
	posting_date,
	company,
	supplier_name,
	name AS invoice,
	bill_no,
	grand_total,
	status
FROM `tabPurchase Invoice`
WHERE docstatus = 0
"""

OPEN_PO_SQL = """
SELECT
	transaction_date,
	schedule_date,
	company,
	supplier_name,
	name AS purchase_order,
	status,
	per_received,
	grand_total
FROM `tabPurchase Order`
WHERE docstatus = 1
	AND status IN ('To Receive', 'To Receive and Bill', 'To Bill')
"""

OPEN_SI_SQL = """
SELECT
	posting_date,
	due_date,
	company,
	customer_name,
	name AS invoice,
	status,
	outstanding_amount,
	DATEDIFF(CURDATE(), due_date) AS days_overdue
FROM `tabSales Invoice`
WHERE docstatus = 1
	AND outstanding_amount > 0.005
	AND IFNULL(is_pos, 0) = 0
"""


def build_accounts_liabilities() -> dict:
	wb = "aimatic-accounts-liabilities"
	gl = _query(
		"tq-liab-gl",
		"Liability GL movement",
		wb,
		[
			_source("tabGL Entry"),
			_filters(_eq("is_cancelled", 0)),
			_join("tabAccount", "account", "name", ["root_type", "account_type", "account_name"]),
			_mutate(
				"liability_amount",
				"Decimal",
				"if_else(root_type == 'Liability', credit - debit, 0)",
			),
			_mutate(
				"tax_payable_amount",
				"Decimal",
				"if_else((root_type == 'Liability') & (account_type == 'Tax'), credit - debit, 0)",
			),
			_mutate(
				"trade_payable_amount",
				"Decimal",
				"if_else((root_type == 'Liability') & (account_type == 'Payable'), credit - debit, 0)",
			),
			_mutate(
				"pos_fee_payable_amount",
				"Decimal",
				"if_else((root_type == 'Liability') & (account_type == 'Liability'), credit - debit, 0)",
			),
		],
		0,
	)
	snap = _native_query("tq-liab-snap", "Liability outstanding snapshot", wb, LIABILITY_SNAPSHOT_SQL, 1)
	pending = _native_query("tq-pending-pi", "Unpaid purchase invoices", wb, PENDING_PI_SQL, 2)
	liab = _cur("Liability movement", "liability_amount")
	tax_p = _cur("Tax payable movement", "tax_payable_amount")
	ap = _cur("Trade payables movement", "trade_payable_amount")
	fee = _cur("POS service fee movement", "pos_fee_payable_amount")
	outst = _cur("Outstanding", "outstanding")
	pi_out = _cur("Outstanding", "outstanding_amount")
	charts = {
		"tc-liab-kpis": _number_chart(
			"tc-liab-kpis",
			"Liability movements this period",
			wb,
			"tq-liab-gl",
			[liab, tax_p, ap, fee],
			"posting_date",
			0,
		),
		"tc-liab-type": _donut(
			"tc-liab-type", "Period movement by liability type", wb, "tq-liab-gl", "account_type", liab, 1
		),
		"tc-liab-acct": _row(
			"tc-liab-acct", "Period movement by account", wb, "tq-liab-gl", "account_name", liab, 2, 15
		),
		"tc-liab-snap": _row(
			"tc-liab-snap", "Outstanding liabilities (as of now)", wb, "tq-liab-snap", "account_name", outst, 3, 15
		),
		"tc-pending-pi": _table(
			"tc-pending-pi",
			"Unpaid supplier invoices",
			wb,
			"tq-pending-pi",
			[
				_dim("Due", "due_date", "Date"),
				_dim("Supplier", "supplier_name"),
				_dim("Invoice", "invoice"),
				_dim("Status", "status"),
			],
			[pi_out, {"measure_name": "Days overdue", "column_name": "days_overdue", "data_type": "Integer", "aggregation": "max"}],
			4,
			25,
			"Days overdue",
			"desc",
		),
	}
	date_links = {
		"tc-liab-kpis": "`tq-liab-gl`.`posting_date`",
		"tc-liab-type": "`tq-liab-gl`.`posting_date`",
		"tc-liab-acct": "`tq-liab-gl`.`posting_date`",
	}
	co_links = {
		"tc-liab-kpis": "`tq-liab-gl`.`company`",
		"tc-liab-type": "`tq-liab-gl`.`company`",
		"tc-liab-acct": "`tq-liab-gl`.`company`",
		"tc-liab-snap": "`tq-liab-snap`.`company`",
		"tc-pending-pi": "`tq-pending-pi`.`company`",
	}
	dash = {
		"td-liab": {
			"name": "td-liab",
			"title": "Liabilities and Payables",
			"workbook": wb,
			"items": [
				_filter_item(
					"Date Range",
					"Date",
					"calendar",
					date_links,
					0,
					{"default_operator": "within", "default_value": "Last 12 months"},
				),
				_filter_item("Company", "String", "building-2", co_links, 4),
				_chart_item("tc-liab-kpis", "item-kpis", 0, 1, 20, 3),
				_chart_item("tc-liab-type", "item-type", 0, 4, 8, 8),
				_chart_item("tc-liab-acct", "item-acct", 8, 4, 12, 8),
				_chart_item("tc-liab-snap", "item-snap", 0, 12, 10, 9),
				_chart_item("tc-pending-pi", "item-pi", 10, 12, 10, 9),
			],
		}
	}
	return _workbook(
		wb,
		"Liabilities and Payables",
		{"tq-liab-gl": gl, "tq-liab-snap": snap, "tq-pending-pi": pending},
		charts,
		dash,
	)


def build_pending_work() -> dict:
	wb = "aimatic-pending-work"
	draft = _native_query("tq-draft-pi", "Draft purchase invoices", wb, DRAFT_PI_SQL, 0)
	open_po = _native_query("tq-open-po", "Open purchase orders", wb, OPEN_PO_SQL, 1)
	open_si = _native_query("tq-open-si", "Unpaid sales invoices", wb, OPEN_SI_SQL, 2)
	pending = _native_query("tq-unpaid-pi", "Unpaid purchase invoices", wb, PENDING_PI_SQL, 3)
	gt = _cur("Value", "grand_total")
	out = _cur("Outstanding", "outstanding_amount")
	charts = {
		"tc-draft": _table(
			"tc-draft",
			"Draft supplier invoices (not submitted)",
			wb,
			"tq-draft-pi",
			[
				_dim("Date", "posting_date", "Date"),
				_dim("Supplier", "supplier_name"),
				_dim("Invoice", "invoice"),
				_dim("Bill no", "bill_no"),
			],
			[gt],
			0,
			25,
			"Value",
			"desc",
		),
		"tc-po": _table(
			"tc-po",
			"POs to receive or bill",
			wb,
			"tq-open-po",
			[
				_dim("Date", "transaction_date", "Date"),
				_dim("Supplier", "supplier_name"),
				_dim("PO", "purchase_order"),
				_dim("Status", "status"),
			],
			[
				gt,
				{
					"measure_name": "% received",
					"column_name": "per_received",
					"data_type": "Decimal",
					"aggregation": "avg",
				},
			],
			1,
			25,
			"Value",
			"desc",
		),
		"tc-pi": _table(
			"tc-pi",
			"Unpaid supplier invoices",
			wb,
			"tq-unpaid-pi",
			[
				_dim("Due", "due_date", "Date"),
				_dim("Supplier", "supplier_name"),
				_dim("Invoice", "invoice"),
				_dim("Status", "status"),
			],
			[
				out,
				{
					"measure_name": "Days overdue",
					"column_name": "days_overdue",
					"data_type": "Integer",
					"aggregation": "max",
				},
			],
			2,
			25,
			"Days overdue",
			"desc",
		),
		"tc-si": _table(
			"tc-si",
			"Unpaid customer invoices (non-POS)",
			wb,
			"tq-open-si",
			[
				_dim("Due", "due_date", "Date"),
				_dim("Customer", "customer_name"),
				_dim("Invoice", "invoice"),
				_dim("Status", "status"),
			],
			[
				out,
				{
					"measure_name": "Days overdue",
					"column_name": "days_overdue",
					"data_type": "Integer",
					"aggregation": "max",
				},
			],
			3,
			25,
			"Days overdue",
			"desc",
		),
	}
	co_links = {
		"tc-draft": "`tq-draft-pi`.`company`",
		"tc-po": "`tq-open-po`.`company`",
		"tc-pi": "`tq-unpaid-pi`.`company`",
		"tc-si": "`tq-open-si`.`company`",
	}
	dash = {
		"td-pending": {
			"name": "td-pending",
			"title": "Pending Invoices and Orders",
			"workbook": wb,
			"items": [
				_filter_item("Company", "String", "building-2", co_links, 0),
				_chart_item("tc-draft", "item-draft", 0, 1, 10, 9),
				_chart_item("tc-po", "item-po", 10, 1, 10, 9),
				_chart_item("tc-pi", "item-pi", 0, 10, 10, 9),
				_chart_item("tc-si", "item-si", 10, 10, 10, 9),
			],
		}
	}
	return _workbook(
		wb,
		"Pending Invoices and Orders",
		{"tq-draft-pi": draft, "tq-open-po": open_po, "tq-open-si": open_si, "tq-unpaid-pi": pending},
		charts,
		dash,
	)


def build_goods_abc() -> dict:
	wb = "aimatic-goods-abc"
	sales = _native_query("tq-sales-abc", "Sales ABC (12 months)", wb, SALES_ABC_SQL, 0)
	stock = _native_query("tq-stock-abc", "Stock ABC (on hand)", wb, STOCK_ABC_SQL, 1)
	rev = _cur("Revenue", "revenue")
	val = _cur("Stock value", "stock_value")
	qty = {"measure_name": "Qty", "column_name": "qty", "data_type": "Decimal", "aggregation": "sum"}
	skus = {
		"measure_name": "SKUs",
		"column_name": "item_code",
		"data_type": "Integer",
		"aggregation": "count_distinct",
	}
	charts = {
		"tc-sales-class": _donut("tc-sales-class", "Sales value by ABC class", wb, "tq-sales-abc", "abc_class", rev, 0),
		"tc-sales-skus": _donut("tc-sales-skus", "SKU count by ABC class (sales)", wb, "tq-sales-abc", "abc_class", skus, 1),
		"tc-sales-a": _table(
			"tc-sales-a",
			"Class A goods by sales (80% of revenue)",
			wb,
			"tq-sales-abc",
			[
				_dim("Item", "item_name"),
				_dim("Group", "item_group"),
				_dim("Class", "abc_class"),
			],
			[rev, qty, {"measure_name": "Cum %", "column_name": "cum_pct", "data_type": "Decimal", "aggregation": "max"}],
			2,
			30,
			"Revenue",
			"desc",
		),
		"tc-stock-class": _donut("tc-stock-class", "Stock value by ABC class", wb, "tq-stock-abc", "abc_class", val, 3),
		"tc-stock-a": _table(
			"tc-stock-a",
			"Class A goods by stock value",
			wb,
			"tq-stock-abc",
			[
				_dim("Item", "item_name"),
				_dim("Group", "item_group"),
				_dim("Class", "abc_class"),
			],
			[val, qty, {"measure_name": "Cum %", "column_name": "cum_pct", "data_type": "Decimal", "aggregation": "max"}],
			4,
			30,
			"Stock value",
			"desc",
		),
	}
	# Table charts cannot easily filter to class A only without a chart filter; show ranked list (A at top).
	dash = {
		"td-abc": {
			"name": "td-abc",
			"title": "Goods ABC Analysis",
			"workbook": wb,
			"items": [
				_chart_item("tc-sales-class", "item-s-class", 0, 0, 10, 8),
				_chart_item("tc-sales-skus", "item-s-skus", 10, 0, 10, 8),
				_chart_item("tc-sales-a", "item-s-a", 0, 8, 20, 9),
				_chart_item("tc-stock-class", "item-st-class", 0, 17, 10, 8),
				_chart_item("tc-stock-a", "item-st-a", 10, 17, 10, 8),
			],
		}
	}
	return _workbook(wb, "Goods ABC Analysis", {"tq-sales-abc": sales, "tq-stock-abc": stock}, charts, dash)


def build_inventory_productivity() -> dict:
	wb = "aimatic-inventory-kpis"
	bins = _query(
		"tq-bins",
		"Bin balances",
		wb,
		[_source("tabBin"), _join("tabItem", "item_code", "name", ["item_name", "item_group", "disabled"])],
		0,
	)
	sle = _query(
		"tq-sle",
		"Stock movements",
		wb,
		[
			_source("tabStock Ledger Entry"),
			_filters(_eq("is_cancelled", 0)),
			_mutate(
				"cogs_value",
				"Decimal",
				"if_else(actual_qty < 0, -stock_value_difference, 0)",
			),
			_mutate(
				"inbound_value",
				"Decimal",
				"if_else(actual_qty > 0, stock_value_difference, 0)",
			),
		],
		1,
	)
	dead = _native_query("tq-dead", "Slow / no-movement stock (90 days)", wb, DEAD_STOCK_SQL, 2)
	wh_gst = _native_query(
		"tq-wh-branch-gst", "Stock by warehouse & branch (incl. GST)", wb, WAREHOUSE_BRANCH_GST_SQL, 3
	)
	stock = {
		"measure_name": "Stock value",
		"column_name": "stock_value",
		"data_type": "Decimal",
		"aggregation": "sum",
		"format": "currency",
	}
	qty = {
		"measure_name": "Qty",
		"column_name": "actual_qty",
		"data_type": "Decimal",
		"aggregation": "sum",
	}
	cogs = {
		"measure_name": "COGS",
		"column_name": "cogs_value",
		"data_type": "Decimal",
		"aggregation": "sum",
		"format": "currency",
	}
	inbound = {
		"measure_name": "Inbound value",
		"column_name": "inbound_value",
		"data_type": "Decimal",
		"aggregation": "sum",
		"format": "currency",
	}
	# tq-wh-branch-gst is already pre-aggregated one row per (company,
	# warehouse, branch); rows below select exactly those same columns so
	# the table's own re-aggregation is a safe no-op over a single matching
	# source row per group (same pattern as the Goods ABC per-item tables).
	wgb_qty = {"measure_name": "Qty", "column_name": "qty", "data_type": "Decimal", "aggregation": "sum"}
	wgb_skus = {"measure_name": "SKUs", "column_name": "skus", "data_type": "Integer", "aggregation": "sum"}
	wgb_excl = {
		"measure_name": "Stock value (excl. GST)",
		"column_name": "stock_value_excl_gst",
		"data_type": "Decimal",
		"aggregation": "sum",
		"format": "currency",
	}
	wgb_gst = {
		"measure_name": "GST value",
		"column_name": "gst_value",
		"data_type": "Decimal",
		"aggregation": "sum",
		"format": "currency",
	}
	wgb_incl = {
		"measure_name": "Stock value (incl. GST)",
		"column_name": "stock_value_incl_gst",
		"data_type": "Decimal",
		"aggregation": "sum",
		"format": "currency",
	}
	# Bin has no posting_date; use SLE date for movement KPIs. Bin snapshot has no date filter.
	bin_kpis = {
		"name": "tc-bin-kpis",
		"title": "On-hand inventory",
		"workbook": wb,
		"folder": None,
		"sort_order": 0,
		"query": "tq-bins",
		"chart_type": "Number",
		"config": {
			"number_columns": [
				stock,
				qty,
				{
					"measure_name": "SKUs",
					"column_name": "item_code",
					"data_type": "Integer",
					"aggregation": "count_distinct",
				},
			],
			"number_column_options": [
				{"decimal": 2, "shorten_numbers": True},
				{"decimal": 2, "shorten_numbers": True},
				{"shorten_numbers": False},
			],
			"comparison": False,
			"sparkline": False,
			"order_by": [],
			"limit": 100,
			"filters": {"logical_operator": "And", "filters": []},
		},
	}
	charts = {
		"tc-bin-kpis": bin_kpis,
		"tc-cogs": _number_chart("tc-cogs", "Movement KPIs", wb, "tq-sle", [cogs, inbound], "posting_date", 1),
		"tc-cogs-trend": _line("tc-cogs-trend", "COGS trend", wb, "tq-sle", "posting_date", cogs, 2),
		"tc-wh": _donut("tc-wh", "Stock value by warehouse", wb, "tq-bins", "warehouse", stock, 3),
		"tc-grp": _donut("tc-grp", "Stock value by item group", wb, "tq-bins", "item_group", stock, 4),
		"tc-top-stock": _row("tc-top-stock", "Top items by value", wb, "tq-bins", "item_name", stock, 5),
		"tc-dead": _table(
			"tc-dead",
			"Goods with qty but no outward movement in 90 days",
			wb,
			"tq-dead",
			[
				_dim("Item", "item_name"),
				_dim("Group", "item_group"),
				_dim("Warehouse", "warehouse"),
				_dim("Last out", "last_out", "Date"),
			],
			[stock, qty],
			6,
			25,
			"Stock value",
			"desc",
		),
		"tc-wh-branch-gst": _table(
			"tc-wh-branch-gst",
			"Stock by warehouse & branch (incl. GST)",
			wb,
			"tq-wh-branch-gst",
			[
				_dim("Company", "company"),
				_dim("Warehouse", "warehouse"),
				_dim("Branch", "branch"),
			],
			[wgb_qty, wgb_skus, wgb_excl, wgb_gst, wgb_incl],
			7,
			50,
			"Stock value (incl. GST)",
			"desc",
		),
	}
	sle_date = {
		"tc-cogs": "`tq-sle`.`posting_date`",
		"tc-cogs-trend": "`tq-sle`.`posting_date`",
	}
	co_links = {
		"tc-cogs": "`tq-sle`.`company`",
		"tc-cogs-trend": "`tq-sle`.`company`",
	}
	dash = {
		"td-inv": {
			"name": "td-inv",
			"title": "Inventory KPIs",
			"workbook": wb,
			"items": [
				_filter_item(
					"Date Range",
					"Date",
					"calendar",
					sle_date,
					0,
					{"default_operator": "within", "default_value": "Last 12 months"},
				),
				_filter_item("Company", "String", "building-2", co_links, 4),
				_chart_item("tc-bin-kpis", "item-bin", 0, 1, 10, 3),
				_chart_item("tc-cogs", "item-cogs", 10, 1, 10, 3),
				_chart_item("tc-cogs-trend", "item-trend", 0, 4, 12, 8),
				_chart_item("tc-wh", "item-wh", 12, 4, 8, 8),
				_chart_item("tc-grp", "item-grp", 0, 12, 10, 8),
				_chart_item("tc-top-stock", "item-top", 10, 12, 10, 8),
				_chart_item("tc-dead", "item-dead", 0, 20, 20, 9),
				_chart_item("tc-wh-branch-gst", "item-wh-branch-gst", 0, 29, 20, 9),
			],
		}
	}
	return _workbook(
		wb,
		"Inventory KPIs",
		{"tq-bins": bins, "tq-sle": sle, "tq-dead": dead, "tq-wh-branch-gst": wh_gst},
		charts,
		dash,
	)


OWNER_DAILY_SQL = """
WITH sales AS (
	SELECT
		posting_date,
		company,
		COALESCE(NULLIF(branch, ''), 'Unassigned') AS branch,
		COUNT(*) AS tickets,
		SUM(base_net_total) AS net_sales,
		SUM(base_grand_total) AS customer_take,
		SUM(CASE WHEN is_return = 1 THEN 1 ELSE 0 END) AS return_tickets,
		SUM(CASE WHEN is_return = 1 THEN base_net_total ELSE 0 END) AS return_amount,
		SUM(CASE WHEN pos_profile LIKE '%Food Panda%' THEN base_net_total ELSE 0 END) AS delivery_sales,
		SUM(CASE WHEN IFNULL(pos_profile, '') NOT LIKE '%Food Panda%' THEN base_net_total ELSE 0 END) AS store_sales,
		SUM(CASE WHEN custom_fbr_status = 'Failed' THEN 1 ELSE 0 END) AS tax_failed
	FROM `tabPOS Invoice`
	WHERE docstatus = 1
		AND posting_date >= DATE_SUB(CURDATE(), INTERVAL 14 DAY)
	GROUP BY posting_date, company, COALESCE(NULLIF(branch, ''), 'Unassigned')
),
basket AS (
	SELECT
		p.posting_date,
		p.company,
		COALESCE(NULLIF(p.branch, ''), 'Unassigned') AS branch,
		SUM(pii.qty) AS units
	FROM `tabPOS Invoice` p
	INNER JOIN `tabPOS Invoice Item` pii
		ON pii.parent = p.name
		AND pii.parenttype = 'POS Invoice'
	WHERE p.docstatus = 1
		AND IFNULL(p.is_return, 0) = 0
		AND p.posting_date >= DATE_SUB(CURDATE(), INTERVAL 14 DAY)
	GROUP BY p.posting_date, p.company, COALESCE(NULLIF(p.branch, ''), 'Unassigned')
),
cogs AS (
	SELECT
		sle.posting_date,
		sle.company,
		COALESCE(NULLIF(w.custom_branch, ''), 'Unassigned') AS branch,
		SUM(CASE WHEN sle.actual_qty < 0 THEN -sle.stock_value_difference ELSE 0 END) AS cogs
	FROM `tabStock Ledger Entry` sle
	INNER JOIN `tabSales Invoice` si ON si.name = sle.voucher_no AND si.is_pos = 1
	LEFT JOIN `tabWarehouse` w ON w.name = sle.warehouse
	WHERE IFNULL(sle.is_cancelled, 0) = 0
		AND sle.voucher_type = 'Sales Invoice'
		AND sle.posting_date >= DATE_SUB(CURDATE(), INTERVAL 14 DAY)
	GROUP BY sle.posting_date, sle.company, COALESCE(NULLIF(w.custom_branch, ''), 'Unassigned')
)
SELECT
	s.posting_date,
	s.company,
	s.branch,
	s.tickets,
	s.net_sales,
	s.customer_take,
	s.store_sales,
	s.delivery_sales,
	s.return_tickets,
	s.return_amount,
	s.tax_failed,
	IFNULL(c.cogs, 0) AS cogs,
	s.net_sales - IFNULL(c.cogs, 0) AS gross_profit,
	IFNULL(bk.units, 0) AS units,
	ROUND(s.net_sales / NULLIF(s.tickets, 0), 2) AS avg_ticket,
	ROUND(IFNULL(bk.units, 0) / NULLIF(s.tickets, 0), 2) AS units_per_ticket
FROM sales s
LEFT JOIN cogs c
	ON c.posting_date = s.posting_date
	AND c.company = s.company
	AND c.branch = s.branch
LEFT JOIN basket bk
	ON bk.posting_date = s.posting_date
	AND bk.company = s.company
	AND bk.branch = s.branch
"""

OWNER_COUNTER_SQL = """
SELECT
	posting_date,
	company,
	COALESCE(NULLIF(branch, ''), 'Unassigned') AS branch,
	pos_profile,
	COUNT(*) AS tickets,
	SUM(base_net_total) AS net_sales,
	SUM(base_grand_total) AS customer_take,
	SUM(CASE WHEN is_return = 1 THEN 1 ELSE 0 END) AS return_tickets
FROM `tabPOS Invoice`
WHERE docstatus = 1
	AND posting_date >= DATE_SUB(CURDATE(), INTERVAL 14 DAY)
GROUP BY posting_date, company, COALESCE(NULLIF(branch, ''), 'Unassigned'), pos_profile
"""

OWNER_PAY_SQL = """
SELECT
	p.posting_date,
	p.company,
	COALESCE(NULLIF(p.branch, ''), 'Unassigned') AS branch,
	sip.mode_of_payment,
	SUM(sip.base_amount) AS collected
FROM `tabSales Invoice Payment` sip
INNER JOIN `tabPOS Invoice` p ON p.name = sip.parent
WHERE sip.parenttype = 'POS Invoice'
	AND p.docstatus = 1
	AND p.posting_date >= DATE_SUB(CURDATE(), INTERVAL 14 DAY)
	AND sip.base_amount <> 0
GROUP BY p.posting_date, p.company, COALESCE(NULLIF(p.branch, ''), 'Unassigned'), sip.mode_of_payment
"""

OWNER_DAYPART_SQL = """
SELECT
	posting_date,
	company,
	COALESCE(NULLIF(branch, ''), 'Unassigned') AS branch,
	CASE
		WHEN HOUR(posting_time) < 12 THEN '1 Morning'
		WHEN HOUR(posting_time) < 17 THEN '2 Afternoon'
		WHEN HOUR(posting_time) < 21 THEN '3 Evening'
		ELSE '4 Night'
	END AS daypart,
	COUNT(*) AS tickets,
	SUM(base_net_total) AS net_sales
FROM `tabPOS Invoice`
WHERE docstatus = 1
	AND posting_date >= DATE_SUB(CURDATE(), INTERVAL 14 DAY)
GROUP BY posting_date, company, COALESCE(NULLIF(branch, ''), 'Unassigned'),
	CASE
		WHEN HOUR(posting_time) < 12 THEN '1 Morning'
		WHEN HOUR(posting_time) < 17 THEN '2 Afternoon'
		WHEN HOUR(posting_time) < 21 THEN '3 Evening'
		ELSE '4 Night'
	END
"""

OWNER_FAILED_TAX_SQL = """
SELECT
	posting_date,
	company,
	COALESCE(NULLIF(branch, ''), 'Unassigned') AS branch,
	pos_profile,
	name AS invoice,
	customer_name,
	base_grand_total,
	custom_fbr_status AS submission_status
FROM `tabPOS Invoice`
WHERE docstatus = 1
	AND posting_date >= DATE_SUB(CURDATE(), INTERVAL 14 DAY)
	AND custom_fbr_status = 'Failed'
"""

OWNER_OVERDUE_PI_SQL = """
SELECT
	due_date,
	company,
	COALESCE(NULLIF(branch, ''), 'Unassigned') AS branch,
	supplier_name,
	name AS invoice,
	bill_no,
	status,
	outstanding_amount,
	DATEDIFF(CURDATE(), due_date) AS days_overdue
FROM `tabPurchase Invoice`
WHERE docstatus = 1
	AND outstanding_amount > 0.005
	AND due_date < CURDATE()
"""

OWNER_NEG_STOCK_SQL = """
SELECT
	w.company,
	COALESCE(NULLIF(w.custom_branch, ''), 'Unassigned') AS branch,
	b.item_code,
	i.item_name,
	i.item_group,
	b.warehouse,
	b.actual_qty,
	b.stock_value
FROM `tabBin` b
INNER JOIN `tabItem` i ON i.name = b.item_code
INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
WHERE b.actual_qty < 0
"""

# This deliberately follows the POS Invoice Gross Margin report's cost path:
# POS lines are matched to their consolidated Sales Invoice Item, then to its
# Stock Ledger Entry cost. POS tickets still awaiting closing, non-stock
# lines, and rows without a ledger cost are omitted rather than reporting a
# misleading margin of 100%.
OWNER_ITEM_MARGIN_SQL = """
WITH pos_lines AS (
	SELECT
		p.posting_date,
		p.company,
		COALESCE(NULLIF(p.branch, ''), 'Unassigned') AS branch,
		pii.item_code,
		pii.item_name,
		pii.item_group,
		pii.qty,
		pii.base_net_amount AS net_sales,
		sii.parent AS sales_invoice,
		sii.name AS sales_invoice_item
	FROM `tabPOS Invoice` p
	INNER JOIN `tabPOS Invoice Item` pii
		ON pii.parent = p.name
		AND pii.parenttype = 'POS Invoice'
		AND pii.docstatus = 1
	LEFT JOIN `tabSales Invoice Item` sii
		ON sii.parent = p.consolidated_invoice
		AND sii.pos_invoice = p.name
		AND sii.pos_invoice_item = pii.name
		AND sii.docstatus = 1
	WHERE p.docstatus = 1
		AND p.posting_date >= DATE_SUB(CURDATE(), INTERVAL 14 DAY)
),
cogs_by_item AS (
	SELECT
		sle.voucher_no AS sales_invoice,
		sle.voucher_detail_no AS sales_invoice_item,
		SUM(-sle.stock_value_difference) AS cogs
	FROM `tabStock Ledger Entry` sle
	INNER JOIN (
		SELECT DISTINCT sales_invoice, sales_invoice_item
		FROM pos_lines
		WHERE sales_invoice IS NOT NULL
			AND sales_invoice_item IS NOT NULL
	) pl
		ON pl.sales_invoice = sle.voucher_no
		AND pl.sales_invoice_item = sle.voucher_detail_no
	WHERE sle.voucher_type = 'Sales Invoice'
		AND IFNULL(sle.is_cancelled, 0) = 0
	GROUP BY sle.voucher_no, sle.voucher_detail_no
)
SELECT
	pl.posting_date,
	pl.company,
	pl.branch,
	pl.item_code,
	MAX(pl.item_name) AS item_name,
	MAX(pl.item_group) AS item_group,
	SUM(pl.qty) AS qty,
	SUM(pl.net_sales) AS net_sales,
	SUM(c.cogs) AS cogs,
	SUM(pl.net_sales) - SUM(c.cogs) AS gross_margin,
	(SUM(pl.net_sales) - SUM(c.cogs)) / NULLIF(ABS(SUM(pl.net_sales)), 0) AS gross_margin_rate
FROM pos_lines pl
INNER JOIN cogs_by_item c
	ON c.sales_invoice = pl.sales_invoice
	AND c.sales_invoice_item = pl.sales_invoice_item
GROUP BY pl.posting_date, pl.company, pl.branch, pl.item_code
"""


OWNER_AVAILABILITY_SQL = """
SELECT
	w.company,
	COALESCE(NULLIF(w.custom_branch, ''), 'Unassigned') AS branch,
	w.name AS warehouse,
	COUNT(*) AS stocked_skus,
	SUM(CASE WHEN b.actual_qty <= 0 THEN 1 ELSE 0 END) AS out_of_stock_skus,
	SUM(CASE WHEN b.actual_qty < 0 THEN 1 ELSE 0 END) AS negative_skus,
	ROUND(
		100 * SUM(CASE WHEN b.actual_qty > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
		2
	) AS on_shelf_rate,
	COALESCE(SUM(b.stock_value), 0) AS stock_value
FROM `tabBin` b
INNER JOIN `tabWarehouse` w
	ON w.name = b.warehouse
	AND w.disabled = 0
INNER JOIN `tabItem` i
	ON i.name = b.item_code
	AND i.is_stock_item = 1
	AND IFNULL(i.disabled, 0) = 0
GROUP BY w.company, COALESCE(NULLIF(w.custom_branch, ''), 'Unassigned'), w.name
"""


def build_owner_flash() -> dict:
	wb = "aimatic-owner-flash"
	daily = _native_query("tq-owner-daily", "Daily scorecard", wb, OWNER_DAILY_SQL, 0)
	counter = _native_query("tq-owner-counter", "Counter scorecard", wb, OWNER_COUNTER_SQL, 1)
	pays = _native_query("tq-owner-pay", "Money in the till", wb, OWNER_PAY_SQL, 2)
	daypart = _native_query("tq-owner-daypart", "Daypart", wb, OWNER_DAYPART_SQL, 3)
	failed = _native_query("tq-owner-tax-fail", "Failed tax invoices", wb, OWNER_FAILED_TAX_SQL, 4)
	overdue = _native_query("tq-owner-ap", "Overdue supplier bills", wb, OWNER_OVERDUE_PI_SQL, 5)
	neg = _native_query("tq-owner-neg", "Negative stock", wb, OWNER_NEG_STOCK_SQL, 6)
	item_margin = _native_query(
		"tq-owner-item-margin", "Item gross margin (last 14 days)", wb, OWNER_ITEM_MARGIN_SQL, 7
	)
	availability = _native_query(
		"tq-owner-availability", "On-shelf availability", wb, OWNER_AVAILABILITY_SQL, 8
	)
	net = _cur("Net sales", "net_sales")
	gp = _cur("Gross profit", "gross_profit")
	cogs = _cur("COGS", "cogs")
	store = _cur("Store sales", "store_sales")
	delivery = _cur("Delivery sales", "delivery_sales")
	take = _cur("Customer take", "customer_take")
	collected = _cur("Collected", "collected")
	tickets = {"measure_name": "Tickets", "column_name": "tickets", "data_type": "Integer", "aggregation": "sum"}
	returns = {"measure_name": "Return tickets", "column_name": "return_tickets", "data_type": "Integer", "aggregation": "sum"}
	tax_fail = {"measure_name": "Failed tax invoices", "column_name": "tax_failed", "data_type": "Integer", "aggregation": "sum"}
	avg_ticket = {"measure_name": "Average ticket", "column_name": "avg_ticket", "data_type": "Decimal", "aggregation": "avg", "format": "currency"}
	basket_size = {"measure_name": "Items per basket", "column_name": "units_per_ticket", "data_type": "Decimal", "aggregation": "avg"}
	on_shelf = {"measure_name": "On-shelf rate", "column_name": "on_shelf_rate", "data_type": "Decimal", "aggregation": "avg", "format": "percent"}
	oos = {"measure_name": "Out-of-stock SKUs", "column_name": "out_of_stock_skus", "data_type": "Integer", "aggregation": "sum"}
	neg_skus = {"measure_name": "Negative SKUs", "column_name": "negative_skus", "data_type": "Integer", "aggregation": "sum"}
	charts = {
		"tc-flash": _number_chart(
			"tc-flash",
			"Last 14 days: sales, customer take, tickets and risk",
			wb,
			"tq-owner-daily",
			[net, take, tickets, avg_ticket, basket_size, returns, tax_fail, store, delivery],
			"posting_date",
			0,
		),
		"tc-margin-kpis": _number_chart(
			"tc-margin-kpis",
			"Gross profit and cost of goods sold",
			wb,
			"tq-owner-daily",
			[gp, cogs],
			"posting_date",
			1,
		),
		"tc-daily": _line("tc-daily", "Net sales by day", wb, "tq-owner-daily", "posting_date", net, 1),
		"tc-gp": _line("tc-gp", "Gross profit by day", wb, "tq-owner-daily", "posting_date", gp, 2),
		"tc-counter": _row("tc-counter", "Which counter is making money", wb, "tq-owner-counter", "pos_profile", net, 3, 8),
		"tc-pay": _donut("tc-pay", "What actually hit the till (zero-amount delivery credit excluded)", wb, "tq-owner-pay", "mode_of_payment", collected, 4),
		"tc-daypart": _row("tc-daypart", "When the store is busy", wb, "tq-owner-daypart", "daypart", net, 5, 4),
		"tc-tax-fail": _table(
			"tc-tax-fail",
			"Tax invoices that failed — fix these today",
			wb,
			"tq-owner-tax-fail",
			[
				_dim("Date", "posting_date", "Date"),
				_dim("Branch", "branch"),
				_dim("Counter", "pos_profile"),
				_dim("Invoice", "invoice"),
				_dim("Customer", "customer_name"),
			],
			[_cur("Take", "base_grand_total")],
			6,
			20,
			"Take",
			"desc",
		),
		"tc-ap": _table(
			"tc-ap",
			"Supplier bills already overdue — cash leaving the business",
			wb,
			"tq-owner-ap",
			[
				_dim("Due", "due_date", "Date"),
				_dim("Branch", "branch"),
				_dim("Supplier", "supplier_name"),
				_dim("Bill", "bill_no"),
				_dim("Invoice", "invoice"),
			],
			[
				_cur("Outstanding", "outstanding_amount"),
				{
					"measure_name": "Days overdue",
					"column_name": "days_overdue",
					"data_type": "Integer",
					"aggregation": "max",
				},
			],
			7,
			20,
			"Days overdue",
			"desc",
		),
		"tc-availability": _snapshot_chart(
			"tc-availability",
			"On-shelf availability right now",
			wb,
			"tq-owner-availability",
			[on_shelf, oos, neg_skus],
			8,
		),
		"tc-availability-branch": _table(
			"tc-availability-branch",
			"What the shelves cannot sell — stocked SKUs at or below zero",
			wb,
			"tq-owner-availability",
			[
				_dim("Branch", "branch"),
				_dim("Warehouse", "warehouse"),
			],
			[
				{"measure_name": "Stocked SKUs", "column_name": "stocked_skus", "data_type": "Integer", "aggregation": "sum"},
				oos,
				neg_skus,
				_cur("Stock value", "stock_value"),
			],
			9,
			20,
			"Out-of-stock SKUs",
			"desc",
		),
		"tc-neg": _table(
			"tc-neg",
			"Negative stock — sales without stock (shrink or missed GRN)",
			wb,
			"tq-owner-neg",
			[
				_dim("Branch", "branch"),
				_dim("Item", "item_name"),
				_dim("Group", "item_group"),
				_dim("Warehouse", "warehouse"),
			],
			[
				{"measure_name": "Qty", "column_name": "actual_qty", "data_type": "Decimal", "aggregation": "sum"},
				_cur("Value", "stock_value"),
			],
			8,
			20,
			"Qty",
			"asc",
		),
		"tc-item-margin": _table(
			"tc-item-margin",
			"Item-wise gross margin — last 14 days (ledger-covered sales only)",
			wb,
			"tq-owner-item-margin",
			[
				_dim("Date", "posting_date", "Date"),
				_dim("Company", "company"),
				_dim("Branch", "branch"),
				_dim("Item", "item_name"),
				_dim("Code", "item_code"),
				_dim("Group", "item_group"),
			],
			[
				{"measure_name": "Qty", "column_name": "qty", "data_type": "Decimal", "aggregation": "sum"},
				_cur("Net sales", "net_sales"),
				_cur("COGS", "cogs"),
				_cur("Gross margin", "gross_margin"),
				{
					"measure_name": "GM %",
					"column_name": "gross_margin_rate",
					"data_type": "Decimal",
					"aggregation": "avg",
					"format": "percent",
				},
			],
			9,
			50,
			"Gross margin",
			"desc",
		),
	}
	date_links = {
		"tc-flash": "`tq-owner-daily`.`posting_date`",
		"tc-daily": "`tq-owner-daily`.`posting_date`",
		"tc-margin-kpis": "`tq-owner-daily`.`posting_date`",
		"tc-gp": "`tq-owner-daily`.`posting_date`",
		"tc-counter": "`tq-owner-counter`.`posting_date`",
		"tc-pay": "`tq-owner-pay`.`posting_date`",
		"tc-daypart": "`tq-owner-daypart`.`posting_date`",
		"tc-tax-fail": "`tq-owner-tax-fail`.`posting_date`",
		"tc-item-margin": "`tq-owner-item-margin`.`posting_date`",
	}
	co_links = {
		**{k: v.replace("posting_date", "company") for k, v in date_links.items()},
		"tc-ap": "`tq-owner-ap`.`company`",
		"tc-neg": "`tq-owner-neg`.`company`",
		"tc-availability": "`tq-owner-availability`.`company`",
		"tc-availability-branch": "`tq-owner-availability`.`company`",
	}
	br_links = {
		"tc-flash": "`tq-owner-daily`.`branch`",
		"tc-daily": "`tq-owner-daily`.`branch`",
		"tc-margin-kpis": "`tq-owner-daily`.`branch`",
		"tc-gp": "`tq-owner-daily`.`branch`",
		"tc-counter": "`tq-owner-counter`.`branch`",
		"tc-pay": "`tq-owner-pay`.`branch`",
		"tc-daypart": "`tq-owner-daypart`.`branch`",
		"tc-tax-fail": "`tq-owner-tax-fail`.`branch`",
		"tc-ap": "`tq-owner-ap`.`branch`",
		"tc-neg": "`tq-owner-neg`.`branch`",
		"tc-item-margin": "`tq-owner-item-margin`.`branch`",
		"tc-availability": "`tq-owner-availability`.`branch`",
		"tc-availability-branch": "`tq-owner-availability`.`branch`",
	}
	wh_links = {
		"tc-neg": "`tq-owner-neg`.`warehouse`",
		"tc-availability": "`tq-owner-availability`.`warehouse`",
		"tc-availability-branch": "`tq-owner-availability`.`warehouse`",
	}
	dash = {
		"td-owner": {
			"name": "td-owner",
			"title": "CEO",
			"preview_image": "/assets/aimatic/images/insights-ceo-preview.svg",
			"workbook": wb,
			"items": [
				_filter_item(
					"Date Range",
					"Date",
					"calendar",
					date_links,
					0,
					{"default_operator": "within", "default_value": "Last 14 days"},
				),
				_filter_item("Company", "String", "building-2", co_links, 4),
				_filter_item("Branch", "String", "store", br_links, 8),
				_filter_item("Warehouse", "String", "warehouse", wh_links, 12),
				_chart_item("tc-flash", "item-flash", 0, 1, 13, 4),
				_chart_item("tc-margin-kpis", "item-margin-kpis", 13, 1, 7, 4),
				_chart_item("tc-availability", "item-availability", 0, 5, 20, 3),
				_chart_item("tc-daily", "item-daily", 0, 8, 10, 8),
				_chart_item("tc-gp", "item-gp", 10, 8, 10, 8),
				_chart_item("tc-counter", "item-counter", 0, 16, 7, 8),
				_chart_item("tc-pay", "item-pay", 7, 16, 6, 8),
				_chart_item("tc-daypart", "item-daypart", 13, 16, 7, 8),
				_chart_item("tc-tax-fail", "item-tax", 0, 24, 7, 8),
				_chart_item("tc-ap", "item-ap", 7, 24, 7, 8),
				_chart_item("tc-neg", "item-neg", 14, 24, 6, 8),
				_chart_item("tc-availability-branch", "item-availability-branch", 0, 32, 20, 7),
				_chart_item("tc-item-margin", "item-margin", 0, 39, 20, 10),
			],
		}
	}
	return _workbook(
		wb,
		"CEO",
		{
			"tq-owner-daily": daily,
			"tq-owner-counter": counter,
			"tq-owner-pay": pays,
			"tq-owner-daypart": daypart,
			"tq-owner-tax-fail": failed,
			"tq-owner-ap": overdue,
			"tq-owner-neg": neg,
			"tq-owner-item-margin": item_margin,
			"tq-owner-availability": availability,
		},
		charts,
		dash,
	)


BASKET_DETAIL_SQL = """
WITH basket_lines AS (
	SELECT
		p.name AS invoice,
		p.posting_date,
		p.company,
		COALESCE(NULLIF(p.branch, ''), 'Unassigned') AS branch,
		p.customer,
		p.base_net_total AS net_sales,
		pii.item_code,
		SUM(pii.qty) AS qty
	FROM `tabPOS Invoice` p
	INNER JOIN `tabPOS Invoice Item` pii
		ON pii.parent = p.name
		AND pii.parenttype = 'POS Invoice'
		AND pii.docstatus = 1
	WHERE p.docstatus = 1
		AND IFNULL(p.is_return, 0) = 0
		AND p.posting_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
		AND pii.qty > 0
	GROUP BY
		p.name,
		p.posting_date,
		p.company,
		COALESCE(NULLIF(p.branch, ''), 'Unassigned'),
		p.customer,
		p.base_net_total,
		pii.item_code
)
SELECT
	invoice,
	posting_date,
	company,
	branch,
	customer,
	net_sales,
	COUNT(*) AS distinct_items,
	SUM(qty) AS units
FROM basket_lines
GROUP BY invoice, posting_date, company, branch, customer, net_sales
"""


PRODUCT_RELEVANCE_SQL = """
WITH basket_totals AS (
	SELECT
		p.name AS invoice,
		p.posting_date,
		p.company,
		COALESCE(NULLIF(p.branch, ''), 'Unassigned') AS branch,
		p.base_net_total AS net_sales
	FROM `tabPOS Invoice` p
	WHERE p.docstatus = 1
		AND IFNULL(p.is_return, 0) = 0
		AND p.posting_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
),
branch_day AS (
	SELECT
		posting_date,
		company,
		branch,
		COUNT(*) AS branch_baskets,
		AVG(net_sales) AS branch_avg_basket,
		SUM(net_sales) AS branch_net_sales
	FROM basket_totals
	GROUP BY posting_date, company, branch
),
item_lines AS (
	SELECT
		bt.invoice,
		bt.posting_date,
		bt.company,
		bt.branch,
		pii.item_code,
		MAX(pii.item_name) AS item_name,
		MAX(pii.item_group) AS item_group,
		MAX(bt.net_sales) AS basket_net_sales,
		SUM(pii.qty) AS units,
		SUM(pii.base_net_amount) AS item_revenue
	FROM basket_totals bt
	INNER JOIN `tabPOS Invoice Item` pii
		ON pii.parent = bt.invoice
		AND pii.parenttype = 'POS Invoice'
		AND pii.docstatus = 1
		AND pii.qty > 0
	GROUP BY bt.invoice, bt.posting_date, bt.company, bt.branch, pii.item_code
),
item_day AS (
	SELECT
		posting_date,
		company,
		branch,
		item_code,
		MAX(item_name) AS item_name,
		MAX(item_group) AS item_group,
		COUNT(DISTINCT invoice) AS baskets_with_item,
		SUM(units) AS units,
		SUM(item_revenue) AS item_revenue,
		AVG(basket_net_sales) AS avg_basket_with_item
	FROM item_lines
	GROUP BY posting_date, company, branch, item_code
	HAVING COUNT(DISTINCT invoice) >= 3
)
SELECT
	id.posting_date,
	id.company,
	id.branch,
	id.item_code,
	id.item_name,
	id.item_group,
	id.baskets_with_item,
	id.units,
	id.item_revenue,
	id.avg_basket_with_item,
	ROUND(id.baskets_with_item / NULLIF(bd.branch_baskets, 0), 4) AS attach_rate,
	ROUND(id.avg_basket_with_item / NULLIF(bd.branch_avg_basket, 0), 2) AS basket_lift
FROM item_day id
INNER JOIN branch_day bd
	ON bd.posting_date = id.posting_date
	AND bd.company = id.company
	AND bd.branch = id.branch
ORDER BY basket_lift DESC, baskets_with_item DESC
LIMIT 500
"""


SINGLE_ITEM_BASKET_SQL = """
WITH basket_lines AS (
	SELECT
		p.name AS invoice,
		p.posting_date,
		p.company,
		COALESCE(NULLIF(p.branch, ''), 'Unassigned') AS branch,
		pii.item_code,
		MAX(pii.item_name) AS item_name,
		MAX(pii.item_group) AS item_group,
		SUM(pii.qty) AS qty,
		SUM(pii.base_net_amount) AS line_revenue
	FROM `tabPOS Invoice` p
	INNER JOIN `tabPOS Invoice Item` pii
		ON pii.parent = p.name
		AND pii.parenttype = 'POS Invoice'
		AND pii.docstatus = 1
	WHERE p.docstatus = 1
		AND IFNULL(p.is_return, 0) = 0
		AND p.posting_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
		AND pii.qty > 0
	GROUP BY
		p.name,
		p.posting_date,
		p.company,
		COALESCE(NULLIF(p.branch, ''), 'Unassigned'),
		pii.item_code
),
basket_size AS (
	SELECT
		invoice,
		COUNT(*) AS distinct_items,
		SUM(qty) AS units,
		SUM(line_revenue) AS basket_revenue
	FROM basket_lines
	GROUP BY invoice
)
SELECT
	bl.posting_date,
	bl.company,
	bl.branch,
	bl.item_code,
	MAX(bl.item_name) AS item_name,
	MAX(bl.item_group) AS item_group,
	COUNT(DISTINCT bl.invoice) AS single_item_baskets,
	SUM(bs.basket_revenue) AS revenue,
	AVG(bs.basket_revenue) AS avg_basket,
	AVG(bs.units) AS units_per_basket
FROM basket_lines bl
INNER JOIN basket_size bs ON bs.invoice = bl.invoice
WHERE bs.distinct_items = 1
GROUP BY bl.posting_date, bl.company, bl.branch, bl.item_code
"""


def build_basket_relevance() -> dict:
	wb = "aimatic-basket-relevance"
	detail = _native_query("tq-basket-detail", "Basket detail (last 30 days)", wb, BASKET_DETAIL_SQL, 0)
	relevance = _native_query(
		"tq-product-relevance",
		"Product relevance to baskets (last 30 days)",
		wb,
		PRODUCT_RELEVANCE_SQL,
		1,
	)
	singles = _native_query(
		"tq-single-item-baskets",
		"Single-item baskets (last 30 days)",
		wb,
		SINGLE_ITEM_BASKET_SQL,
		2,
	)
	net = _cur("Net sales", "net_sales")
	baskets = {"measure_name": "Baskets", "column_name": "invoice", "data_type": "Integer", "aggregation": "count_distinct"}
	customers = {
		"measure_name": "Customers",
		"column_name": "customer",
		"data_type": "Integer",
		"aggregation": "count_distinct",
	}
	avg_basket = {
		"measure_name": "Avg basket",
		"column_name": "net_sales",
		"data_type": "Decimal",
		"aggregation": "avg",
		"format": "currency",
	}
	items_per_basket = {
		"measure_name": "Items / basket",
		"column_name": "distinct_items",
		"data_type": "Decimal",
		"aggregation": "avg",
	}
	units_per_basket = {
		"measure_name": "Units / basket",
		"column_name": "units",
		"data_type": "Decimal",
		"aggregation": "avg",
	}
	item_baskets = {
		"measure_name": "Baskets with item",
		"column_name": "baskets_with_item",
		"data_type": "Integer",
		"aggregation": "sum",
	}
	attach_rate = {
		"measure_name": "Attach rate",
		"column_name": "attach_rate",
		"data_type": "Decimal",
		"aggregation": "avg",
		"format": "percent",
	}
	basket_lift = {
		"measure_name": "Basket lift",
		"column_name": "basket_lift",
		"data_type": "Decimal",
		"aggregation": "avg",
	}
	avg_with_item = {
		"measure_name": "Avg basket with item",
		"column_name": "avg_basket_with_item",
		"data_type": "Decimal",
		"aggregation": "avg",
		"format": "currency",
	}
	single_count = {
		"measure_name": "Single-item baskets",
		"column_name": "single_item_baskets",
		"data_type": "Integer",
		"aggregation": "sum",
	}
	charts = {
		"tc-basket-kpis": _number_chart(
			"tc-basket-kpis",
			"Customer basket KPIs",
			wb,
			"tq-basket-detail",
			[net, baskets, avg_basket, items_per_basket, units_per_basket, customers],
			"posting_date",
			0,
		),
		"tc-basket-branch-sales": _row(
			"tc-basket-branch-sales", "Net sales by branch", wb, "tq-basket-detail", "branch", net, 1, 8
		),
		"tc-basket-branch-aov": _row(
			"tc-basket-branch-aov", "Average basket by branch", wb, "tq-basket-detail", "branch", avg_basket, 2, 8
		),
		"tc-basket-branch-items": _row(
			"tc-basket-branch-items", "Items per basket by branch", wb, "tq-basket-detail", "branch", items_per_basket, 3, 8
		),
		"tc-product-relevance": _table(
			"tc-product-relevance",
			"Product relevance: basket lift and attach rate",
			wb,
			"tq-product-relevance",
			[
				_dim("Branch", "branch"),
				_dim("Item", "item_name"),
				_dim("Group", "item_group"),
			],
			[item_baskets, attach_rate, avg_with_item, basket_lift, _cur("Item revenue", "item_revenue")],
			4,
			50,
			"Basket lift",
			"desc",
		),
		"tc-single-baskets": _table(
			"tc-single-baskets",
			"Single-item baskets that need cross-sell",
			wb,
			"tq-single-item-baskets",
			[
				_dim("Branch", "branch"),
				_dim("Item", "item_name"),
				_dim("Group", "item_group"),
			],
			[
				single_count,
				_cur("Revenue", "revenue"),
				{
					"measure_name": "Avg basket",
					"column_name": "avg_basket",
					"data_type": "Decimal",
					"aggregation": "avg",
					"format": "currency",
				},
				{
					"measure_name": "Units / basket",
					"column_name": "units_per_basket",
					"data_type": "Decimal",
					"aggregation": "avg",
				},
			],
			5,
			50,
			"Single-item baskets",
			"desc",
		),
	}
	date_links = {
		"tc-basket-kpis": "`tq-basket-detail`.`posting_date`",
		"tc-basket-branch-sales": "`tq-basket-detail`.`posting_date`",
		"tc-basket-branch-aov": "`tq-basket-detail`.`posting_date`",
		"tc-basket-branch-items": "`tq-basket-detail`.`posting_date`",
		"tc-product-relevance": "`tq-product-relevance`.`posting_date`",
		"tc-single-baskets": "`tq-single-item-baskets`.`posting_date`",
	}
	co_links = {
		**{k: v.replace("posting_date", "company") for k, v in date_links.items()},
	}
	br_links = {
		**{k: v.replace("posting_date", "branch") for k, v in date_links.items()},
	}
	dash = {
		"td-basket-relevance": {
			"name": "td-basket-relevance",
			"title": "Customer Basket & Product Relevance",
			"preview_image": "/assets/aimatic/images/insights-basket-preview.svg",
			"workbook": wb,
			"items": [
				_filter_item(
					"Date Range",
					"Date",
					"calendar",
					date_links,
					0,
					{"default_operator": "within", "default_value": "Last 30 days"},
				),
				_filter_item("Company", "String", "building-2", co_links, 4),
				_filter_item("Branch", "String", "store", br_links, 8),
				_chart_item("tc-basket-kpis", "item-kpis", 0, 1, 20, 4),
				_chart_item("tc-basket-branch-sales", "item-sales", 0, 5, 7, 8),
				_chart_item("tc-basket-branch-aov", "item-aov", 7, 5, 6, 8),
				_chart_item("tc-basket-branch-items", "item-items", 13, 5, 7, 8),
				_chart_item("tc-product-relevance", "item-relevance", 0, 13, 20, 10),
				_chart_item("tc-single-baskets", "item-single", 0, 23, 20, 10),
			],
		}
	}
	return _workbook(
		wb,
		"Customer Basket & Product Relevance",
		{
			"tq-basket-detail": detail,
			"tq-product-relevance": relevance,
			"tq-single-item-baskets": singles,
		},
		charts,
		dash,
	)


MANIFESTS = {
	"owner_flash": {
		"version": 4,
		"title": "CEO",
		"description": "Executive morning scorecard: branch-filtered 14-day net sales, customer take, gross profit, COGS, average ticket, items per basket, tickets, returns, tax failures, store vs delivery, counters, till mix, on-shelf availability, overdue supplier bills, negative stock, and item margin.",
		"notes": "Queries are bounded to 14 days. Branch filters use POS Invoice.branch for sales and Warehouse.custom_branch for stock cost/negative-stock rows. Delivery sales are Foodpanda-profile GMV; till charts exclude zero-amount delivery credit. Headline gross profit is POS net sales minus outward Stock Ledger cost on consolidated POS Sales Invoices, so it covers every POS sale; the item table narrows to lines that carry their own ledger cost and will read lower. POS service fee is not in net sales. On-shelf availability is a live Bin snapshot over enabled stock Items in enabled warehouses, so the date filter does not apply to it; it is filtered by company, branch and warehouse only.",
		"module": "Selling",
		"required_apps": ["erpnext", "aimatic"],
		"source_doctypes": [
			"POS Invoice",
			"POS Invoice Item",
			"Sales Invoice Item",
			"Sales Invoice Payment",
			"Stock Ledger Entry",
			"Purchase Invoice",
			"Bin",
			"Item",
		],
	},
	"basket_relevance": {
		"version": 2,
		"title": "Customer Basket & Product Relevance",
		"description": "Branch-aware basket KPIs, average basket, items per basket, high-lift product pairs, and single-item baskets that need cross-sell action.",
		"notes": "Product relevance is deliberately bounded: last 30 days, submitted non-return POS Invoices, positive-qty lines only, branch/day basket baseline, minimum 3 baskets with the item in a branch/day, and 500 output rows. It measures attach rate and basket lift rather than running an expensive all-pairs recommender on dashboard load.",
		"module": "Selling",
		"required_apps": ["erpnext", "aimatic"],
		"source_doctypes": [
			"POS Invoice",
			"POS Invoice Item",
		],
	},
	"pos_retail": {
		"version": 2,
		"title": "POS Sales and Customers",
		"description": "Live POS tickets, average ticket, active customers, branch mix, top items and payment-mode collections. Built from submitted POS Invoices, not Sales Invoices after POS closing.",
		"notes": "Retail supermarket KPIs (NetSuite/ROI-style: net sales, AOV, customer count). Amounts are company base currency and include returns as negative tickets.",
		"module": "Selling",
		"required_apps": ["erpnext", "aimatic"],
		"source_doctypes": ["POS Invoice", "POS Invoice Item", "Sales Invoice Payment"],
	},
	"tax_compliance": {
		"version": 3,
		"title": "Tax Compliance",
		"description": "Splits output tax from POS service fee (fee is a payable, not merchandise tax). Submission mix and sales vs returns.",
		"notes": "POS service fee rides on the tax table but posts to a liability ledger. Do not add it to income. Output tax is the inclusive GST row.",
		"module": "Accounts",
		"required_apps": ["erpnext", "aimatic"],
		"source_doctypes": ["POS Invoice", "Sales Taxes and Charges"],
	},
	"accounts_pnl": {
		"version": 3,
		"title": "Merchandise P&L",
		"description": "Income and expense from GL with Tax account-type excluded so output tax and POS service fee cannot inflate merchandise income.",
		"notes": "POS service fee is a Liability. Use Liabilities and Payables for GST payable, creditors and fee payable. Older GL rows may lack Branch.",
		"module": "Accounts",
		"required_apps": ["erpnext"],
		"source_doctypes": ["GL Entry", "Account"],
	},
	"accounts_liabilities": {
		"version": 2,
		"title": "Liabilities and Payables",
		"description": "Period liability movement (tax payable, trade creditors, POS service fee) plus current outstanding snapshot and unpaid supplier invoices.",
		"notes": "Snapshot is as-of-now (all uncancelled GL). Date filter applies only to period movement charts. Complements bundled AR/AP/Cash.",
		"module": "Accounts",
		"required_apps": ["erpnext", "aimatic"],
		"source_doctypes": ["GL Entry", "Account", "Purchase Invoice"],
	},
	"pending_work": {
		"version": 1,
		"title": "Pending Invoices and Orders",
		"description": "Draft supplier invoices, POs still to receive or bill, unpaid purchase invoices, unpaid non-POS sales invoices.",
		"notes": "Work queues for accounts and receiving. POS tickets are cash-complete and are not listed here.",
		"module": "Accounts",
		"required_apps": ["erpnext"],
		"source_doctypes": ["Purchase Invoice", "Purchase Order", "Sales Invoice"],
	},
	"goods_abc": {
		"version": 1,
		"title": "Goods ABC Analysis",
		"description": "Pareto ABC on POS item sales (trailing 12 months: A=80%, B=next 15%, C=rest) and on-hand stock value.",
		"notes": "Standard retail ABC cutoffs. Sales class is not date-filterable inside the workbook because class is computed on the trailing year. Stock class is current Bin value.",
		"module": "Stock",
		"required_apps": ["erpnext", "aimatic"],
		"source_doctypes": ["POS Invoice Item", "POS Invoice", "Bin", "Item"],
	},
	"inventory_kpis": {
		"version": 3,
		"title": "Inventory KPIs",
		"description": "On-hand stock, COGS, warehouse mix, plus goods with quantity but no outward movement in 90 days.",
		"notes": "COGS uses SLE cost basis. Dead-stock query scans SLE last-out dates — open with a date-independent Bin snapshot. Date filter applies to COGS charts only.",
		"module": "Stock",
		"required_apps": ["erpnext"],
		"source_doctypes": ["Bin", "Stock Ledger Entry", "Item"],
	},
}


BUILDERS = {
	"owner_flash": build_owner_flash,
	"basket_relevance": build_basket_relevance,
	"pos_retail": build_pos_retail,
	"tax_compliance": build_tax_compliance,
	"accounts_pnl": build_accounts_pnl,
	"accounts_liabilities": build_accounts_liabilities,
	"pending_work": build_pending_work,
	"goods_abc": build_goods_abc,
	"inventory_kpis": build_inventory_productivity,
}


def main() -> None:
	for folder, builder in BUILDERS.items():
		_dump(folder, builder())
		manifest_path = HERE / folder / "manifest.json"
		manifest_path.write_text(json.dumps(MANIFESTS[folder], indent="\t") + "\n", encoding="utf-8")
		print(f"wrote {folder}")


if __name__ == "__main__":
	main()
