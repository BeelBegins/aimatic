from collections import Counter
from unittest import TestCase

from aimatic.ai.golden_questions import GOLDEN_QUESTIONS


class TestGoldenQuestions(TestCase):
	def test_has_twenty_questions_in_every_required_category(self):
		counts = Counter(row["category"] for row in GOLDEN_QUESTIONS)
		self.assertEqual(
			counts,
			{
				"simple": 20,
				"comparison": 20,
				"diagnostic": 20,
				"forecasting": 20,
				"inventory": 20,
				"pricing": 20,
			},
		)
		self.assertEqual(len(GOLDEN_QUESTIONS), 120)

	def test_every_case_has_governance_expectations(self):
		for row in GOLDEN_QUESTIONS:
			self.assertEqual(row["expected_route"], "certified_tool")
			self.assertTrue(row["expected_tool"].startswith("get_"))
			self.assertIsInstance(row["required_parameters"], list)
			self.assertTrue(row["no_invented_figures"])
			self.assertTrue(row["structured_output_type"])
			self.assertIn(
				row["insufficient_data_behavior"],
				{"explicit_limitation", "valid_zero_or_factual_result"},
			)
