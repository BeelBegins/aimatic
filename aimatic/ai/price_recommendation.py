"""Governed, read-only retail sales-price recommendations."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate

from aimatic.ai.demand_forecasting import get_demand_forecast
from aimatic.ai.tools import _branch_warehouses, _resolve_branch_filter, _resolve_company

CALCULATION_VERSION = "price-recommendation-v1"
NOTICE = "Decision-support recommendation — not an automatic price update."
_OBJECTIVES = {
	"maximize gross margin",
	"maximize revenue",
	"improve sell-through",
	"clear aging stock",
	"protect market share",
	"maintain current volume",
}


def retail_round(value: float, increment: float = 1.0) -> float:
	increment = max(flt(increment), 0.01)
	return round(round(flt(value) / increment) * increment, 2)


def enforce_price_constraints(
	candidate_price: float,
	current_price: float,
	cost: float,
	mrp: float | None,
	minimum_margin_pct: float,
	maximum_price_change_pct: float,
	rounding_increment: float = 1.0,
) -> dict[str, Any]:
	"""Enforce hard server rules. Margin and MRP override movement preferences."""
	current_price = max(flt(current_price), 0)
	cost = max(flt(cost), 0)
	minimum_margin_pct = max(0, min(flt(minimum_margin_pct), 95))
	maximum_price_change_pct = max(0, min(flt(maximum_price_change_pct), 50))
	price_floor = cost / (1 - minimum_margin_pct / 100) if cost > 0 and minimum_margin_pct < 100 else cost
	movement_low = current_price * (1 - maximum_price_change_pct / 100)
	movement_high = current_price * (1 + maximum_price_change_pct / 100)
	hard_low = max(price_floor, 0)
	hard_high = flt(mrp) if flt(mrp) > 0 else float("inf")
	warnings = []
	if hard_low > hard_high:
		return {
			"valid": False,
			"error": "Configured minimum margin cannot be achieved without exceeding MRP.",
			"price_floor": round(price_floor, 4),
			"maximum_price": round(hard_high, 4),
			"warnings": ["Price floor exceeds the MRP ceiling."],
		}
	soft_low = max(movement_low, hard_low)
	soft_high = min(movement_high, hard_high)
	if soft_low > soft_high:
		# Hard legal/margin limits take precedence over the change guardrail.
		soft_low, soft_high = hard_low, hard_high
		warnings.append(
			"Maximum price-change guardrail was overridden to satisfy the hard margin or MRP constraint."
		)
	constrained = min(max(flt(candidate_price), soft_low), soft_high)
	constrained = retail_round(constrained, rounding_increment)
	# Rounding must never push the price outside hard constraints.
	if constrained < hard_low:
		constrained = math.ceil(hard_low / rounding_increment) * rounding_increment
	if constrained > hard_high:
		constrained = math.floor(hard_high / rounding_increment) * rounding_increment
	if constrained < hard_low or constrained > hard_high:
		return {
			"valid": False,
			"error": "Retail rounding cannot produce a price within the hard constraints.",
			"price_floor": round(price_floor, 4),
			"maximum_price": round(hard_high, 4),
			"warnings": warnings,
		}
	return {
		"valid": True,
		"price": round(constrained, 2),
		"price_floor": round(price_floor, 4),
		"maximum_price": round(hard_high, 4) if math.isfinite(hard_high) else None,
		"warnings": warnings,
	}


def estimate_elasticity(observations: list[dict[str, Any]]) -> dict[str, Any]:
	"""Estimate log-log elasticity only after strict sufficiency/distortion checks."""
	usable = [
		row
		for row in observations
		if flt(row.get("price")) > 0
		and flt(row.get("quantity")) > 0
		and not row.get("promotion")
		and not row.get("stockout")
	]
	reasons = []
	if len(usable) < 8:
		reasons.append("At least 8 non-promotional, in-stock periods are required.")
	prices = [flt(row["price"]) for row in usable]
	if len({round(value, 2) for value in prices}) < 3:
		reasons.append("At least 3 distinct realized prices are required.")
	price_variation = (max(prices) - min(prices)) / (sum(prices) / len(prices)) * 100 if prices else 0
	if price_variation < 3:
		reasons.append("Realized selling-price variation is below 3%.")
	if len(observations) - len(usable) > max(2, len(observations) * 0.40):
		reasons.append("Promotions or stockouts distort too much of the history.")
	if reasons:
		return {
			"valid": False,
			"elasticity": None,
			"confidence": "low",
			"reasons": reasons,
			"usable_observations": len(usable),
			"price_variation_pct": round(price_variation, 2),
		}

	x = [math.log(flt(row["price"])) for row in usable]
	y = [math.log(flt(row["quantity"])) for row in usable]
	x_mean, y_mean = sum(x) / len(x), sum(y) / len(y)
	denominator = sum((value - x_mean) ** 2 for value in x)
	if denominator <= 0:
		return {
			"valid": False,
			"elasticity": None,
			"confidence": "low",
			"reasons": ["Price variation is zero."],
		}
	slope = sum((xv - x_mean) * (yv - y_mean) for xv, yv in zip(x, y)) / denominator
	predicted = [y_mean + slope * (value - x_mean) for value in x]
	total_variation = sum((value - y_mean) ** 2 for value in y)
	residual = sum((actual - estimate) ** 2 for actual, estimate in zip(y, predicted))
	r_squared = max(0, 1 - residual / total_variation) if total_variation else 0
	if slope >= -0.05 or slope < -5 or r_squared < 0.20:
		return {
			"valid": False,
			"elasticity": None,
			"confidence": "low",
			"reasons": ["Historical price response is weak, implausible, or dominated by other effects."],
			"raw_elasticity": round(slope, 4),
			"r_squared": round(r_squared, 4),
			"usable_observations": len(usable),
			"price_variation_pct": round(price_variation, 2),
		}
	confidence = "high" if len(usable) >= 18 and r_squared >= 0.60 else "medium"
	return {
		"valid": True,
		"elasticity": round(slope, 4),
		"confidence": confidence,
		"r_squared": round(r_squared, 4),
		"usable_observations": len(usable),
		"price_variation_pct": round(price_variation, 2),
		"reasons": [],
	}


def _scenario_changes(objective: str, maximum_change: float) -> list[tuple[str, float]]:
	maximum_change = max(0, min(maximum_change, 50))
	if objective in {"improve sell-through", "clear aging stock", "protect market share"}:
		return [
			("Conservative", -min(2, maximum_change)),
			("Recommended", -min(5, maximum_change)),
			("Aggressive", -maximum_change),
		]
	if objective in {"maximize gross margin", "maximize revenue"}:
		return [
			("Conservative", min(2, maximum_change)),
			("Recommended", min(5, maximum_change)),
			("Aggressive", maximum_change),
		]
	return [
		("Conservative", 0),
		("Recommended", min(2, maximum_change)),
		("Aggressive", -min(2, maximum_change)),
	]


def build_price_scenarios(
	current_price: float,
	cost: float,
	mrp: float | None,
	baseline_quantity: float,
	minimum_margin_pct: float,
	maximum_price_change_pct: float,
	objective: str,
	elasticity: dict[str, Any],
	current_stock: float = 0,
	rounding_increment: float = 1.0,
) -> dict[str, Any]:
	scenarios = []
	constraint_warnings = []
	for name, raw_change in _scenario_changes(objective, maximum_price_change_pct):
		raw_price = current_price * (1 + raw_change / 100)
		constrained = enforce_price_constraints(
			raw_price,
			current_price,
			cost,
			mrp,
			minimum_margin_pct,
			maximum_price_change_pct,
			rounding_increment,
		)
		constraint_warnings.extend(constrained.get("warnings") or [])
		if not constrained.get("valid"):
			return {"error": constrained.get("error"), "constraint_warnings": constrained.get("warnings")}
		price = constrained["price"]
		change_pct = (price - current_price) / current_price * 100 if current_price else 0
		if elasticity.get("valid"):
			expected_quantity = baseline_quantity * (price / current_price) ** flt(elasticity["elasticity"])
			method_reason = "Historical elasticity applied after sufficiency checks."
			confidence = elasticity.get("confidence")
		else:
			response_factor = -0.30 if change_pct > 0 else -0.50
			expected_quantity = baseline_quantity * max(0, 1 + response_factor * change_pct / 100)
			method_reason = "Transparent rule-based volume response; reliable elasticity was unavailable."
			confidence = "low"
		expected_quantity = max(expected_quantity, 0)
		revenue = price * expected_quantity
		gross_profit = (price - cost) * expected_quantity if cost > 0 else None
		margin_pct = (price - cost) / price * 100 if price and cost > 0 else None
		sell_through = min(expected_quantity / current_stock * 100, 100) if current_stock > 0 else None
		stock_cover = current_stock / expected_quantity if expected_quantity > 0 else None
		scenarios.append(
			{
				"name": name,
				"suggested_price": round(price, 2),
				"price_change_pct": round(change_pct, 2),
				"expected_quantity": round(expected_quantity, 4),
				"expected_revenue": round(revenue, 2),
				"expected_gross_profit": round(gross_profit, 2) if gross_profit is not None else None,
				"expected_gross_margin_pct": round(margin_pct, 2) if margin_pct is not None else None,
				"expected_sell_through": round(sell_through, 2) if sell_through is not None else None,
				"stock_cover_impact": round(stock_cover, 2) if stock_cover is not None else None,
				"confidence": confidence,
				"main_reasons": [method_reason, f"Server objective: {objective}."],
				"main_risks": (
					[]
					if elasticity.get("valid")
					else [
						"Expected volume is rule-based and should be reviewed after observing actual response."
					]
				),
			}
		)
	return {
		"scenarios": scenarios,
		"price_floor": scenarios
		and enforce_price_constraints(
			current_price,
			current_price,
			cost,
			mrp,
			minimum_margin_pct,
			maximum_price_change_pct,
			rounding_increment,
		).get("price_floor"),
		"constraint_warnings": list(dict.fromkeys(constraint_warnings)),
	}


def _branch_scope(company: str, branch: str | None):
	branches = _resolve_branch_filter(company, branch)
	warehouses = _branch_warehouses(branches)
	if branches is not None and not branches:
		return branches, warehouses
	return branches, warehouses


def _item_context(item_code, company, branch, warehouses):
	if not frappe.has_permission("Item", ptype="read", doc=item_code):
		frappe.throw(_("Not permitted to view this item."), frappe.PermissionError)
	item = frappe.db.get_value(
		"Item",
		item_code,
		[
			"item_name",
			"item_group",
			"brand",
			"stock_uom",
			"custom_mrp",
			"custom_latest_price_incl_taxes",
			"valuation_rate",
			"last_purchase_rate",
		],
		as_dict=True,
	)
	if not item:
		return None
	price_list = frappe.db.get_value("Branch", branch, "default_selling_price_list") if branch else None
	if not price_list:
		price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")
	price = frappe.db.sql(
		"""
		SELECT ip.price_list_rate, ip.currency, ip.uom, ip.valid_from
		FROM `tabItem Price` ip
		INNER JOIN `tabPrice List` pl ON pl.name = ip.price_list
		WHERE ip.item_code = %(item_code)s AND pl.selling = 1 AND pl.enabled = 1
		  AND (%(price_list)s IS NULL OR ip.price_list = %(price_list)s)
		  AND (ip.valid_from IS NULL OR ip.valid_from <= CURRENT_DATE)
		  AND (ip.valid_upto IS NULL OR ip.valid_upto >= CURRENT_DATE)
		ORDER BY CASE WHEN ip.price_list = %(price_list)s THEN 0 ELSE 1 END,
		         ip.valid_from DESC, ip.modified DESC
		LIMIT 1
		""",
		{"item_code": item_code, "price_list": price_list},
		as_dict=True,
	)
	stock = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(actual_qty), 0) AS current_stock,
		       COALESCE(SUM(stock_value), 0) AS stock_value
		FROM `tabBin`
		WHERE item_code = %(item_code)s
		  AND (%(has_warehouses)s = 0 OR warehouse IN %(warehouses)s)
		""",
		{
			"item_code": item_code,
			"has_warehouses": 1 if warehouses is not None else 0,
			"warehouses": tuple(warehouses or ("",)),
		},
		as_dict=True,
	)[0]
	purchase_cost_rows = frappe.db.sql(
		"""
		SELECT cost
		FROM (
			SELECT COALESCE(NULLIF(pri.custom_price_after_taxes, 0), pri.rate) AS cost,
			       pr.posting_date, pr.creation
			FROM `tabPurchase Receipt Item` pri
			INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
			WHERE pr.docstatus = 1 AND IFNULL(pr.is_return, 0) = 0
			  AND pr.company = %(company)s AND pri.item_code = %(item_code)s
			UNION ALL
			SELECT COALESCE(NULLIF(pii.custom_price_after_taxes, 0), pii.rate) AS cost,
			       pi.posting_date, pi.creation
			FROM `tabPurchase Invoice Item` pii
			INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
			WHERE pi.docstatus = 1 AND IFNULL(pi.is_return, 0) = 0
			  AND pi.company = %(company)s AND pii.item_code = %(item_code)s
		) costs
		WHERE cost > 0
		ORDER BY posting_date DESC, creation DESC
		LIMIT 1
		""",
		{"company": company, "item_code": item_code},
		as_dict=True,
	)
	latest_purchase_cost = flt(purchase_cost_rows[0].cost) if purchase_cost_rows else 0
	return item, (price[0] if price else None), stock, price_list, latest_purchase_cost


