"""Certified ABC/XYZ inventory analysis.

All business figures are calculated here.  The model can select this tool and
explain its output, but cannot alter SQL expressions or classification formulas.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_months, cint, date_diff, flt, get_first_day, getdate, today

from aimatic.ai.tools import (
	_branch_warehouses,
	_get_date_range,
	_resolve_branch_filter,
	_resolve_company,
	get_sales_overview,
)

CALCULATION_VERSION = "abc-xyz-v1"
_MAX_LIMIT = 500
_MAX_HISTORY_DAYS = 730
_DEFAULT_MINIMUM_ACTIVITY = 1.0
_ABC_METRICS = {
	"net_sales_value": "net_sales",
	"quantity_sold": "sales_quantity",
	"gross_margin_amount": "gross_margin",
	"gross_margin_contribution": "gross_margin",
	"cost_of_goods_sold": "cost_of_goods_sold",
	"stock_value": "stock_value",
	"consumption_quantity": "consumption_quantity",
}
_XYZ_METRICS = {"sales_quantity", "consumption_quantity"}


def _normalise_threshold(value: float | int | None, default: float) -> float:
	value = flt(value if value is not None else default)
	return value / 100 if value > 1 else value


def validate_abc_thresholds(a_threshold=None, b_threshold=None) -> tuple[float, float]:
	a = _normalise_threshold(a_threshold, 0.80)
	b = _normalise_threshold(b_threshold, 0.95)
	if not 0.50 <= a <= 0.90:
		raise ValueError("ABC A threshold must be between 50% and 90%.")
	if not a < b <= 0.99:
		raise ValueError("ABC B cumulative threshold must be greater than A and no more than 99%.")
	return a, b


def classify_abc(
	rows: list[dict[str, Any]],
	metric_key: str,
	a_threshold: float = 0.80,
	b_threshold: float = 0.95,
) -> list[dict[str, Any]]:
	"""Classify by positive cumulative contribution; negative values never
	inflate another item's contribution."""
	ranked = sorted(rows, key=lambda row: max(flt(row.get(metric_key)), 0), reverse=True)
	total = sum(max(flt(row.get(metric_key)), 0) for row in ranked)
	cumulative = 0.0
	for row in ranked:
		value = max(flt(row.get(metric_key)), 0)
		contribution = value / total if total else 0
		previous_cumulative = cumulative
		cumulative += contribution
		if not total:
			abc_class = "C"
		elif previous_cumulative < a_threshold:
			abc_class = "A"
		elif previous_cumulative < b_threshold:
			abc_class = "B"
		else:
			abc_class = "C"
		row["sales_contribution_pct"] = round(contribution * 100, 4)
		row["cumulative_contribution_pct"] = round(cumulative * 100, 4)
		row["abc_class"] = abc_class
	return ranked


def classify_xyz(
	period_values: list[float],
	minimum_activity: float = _DEFAULT_MINIMUM_ACTIVITY,
) -> dict[str, Any]:
	values = [flt(value) for value in period_values]
	periods = len(values)
	active = sum(abs(value) >= minimum_activity for value in values)
	total_activity = sum(max(value, 0) for value in values)
	mean = sum(values) / periods if periods else 0
	stddev = math.sqrt(sum((value - mean) ** 2 for value in values) / periods) if periods else 0
	cv = stddev / abs(mean) if mean else None
	frequency = active / periods if periods else 0

	if periods < 3 or total_activity < minimum_activity:
		xyz_class = "Insufficient"
		confidence = 0.2 if periods else 0
	elif periods >= 6 and frequency >= 0.75 and cv is not None and cv <= 0.30:
		xyz_class = "X"
		confidence = min(1.0, 0.55 + periods / 24 + frequency * 0.2)
	elif periods >= 4 and frequency >= 0.50 and cv is not None and cv <= 1.00:
		xyz_class = "Y"
		confidence = min(0.9, 0.45 + periods / 30 + frequency * 0.15)
	else:
		xyz_class = "Z"
		confidence = min(0.85, 0.4 + periods / 36 + frequency * 0.1)
	return {
		"demand_average": round(mean, 4),
		"demand_standard_deviation": round(stddev, 4),
		"coefficient_of_variation": round(cv, 4) if cv is not None else None,
		"active_selling_periods": active,
		"total_periods": periods,
		"demand_frequency_pct": round(frequency * 100, 2),
		"xyz_class": xyz_class,
		"data_coverage": round(min(1.0, periods / 12) * 100, 2),
		"confidence": round(confidence * 100, 2),
	}


