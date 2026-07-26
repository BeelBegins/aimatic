"""Deterministic analysis planning and certified-tool candidate selection.

The language model may propose a business plan through ``submit_analysis_plan``,
but this module validates that proposal into a small business vocabulary and
selects executable tools from the server-owned registry.  No SQL/table/field
identifier from the model is accepted or forwarded.
"""

from __future__ import annotations

import json
from datetime import date
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from frappe.utils import add_days, get_first_day, get_last_day, getdate, today

from aimatic.ai.report_registry import DataSource, get_registry

CALCULATION_VERSION = "routing-v1"

_ALLOWED_INTENTS = {
	"lookup",
	"comparison",
	"ranking",
	"diagnostic",
	"forecast",
	"recommendation",
	"segmentation",
	"anomaly_detection",
	"drill_down",
}
_ALLOWED_DOMAINS = {
	"sales",
	"profitability",
	"purchasing",
	"inventory",
	"customers",
	"finance",
	"operations",
	"pricing",
	"promotions",
	"vendors",
	"general",
}
_ALLOWED_DIMENSIONS = {
	"branch",
	"warehouse",
	"item",
	"item_group",
	"brand",
	"supplier",
	"customer",
	"customer_group",
	"account",
	"payment_mode",
	"month",
	"week",
	"day",
	"day_of_week",
	"hour",
	"abc_class",
	"xyz_class",
}
_ALLOWED_ROUTES = {"certified_tool", "analytics", "erp_report", "dynamic_report"}
_ROUTE_ORDER = {
	"certified_tool": 1,
	"analytics": 2,
	"erp_report": 3,
	"dynamic_report": 4,
}
_ROUTE_TO_TOOLS = {
	"analytics": {"run_analytics_query", "drill_down_transactions"},
	"erp_report": {"list_frappe_reports", "run_frappe_report"},
	"dynamic_report": {"run_dynamic_report"},
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_DOMAIN_HINTS = {
	"sales": {"sale", "sales", "revenue", "basket", "transaction", "return", "discount", "hourly"},
	"profitability": {"margin", "profit", "profitable", "cost", "cogs", "below"},
	"purchasing": {"purchase", "purchasing", "receipt", "supplier", "vendor", "po"},
	"inventory": {"stock", "inventory", "warehouse", "reorder", "restock", "dead", "aging", "transfer", "abc", "xyz"},
	"customers": {"customer", "receivable", "rfm", "churn", "segment"},
	"finance": {"payable", "cash", "bank", "expense", "tax", "trial", "balance", "payment", "account"},
	"operations": {"shift", "open", "document", "operational"},
	"pricing": {"price", "pricing", "elasticity", "mrp", "sell-through"},
	"promotions": {"promotion", "promo", "campaign", "incremental", "cannibalization"},
	"vendors": {"vendor", "supplier", "lead-time", "short", "delivery"},
}


@dataclass(frozen=True)
class AnalysisPlan:
	intent: str = "lookup"
	business_domain: str = "general"
	metric: str | None = None
	dimensions: list[str] = field(default_factory=list)
	company: str | None = None
	branch: str | None = None
	item: str | None = None
	supplier: str | None = None
	customer: str | None = None
	warehouse: str | None = None
	date_from: str | None = None
	date_to: str | None = None
	comparison_from: str | None = None
	comparison_to: str | None = None
	requested_ranking: dict[str, Any] | None = None
	forecast_horizon: int | None = None
	required_confidence: str = "medium"
	preferred_route: str = "certified_tool"
	fallback_route: str = "analytics"
	calculation_version: str = CALCULATION_VERSION

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


ANALYSIS_PLAN_TOOL_SPEC = {
	"type": "function",
	"function": {
		"name": "submit_analysis_plan",
		"description": (
			"Submit a structured business-analysis plan before requesting data. "
			"Use business concepts only. Never include SQL, table names, column names, "
			"database identifiers, formulas, or calculated business figures."
		),
		"parameters": {
			"type": "object",
			"properties": {
				"intent": {"type": "string", "enum": sorted(_ALLOWED_INTENTS)},
				"business_domain": {"type": "string", "enum": sorted(_ALLOWED_DOMAINS)},
				"metric": {"type": "string"},
				"dimensions": {"type": "array", "items": {"type": "string", "enum": sorted(_ALLOWED_DIMENSIONS)}},
				"branch": {"type": "string"},
				"item": {"type": "string"},
				"supplier": {"type": "string"},
				"customer": {"type": "string"},
				"warehouse": {"type": "string"},
				"date_from": {"type": "string", "description": "Resolved YYYY-MM-DD start date."},
				"date_to": {"type": "string", "description": "Resolved YYYY-MM-DD end date."},
				"comparison_from": {"type": "string", "description": "Resolved YYYY-MM-DD comparison start."},
				"comparison_to": {"type": "string", "description": "Resolved YYYY-MM-DD comparison end."},
				"requested_ranking": {
					"type": "object",
					"properties": {
						"direction": {"type": "string", "enum": ["top", "bottom", "best", "worst"]},
						"limit": {"type": "integer"},
					},
				},
				"forecast_horizon": {"type": "integer"},
				"required_confidence": {"type": "string", "enum": ["low", "medium", "high"]},
				"preferred_route": {"type": "string", "enum": sorted(_ALLOWED_ROUTES)},
				"fallback_route": {"type": "string", "enum": sorted(_ALLOWED_ROUTES)},
			},
			"required": ["intent", "business_domain", "dimensions", "preferred_route", "fallback_route"],
		},
	},
}


def _clean_text(value: Any, max_length: int = 180) -> str | None:
	if value is None:
		return None
	text = str(value).strip()
	if not text:
		return None
	# Plans are business metadata, never an execution language.
	if re.search(r"\b(select|from|join|where|union|insert|update|delete)\b|`tab", text, re.IGNORECASE):
		return None
	return text[:max_length]


def _clean_date(value: Any) -> str | None:
	if not value:
		return None
	try:
		return str(getdate(value))
	except Exception:
		return None


def _infer_dates(question: str) -> tuple[str, str, str | None, str | None]:
	now = getdate(date.today())
	q = question.lower()
	if "this month" in q:
		date_from, date_to = get_first_day(now), now
	elif "last month" in q:
		last_day = add_days(get_first_day(now), -1)
		date_from, date_to = get_first_day(last_day), get_last_day(last_day)
	elif "this year" in q:
		date_from, date_to = now.replace(month=1, day=1), now
	elif "last week" in q:
		this_week_start = add_days(now, -now.weekday())
		date_from, date_to = add_days(this_week_start, -7), add_days(this_week_start, -1)
	elif "this week" in q:
		date_from, date_to = add_days(now, -now.weekday()), now
	elif "yesterday" in q:
		date_from = date_to = add_days(now, -1)
	else:
		date_from = date_to = now

	compare_from = compare_to = None
	if any(token in q for token in ("compare", "compared", "versus", " vs ", "previous period")):
		span = (date_to - date_from).days
		compare_to = add_days(date_from, -1)
		compare_from = add_days(compare_to, -span)
	return str(date_from), str(date_to), str(compare_from) if compare_from else None, str(compare_to) if compare_to else None


def fallback_plan(question: str, company: str | None = None) -> AnalysisPlan:
	"""Deterministic fallback when the planning model fails or malforms its call."""
	tokens = set(_TOKEN_RE.findall(question.lower()))
	domain = "general"
	best_overlap = 0
	for candidate, hints in _DOMAIN_HINTS.items():
		overlap = len(tokens & hints)
		if overlap > best_overlap:
			domain, best_overlap = candidate, overlap

	if any(t in question.lower() for t in ("forecast", "predict", "next week", "next month")):
		intent = "forecast"
	elif any(t in question.lower() for t in ("recommend", "should i", "what should", "transfer")):
		intent = "recommendation"
	elif any(t in question.lower() for t in ("compare", "versus", " vs ", "change")):
		intent = "comparison"
	elif any(t in question.lower() for t in ("top ", "bottom ", "best ", "worst ", "rank")):
		intent = "ranking"
	elif any(t in question.lower() for t in ("why ", "driver", "cause", "behind")):
		intent = "diagnostic"
	elif any(t in question.lower() for t in ("anomaly", "unusual", "unexpected", "abnormal")):
		intent = "anomaly_detection"
	elif any(t in question.lower() for t in ("segment", "abc", "xyz", "rfm")):
		intent = "segmentation"
	else:
		intent = "lookup"

	dimensions = [dimension for dimension in _ALLOWED_DIMENSIONS if dimension.replace("_", " ") in question.lower()]
	date_from, date_to, comparison_from, comparison_to = _infer_dates(question)
	return AnalysisPlan(
		intent=intent,
		business_domain=domain,
		metric=_clean_text(question, 120),
		dimensions=dimensions[:4],
		company=company,
		date_from=date_from,
		date_to=date_to,
		comparison_from=comparison_from,
		comparison_to=comparison_to,
		forecast_horizon=4 if intent == "forecast" else None,
	)


def validate_plan(raw: dict[str, Any] | None, question: str, company: str) -> AnalysisPlan:
	raw = dict(raw or {})
	fallback = fallback_plan(question, company)
	intent = raw.get("intent") if raw.get("intent") in _ALLOWED_INTENTS else fallback.intent
	domain = raw.get("business_domain") if raw.get("business_domain") in _ALLOWED_DOMAINS else fallback.business_domain
	dimensions = [d for d in (raw.get("dimensions") or []) if d in _ALLOWED_DIMENSIONS][:6]
	date_from = _clean_date(raw.get("date_from")) or fallback.date_from
	date_to = _clean_date(raw.get("date_to")) or fallback.date_to
	if getdate(date_from) > getdate(date_to):
		date_from, date_to = date_to, date_from

	comparison_from = _clean_date(raw.get("comparison_from")) or fallback.comparison_from
	comparison_to = _clean_date(raw.get("comparison_to")) or fallback.comparison_to
	ranking = raw.get("requested_ranking")
	if isinstance(ranking, dict):
		direction = ranking.get("direction")
		limit = max(1, min(int(ranking.get("limit") or 10), 100))
		ranking = {"direction": direction if direction in {"top", "bottom", "best", "worst"} else "top", "limit": limit}
	else:
		ranking = None

	horizon = raw.get("forecast_horizon")
	try:
		horizon = max(1, min(int(horizon), 365)) if horizon is not None else fallback.forecast_horizon
	except (TypeError, ValueError):
		horizon = fallback.forecast_horizon

	preferred = raw.get("preferred_route")
	fallback_route = raw.get("fallback_route")
	if preferred not in _ALLOWED_ROUTES:
		preferred = "certified_tool"
	if fallback_route not in _ALLOWED_ROUTES or _ROUTE_ORDER[fallback_route] <= _ROUTE_ORDER[preferred]:
		fallback_route = "analytics" if preferred == "certified_tool" else "erp_report"

	return AnalysisPlan(
		intent=intent,
		business_domain=domain,
		metric=_clean_text(raw.get("metric")) or fallback.metric,
		dimensions=dimensions or fallback.dimensions,
		company=company,
		branch=_clean_text(raw.get("branch")),
		item=_clean_text(raw.get("item")),
		supplier=_clean_text(raw.get("supplier")),
		customer=_clean_text(raw.get("customer")),
		warehouse=_clean_text(raw.get("warehouse")),
		date_from=date_from,
		date_to=date_to,
		comparison_from=comparison_from,
		comparison_to=comparison_to,
		requested_ranking=ranking,
		forecast_horizon=horizon,
		required_confidence=raw.get("required_confidence") if raw.get("required_confidence") in {"low", "medium", "high"} else "medium",
		preferred_route=preferred,
		fallback_route=fallback_route,
	)


def parse_plan_message(message: dict[str, Any], question: str, company: str) -> AnalysisPlan:
	for call in message.get("tool_calls") or []:
		function = call.get("function") or {}
		if function.get("name") != "submit_analysis_plan":
			continue
		raw = function.get("arguments") or {}
		try:
			raw = json.loads(raw) if isinstance(raw, str) else raw
		except (TypeError, ValueError):
			raw = {}
		return validate_plan(raw if isinstance(raw, dict) else {}, question, company)
	return fallback_plan(question, company)


def route_for_tool(tool_name: str) -> str:
	for route, names in _ROUTE_TO_TOOLS.items():
		if tool_name in names:
			return route
	return "certified_tool"


def _source_text(tool_name: str, source: DataSource) -> set[str]:
	text = " ".join(
		[
			tool_name.replace("_", " "),
			source.name,
			source.description,
			" ".join(source.supported_filters),
			" ".join(source.returned_fields),
			" ".join(source.example_questions),
		]
	)
	return set(_TOKEN_RE.findall(text.lower()))


def _score_source(question_tokens: set[str], plan: AnalysisPlan, tool_name: str, source: DataSource) -> int:
	source_tokens = _source_text(tool_name, source)
	score = len(question_tokens & source_tokens) * 4
	score += len(set(_TOKEN_RE.findall((plan.metric or "").lower())) & source_tokens) * 2
	score += len(set(plan.dimensions) & set(source.supported_filters)) * 3
	if plan.business_domain in source_tokens:
		score += 4
	for entity_name, value in (
		("branch", plan.branch),
		("warehouse", plan.warehouse),
		("item", plan.item),
		("supplier", plan.supplier),
		("customer", plan.customer),
	):
		if value and any(f == entity_name or f.startswith(f"{entity_name}_") for f in source.supported_filters):
			score += 3
	if plan.intent == "forecast" and "forecast" in source_tokens:
		score += 20
	if plan.intent == "recommendation" and {"recommendation", "recommendations", "reorder", "transfer", "price"} & source_tokens:
		score += 12
	if plan.intent == "segmentation" and {"segment", "segmentation", "abc", "xyz", "rfm"} & source_tokens:
		score += 14
	if plan.intent == "anomaly_detection" and {"anomaly", "abnormal", "unusual", "negative"} & source_tokens:
		score += 14
	return score


def select_candidate_names(
	question: str,
	plan: AnalysisPlan,
	available_names: set[str],
	failed_tools: set[str] | None = None,
	successful_routes: set[str] | None = None,
	max_certified: int = 8,
) -> list[str]:
	"""Return a narrow, ordered tool list while preserving route precedence."""
	failed_tools = failed_tools or set()
	successful_routes = successful_routes or set()
	question_tokens = set(_TOKEN_RE.findall(question.lower()))
	registry = get_registry()

	# Once a route has returned valid data (including a valid zero), never expose
	# a lower-quality route on later iterations.
	max_route_order = min((_ROUTE_ORDER[r] for r in successful_routes), default=4)
	route_candidates: dict[str, list[tuple[int, str]]] = {route: [] for route in _ROUTE_ORDER}
	for name in available_names:
		if name in failed_tools:
			continue
		route = route_for_tool(name)
		if _ROUTE_ORDER[route] > max_route_order:
			continue
		source = registry.get(name)
		if not source:
			continue
		score = _score_source(question_tokens, plan, name, source)
		route_candidates[route].append((score, name))

	preferred_order = sorted(_ROUTE_ORDER, key=_ROUTE_ORDER.get)
	if plan.preferred_route in preferred_order:
		preferred_order.remove(plan.preferred_route)
		preferred_order.insert(0, plan.preferred_route)
		# Never violate the quality floor: a lower-quality preferred route is
		# considered only after higher-quality routes that have a real match.
		preferred_order.sort(key=lambda r: (_ROUTE_ORDER[r], r != plan.preferred_route))

	for route in preferred_order:
		candidates = route_candidates[route]
		if not candidates:
			continue
		candidates.sort(key=lambda pair: (-pair[0], pair[1]))
		if route == "certified_tool":
			matched = [name for score, name in candidates if score > 0][:max_certified]
			if matched:
				return matched
			continue
		if route == "analytics":
			return [name for _score, name in candidates]
		if route == "erp_report":
			return [name for _score, name in candidates]
		return [name for _score, name in candidates[:1]]
	return []


def select_tool_specs(
	question: str,
	plan: AnalysisPlan,
	all_specs: list[dict[str, Any]],
	failed_tools: set[str] | None = None,
	successful_routes: set[str] | None = None,
) -> list[dict[str, Any]]:
	by_name = {
		spec.get("function", {}).get("name"): spec
		for spec in all_specs
		if spec.get("function", {}).get("name")
	}
	names = select_candidate_names(
		question,
		plan,
		set(by_name),
		failed_tools=failed_tools,
		successful_routes=successful_routes,
	)
	return [by_name[name] for name in names if name in by_name]