def _history(item_code, company, branch, warehouses, customer_group, date_from, date_to):
	branch_clause = "AND COALESCE(pi.branch, pp.branch, w.custom_branch) = %(branch)s" if branch else ""
	warehouse_clause = "AND pii.warehouse IN %(warehouses)s" if warehouses is not None else ""
	customer_clause = "AND pi.customer_group = %(customer_group)s" if customer_group else ""
	allocated = (
		"CASE WHEN ABS(IFNULL(pi.base_net_total, 0)) > 0 "
		"THEN (pii.base_net_amount / pi.base_net_total) * pi.grand_total "
		"ELSE pii.base_net_amount END"
	)
	return frappe.db.sql(
		f"""
		SELECT DATE_FORMAT(pi.posting_date, '%%Y-%%m-01') AS period,
		       SUM(pii.stock_qty) AS quantity,
		       SUM({allocated}) / NULLIF(SUM(pii.stock_qty), 0) AS realized_price,
		       AVG(COALESCE(pii.discount_percentage, 0)) AS discount_pct,
		       COUNT(DISTINCT pi.name) AS transaction_count,
		       SUM(CASE WHEN pi.is_return = 1 THEN ABS(pii.stock_qty) ELSE 0 END) AS return_quantity
		FROM `tabPOS Invoice Item` pii
		INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
		LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
		LEFT JOIN `tabWarehouse` w ON w.name = pii.warehouse
		WHERE pi.docstatus = 1 AND pi.company = %(company)s
		  AND pii.item_code = %(item_code)s
		  AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
		  {branch_clause} {warehouse_clause} {customer_clause}
		GROUP BY period
		ORDER BY period
		""",
		{
			"company": company,
			"item_code": item_code,
			"branch": branch,
			"warehouses": tuple(warehouses or ("",)),
			"customer_group": customer_group,
			"date_from": date_from,
			"date_to": date_to,
		},
		as_dict=True,
	)


