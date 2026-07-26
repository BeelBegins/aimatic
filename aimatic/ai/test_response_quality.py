from __future__ import annotations

from unittest import TestCase

from aimatic.ai.response_quality import (
	calculate_drivers,
	calculate_quality,
	deterministic_recommendations,
	direct_answer,
)
from aimatic.ai.response_schema import KPI, ToolInvocation


def _invocation(result, status="success", route="certified_tool"):
	return ToolInvocation(
		call_id="call-1",
		tool_name="test_tool",
		arguments={"date_from": "2026-07-01", "date_to": "2026-07-31"},
		result=result,
		sequence=1,
		status=status,
		route=route,
		period_role="current",
	)


class TestResponseQuality(TestCase):
	def test_confidence_is_calculated_from_evidence_not_fixed(self):
		strong = calculate_quality(
			[
				_invocation(
					{
						"row_count": 100,
						"rows": [{"value": index} for index in range(100)],
						"coverage_pct": 100,
						"reconciliation": {"passed": True},
					}
				)
			]
		)
		weak = calculate_quality(
			[
				_invocation(
					{
						"row_count": 1,
						"rows": [{"value": None}],
						"coverage_pct": 20,
						"reconciliation": {"passed": False},
					},
					route="dynamic_report",
				)
			]
		)
		self.assertGreater(strong["score"], weak["score"])
		self.assertNotEqual(strong["score"], 85)
		self.assertEqual(strong["calculation_version"], "response-quality-v1")

	def test_direct_answer_includes_comparison_variance(self):
		answer = direct_answer(
			[
				KPI(
					key="sales",
					label="Net Sales",
					value=120,
					format="currency",
					currency="PKR",
					comparison=100,
					variance_amount=20,
					variance_pct=20,
				)
			]
		)
		self.assertIn("PKR 120.00", answer)
		self.assertIn("20.00%", answer)

	def test_drivers_are_server_ranked_by_absolute_impact(self):
		drivers = calculate_drivers(
			[
				_invocation(
					{
						"rows": [
							{"branch": "Small", "net_sales": 10},
							{"branch": "Large", "net_sales": 90},
						]
					}
				)
			]
		)
		self.assertEqual(drivers[0]["label"], "Large")
		self.assertEqual(drivers[0]["contribution_pct"], 90)

	def test_price_recommendation_is_explicitly_non_automatic(self):
		recommendations = deterministic_recommendations(
			[
				_invocation(
					{
						"scenarios": [
							{
								"name": "Recommended",
								"suggested_price": 125,
								"main_reasons": ["Margin floor satisfied"],
							}
						]
					}
				)
			]
		)
		self.assertFalse(recommendations[0]["automatic_update"])
