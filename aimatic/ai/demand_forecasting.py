"""Certified deterministic retail-demand forecasting.

The LLM may select this tool and explain its returned facts.  It cannot choose
the forecasting model, change the calculations, or provide forecast figures.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Callable
from datetime import timedelta
from statistics import fmean
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_months, add_to_date, cint, flt, get_first_day, getdate, now_datetime

from aimatic.ai.tools import _branch_warehouses, _resolve_branch_filter, _resolve_company

CALCULATION_VERSION = "demand-forecast-v1"
_CACHE_TTL_SECONDS = 6 * 60 * 60
_MAX_ITEMS = 50
_MAX_HISTORY_MONTHS = 36
_SEASON_LENGTH = {"daily": 7, "weekly": 52, "monthly": 12}
_MAX_HORIZON = {"daily": 90, "weekly": 26, "monthly": 12}
_PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30.4375}


def _mean(values: list[float]) -> float:
	return fmean(values) if values else 0.0


def _stddev(values: list[float]) -> float:
	if not values:
		return 0.0
	mean = _mean(values)
	return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _forecast_naive(history: list[float], horizon: int, **_) -> list[float]:
	return [max(history[-1], 0) if history else 0.0] * horizon


def _forecast_moving_average(history: list[float], horizon: int, window: int = 3, **_) -> list[float]:
	working = list(history)
	result = []
	for _index in range(horizon):
		value = max(_mean(working[-min(window, len(working)) :]), 0) if working else 0
		result.append(value)
		working.append(value)
	return result


def _forecast_exponential_smoothing(
	history: list[float], horizon: int, alpha: float = 0.30, **_
) -> list[float]:
	if not history:
		return [0.0] * horizon
	level = max(history[0], 0)
	for value in history[1:]:
		level = alpha * max(value, 0) + (1 - alpha) * level
	return [max(level, 0)] * horizon


def _forecast_seasonal_naive(history: list[float], horizon: int, season_length: int, **_) -> list[float]:
	if len(history) < season_length:
		return _forecast_naive(history, horizon)
	cycle = list(history[-season_length:])
	return [max(cycle[index % season_length], 0) for index in range(horizon)]


def _forecast_seasonal_average(history: list[float], horizon: int, season_length: int, **_) -> list[float]:
	if len(history) < season_length * 2:
		return _forecast_seasonal_naive(history, horizon, season_length)
	seasonal = []
	for position in range(season_length):
		values = [max(history[index], 0) for index in range(position, len(history), season_length)]
		seasonal.append(_mean(values))
	start = len(history) % season_length
	return [seasonal[(start + index) % season_length] for index in range(horizon)]


def _forecast_tsb(
	history: list[float],
	horizon: int,
	alpha: float = 0.20,
	beta: float = 0.20,
	**_,
) -> list[float]:
	"""Teunter-Syntetos-Babai forecast for intermittent demand."""
	if not history:
		return [0.0] * horizon
	positive = [max(value, 0) for value in history if value > 0]
	if not positive:
		return [0.0] * horizon
	size = positive[0]
	probability = 1.0 / max(1, next((i + 1 for i, value in enumerate(history) if value > 0), 1))
	for value in history:
		occurrence = 1.0 if value > 0 else 0.0
		probability = beta * occurrence + (1 - beta) * probability
		if value > 0:
			size = alpha * value + (1 - alpha) * size
	return [max(probability * size, 0)] * horizon


_FORECASTERS: dict[str, Callable[..., list[float]]] = {
	"naive": _forecast_naive,
	"moving_average": _forecast_moving_average,
	"exponential_smoothing": _forecast_exponential_smoothing,
	"seasonal_naive": _forecast_seasonal_naive,
	"seasonal_average": _forecast_seasonal_average,
	"tsb_intermittent": _forecast_tsb,
}


def _eligible_methods(history: list[float], season_length: int) -> list[str]:
	methods = ["naive"]
	if len(history) >= 4:
		methods.extend(["moving_average", "exponential_smoothing"])
	zero_ratio = sum(value <= 0 for value in history) / len(history) if history else 1
	if len(history) >= 6 and zero_ratio >= 0.35:
		methods.append("tsb_intermittent")
	if len(history) >= season_length * 2:
		methods.extend(["seasonal_naive", "seasonal_average"])
	return methods


def evaluate_forecast(
	actual: list[float],
	predicted: list[float],
) -> dict[str, float]:
	pairs = list(zip(actual, predicted))
	absolute_error = [abs(observed - estimate) for observed, estimate in pairs]
	denominator = sum(abs(observed) for observed, _estimate in pairs)
	return {
		"wape": round(sum(absolute_error) / denominator * 100, 4) if denominator else 0.0,
		"mae": round(_mean(absolute_error), 4),
		"bias": round(_mean([estimate - observed for observed, estimate in pairs]), 4),
	}


def select_forecast_model(
	history: list[float],
	season_length: int,
) -> dict[str, Any]:
	"""Select the lowest-WAPE model using one-step rolling-origin backtesting."""
	history = [max(flt(value), 0) for value in history]
	if len(history) < 4:
		return {
			"selected_model": "naive",
			"metrics": {"wape": None, "mae": None, "bias": None},
			"backtest_points": 0,
			"insufficient_data": True,
			"fallback_method": "naive",
		}
	test_points = min(12, max(3, len(history) // 3))
	start = len(history) - test_points
	scores = {}
	for method in _eligible_methods(history, season_length):
		actual = []
		predicted = []
		for index in range(start, len(history)):
			training = history[:index]
			if not training:
				continue
			estimate = _FORECASTERS[method](training, 1, season_length=season_length)[0]
			actual.append(history[index])
			predicted.append(max(estimate, 0))
		if actual:
			scores[method] = evaluate_forecast(actual, predicted)
	if not scores:
		selected = "naive"
		metrics = {"wape": None, "mae": None, "bias": None}
	else:
		selected = min(
			scores,
			key=lambda name: (scores[name]["wape"], scores[name]["mae"], name),
		)
		metrics = scores[selected]
	return {
		"selected_model": selected,
		"metrics": metrics,
		"candidate_scores": scores,
		"backtest_points": test_points,
		"insufficient_data": len(history) < 6,
		"fallback_method": selected if len(history) < 6 else None,
	}


def build_forecast(
	history: list[float],
	horizon: int,
	season_length: int,
) -> dict[str, Any]:
	history = [max(flt(value), 0) for value in history]
	selection = select_forecast_model(history, season_length)
	method = selection["selected_model"]
	forecast = _FORECASTERS[method](history, horizon, season_length=season_length)
	forecast = [round(max(value, 0), 4) for value in forecast]

	# Use rolling-test residuals when available; fall back to observed variability.
	mae = selection["metrics"].get("mae")
	residual_sigma = flt(mae) * 1.2533 if mae is not None else _stddev(history)
	lower = [
		round(max(value - 1.96 * residual_sigma * math.sqrt(index + 1), 0), 4)
		for index, value in enumerate(forecast)
	]
	upper = [
		round(value + 1.96 * residual_sigma * math.sqrt(index + 1), 4) for index, value in enumerate(forecast)
	]
	return {
		**selection,
		"forecast": forecast,
		"lower_bound": lower,
		"upper_bound": upper,
		"residual_sigma": round(residual_sigma, 4),
	}


def forecast_confidence(
	period_count: int,
	wape: float | None,
	zero_periods: int,
	outlier_percentage: float,
	stockout_periods: int,
) -> dict[str, Any]:
	coverage = min(period_count / 24, 1.0)
	accuracy = 0.25 if wape is None else max(0, 1 - min(wape, 100) / 100)
	zero_penalty = (zero_periods / period_count) * 0.20 if period_count else 0.20
	outlier_penalty = min(outlier_percentage / 100, 1) * 0.15
	stockout_penalty = min(stockout_periods / max(period_count, 1), 1) * 0.15
	score = max(
		0.05,
		min(
			0.98, 0.45 * coverage + 0.45 * accuracy + 0.10 - zero_penalty - outlier_penalty - stockout_penalty
		),
	)
	label = "high" if score >= 0.80 else "medium" if score >= 0.55 else "low"
	return {
		"score": round(score * 100, 2),
		"label": label,
		"components": {
			"historical_coverage": round(coverage * 100, 2),
			"backtest_accuracy": round(accuracy * 100, 2),
			"zero_demand_penalty": round(zero_penalty * 100, 2),
			"outlier_penalty": round(outlier_penalty * 100, 2),
			"stockout_penalty": round(stockout_penalty * 100, 2),
		},
	}


def _period_key(value, granularity: str) -> str:
	value = getdate(value)
	if granularity == "daily":
		return str(value)
	if granularity == "weekly":
		monday = value - timedelta(days=value.weekday())
		return str(monday)
	return str(get_first_day(value))


def _period_keys(date_from, date_to, granularity: str) -> list[str]:
	current = getdate(date_from)
	end = getdate(date_to)
	if granularity == "weekly":
		current -= timedelta(days=current.weekday())
	elif granularity == "monthly":
		current = get_first_day(current)
	keys = []
	while current <= end:
		keys.append(str(current))
		if granularity == "daily":
			current += timedelta(days=1)
		elif granularity == "weekly":
			current += timedelta(days=7)
		else:
			current = add_months(current, 1)
	return keys


def _future_periods(last_period: str, horizon: int, granularity: str) -> list[str]:
	current = getdate(last_period)
	result = []
	for _index in range(horizon):
		if granularity == "daily":
			current += timedelta(days=1)
		elif granularity == "weekly":
			current += timedelta(days=7)
		else:
			current = add_months(current, 1)
		result.append(str(current))
	return result


def _winsorise(values: list[float]) -> tuple[list[float], float]:
	if len(values) < 6:
		return list(values), 0.0
	sorted_values = sorted(values)
	q1 = sorted_values[len(values) // 4]
	q3 = sorted_values[(len(values) * 3) // 4]
	iqr = q3 - q1
	upper = q3 + 1.5 * iqr
	if upper <= 0:
		return list(values), 0.0
	clipped = [min(value, upper) for value in values]
	changed = sum(original != adjusted for original, adjusted in zip(values, clipped))
	return clipped, round(changed / len(values) * 100, 2)


def _cache_key(arguments: dict[str, Any]) -> str:
	payload = json.dumps(
		{"version": CALCULATION_VERSION, **arguments},
		sort_keys=True,
		default=str,
		separators=(",", ":"),
	)
	return "aimatic:forecast:" + hashlib.sha256(payload.encode()).hexdigest()


def _warehouse_scope(company: str, branches: list[str] | None, warehouse: str | None):
	if warehouse:
		if not frappe.has_permission("Warehouse", ptype="read", doc=warehouse):
			frappe.throw(_("Not permitted to view this warehouse."), frappe.PermissionError)
		warehouse_company, warehouse_branch = frappe.db.get_value(
			"Warehouse", warehouse, ["company", "custom_branch"]
		) or (None, None)
		if warehouse_company != company:
			frappe.throw(_("Warehouse does not belong to the selected company."))
		if branches is not None and warehouse_branch not in branches:
			frappe.throw(_("Warehouse is outside the visible branch scope."), frappe.PermissionError)
		return [warehouse]
	return _branch_warehouses(branches)


def _query_history(
	params,
	branches,
	warehouses,
	item_code,
	item_group,
	brand,
	limit,
):
	branch_clause = (
		"AND COALESCE(pi.branch, pp.branch, w.custom_branch) IN %(branches)s" if branches is not None else ""
	)
	warehouse_clause = "AND pii.warehouse IN %(warehouses)s" if warehouses is not None else ""
	item_clause = "AND pii.item_code = %(item_code)s" if item_code else ""
	group_clause = "AND i.item_group = %(item_group)s" if item_group else ""
	brand_clause = "AND i.brand = %(brand)s" if brand else ""
	params["limit"] = limit
	candidates = frappe.db.sql(
		f"""
		SELECT pii.item_code, pii.warehouse,
		       MAX(i.item_name) AS item_name, MAX(i.item_group) AS item_group,
		       MAX(i.brand) AS brand, MAX(w.custom_branch) AS branch,
		       SUM(CASE WHEN pi.is_return = 0 THEN pii.stock_qty ELSE 0 END) AS positive_qty
		FROM `tabPOS Invoice Item` pii
		INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
		LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
		LEFT JOIN `tabWarehouse` w ON w.name = pii.warehouse
		INNER JOIN `tabItem` i ON i.name = pii.item_code
		WHERE pi.docstatus = 1 AND pi.company = %(company)s
		  AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
		  {branch_clause} {warehouse_clause} {item_clause} {group_clause} {brand_clause}
		GROUP BY pii.item_code, pii.warehouse
		ORDER BY positive_qty DESC
		LIMIT %(limit)s
		""",
		params,
		as_dict=True,
	)
	if not candidates:
		return [], []
	pairs = [(row.item_code, row.warehouse) for row in candidates]
	params["item_codes"] = tuple({pair[0] for pair in pairs})
	params["selected_warehouses"] = tuple({pair[1] for pair in pairs if pair[1]})
	history = frappe.db.sql(
		"""
		SELECT pii.item_code, pii.warehouse, pi.posting_date,
		       SUM(pii.stock_qty) AS net_quantity,
		       SUM(CASE WHEN pi.is_return = 1 THEN ABS(pii.stock_qty) ELSE 0 END) AS return_quantity,
		       AVG(COALESCE(pii.discount_percentage, 0)) AS discount_percentage,
		       COUNT(DISTINCT pi.name) AS transaction_count
		FROM `tabPOS Invoice Item` pii
		INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
		WHERE pi.docstatus = 1 AND pi.company = %(company)s
		  AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
		  AND pii.item_code IN %(item_codes)s
		  AND pii.warehouse IN %(selected_warehouses)s
		GROUP BY pii.item_code, pii.warehouse, pi.posting_date
		""",
		params,
		as_dict=True,
	)
	return candidates, history


def _stock_positions(params):
	return frappe.db.sql(
		"""
		SELECT b.item_code, b.warehouse, i.lead_time_days,
		       SUM(b.actual_qty) AS current_stock,
		       SUM(b.actual_qty - b.reserved_qty) AS available_stock,
		       SUM(b.ordered_qty) AS incoming_stock
		FROM `tabBin` b
		INNER JOIN `tabItem` i ON i.name = b.item_code
		WHERE b.item_code IN %(item_codes)s
		  AND b.warehouse IN %(selected_warehouses)s
		GROUP BY b.item_code, b.warehouse, i.lead_time_days
		""",
		params,
		as_dict=True,
	)


def _stockout_periods(params, granularity):
	rows = frappe.db.sql(
		"""
		SELECT item_code, warehouse, posting_date,
		       MIN(qty_after_transaction) AS minimum_balance
		FROM `tabStock Ledger Entry`
		WHERE is_cancelled = 0
		  AND posting_date BETWEEN %(date_from)s AND %(date_to)s
		  AND item_code IN %(item_codes)s
		  AND warehouse IN %(selected_warehouses)s
		GROUP BY item_code, warehouse, posting_date
		HAVING minimum_balance <= 0
		""",
		params,
		as_dict=True,
	)
	result = defaultdict(set)
	for row in rows:
		result[(row.item_code, row.warehouse)].add(_period_key(row.posting_date, granularity))
	return result


def _inventory_plan(
	forecast: list[float],
	available_stock: float,
	current_stock: float,
	incoming_stock: float,
	lead_time_days: int,
	granularity: str,
	residual_sigma: float,
):
	period_days = _PERIOD_DAYS[granularity]
	lead_periods = max(1, math.ceil(max(lead_time_days, 1) / period_days))
	expected_lead_demand = sum(forecast[:lead_periods])
	if lead_periods > len(forecast):
		expected_lead_demand += _mean(forecast) * (lead_periods - len(forecast))
	safety_stock = 1.65 * residual_sigma * math.sqrt(lead_periods)
	reorder_point = expected_lead_demand + safety_stock
	suggested = max(0, reorder_point - available_stock - incoming_stock)
	average_period_demand = _mean(forecast)
	stockout_periods = available_stock / average_period_demand if average_period_demand > 0 else None
	stockout_days = stockout_periods * period_days if stockout_periods is not None else None
	stockout_date = (
		str(getdate() + timedelta(days=math.ceil(stockout_days))) if stockout_days is not None else None
	)
	return {
		"current_stock": round(current_stock, 4),
		"available_stock": round(available_stock, 4),
		"incoming_stock": round(incoming_stock, 4),
		"lead_time_days": lead_time_days,
		"expected_demand_during_lead_time": round(expected_lead_demand, 4),
		"safety_stock": round(safety_stock, 4),
		"reorder_point": round(reorder_point, 4),
		"stockout_risk_days": round(stockout_days, 1) if stockout_days is not None else None,
		"stockout_risk_date": stockout_date,
		"suggested_reorder_quantity": round(suggested, 4),
		"expected_ending_stock": round(current_stock + incoming_stock - sum(forecast), 4),
	}


def get_demand_forecast(
	item_code: str | None = None,
	item_group: str | None = None,
	brand: str | None = None,
	branch: str | None = None,
	warehouse: str | None = None,
	granularity: str = "weekly",
	history_months: int = 12,
	forecast_horizon: int = 4,
	include_stock_plan: bool = False,
	limit: int = 10,
) -> dict:
	granularity = (granularity or "weekly").lower()
	if granularity not in _SEASON_LENGTH:
		return {"error": "Granularity must be Daily, Weekly, or Monthly."}
	history_months = max(1, min(cint(history_months or 12), _MAX_HISTORY_MONTHS))
	forecast_horizon = max(1, min(cint(forecast_horizon or 4), _MAX_HORIZON[granularity]))
	limit = max(1, min(cint(limit or 10), _MAX_ITEMS))
	company = _resolve_company()
	branches = _resolve_branch_filter(company, branch)
	warehouses = _warehouse_scope(company, branches, warehouse)
	if branches is not None and not branches:
		return {"forecasts": [], "row_count": 0, "calculation_version": CALCULATION_VERSION}
	if warehouses is not None and not warehouses:
		return {"forecasts": [], "row_count": 0, "calculation_version": CALCULATION_VERSION}

	date_to = getdate()
	date_from = add_to_date(date_to, months=-history_months, days=1)
	arguments = {
		"company": company,
		"item_code": item_code,
		"item_group": item_group,
		"brand": brand,
		"branch": branch,
		"warehouse": warehouse,
		"granularity": granularity,
		"history_months": history_months,
		"forecast_horizon": forecast_horizon,
		"include_stock_plan": bool(include_stock_plan),
		"limit": limit,
		"date_to": str(date_to),
	}
	cache_key = _cache_key(arguments)
	cached = frappe.cache().get_value(cache_key)
	if cached:
		return json.loads(cached) if isinstance(cached, str) else cached

	params = {
		"company": company,
		"date_from": date_from,
		"date_to": date_to,
		"branches": tuple(branches or ()),
		"warehouses": tuple(warehouses or ()),
		"item_code": item_code,
		"item_group": item_group,
		"brand": brand,
	}
	candidates, history_rows = _query_history(
		params, branches, warehouses, item_code, item_group, brand, limit
	)
	if not candidates:
		result = {
			"company": company,
			"date_from": str(date_from),
			"date_to": str(date_to),
			"granularity": granularity,
			"forecasts": [],
			"row_count": 0,
			"warnings": ["No submitted POS sales matched the requested filters."],
			"calculation_version": CALCULATION_VERSION,
		}
		frappe.cache().set_value(cache_key, json.dumps(result), expires_in_sec=_CACHE_TTL_SECONDS)
		return result

	keys = _period_keys(date_from, date_to, granularity)
	series = defaultdict(
		lambda: defaultdict(lambda: {"quantity": 0.0, "returns": 0.0, "discount": [], "transactions": 0})
	)
	for row in history_rows:
		period = _period_key(row.posting_date, granularity)
		cell = series[(row.item_code, row.warehouse)][period]
		cell["quantity"] += flt(row.net_quantity)
		cell["returns"] += flt(row.return_quantity)
		cell["discount"].append(flt(row.discount_percentage))
		cell["transactions"] += cint(row.transaction_count)

	stock_map = {}
	stockout_map = {}
	if params.get("selected_warehouses"):
		stock_map = {(row.item_code, row.warehouse): row for row in _stock_positions(params)}
		stockout_map = _stockout_periods(params, granularity)

	results = []
	season_length = _SEASON_LENGTH[granularity]
	for candidate in candidates:
		pair = (candidate.item_code, candidate.warehouse)
		first_active_index = next(
			(index for index, period in enumerate(keys) if series[pair][period]["transactions"] > 0),
			len(keys) - 1,
		)
		item_keys = keys[first_active_index:]
		raw_history = [max(series[pair][period]["quantity"], 0) for period in item_keys]
		returns = sum(series[pair][period]["returns"] for period in item_keys)
		discount_periods = sum(1 for period in item_keys if _mean(series[pair][period]["discount"]) >= 10)
		observed_stockouts = stockout_map.get(pair, set())
		# A zero caused by an observable stockout is treated as missing and
		# conservatively imputed from nearby non-zero demand for model fitting.
		model_history = list(raw_history)
		nonzero_average = _mean([value for value in raw_history if value > 0])
		for index, period in enumerate(item_keys):
			if period in observed_stockouts and model_history[index] <= 0 and nonzero_average > 0:
				model_history[index] = nonzero_average
		clean_history, outlier_percentage = _winsorise(model_history)
		built = build_forecast(clean_history, forecast_horizon, season_length)
		future_periods = _future_periods(item_keys[-1], forecast_horizon, granularity)
		zero_periods = sum(value <= 0 for value in raw_history)
		confidence = forecast_confidence(
			len(raw_history),
			built["metrics"].get("wape"),
			zero_periods,
			outlier_percentage,
			len(observed_stockouts),
		)
		warnings = []
		if built["insufficient_data"]:
			warnings.append(
				f"Insufficient history for robust model selection; fallback method {built['fallback_method']} was used."
			)
		if zero_periods / max(len(raw_history), 1) >= 0.35:
			warnings.append("Demand is intermittent with many zero-demand periods.")
		if observed_stockouts:
			warnings.append(
				"Observable stockout periods were imputed for model fitting because recorded sales may understate demand."
			)
		if discount_periods:
			warnings.append(
				f"{discount_periods} period(s) include average discount of at least 10%; promotion effects may influence demand."
			)
		if outlier_percentage:
			warnings.append(
				f"{outlier_percentage}% of periods were capped as high outliers for model fitting."
			)
		if returns:
			warnings.append(f"Returns totaling {round(returns, 4)} units were netted from submitted sales.")

		forecast_rows = [
			{
				"period": period,
				"forecast_quantity": built["forecast"][index],
				"lower_confidence_bound": built["lower_bound"][index],
				"upper_confidence_bound": built["upper_bound"][index],
			}
			for index, period in enumerate(future_periods)
		]
		result = {
			"item_code": candidate.item_code,
			"item_name": candidate.item_name,
			"item_group": candidate.item_group,
			"brand": candidate.brand,
			"branch": candidate.branch or _("Unassigned"),
			"warehouse": candidate.warehouse,
			"selected_model": built["selected_model"],
			"forecast_period": f"{future_periods[0]} to {future_periods[-1]}",
			"forecast_quantity": round(sum(built["forecast"]), 4),
			"lower_confidence_bound": round(sum(built["lower_bound"]), 4),
			"upper_confidence_bound": round(sum(built["upper_bound"]), 4),
			"historical_average": round(_mean(raw_history), 4),
			"recent_trend": round(_mean(raw_history[-3:]) - _mean(raw_history[-6:-3]), 4)
			if len(raw_history) >= 6
			else None,
			"seasonality_status": (
				"detected"
				if built["selected_model"] in {"seasonal_naive", "seasonal_average"}
				else "not selected"
			),
			"wape": built["metrics"].get("wape"),
			"mae": built["metrics"].get("mae"),
			"bias": built["metrics"].get("bias"),
			"historical_periods": len(raw_history),
			"zero_demand_periods": zero_periods,
			"stockout_periods_observed": len(observed_stockouts),
			"promotion_periods_observed": discount_periods,
			"outlier_percentage": outlier_percentage,
			"forecast_confidence": confidence["score"],
			"confidence_label": confidence["label"],
			"confidence_details": confidence["components"],
			"insufficient_data": built["insufficient_data"],
			"fallback_method": built["fallback_method"],
			"additional_history_required": (
				max(0, 6 - len(raw_history)) if built["insufficient_data"] else 0
			),
			"history": [
				{"period": period, "quantity": round(raw_history[index], 4)}
				for index, period in enumerate(item_keys)
			],
			"forecast": forecast_rows,
			"data_quality_warnings": warnings,
			"candidate_model_scores": built.get("candidate_scores") or {},
		}
		if include_stock_plan:
			stock = stock_map.get(pair)
			result["stock_plan"] = _inventory_plan(
				built["forecast"],
				flt(stock.available_stock) if stock else 0,
				flt(stock.current_stock) if stock else 0,
				flt(stock.incoming_stock) if stock else 0,
				cint(stock.lead_time_days) if stock else 0,
				granularity,
				built["residual_sigma"],
			)
		results.append(result)

	response = {
		"company": company,
		"branch": branch,
		"warehouse": warehouse,
		"date_from": str(date_from),
		"date_to": str(date_to),
		"granularity": granularity,
		"forecast_horizon": forecast_horizon,
		"include_stock_plan": bool(include_stock_plan),
		"forecasts": results,
		"row_count": len(results),
		"data_freshness": str(now_datetime()),
		"source": "Submitted POS Invoice and POS Invoice Item",
		"calculation_version": CALCULATION_VERSION,
	}
	frappe.cache().set_value(cache_key, json.dumps(response, default=str), expires_in_sec=_CACHE_TTL_SECONDS)
	return response


TOOL_SPECS = [
	{
		"type": "function",
		"function": {
			"name": "get_demand_forecast",
			"description": (
				"Certified deterministic POS demand forecast with rolling backtesting, "
				"server-selected model, confidence intervals, data-quality warnings, "
				"and optional read-only stock plan. Use for forecast, future demand, "
				"reorder point, safety stock, or stockout-risk questions."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"item_code": {"type": "string"},
					"item_group": {"type": "string"},
					"brand": {"type": "string"},
					"branch": {"type": "string"},
					"warehouse": {"type": "string"},
					"granularity": {
						"type": "string",
						"enum": ["daily", "weekly", "monthly"],
					},
					"history_months": {"type": "integer"},
					"forecast_horizon": {"type": "integer"},
					"include_stock_plan": {"type": "boolean"},
					"limit": {"type": "integer", "description": f"Maximum {_MAX_ITEMS}."},
				},
			},
		},
	}
]

TOOL_DISPATCH = {"get_demand_forecast": get_demand_forecast}
