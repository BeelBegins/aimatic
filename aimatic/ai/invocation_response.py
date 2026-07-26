"""Invocation-oriented response assembly.

This adapter preserves the mature per-tool KPI/table/chart builders while
allowing repeated calls to the same tool to coexist and reconcile into explicit
current/previous comparisons.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from frappe.utils import flt

from aimatic.ai.answer_builder import (
	_KPI_DISPATCH,
	_TABLE_DISPATCH,
	build_response as build_legacy_response,
)
from aimatic.ai.chart_recommender import recommend_chart
from aimatic.ai.report_registry import get_registry
from aimatic.ai.response_schema import (
	ComparisonPeriod,
	DateRange,
	KPI,
	Source,
	StructuredResponse,
	Table,
	ToolInvocation,
)
from aimatic.ai.routing_engine import AnalysisPlan, route_for_tool


def _as_plan(plan: AnalysisPlan | dict[str, Any] | None) -> dict[str, Any]:
	if isinstance(plan, AnalysisPlan):
		return plan.to_dict()
	return dict(plan or {})


def _period_role(arguments: dict[str, Any], plan: dict[str, Any], prior_same_tool: int) -> str:
	arg_from = str(arguments.get("date_from") or "")
	arg_to = str(arguments.get("date_to") or "")
	if (
		plan.get("comparison_from")
		and arg_from == str(plan.get("comparison_from"))
		and arg_to == str(plan.get("comparison_to"))
	):
		return "previous"
	if plan.get("date_from") and arg_from == str(plan.get("date_from")) and arg_to == str(plan.get("date_to")):
		return "current"
	if arguments.get("scenario"):
		return "scenario"
	if prior_same_tool == 0:
		return "current"
	if plan.get("comparison_from") and prior_same_tool == 1:
		return "previous"
	return "supporting"


def normalize_invocations(
	raw: list[dict[str, Any] | ToolInvocation] | dict[str, dict[str, Any]],
	plan: AnalysisPlan | dict[str, Any] | None = None,
) -> list[ToolInvocation]:
	"""Accept both the new list and the legacy ``{tool_name: result}`` shape."""
	plan_dict = _as_plan(plan)
	if isinstance(raw, dict):
		raw = [
			{
				"call_id": f"legacy-{index}",
				"tool_name": name,
				"arguments": {},
				"result": result,
				"sequence": index,
				"status": "error" if isinstance(result, dict) and "error" in result else "success",
				"route": route_for_tool(name),
			}
			for index, (name, result) in enumerate(raw.items(), start=1)
		]

	counts: dict[str, int] = {}
	normalized: list[ToolInvocation] = []
	for sequence, item in enumerate(raw or [], start=1):
		if isinstance(item, ToolInvocation):
			invocation = item
		else:
			name = str(item.get("tool_name") or "")
			arguments = dict(item.get("arguments") or {})
			prior = counts.get(name, 0)
			invocation = ToolInvocation(
				call_id=str(item.get("call_id") or f"call-{sequence}"),
				tool_name=name,
				arguments=arguments,
				result=dict(item.get("result") or {}),
				sequence=int(item.get("sequence") or sequence),
				status="error" if item.get("status") == "error" or "error" in (item.get("result") or {}) else "success",
				route=item.get("route") or route_for_tool(name),
				period_role=item.get("period_role") or _period_role(arguments, plan_dict, prior),
				scenario=item.get("scenario") or arguments.get("scenario"),
			)
		counts[invocation.tool_name] = counts.get(invocation.tool_name, 0) + 1
		normalized.append(invocation)
	return normalized


def _period(invocation: ToolInvocation) -> dict[str, Any] | None:
	date_from = invocation.arguments.get("date_from") or invocation.result.get("date_from")
	date_to = invocation.arguments.get("date_to") or invocation.result.get("date_to")
	if not date_from and not date_to:
		return None
	return {"from": str(date_from or date_to), "to": str(date_to or date_from)}


def _decorate_kpi(kpi: KPI, invocation: ToolInvocation, key_suffix: str = "") -> KPI:
	return replace(
		kpi,
		key=f"{kpi.key}{key_suffix}",
		invocation_ids=[invocation.call_id],
		period=_period(invocation),
		scenario=invocation.scenario,
	)


def _merge_kpis(invocations: list[ToolInvocation]) -> list[KPI]:
	current: dict[tuple[str, str, str | None], KPI] = {}
	previous: dict[tuple[str, str, str | None], tuple[KPI, ToolInvocation]] = {}
	extras: list[KPI] = []

	for invocation in invocations:
		if invocation.status != "success":
			continue
		builder = _KPI_DISPATCH.get(invocation.tool_name)
		if not builder:
			continue
		for kpi in builder(invocation.result):
			key = (invocation.tool_name, kpi.key, invocation.scenario)
			if invocation.period_role == "previous":
				previous[key] = (kpi, invocation)
			elif invocation.period_role in {"current", "scenario"} and key not in current:
				current[key] = _decorate_kpi(kpi, invocation)
			else:
				extras.append(
					_decorate_kpi(
						kpi,
						invocation,
						key_suffix=f"__{invocation.sequence}",
					)
				)

	for key, (prior_kpi, prior_invocation) in previous.items():
		if key not in current:
			extras.append(
				replace(
					_decorate_kpi(prior_kpi, prior_invocation, f"__previous_{prior_invocation.sequence}"),
					label=f"{prior_kpi.label} (Previous)",
				)
			)
			continue
		cur = current[key]
		variance = flt(cur.value) - flt(prior_kpi.value)
		variance_pct = round(variance / abs(flt(prior_kpi.value)) * 100, 2) if flt(prior_kpi.value) else None
		current[key] = replace(
			cur,
			comparison=flt(prior_kpi.value),
			variance_amount=variance,
			variance_pct=variance_pct,
			trend="up" if variance > 0 else "down" if variance < 0 else "flat",
			invocation_ids=cur.invocation_ids + [prior_invocation.call_id],
			comparison_period=_period(prior_invocation),
		)
	return list(current.values()) + extras


def _merge_charts(invocations: list[ToolInvocation]):
	charts = []
	for invocation in invocations:
		if invocation.status != "success":
			continue
		chart = recommend_chart(invocation.tool_name, invocation.result)
		if not chart:
			continue
		label = invocation.period_role.title() if invocation.period_role else ""
		charts.append(
			replace(
				chart,
				id=f"{chart.id}__{invocation.sequence}",
				title=f"{chart.title} — {label}" if label in {"Current", "Previous"} else chart.title,
				invocation_id=invocation.call_id,
				period_role=invocation.period_role,
				scenario=invocation.scenario,
			)
		)
	return charts


def _merge_tables(invocations: list[ToolInvocation]) -> list[Table]:
	tables = []
	for invocation in invocations:
		if invocation.status != "success":
			continue
		builder = _TABLE_DISPATCH.get(invocation.tool_name)
		if not builder:
			continue
		table = builder(invocation.result)
		if not table:
			continue
		label = invocation.period_role.title() if invocation.period_role else ""
		tables.append(
			replace(
				table,
				id=f"{table.id}__{invocation.sequence}",
				title=f"{table.title} — {label}" if label in {"Current", "Previous"} else table.title,
				invocation_id=invocation.call_id,
				period_role=invocation.period_role,
				scenario=invocation.scenario,
				metadata={
					"tool_name": invocation.tool_name,
					"arguments": invocation.arguments,
					"sequence": invocation.sequence,
				},
			)
		)
	return tables


def _sources(invocations: list[ToolInvocation]) -> list[Source]:
	registry = get_registry()
	sources = []
	for invocation in invocations:
		if invocation.status != "success":
			continue
		source = registry.get(invocation.tool_name)
		if not source:
			continue
		sources.append(
			Source(
				type="tool",
				name=source.name,
				description=source.description,
				filters=invocation.arguments,
			)
		)
	return sources


def build_invocation_response(
	question: str,
	reply_text: str,
	tool_invocations: list[dict[str, Any] | ToolInvocation] | dict[str, dict[str, Any]],
	company: str,
	branch_names: list[str],
	user_role: str,
	analysis_plan: AnalysisPlan | dict[str, Any] | None = None,
) -> StructuredResponse:
	"""Build a backward-compatible response without collapsing repeated calls."""
	plan = _as_plan(analysis_plan)
	invocations = normalize_invocations(tool_invocations, plan)

	# Legacy detectors/follow-ups use one result per tool. Prefer current, then
	# the first successful invocation, while the rich structures below retain all.
	legacy_results: dict[str, dict[str, Any]] = {}
	for invocation in invocations:
		if invocation.status != "success":
			continue
		if invocation.tool_name not in legacy_results or invocation.period_role == "current":
			legacy_results[invocation.tool_name] = invocation.result

	response = build_legacy_response(
		question,
		reply_text,
		legacy_results,
		company,
		branch_names,
		user_role,
	)

	context = response.context
	if plan.get("date_from") and plan.get("date_to"):
		comparison = None
		if plan.get("comparison_from") and plan.get("comparison_to"):
			comparison = ComparisonPeriod(
				from_=str(plan["comparison_from"]),
				to=str(plan["comparison_to"]),
				label="Previous period",
			)
		context = replace(
			context,
			date_range=DateRange(from_=str(plan["date_from"]), to=str(plan["date_to"])),
			comparison_period=comparison,
			filters={
				key: plan.get(key)
				for key in ("item", "supplier", "customer", "warehouse")
				if plan.get(key)
			},
		)

	return replace(
		response,
		context=context,
		analysis_plan=plan,
		tool_invocations=invocations,
		kpis=_merge_kpis(invocations),
		charts=_merge_charts(invocations),
		tables=_merge_tables(invocations),
		sources=_sources(invocations),
	)