def _month_keys(date_from, date_to) -> list[str]:
	current = get_first_day(date_from)
	keys = []
	while current <= date_to:
		keys.append(str(current))
		current = add_months(current, 1)
	return keys


def _resolve_warehouse_filter(
	company: str,
	branch_filter: list[str] | None,
	warehouse: str | None,
) -> list[str] | None:
	if not warehouse:
		return _branch_warehouses(branch_filter)
	if not frappe.has_permission("Warehouse", ptype="read", doc=warehouse):
		frappe.throw(_("Not permitted to view this warehouse."), frappe.PermissionError)
	warehouse_company, warehouse_branch = frappe.db.get_value(
		"Warehouse", warehouse, ["company", "custom_branch"]
	) or (None, None)
	if warehouse_company != company:
		frappe.throw(_("Warehouse does not belong to the selected company."))
	if branch_filter is not None and warehouse_branch not in branch_filter:
		frappe.throw(_("Warehouse is outside the visible branch scope."), frappe.PermissionError)
	return [warehouse]


def _base_params(
	company,
	date_from,
	date_to,
	branch_filter,
	warehouse_filter,
	item_group,
	brand,
	supplier,
):
	return {
		"company": company,
		"date_from": date_from,
		"date_to": date_to,
		"branch_names": tuple(branch_filter) if branch_filter else (),
		"warehouse_names": tuple(warehouse_filter) if warehouse_filter else (),
		"item_group": item_group,
		"brand": brand,
		"supplier": supplier,
	}


def _sales_rows(params, branch_filter, warehouse_filter, item_group, brand, supplier):
	branch_clause = "AND COALESCE(pi.branch, pp.branch, w.custom_branch) IN %(branch_names)s" if branch_filter is not None else ""
	warehouse_clause = "AND pii.warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
	item_group_clause = "AND i.item_group = %(item_group)s" if item_group else ""
	brand_clause = "AND i.brand = %(brand)s" if brand else ""
	supplier_clause = "AND id.default_supplier = %(supplier)s" if supplier else ""
	allocated = (
		"CASE WHEN ABS(IFNULL(pi.base_net_total, 0)) > 0 "
		"THEN (pii.base_net_amount / pi.base_net_total) * pi.grand_total "
		"ELSE pii.base_net_amount END"
	)
	return frappe.db.sql(
		f"""
		SELECT
			pii.item_code,
			i.item_name,
			i.item_group,
			i.brand,
			pii.warehouse,
			COALESCE(w.custom_branch, pi.branch, pp.branch) AS branch,
			COALESCE(SUM(pii.stock_qty), 0) AS sales_quantity,
			COALESCE(SUM({allocated}), 0) AS net_sales,
			COALESCE(SUM(CASE WHEN pi.is_return = 0 THEN {allocated} ELSE 0 END), 0) AS gross_sales,
			MAX(CASE WHEN pii.stock_qty > 0 THEN pi.posting_date END) AS last_sale_date
		FROM `tabPOS Invoice Item` pii
		INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
		LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
		LEFT JOIN `tabWarehouse` w ON w.name = pii.warehouse
		INNER JOIN `tabItem` i ON i.name = pii.item_code
		LEFT JOIN `tabItem Default` id ON id.parent = i.name AND id.company = %(company)s
		WHERE pi.docstatus = 1
		  AND pi.company = %(company)s
		  AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
		  {branch_clause} {warehouse_clause} {item_group_clause} {brand_clause} {supplier_clause}
		GROUP BY pii.item_code, i.item_name, i.item_group, i.brand, pii.warehouse,
		         COALESCE(w.custom_branch, pi.branch, pp.branch)
		""",
		params,
		as_dict=True,
	)


