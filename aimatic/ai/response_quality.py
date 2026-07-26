"""Deterministic response quality, drivers, recommendations and explainability."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from frappe.utils import flt

from aimatic.ai.report_registry import get_registry
from aimatic.ai.response_schema import KPI, ToolInvocation

CALCULATION_VERSION = "response-quality-v1"

_ROW_KEYS = (
	"rows",
	"items",
	"vendors",
	"top_suppliers",
	"customers",
	"branches",
	"recommendations",
	"forecasts",
	"segments",
	"anomalies",
	"pairs",
)
_IMPACT_KEYS = (
	"net_sales",
	"sales_amount",
	"gross_margin_amount",
	"outstanding_amount",
	"stock_value",
	"purchase_amount",
	"forecast_quantity",
	"suggested_order_qty",
	"incremental_revenue",
	"monetary_value",
	"value",
)
_LABEL_KEYS = (
	"branch",
	"item_name",
	"item_code",
	"supplier",
	"customer",
	"customer_name",
	"account",
	"dimension_value",
	"warehouse",
	"segment",
	"name",
)


def _successful(invocations: Iterable[ToolInvocation]) -> list[ToolInvocation]:
	return [invocation for invocation in invocations if invocation.status == "success"]


def _rows(result: dict[str, Any]) -> list[dict[str, Any]]:
	for key in _ROW_KEYS:
		value = result.get(key)
		if isinstance(value, list):
			return [row for row in value if isinstance(row, dict)]
	return []


def _row_count(result: dict[str, Any]) -> int:
	explicit = result.get("total_row_count")
	if explicit is None:
		explicit = result.get("row_count")
	if explicit is not None:
		try:
			return max(0, int(explicit))
		except (TypeError, ValueError):
			pass
	return len(_rows(result))


def _flatten_scalars(value: Any, limit: int = 500) -> list[Any]:
	output: list[Any] = []

	def visit(node):
		if len(output) >= limit:
			return
		if isinstance(node, dict):
			for child in node.values():
				visit(child)
		elif isinstance(node, list):
			for child in node[:100]:
				visit(child)
		else:
			output.append(node)

	visit(value)
	return output


def calculate_quality(invocations: list[ToolInvocation]) -> dict[str, Any]:
	successful = _successful(invocations)
	if not successful:
		return {
			"score": 0,
			"grade": "poor",
			"factors": {"tool_success": 0, "data_volume": 0, "coverage": 0, "completeness": 0, "reconciliation": 0},
			"calculation_version": CALCULATION_VERSION,
		}

	success_rate = len(successful) / max(len(invocations), 1)
	total_rows = sum(_row_count(i.result) for i in successful)
	data_volume = min(1.0, 0.35 + total_rows / 100)

	coverage_values = []
	reconciliation_values = []
	forecast_values = []
	outlier_rates = []
	for invocation in successful:
		result = invocation.result
		quality_rows = [result, *_rows(result)]
		for quality_row in quality_rows:
			for key in ("data_coverage", "coverage", "coverage_pct", "forecast_confidence"):
				if quality_row.get(key) is not None:
					value = flt(quality_row.get(key))
					coverage_values.append(value / 100 if value > 1 else value)
			if quality_row.get("wape") is not None:
				forecast_values.append(max(0.0, 1.0 - flt(quality_row.get("wape")) / 100))
			if quality_row.get("outlier_percentage") is not None:
				outlier_rates.append(min(1.0, flt(quality_row.get("outlier_percentage")) / 100))
		reconciliation = result.get("reconciliation")
		if isinstance(reconciliation, dict):
			reconciliation_values.append(1.0 if reconciliation.get("passed") else 0.0)

	if any(invocation.tool_name == "get_demand_forecast" for invocation in successful) and not forecast_values:
		forecast_values.append(0.25)

	scalars = []
	for invocation in successful:
		scalars.extend(_flatten_scalars(invocation.result))
	missing = sum(value is None or value == "" for value in scalars)
	completeness = 1.0 - missing / max(len(scalars), 1)

	coverage = sum(coverage_values) / len(coverage_values) if coverage_values else min(1.0, 0.5 + total_rows / 60)
	reconciliation = (
		sum(reconciliation_values) / len(reconciliation_values) if reconciliation_values else 0.85
	)
	forecast_accuracy = sum(forecast_values) / len(forecast_values) if forecast_values else 1.0
	outlier_quality = 1.0 - (sum(outlier_rates) / len(outlier_rates) if outlier_rates else 0.0)
	route_reliability = sum(
		{"certified_tool": 1.0, "analytics": 0.92, "erp_report": 0.85, "dynamic_report": 0.72}.get(
			invocation.route, 0.7
		)
		for invocation in successful
	) / len(successful)

	factors = {
		"tool_success": round(success_rate, 4),
		"data_volume": round(data_volume, 4),
		"coverage": round(coverage, 4),
		"completeness": round(completeness, 4),
		"reconciliation": round(reconciliation, 4),
		"forecast_accuracy": round(forecast_accuracy, 4),
		"outlier_quality": round(outlier_quality, 4),
		"tool_reliability": round(route_reliability, 4),
	}
	score = round(
		100
		* (
			success_rate * 0.15
			+ data_volume * 0.10
			+ coverage * 0.20
			+ completeness * 0.15
			+ reconciliation * 0.15
			+ forecast_accuracy * 0.10
			+ outlier_quality * 0.05
			+ route_reliability * 0.10
		),
		1,
	)
	grade = "excellent" if score >= 90 else "good" if score >= 75 else "fair" if score >= 55 else "poor"
	return {
		"score": score,
		"grade": grade,
		"rows_analyzed": total_rows,
		"factors": factors,
		"calculation_version": CALCULATION_VERSION,
	}


def direct_answer(kpis: list[KPI]) -> str:
	if not kpis:
		return ""
	kpi = kpis[0]
	if kpi.format == "currency":
		value = f"{kpi.currency or ''} {flt(kpi.value):,.2f}".strip()
	elif kpi.format == "percent":
		value = f"{flt(kpi.value):,.2f}%"
	elif kpi.format == "qty":
		value = f"{flt(kpi.value):,.2f}"
	else:
		value = f"{flt(kpi.value):,.2f}"
	answer = f"{kpi.label}: {value}"
	if kpi.variance_amount is not None:
		direction = "up" if kpi.variance_amount > 0 else "down" if kpi.variance_amount < 0 else "unchanged"
		pct = f" ({abs(flt(kpi.variance_pct)):.2f}%)" if kpi.variance_pct is not None else ""
		answer += f" — {direction} by {abs(flt(kpi.variance_amount)):,.2f}{pct} versus the comparison period"
	return answer


def calculate_drivers(invocations: list[ToolInvocation], limit: int = 5) -> list[dict[str, Any]]:
	candidates = []
	for invocation in _successful(invocations):
		rows = _rows(invocation.result)
		for row in rows:
			label = next((str(row[key]) for key in _LABEL_KEYS if row.get(key) not in (None, "")), None)
			if not label:
				continue
			metric = next((key for key in _IMPACT_KEYS if row.get(key) is not None), None)
			if not metric:
				continue
			value = flt(row.get(metric))
			candidates.append(
				{
					"label": label,
					"metric": metric,
					"value": value,
					"tool_name": invocation.tool_name,
					"invocation_id": invocation.call_id,
				}
			)
	candidates.sort(key=lambda row: abs(row["value"]), reverse=True)
	total = sum(abs(row["value"]) for row in candidates) or 0
	for row in candidates:
		row["contribution_pct"] = round(abs(row["value"]) / total * 100, 2) if total else None
	return candidates[:limit]


def deterministic_recommendations(invocations: list[ToolInvocation], limit: int = 6) -> list[dict[str, Any]]:
	recommendations = []
	for invocation in _successful(invocations):
		result = invocation.result
		for row in _rows(result):
			stock_plan = row.get("stock_plan") or {}
			reorder_quantity = flt(row.get("suggested_order_qty") or stock_plan.get("suggested_reorder_quantity"))
			if reorder_quantity > 0:
				recommendations.append(
					{
						"title": f"Reorder {row.get('item_name') or row.get('item_code')}",
						"action": "review_reorder",
						"quantity": reorder_quantity,
						"reason": (
							f"Forecast demand during lead time is {flt(stock_plan.get('expected_demand_during_lead_time')):.2f}."
							if stock_plan
							else f"Current cover is {flt(row.get('days_of_stock')):.1f} days."
						),
						"invocation_id": invocation.call_id,
					}
				)
			if flt(row.get("transfer_qty")) > 0:
				recommendations.append(
					{
						"title": f"Review transfer for {row.get('item_name') or row.get('item_code')}",
						"action": "review_stock_transfer",
						"quantity": flt(row.get("transfer_qty")),
						"reason": f"Move from {row.get('from_branch')} to {row.get('to_branch')}.",
						"invocation_id": invocation.call_id,
					}
				)
		for scenario in result.get("scenarios") or []:
			if scenario.get("name") == "Recommended":
				recommendations.append(
					{
						"title": "Review recommended selling-price scenario",
						"action": "review_price_recommendation",
						"suggested_price": scenario.get("suggested_price"),
						"reason": "; ".join(scenario.get("main_reasons") or []),
						"invocation_id": invocation.call_id,
						"automatic_update": False,
					}
				)
	return recommendations[:limit]


def explainability(invocations: list[ToolInvocation]) -> dict[str, Any]:
	registry = get_registry()
	included = []
	excluded = []
	assumptions = []
	limitations = []
	source_transactions = []
	for invocation in invocations:
		source = registry.get(invocation.tool_name)
		if invocation.status == "error":
			excluded.append(
				{
					"tool_name": invocation.tool_name,
					"reason": invocation.result.get("error") or "Tool execution failed",
				}
			)
			continue
		included.append(
			{
				"tool_name": invocation.tool_name,
				"description": source.description if source else None,
				"filters": invocation.arguments,
				"rows_analyzed": _row_count(invocation.result),
				"calculation_version": invocation.result.get("calculation_version"),
			}
		)
		assumptions.extend(invocation.result.get("assumptions") or [])
		limitations.extend(invocation.result.get("limitations") or [])
		if invocation.tool_name == "drill_down_transactions":
			source_transactions.extend(_rows(invocation.result))
	return {
		"how_calculated": included,
		"data_included": included,
		"data_excluded": excluded,
		"assumptions": list(dict.fromkeys(str(value) for value in assumptions)),
		"limitations": list(dict.fromkeys(str(value) for value in limitations)),
		"source_transactions": source_transactions[:50],
		"calculation_version": CALCULATION_VERSION,
	}


def result_follow_ups(
	invocations: list[ToolInvocation],
	drivers: list[dict[str, Any]],
	recommendations: list[dict[str, Any]],
) -> list[str]:
	follow_ups = []
	tool_names = {invocation.tool_name for invocation in _successful(invocations)}
	if "get_abc_xyz_analysis" in tool_names:
		follow_ups.extend(["Show A-class items at stockout risk", "Forecast the AX items for the next four weeks"])
	if "get_demand_forecast" in tool_names:
		follow_ups.extend(["Show source sales periods for this forecast", "Which branch can transfer this stock?"])
	if "get_price_recommendation" in tool_names:
		follow_ups.extend(["Compare the recommended price scenarios", "Show transactions behind the elasticity estimate"])
	if drivers:
		follow_ups.append(f"Show source transactions behind {drivers[0]['label']}")
	if recommendations:
		follow_ups.append(f"Explain the assumptions behind {recommendations[0]['title']}")
	if any(invocation.result.get("previous_date_from") for invocation in _successful(invocations)):
		follow_ups.append("Show which drivers caused the period change")
	return list(dict.fromkeys(follow_ups))[:5]