def get_price_recommendation(
	item_code: str,
	branch: str | None = None,
	customer_group: str | None = None,
	history_months: int = 12,
	minimum_margin_pct: float = 10,
	maximum_price_change_pct: float = 10,
	objective: str = "maintain current volume",
	include_scenarios: bool = True,
) -> dict:
	if not item_code:
		return {"error": "item_code is required."}
	objective = (objective or "maintain current volume").strip().lower()
	if objective not in _OBJECTIVES:
		return {"error": f"Unsupported objective. Available: {sorted(_OBJECTIVES)}"}
	history_months = max(3, min(cint(history_months or 12), 36))
	minimum_margin_pct = max(0, min(flt(minimum_margin_pct), 95))
	maximum_price_change_pct = max(0, min(flt(maximum_price_change_pct), 50))
	company = _resolve_company()
	branches, warehouses = _branch_scope(company, branch)
	if branches is not None and not branches:
		return {"error": "The selected branch is outside the visible scope."}
	context = _item_context(item_code, company, branch, warehouses)
	if not context:
		return {"error": "Item was not found."}
	item, price_row, stock, price_list, latest_purchase_cost = context
	date_to = getdate()
	date_from = add_months(date_to, -history_months)
	history = _history(item_code, company, branch, warehouses, customer_group, date_from, date_to)
	realized_prices = [flt(row.realized_price) for row in history if flt(row.realized_price) > 0]
	current_price = (
		flt(price_row.price_list_rate)
		if price_row
		else (realized_prices[-1] if realized_prices else flt(item.custom_mrp))
	)
	cost_candidates = [
		flt(item.custom_latest_price_incl_taxes),
		flt(latest_purchase_cost),
		flt(item.valuation_rate),
		flt(item.last_purchase_rate),
	]
	cost = next((value for value in cost_candidates if value > 0), 0)
	mrp = flt(item.custom_mrp)
	if cost <= 0 and minimum_margin_pct > 0:
		return {
			"error": "Certified cost data is required to enforce the minimum-margin constraint.",
			"item_code": item_code,
			"current_price": round(current_price, 2),
			"mrp": round(mrp, 2) if mrp > 0 else None,
			"data_quality_warnings": [
				"No scenario was produced because recommending without a validated cost could violate the required margin."
			],
			"notice": NOTICE,
			"automatic_update": False,
			"calculation_version": CALCULATION_VERSION,
		}
	if current_price <= 0:
		return {
			"error": "No current or realized selling price is available.",
			"item_code": item_code,
			"notice": NOTICE,
		}
	observations = [
		{
			"period": str(row.period),
			"price": flt(row.realized_price),
			"quantity": max(flt(row.quantity), 0),
			"promotion": flt(row.discount_pct) >= 10,
			"stockout": False,
		}
		for row in history
	]
	elasticity = estimate_elasticity(observations)
	forecast = get_demand_forecast(
		item_code=item_code,
		branch=branch,
		granularity="monthly",
		history_months=history_months,
		forecast_horizon=1,
		include_stock_plan=False,
		limit=1,
	)
	forecast_rows = forecast.get("forecasts") or []
	baseline_quantity = (
		flt(forecast_rows[0].get("forecast_quantity"))
		if forecast_rows
		else sum(max(flt(row.quantity), 0) for row in history[-3:]) / max(len(history[-3:]), 1)
	)
	forecast_context = forecast_rows[0] if forecast_rows else {}
	stockout_distortion = cint(forecast_context.get("stockout_periods_observed"))
	seasonality_dominates = forecast_context.get("seasonality_status") == "detected"
	if elasticity.get("valid") and (stockout_distortion or seasonality_dominates):
		distortion_reasons = []
		if stockout_distortion:
			distortion_reasons.append("Observable stockouts distorted historical demand.")
		if seasonality_dominates:
			distortion_reasons.append("Seasonality dominates the selected demand model.")
		elasticity = {
			**elasticity,
			"valid": False,
			"elasticity": None,
			"confidence": "low",
			"reasons": distortion_reasons,
		}
	scenario_result = build_price_scenarios(
		current_price,
		cost,
		mrp,
		baseline_quantity,
		minimum_margin_pct,
		maximum_price_change_pct,
		objective,
		elasticity,
		flt(stock.current_stock),
	)
	if scenario_result.get("error"):
		return {
			"error": scenario_result["error"],
			"constraint_warnings": scenario_result.get("constraint_warnings"),
			"notice": NOTICE,
		}
	if cost <= 0:
		for scenario in scenario_result.get("scenarios") or []:
			scenario["expected_gross_profit"] = None
			scenario["expected_gross_margin_pct"] = None
			scenario["main_risks"] = list(scenario.get("main_risks") or []) + [
				"Gross profit and margin are unavailable because certified cost data is missing."
			]
	promotion_periods = sum(row["promotion"] for row in observations)
	return_period_units = sum(flt(row.return_quantity) for row in history)
	warnings = list(scenario_result.get("constraint_warnings") or [])
	if cost <= 0:
		warnings.append("Cost information is missing; minimum-margin protection cannot be fully validated.")
	if not elasticity.get("valid"):
		warnings.extend(elasticity.get("reasons") or [])
	if promotion_periods:
		warnings.append(
			f"{promotion_periods} promotional period(s) were excluded from elasticity estimation."
		)
	return {
		"notice": NOTICE,
		"automatic_update": False,
		"company": company,
		"branch": branch,
		"customer_group": customer_group,
		"item_code": item_code,
		"item_name": item.item_name,
		"item_group": item.item_group,
		"brand": item.brand,
		"uom": price_row.uom if price_row else item.stock_uom,
		"currency": price_row.currency
		if price_row
		else frappe.get_cached_value("Company", company, "default_currency"),
		"price_list": price_list,
		"current_price": round(current_price, 2),
		"cost": round(cost, 2),
		"mrp": round(mrp, 2) if mrp > 0 else None,
		"price_floor": scenario_result.get("price_floor"),
		"tax_inclusive_pricing": True,
		"historical_realized_price_range": {
			"minimum": round(min(realized_prices), 2) if realized_prices else None,
			"maximum": round(max(realized_prices), 2) if realized_prices else None,
			"average": round(sum(realized_prices) / len(realized_prices), 2) if realized_prices else None,
		},
		"elasticity_estimate": elasticity,
		"recommendation_method": (
			"validated_historical_elasticity" if elasticity.get("valid") else "transparent_rule_based"
		),
		"objective": objective,
		"scenarios": scenario_result["scenarios"] if include_scenarios else scenario_result["scenarios"][1:2],
		"current_stock": round(flt(stock.current_stock), 4),
		"forecast_baseline_quantity": round(baseline_quantity, 4),
		"return_quantity": round(return_period_units, 4),
		"data_coverage": {
			"historical_months": len(history),
			"transaction_count": sum(cint(row.transaction_count) for row in history),
			"distinct_realized_prices": len({round(value, 2) for value in realized_prices}),
			"promotion_periods": promotion_periods,
		},
		"assumptions": [
			"Prices, costs, MRP, revenue, and margin are treated as tax-inclusive.",
			"Retail rounding uses the nearest whole currency unit.",
			"Expected scenario outcomes are deterministic decision-support estimates, not guarantees.",
		],
		"data_quality_warnings": list(dict.fromkeys(warnings)),
		"constraint_warnings": list(dict.fromkeys(scenario_result.get("constraint_warnings") or [])),
		"calculation_version": CALCULATION_VERSION,
	}


TOOL_SPECS = [
	{
		"type": "function",
		"function": {
			"name": "get_price_recommendation",
			"description": (
				"Read-only governed retail sales-price recommendation with conservative, "
				"recommended, and aggressive scenarios. Enforces price floor, minimum "
				"margin, MRP, tax-inclusive pricing, maximum movement, branch price-list "
				"scope, and retail rounding. Never updates Item Price or any ERP document."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"item_code": {"type": "string"},
					"branch": {"type": "string"},
					"customer_group": {"type": "string"},
					"history_months": {"type": "integer"},
					"minimum_margin_pct": {"type": "number"},
					"maximum_price_change_pct": {"type": "number"},
					"objective": {"type": "string", "enum": sorted(_OBJECTIVES)},
					"include_scenarios": {"type": "boolean"},
				},
				"required": ["item_code"],
			},
		},
	}
]

TOOL_DISPATCH = {"get_price_recommendation": get_price_recommendation}