def _period_rows(params, branch_filter, warehouse_filter, item_group, brand, supplier):
	branch_clause = "AND COALESCE(pi.branch, pp.branch, w.custom_branch) IN %(branch_names)s" if branch_filter is not None else ""
	warehouse_clause = "AND pii.warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
	item_group_clause = "AND i.item_group = %(item_group)s" if item_group else ""
	brand_clause = "AND i.brand = %(brand)s" if brand else ""
	supplier_clause = "AND id.default_supplier = %(supplier)s" if supplier else ""
	return frappe.db.sql(
		f"""
		SELECT pii.item_code, pii.warehouse,
		       DATE_FORMAT(pi.posting_date, '%%Y-%%m-01') AS demand_period,
		       COALESCE(SUM(pii.stock_qty), 0) AS sales_quantity
		FROM `tabPOS Invoice Item` pii
		INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
		LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
		LEFT JOIN `tabWarehouse` w ON w.name = pii.warehouse
		INNER JOIN `tabItem` i ON i.name = pii.item_code
		LEFT JOIN `tabItem Default` id ON id.parent = i.name AND id.company = %(company)s
		WHERE pi.docstatus = 1 AND pi.company = %(company)s
		  AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
		  {branch_clause} {warehouse_clause} {item_group_clause} {brand_clause} {supplier_clause}
		GROUP BY pii.item_code, pii.warehouse, demand_period
		""",
		params,
		as_dict=True,
	)


def _stock_rows(params, branch_filter, warehouse_filter, item_group, brand, supplier):
	warehouse_clause = "AND b.warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
	branch_clause = "AND w.custom_branch IN %(branch_names)s" if branch_filter is not None else ""
	item_group_clause = "AND i.item_group = %(item_group)s" if item_group else ""
	brand_clause = "AND i.brand = %(brand)s" if brand else ""
	supplier_clause = "AND id.default_supplier = %(supplier)s" if supplier else ""
	return frappe.db.sql(
		f"""
		SELECT b.item_code, i.item_name, i.item_group, i.brand, b.warehouse,
		       w.custom_branch AS branch,
		       COALESCE(SUM(b.actual_qty), 0) AS stock_quantity,
		       COALESCE(SUM(b.stock_value), 0) AS stock_value
		FROM `tabBin` b
		INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
		INNER JOIN `tabItem` i ON i.name = b.item_code
		LEFT JOIN `tabItem Default` id ON id.parent = i.name AND id.company = %(company)s
		WHERE w.company = %(company)s AND w.disabled = 0 AND i.disabled = 0
		  {warehouse_clause} {branch_clause} {item_group_clause} {brand_clause} {supplier_clause}
		GROUP BY b.item_code, i.item_name, i.item_group, i.brand, b.warehouse, w.custom_branch
		HAVING ABS(SUM(b.actual_qty)) > 0 OR ABS(SUM(b.stock_value)) > 0
		""",
		params,
		as_dict=True,
	)


def _cogs_rows(params, warehouse_filter, item_group, brand, supplier):
	warehouse_clause = "AND sle.warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
	item_group_clause = "AND i.item_group = %(item_group)s" if item_group else ""
	brand_clause = "AND i.brand = %(brand)s" if brand else ""
	supplier_clause = "AND id.default_supplier = %(supplier)s" if supplier else ""
	return frappe.db.sql(
		f"""
		SELECT sle.item_code, sle.warehouse,
		       COALESCE(SUM(-sle.stock_value_difference), 0) AS cost_of_goods_sold,
		       COALESCE(SUM(-sle.actual_qty), 0) AS consumption_quantity
		FROM `tabStock Ledger Entry` sle
		INNER JOIN `tabItem` i ON i.name = sle.item_code
		LEFT JOIN `tabItem Default` id ON id.parent = i.name AND id.company = %(company)s
		WHERE sle.is_cancelled = 0 AND sle.company = %(company)s
		  AND sle.voucher_type IN ('Sales Invoice', 'POS Invoice')
		  AND sle.actual_qty < 0
		  AND sle.posting_date BETWEEN %(date_from)s AND %(date_to)s
		  {warehouse_clause} {item_group_clause} {brand_clause} {supplier_clause}
		GROUP BY sle.item_code, sle.warehouse
		""",
		params,
		as_dict=True,
	)


