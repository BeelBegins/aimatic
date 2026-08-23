"""Read-only branch stock-transfer recommendations."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import frappe
from frappe.utils import add_days, cint, flt, getdate

from aimatic.ai.tools import _branch_warehouses, _resolve_branch_filter, _resolve_company

CALCULATION_VERSION = "inventory-transfer-v1"
_MAX_ITEMS = 200


def calculate_transfers(
	positions: list[dict[str, Any]],
	target_cover_days: int = 30,
	minimum_transfer_qty: float = 1,
	limit: int = 100,
) -> list[dict[str, Any]]:
	"""Greedily match item-level surplus and deficit without double allocating stock."""
	by_item = defaultdict(list)
	for raw in positions:
		row = dict(raw)
		daily_demand = max(flt(row.get("daily_demand")), 0)
		target = daily_demand * target_cover_days
		row["target_stock"] = target
		row["surplus"] = max(flt(row.get("stock_quantity")) - target, 0)
		row["deficit"] = max(target - flt(row.get("stock_quantity")), 0)
		by_item[row.get("item_code")].append(row)
	recommendations = []
	for item_code, rows in by_item.items():
		sources = sorted(
			[row for row in rows if row["surplus"] >= minimum_transfer_qty],
			key=lambda row: row["surplus"],
			reverse=True,
		)
		destinations = sorted(
			[row for row in rows if row["deficit"] >= minimum_transfer_qty],
			key=lambda row: row["deficit"],
			reverse=True,
		)
		for destination in destinations:
			for source in sources:
				if source.get("branch") == destination.get("branch"):
					continue
				quantity = min(source["surplus"], destination["deficit"])
				if quantity < minimum_transfer_qty:
					continue
				source["surplus"] -= quantity
				destination["deficit"] -= quantity
				daily_demand = max(flt(destination.get("daily_demand")), 0)
				avoided_days = quantity / daily_demand if daily_demand > 0 else 0
				value_rate = flt(source.get("valuation_rate"))
				periods = min(cint(source.get("active_days")), cint(destination.get("active_days")))
				confidence = min(95, 35 + periods * 1.5 + min(daily_demand, 20))
				recommendations.append(
					{
						"item_code": item_code,
						"item_name": source.get("item_name") or destination.get("item_name"),
						"from_branch": source.get("branch"),
						"from_warehouse": source.get("warehouse"),
						"to_branch": destination.get("branch"),
						"to_warehouse": destination.get("warehouse"),
						"transfer_qty": round(quantity, 4),
						"expected_avoided_stockout_days": round(avoided_days, 1),
						"expected_dead_stock_reduction": round(quantity * value_rate, 2),
						"source_stock_before": round(flt(source.get("stock_quantity")), 4),
						"destination_stock_before": round(flt(destination.get("stock_quantity")), 4),
						"destination_daily_demand": round(daily_demand, 4),
						"transfer_confidence": round(confidence, 2),
						"reason": "Measured surplus exceeds target cover while another branch is below target cover.",
					}
				)
				if destination["deficit"] < minimum_transfer_qty:
					break
	recommendations.sort(
		key=lambda row: (
			-row["transfer_confidence"],
			-row["expected_dead_stock_reduction"],
		)
	)
	return recommendations[:limit]


def get_branch_transfer_recommendations(
	item_code: str | None = None,
	item_group: str | None = None,
	brand: str | None = None,
	history_days: int = 90,
	target_cover_days: int = 30,
	minimum_transfer_qty: float = 1,
	limit: int = 50,
) -> dict:
	company = _resolve_company()
	history_days = max(28, min(cint(history_days or 90), 365))
	target_cover_days = max(7, min(cint(target_cover_days or 30), 180))
	minimum_transfer_qty = max(flt(minimum_transfer_qty), 0.001)
	limit = max(1, min(cint(limit or 50), 100))
	branches = _resolve_branch_filter(company, None)
	warehouses = _branch_warehouses(branches)
	if branches is not None and not branches:
		return {"recommendations": [], "row_count": 0, "calculation_version": CALCULATION_VERSION}
	date_to = getdate()
	date_from = add_days(date_to, -history_days + 1)
	item_clause = "AND i.name = %(item_code)s" if item_code else ""
	group_clause = "AND i.item_group = %(item_group)s" if item_group else ""
	brand_clause = "AND i.brand = %(brand)s" if brand else ""
	branch_clause = "AND w.custom_branch IN %(branches)s" if branches is not None else ""
	warehouse_clause = "AND b.warehouse IN %(warehouses)s" if warehouses is not None else ""
	params = {
		"company": company,
		"date_from": date_from,
		"date_to": date_to,
		"history_days": history_days,
		"branches": tuple(branches or ("",)),
		"warehouses": tuple(warehouses or ("",)),
		"item_code": item_code,
		"item_group": item_group,
		"brand": brand,
		"max_items": _MAX_ITEMS,
	}
	positions = frappe.db.sql(
		f"""
		SELECT b.item_code, MAX(i.item_name) AS item_name,
		       w.custom_branch AS branch, b.warehouse,
		       SUM(b.actual_qty) AS stock_quantity,
		       SUM(b.stock_value) / NULLIF(SUM(b.actual_qty), 0) AS valuation_rate,
		       COALESCE(s.sales_quantity, 0) / %(history_days)s AS daily_demand,
		       COALESCE(s.active_days, 0) AS active_days
		FROM `tabBin` b
		INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
		INNER JOIN `tabItem` i ON i.name = b.item_code
		LEFT JOIN (
			SELECT pii.item_code, pii.warehouse,
			       SUM(pii.stock_qty) AS sales_quantity,
			       COUNT(DISTINCT pi.posting_date) AS active_days
			FROM `tabPOS Invoice Item` pii
			INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
			WHERE pi.docstatus = 1 AND pi.company = %(company)s
			  AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
			GROUP BY pii.item_code, pii.warehouse
		) s ON s.item_code = b.item_code AND s.warehouse = b.warehouse
		WHERE w.company = %(company)s AND w.disabled = 0 AND i.disabled = 0
		  {item_clause} {group_clause} {brand_clause} {branch_clause} {warehouse_clause}
		GROUP BY b.item_code, w.custom_branch, b.warehouse, s.sales_quantity, s.active_days
		ORDER BY ABS(SUM(b.stock_value)) DESC
		LIMIT %(max_items)s
		""",
		params,
		as_dict=True,
	)
	recommendations = calculate_transfers(positions, target_cover_days, minimum_transfer_qty, limit)
	return {
		"company": company,
		"date_from": str(date_from),
		"date_to": str(date_to),
		"history_days": history_days,
		"target_cover_days": target_cover_days,
		"recommendations": recommendations,
		"row_count": len(recommendations),
		"positions_analyzed": len(positions),
		"data_quality_warnings": (
			["No cross-branch surplus/deficit match satisfied the transfer threshold."]
			if not recommendations
			else []
		),
		"assumptions": [
			"Demand is the net submitted POS quantity divided by the bounded history window.",
			"Transfer logistics, shelf capacity, and in-transit reservations are not included.",
			"Recommendations are review-only and do not create Stock Entries.",
		],
		"automatic_update": False,
		"calculation_version": CALCULATION_VERSION,
	}


TOOL_SPECS = [
	{
		"type": "function",
		"function": {
			"name": "get_branch_transfer_recommendations",
			"description": (
				"Read-only branch stock-transfer recommendations matching measured item "
				"surplus to measured deficit. Returns transfer quantity, avoided stockout, "
				"dead-stock reduction, confidence, and source/destination drill-down."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"item_code": {"type": "string"},
					"item_group": {"type": "string"},
					"brand": {"type": "string"},
					"history_days": {"type": "integer"},
					"target_cover_days": {"type": "integer"},
					"minimum_transfer_qty": {"type": "number"},
					"limit": {"type": "integer"},
				},
			},
		},
	}
]

TOOL_DISPATCH = {"get_branch_transfer_recommendations": get_branch_transfer_recommendations}
