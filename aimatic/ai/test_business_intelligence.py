"""Deterministic regression tests for the governed BI engines."""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from aimatic.ai.invocation_response import _merge_kpis, normalize_invocations
from aimatic.ai.report_registry import DataSource
from aimatic.ai.response_schema import KPI
from aimatic.ai.routing_engine import (
	AnalysisPlan,
	select_candidate_names,
	validate_plan,
)


def _source(name: str, description: str, filters: list[str], examples: list[str]) -> DataSource:
	return DataSource(
		key=f"tool:{name}",
		name=name.replace("_", " ").title(),
		description=description,
		source_type="tool",
		supported_filters=filters,
		returned_fields=[],
		supported_visualizations=["kpi"],
		example_questions=examples,
	)


class TestInvocationArchitecture(TestCase):
	def test_repeated_calls_to_same_tool_are_preserved_and_compared(self):
		plan = AnalysisPlan(
			intent="comparison",
			business_domain="sales",
			date_from="2026-07-01",
			date_to="2026-07-31",
			comparison_from="2026-06-01",
			comparison_to="2026-06-30",
		)
		invocations = normalize_invocations(
			[
				{
					"call_id": "current",
					"tool_name": "get_sales_overview",
					"arguments": {"date_from": "2026-07-01", "date_to": "2026-07-31"},
					"result": {"net_sales": 120, "currency": "PKR"},
					"sequence": 1,
					"status": "success",
					"route": "certified_tool",
				},
				{
					"call_id": "previous",
					"tool_name": "get_sales_overview",
					"arguments": {"date_from": "2026-06-01", "date_to": "2026-06-30"},
					"result": {"net_sales": 100, "currency": "PKR"},
					"sequence": 2,
					"status": "success",
					"route": "certified_tool",
				},
			],
			plan,
		)
		self.assertEqual(len(invocations), 2)
		self.assertEqual([i.period_role for i in invocations], ["current", "previous"])

		def build(result):
			return [
				KPI(
					key="sales",
					label="Net Sales",
					value=result["net_sales"],
					format="currency",
					currency="PKR",
				)
			]

		with patch.dict("aimatic.ai.invocation_response._KPI_DISPATCH", {"get_sales_overview": build}):
			kpis = _merge_kpis(invocations)
		self.assertEqual(len(kpis), 1)
		self.assertEqual(kpis[0].value, 120)
		self.assertEqual(kpis[0].comparison, 100)
		self.assertEqual(kpis[0].variance_amount, 20)
		self.assertEqual(kpis[0].variance_pct, 20)
		self.assertEqual(kpis[0].invocation_ids, ["current", "previous"])

	def test_legacy_result_dict_remains_supported(self):
		invocations = normalize_invocations(
			{
				"get_sales_overview": {"net_sales": 50},
				"get_returns_overview": {"returns_amount": 5},
			}
		)
		self.assertEqual(len(invocations), 2)
		self.assertTrue(all(i.status == "success" for i in invocations))


class TestAnalysisPlanningAndRouting(TestCase):
	def test_sql_language_is_removed_from_model_plan(self):
		plan = validate_plan(
			{
				"intent": "lookup",
				"business_domain": "sales",
				"metric": "SELECT grand_total FROM tabPOS Invoice",
				"dimensions": ["branch", "made_up_column"],
				"preferred_route": "certified_tool",
				"fallback_route": "analytics",
			},
			"Show sales by branch",
			"Test Company",
		)
		self.assertNotIn("SELECT", plan.metric or "")
		self.assertEqual(plan.dimensions, ["branch"])
		self.assertEqual(plan.company, "Test Company")

	def test_certified_match_wins_over_fallbacks(self):
		registry = {
			"get_sales_overview": _source(
				"get_sales_overview",
				"Certified net sales and transaction totals.",
				["date_from", "date_to", "branch"],
				["What were sales this month?"],
			),
			"run_analytics_query": _source(
				"run_analytics_query",
				"Governed analytics fallback.",
				["dataset", "measures", "dimension"],
				["Analyze a custom measure breakdown"],
			),
			"run_dynamic_report": _source(
				"run_dynamic_report",
				"Whitelisted last resort.",
				["doctype"],
				["List documents"],
			),
		}
		plan = AnalysisPlan(intent="lookup", business_domain="sales", metric="net sales")
		with patch("aimatic.ai.routing_engine.get_registry", return_value=registry):
			names = select_candidate_names(
				"What were net sales this month?",
				plan,
				set(registry),
			)
		self.assertEqual(names, ["get_sales_overview"])

	def test_successful_certified_route_hides_lower_quality_routes(self):
		registry = {
			"get_sales_overview": _source("get_sales_overview", "sales", [], ["sales"]),
			"run_analytics_query": _source("run_analytics_query", "sales analytics", [], ["sales"]),
			"run_dynamic_report": _source("run_dynamic_report", "sales rows", [], ["sales"]),
		}
		with patch("aimatic.ai.routing_engine.get_registry", return_value=registry):
			names = select_candidate_names(
				"sales",
				AnalysisPlan(business_domain="sales"),
				set(registry),
				successful_routes={"certified_tool"},
			)
		self.assertEqual(names, ["get_sales_overview"])