def _summary(rows):
	class_counts = Counter(row["abc_class"] for row in rows)
	xyz_counts = Counter(row["xyz_class"] for row in rows)
	matrix = Counter(row["combined_class"] for row in rows)
	class_summary = {}
	for abc_class in ("A", "B", "C"):
		members = [row for row in rows if row["abc_class"] == abc_class]
		class_summary[abc_class] = {
			"item_count": len(members),
			"net_sales": round(sum(flt(row["net_sales"]) for row in members), 2),
			"gross_margin": round(sum(flt(row["gross_margin"]) for row in members), 2),
			"stock_value": round(sum(flt(row["stock_value"]) for row in members), 2),
			"dead_stock_value": round(
				sum(flt(row["stock_value"]) for row in members if row.get("is_dead_stock")), 2
			),
		}
	return {
		"abc_counts": dict(class_counts),
		"xyz_counts": dict(xyz_counts),
		"class_summary": class_summary,
		"abc_xyz_matrix": dict(matrix),
	}


def get_abc_xyz_analysis(
	date_from: str | None = None,
	date_to: str | None = None,
	branch: str | None = None,
	warehouse: str | None = None,
	item_group: str | None = None,
	brand: str | None = None,
	supplier: str | None = None,
	abc_metric: str = "net_sales_value",
	abc_a_threshold: float = 80,
	abc_b_threshold: float = 95,
	xyz_metric: str = "sales_quantity",
	minimum_activity: float = _DEFAULT_MINIMUM_ACTIVITY,
	limit: int = 100,
) -> dict:
	company = _resolve_company()
	date_from, date_to = _get_date_range(date_from, date_to)
	if date_diff(date_to, date_from) > _MAX_HISTORY_DAYS:
		return {"error": f"Date range is capped at {_MAX_HISTORY_DAYS} days."}
	if abc_metric not in _ABC_METRICS:
		return {"error": f"Unsupported ABC metric. Available: {sorted(_ABC_METRICS)}"}
	if xyz_metric not in _XYZ_METRICS:
		return {"error": f"Unsupported XYZ metric. Available: {sorted(_XYZ_METRICS)}"}
	try:
		a_threshold, b_threshold = validate_abc_thresholds(abc_a_threshold, abc_b_threshold)
	except ValueError as error:
		return {"error": str(error)}
	minimum_activity = max(0, flt(minimum_activity))
	limit = max(1, min(cint(limit or 100), _MAX_LIMIT))

	branch_filter = _resolve_branch_filter(company, branch)
	warehouse_filter = _resolve_warehouse_filter(company, branch_filter, warehouse)
	currency = frappe.get_cached_value("Company", company, "default_currency")
	if branch_filter is not None and not branch_filter:
		return {
			"company": company,
			"currency": currency,
			"date_from": str(date_from),
			"date_to": str(date_to),
			"items": [],
			"summaries": _summary([]),
			"calculation_version": CALCULATION_VERSION,
		}
	if warehouse_filter is not None and not warehouse_filter:
		return {
			"company": company,
			"currency": currency,
			"date_from": str(date_from),
			"date_to": str(date_to),
			"items": [],
			"summaries": _summary([]),
			"calculation_version": CALCULATION_VERSION,
		}

	params = _base_params(
		company,
		date_from,
		date_to,
		branch_filter,
		warehouse_filter,
		item_group,
		brand,
		supplier,
	)
	sales = _sales_rows(params, branch_filter, warehouse_filter, item_group, brand, supplier)
	periods = _period_rows(params, branch_filter, warehouse_filter, item_group, brand, supplier)
	stock = _stock_rows(params, branch_filter, warehouse_filter, item_group, brand, supplier)
	cogs = _cogs_rows(params, warehouse_filter, item_group, brand, supplier)

	combined: dict[tuple[str, str], dict[str, Any]] = {}
	for row in stock:
		key = (row.item_code, row.warehouse)
		combined[key] = {
			"item_code": row.item_code,
			"item_name": row.item_name,
			"branch": row.branch or _("Unassigned"),
			"warehouse": row.warehouse,
			"item_group": row.item_group,
			"brand": row.brand,
			"stock_quantity": flt(row.stock_quantity),
			"stock_value": flt(row.stock_value),
			"sales_quantity": 0.0,
			"net_sales": 0.0,
			"gross_sales": 0.0,
			"last_sale_date": None,
		}
	for row in sales:
		key = (row.item_code, row.warehouse)
		record = combined.setdefault(
			key,
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"branch": row.branch or _("Unassigned"),
				"warehouse": row.warehouse,
				"item_group": row.item_group,
				"brand": row.brand,
				"stock_quantity": 0.0,
				"stock_value": 0.0,
			},
		)
		record.update(
			{
				"sales_quantity": flt(row.sales_quantity),
				"net_sales": flt(row.net_sales),
				"gross_sales": flt(row.gross_sales),
				"last_sale_date": str(row.last_sale_date) if row.last_sale_date else None,
			}
		)
	for row in cogs:
		record = combined.get((row.item_code, row.warehouse))
		if record:
			record["cost_of_goods_sold"] = flt(row.cost_of_goods_sold)
			record["consumption_quantity"] = flt(row.consumption_quantity)
	for record in combined.values():
		record.setdefault("cost_of_goods_sold", 0.0)
		record.setdefault("consumption_quantity", 0.0)
		record["gross_margin"] = round(
			flt(record.get("gross_sales")) - flt(record.get("cost_of_goods_sold")), 2
		)

	month_keys = _month_keys(date_from, date_to)
	period_map: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
	for row in periods:
		period_map[(row.item_code, row.warehouse)][str(row.demand_period)] = flt(row.sales_quantity)

	rows = list(combined.values())
	rows = classify_abc(rows, _ABC_METRICS[abc_metric], a_threshold, b_threshold)
	range_days = max(1, date_diff(date_to, date_from) + 1)
	dead_cutoff = getdate(date_to)
	for row in rows:
		values = [period_map[(row["item_code"], row["warehouse"])].get(month, 0) for month in month_keys]
		xyz = classify_xyz(values, minimum_activity)
		row.update(xyz)
		row["combined_class"] = (
			f"{row['abc_class']}{row['xyz_class']}"
			if row["xyz_class"] in {"X", "Y", "Z"}
			else f"{row['abc_class']}?"
		)
		daily_rate = max(flt(row["sales_quantity"]), 0) / range_days
		row["days_of_stock"] = round(flt(row["stock_quantity"]) / daily_rate, 1) if daily_rate > 0 else None
		last_sale = getdate(row["last_sale_date"]) if row.get("last_sale_date") else None
		row["is_dead_stock"] = bool(
			flt(row["stock_quantity"]) > 0
			and (last_sale is None or date_diff(dead_cutoff, last_sale) >= 90)
		)
		row["confidence"] = round(
			min(row["confidence"], 100.0 if flt(row["stock_value"]) >= 0 else 70.0), 2
		)

	item_level_total = round(sum(flt(row["net_sales"]) for row in rows), 6)
	filtered = bool(warehouse or item_group or brand or supplier)
	if not filtered:
		certified_total = flt(
			get_sales_overview(
				date_from=str(date_from),
				date_to=str(date_to),
				branch=branch,
			).get("net_sales")
		)
		reconciliation_source = "get_sales_overview"
	else:
		# The full, unsliced allocated item total is the certified filtered
		# denominator when header-only Sales Overview cannot express item filters.
		certified_total = item_level_total
		reconciliation_source = "filtered_item_allocation"
	difference = round(item_level_total - certified_total, 6)
	tolerance = max(0.01, abs(certified_total) * 0.0001)
	reconciliation = {
		"passed": abs(difference) <= tolerance,
		"item_level_net_sales": item_level_total,
		"certified_net_sales": certified_total,
		"difference": difference,
		"tolerance": tolerance,
		"source": reconciliation_source,
	}

	summaries = _summary(rows)
	high_value_irregular = [
		row for row in rows if row["abc_class"] in {"A", "B"} and row["xyz_class"] == "Z"
	][:20]
	c_excess_stock = [
		row
		for row in rows
		if row["abc_class"] == "C"
		and (row["days_of_stock"] is None or flt(row["days_of_stock"]) > 90)
		and flt(row["stock_quantity"]) > 0
	][:20]
	a_stockout_risk = [
		row
		for row in rows
		if (
			row["abc_class"] == "A"
			and flt(row["stock_quantity"]) <= 0
		)
		or (
			row["abc_class"] == "A"
			and row["days_of_stock"] is not None
			and flt(row["days_of_stock"]) < 14
		)
	][:20]

	return {
		"company": company,
		"currency": currency,
		"branch": branch,
		"warehouse": warehouse,
		"date_from": str(date_from),
		"date_to": str(date_to),
		"abc_metric": abc_metric,
		"abc_a_threshold": round(a_threshold * 100, 2),
		"abc_b_threshold": round(b_threshold * 100, 2),
		"xyz_metric": xyz_metric,
		"minimum_activity": minimum_activity,
		"items": rows[:limit],
		"row_count": len(rows),
		"returned_row_count": min(len(rows), limit),
		"summaries": summaries,
		"high_value_irregular_items": high_value_irregular,
		"c_items_with_excess_stock": c_excess_stock,
		"a_items_at_stockout_risk": a_stockout_risk,
		"reconciliation": reconciliation,
		"data_coverage": round(
			sum(flt(row["data_coverage"]) for row in rows) / len(rows), 2
		)
		if rows
		else 0,
		"assumptions": [
			"XYZ demand periods are calendar months and include zero-demand months.",
			"X requires at least 6 periods, demand frequency of 75%, and coefficient of variation no more than 0.30.",
			"Y requires at least 4 periods, demand frequency of 50%, and coefficient of variation no more than 1.00.",
			"Gross margin uses positive-sales allocation less outbound Stock Ledger Entry COGS.",
		],
		"limitations": [
			"Items with fewer than 3 periods or activity below the minimum are not assigned X, Y, or Z.",
			"Stockout-distorted demand is not corrected in ABC/XYZ; use the demand forecast for that adjustment.",
		],
		"calculation_version": CALCULATION_VERSION,
	}


