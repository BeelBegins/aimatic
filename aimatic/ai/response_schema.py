"""Shared response contract for the AI Assistant: structured dataclasses that
`ask()` builds and the frontend renders against. JSON-serializable via `to_dict()`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional


@dataclass(frozen=True)
class DateRange:
	from_: str = field(metadata={"alias": "from"})
	to: str
	preset: str | None = None

	def to_dict(self) -> dict[str, Any]:
		d = {"from": self.from_, "to": self.to}
		if self.preset is not None:
			d["preset"] = self.preset
		return d


@dataclass(frozen=True)
class ComparisonPeriod:
	from_: str = field(metadata={"alias": "from"})
	to: str
	label: str

	def to_dict(self) -> dict[str, Any]:
		return {"from": self.from_, "to": self.to, "label": self.label}


@dataclass(frozen=True)
class Permissions:
	can_export: bool
	can_schedule: bool
	branches_visible: int
	branches_total: int

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


@dataclass(frozen=True)
class Context:
	company: str
	branch: list[str]
	date_range: DateRange
	filters: dict[str, Any | None]
	comparison_period: ComparisonPeriod | None
	user_role: str
	data_freshness: str
	permissions: Permissions

	def to_dict(self) -> dict[str, Any]:
		return {
			"company": self.company,
			"branch": self.branch,
			"date_range": self.date_range.to_dict(),
			"filters": self.filters,
			"comparison_period": self.comparison_period.to_dict() if self.comparison_period else None,
			"user_role": self.user_role,
			"data_freshness": self.data_freshness,
			"permissions": self.permissions.to_dict(),
		}


@dataclass(frozen=True)
class Answer:
	title: str
	summary: str
	confidence: float
	data_quality: Literal["excellent", "good", "fair", "poor"]
	intent: str
	entities: dict[str, Any]
	direct_answer: str | None = None
	executive_summary: str | None = None
	confidence_details: dict[str, Any] = field(default_factory=dict)

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


@dataclass(frozen=True)
class ToolInvocation:
	call_id: str
	tool_name: str
	arguments: dict[str, Any]
	result: dict[str, Any]
	sequence: int
	status: Literal["success", "error"]
	route: Literal["certified_tool", "analytics", "erp_report", "dynamic_report"]
	period_role: Literal["current", "previous", "scenario", "supporting"] | None = None
	scenario: str | None = None

	def to_dict(self) -> dict[str, Any]:
		d = asdict(self)
		if self.period_role is None:
			d.pop("period_role")
		if self.scenario is None:
			d.pop("scenario")
		return d


@dataclass(frozen=True)
class KPI:
	key: str
	label: str
	value: float
	format: Literal["currency", "percent", "number", "qty"]
	currency: str | None = None
	comparison: float | None = None
	variance_amount: float | None = None
	variance_pct: float | None = None
	trend: Literal["up", "down", "flat"] | None = None
	tooltip: str | None = None
	severity: Literal["info", "watch", "warning", "critical"] | None = None
	target: float | None = None
	status: str | None = None
	unit: str | None = None
	invocation_ids: list[str] = field(default_factory=list)
	period: dict[str, Any] | None = None
	comparison_period: dict[str, Any] | None = None
	scenario: str | None = None

	def to_dict(self) -> dict[str, Any]:
		d = {
			"key": self.key,
			"label": self.label,
			"value": self.value,
			"format": self.format,
		}
		if self.currency is not None:
			d["currency"] = self.currency
		if self.comparison is not None:
			d["comparison"] = self.comparison
			d["previous_value"] = self.comparison
		if self.variance_amount is not None:
			d["variance_amount"] = self.variance_amount
		if self.variance_pct is not None:
			d["variance_pct"] = self.variance_pct
		if self.trend is not None:
			d["trend"] = self.trend
		if self.tooltip is not None:
			d["tooltip"] = self.tooltip
		if self.severity is not None:
			d["severity"] = self.severity
		if self.target is not None:
			d["target"] = self.target
		if self.status is not None:
			d["status"] = self.status
		if self.unit is not None:
			d["unit"] = self.unit
		if self.invocation_ids:
			d["invocation_ids"] = self.invocation_ids
		if self.period is not None:
			d["period"] = self.period
		if self.comparison_period is not None:
			d["comparison_period"] = self.comparison_period
		if self.scenario is not None:
			d["scenario"] = self.scenario
		return d


@dataclass(frozen=True)
class ChartData:
	labels: list[str]
	datasets: list[dict[str, Any]]

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


@dataclass(frozen=True)
class ChartOptions:
	xAxis: dict[str, Any] | None = None
	yAxis: dict[str, Any] | None = None
	horizontal: bool | None = None

	def to_dict(self) -> dict[str, Any]:
		d = {}
		if self.xAxis is not None:
			d["xAxis"] = self.xAxis
		if self.yAxis is not None:
			d["yAxis"] = self.yAxis
		if self.horizontal is not None:
			d["horizontal"] = self.horizontal
		return d


@dataclass(frozen=True)
class Chart:
	id: str
	title: str
	type: Literal[
		"line", "bar", "donut", "pie", "area", "scatter", "heatmap", "pareto", "waterfall", "treemap"
	]
	data: ChartData
	options: ChartOptions
	auto_selected: bool
	manual_override_allowed: bool = True
	invocation_id: str | None = None
	period_role: str | None = None
	scenario: str | None = None

	def to_dict(self) -> dict[str, Any]:
		d = {
			"id": self.id,
			"title": self.title,
			"type": self.type,
			"data": self.data.to_dict(),
			"options": self.options.to_dict(),
			"auto_selected": self.auto_selected,
			"manual_override_allowed": self.manual_override_allowed,
		}
		if self.invocation_id is not None:
			d["invocation_id"] = self.invocation_id
		if self.period_role is not None:
			d["period_role"] = self.period_role
		if self.scenario is not None:
			d["scenario"] = self.scenario
		return d


@dataclass(frozen=True)
class TableColumn:
	key: str
	label: str
	type: Literal["text", "link", "currency", "percent", "float", "int", "date", "qty"]
	doctype: str | None = None
	format: str | None = None
	currency: str | None = None

	def to_dict(self) -> dict[str, Any]:
		d = {"key": self.key, "label": self.label, "type": self.type}
		if self.doctype is not None:
			d["doctype"] = self.doctype
		if self.format is not None:
			d["format"] = self.format
		if self.currency is not None:
			d["currency"] = self.currency
		return d


@dataclass(frozen=True)
class Pagination:
	page: int
	page_size: int
	total: int

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


@dataclass(frozen=True)
class DrillDown:
	target: str
	param_map: dict[str, str]

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


@dataclass(frozen=True)
class Table:
	id: str
	title: str
	columns: list[TableColumn]
	rows: list[dict[str, Any]]
	sortable: bool = True
	filterable: bool = True
	pagination: Pagination | None = None
	exportable: bool = True
	drill_down: DrillDown | None = None
	invocation_id: str | None = None
	period_role: str | None = None
	scenario: str | None = None
	metadata: dict[str, Any] = field(default_factory=dict)

	def to_dict(self) -> dict[str, Any]:
		d = {
			"id": self.id,
			"title": self.title,
			"columns": [c.to_dict() for c in self.columns],
			"rows": self.rows,
			"sortable": self.sortable,
			"filterable": self.filterable,
			"exportable": self.exportable,
		}
		if self.pagination is not None:
			d["pagination"] = self.pagination.to_dict()
		if self.drill_down is not None:
			d["drill_down"] = self.drill_down.to_dict()
		if self.invocation_id is not None:
			d["invocation_id"] = self.invocation_id
		if self.period_role is not None:
			d["period_role"] = self.period_role
		if self.scenario is not None:
			d["scenario"] = self.scenario
		if self.metadata:
			d["metadata"] = self.metadata
		return d


@dataclass(frozen=True)
class Insight:
	type: Literal["anomaly", "opportunity", "positive", "trend", "risk"]
	severity: Literal["low", "medium", "high", "critical"]
	title: str
	description: str
	supporting_data: dict[str, Any]
	actionable: bool

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


@dataclass(frozen=True)
class Warning:
	code: str
	message: str
	affected_metrics: list[str]
	severity: Literal["info", "watch", "warning", "critical"] | None = None
	details: dict[str, Any] = field(default_factory=dict)

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


@dataclass(frozen=True)
class Source:
	type: Literal["tool", "report", "chart", "number_card"]
	name: str
	description: str | None = None
	doctype: str | None = None
	filters: dict[str, Any] | None = None
	metadata: dict[str, Any] = field(default_factory=dict)

	def to_dict(self) -> dict[str, Any]:
		d = {"type": self.type, "name": self.name}
		if self.description is not None:
			d["description"] = self.description
		if self.doctype is not None:
			d["doctype"] = self.doctype
		if self.filters is not None:
			d["filters"] = self.filters
		if self.metadata:
			d["metadata"] = self.metadata
		return d


@dataclass(frozen=True)
class Action:
	type: Literal[
		"save_as_report",
		"add_to_dashboard",
		"export_excel",
		"export_csv",
		"export_pdf",
		"schedule",
		"create_alert",
		"pin_conversation",
	]
	label: str
	payload: dict[str, Any]

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


@dataclass(frozen=True)
class StructuredResponse:
	answer: Answer
	context: Context
	analysis_plan: dict[str, Any] = field(default_factory=dict)
	tool_invocations: list[ToolInvocation] = field(default_factory=list)
	key_drivers: list[dict[str, Any]] = field(default_factory=list)
	recommendations: list[dict[str, Any]] = field(default_factory=list)
	explainability: dict[str, Any] = field(default_factory=dict)
	data_quality_detail: dict[str, Any] = field(default_factory=dict)
	kpis: list[KPI] = field(default_factory=list)
	charts: list[Chart] = field(default_factory=list)
	tables: list[Table] = field(default_factory=list)
	insights: list[Insight] = field(default_factory=list)
	warnings: list[Warning] = field(default_factory=list)
	follow_up_questions: list[str] = field(default_factory=list)
	sources: list[Source] = field(default_factory=list)
	actions: list[Action] = field(default_factory=list)

	def to_dict(self) -> dict[str, Any]:
		return {
			"answer": self.answer.to_dict(),
			"context": self.context.to_dict(),
			"analysis_plan": self.analysis_plan,
			"tool_invocations": [invocation.to_dict() for invocation in self.tool_invocations],
			"key_drivers": self.key_drivers,
			"recommendations": self.recommendations,
			"explainability": self.explainability,
			"data_quality_detail": self.data_quality_detail,
			"kpis": [k.to_dict() for k in self.kpis],
			"charts": [c.to_dict() for c in self.charts],
			"tables": [t.to_dict() for t in self.tables],
			"insights": [i.to_dict() for i in self.insights],
			"warnings": [w.to_dict() for w in self.warnings],
			"follow_up_questions": self.follow_up_questions,
			"sources": [s.to_dict() for s in self.sources],
			"actions": [a.to_dict() for a in self.actions],
		}

	def to_json(self) -> str:
		return json.dumps(self.to_dict(), ensure_ascii=False)