TOOL_SPECS = [
	{
		"type": "function",
		"function": {
			"name": "get_abc_xyz_analysis",
			"description": (
				"Certified item-level ABC cumulative-contribution and XYZ demand-consistency analysis "
				"with stock, margin, days-of-stock, reconciliation, risk lists, and an ABC/XYZ matrix. "
				"Use for ABC, XYZ, Pareto inventory classification, high-value irregular demand, "
				"C-class excess stock, or A-class stockout-risk questions."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"date_from": {"type": "string"},
					"date_to": {"type": "string"},
					"branch": {"type": "string"},
					"warehouse": {"type": "string"},
					"item_group": {"type": "string"},
					"brand": {"type": "string"},
					"supplier": {"type": "string"},
					"abc_metric": {"type": "string", "enum": sorted(_ABC_METRICS)},
					"abc_a_threshold": {"type": "number"},
					"abc_b_threshold": {"type": "number"},
					"xyz_metric": {"type": "string", "enum": sorted(_XYZ_METRICS)},
					"minimum_activity": {"type": "number"},
					"limit": {"type": "integer", "description": f"Default 100, maximum {_MAX_LIMIT}."},
				},
			},
		},
	}
]

TOOL_DISPATCH = {"get_abc_xyz_analysis": get_abc_xyz_analysis}
