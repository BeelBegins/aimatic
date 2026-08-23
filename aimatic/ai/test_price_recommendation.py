from unittest import TestCase

from aimatic.ai.price_recommendation import (
	build_price_scenarios,
	enforce_price_constraints,
	estimate_elasticity,
)


class TestPriceConstraints(TestCase):
	def test_price_floor_enforces_minimum_margin(self):
		result = enforce_price_constraints(90, 100, 90, 150, 20, 50)
		self.assertTrue(result["valid"])
		self.assertGreaterEqual(result["price"], 112.5)

	def test_mrp_is_hard_ceiling(self):
		result = enforce_price_constraints(140, 100, 50, 120, 10, 50)
		self.assertEqual(result["price"], 120)

	def test_conflicting_floor_and_mrp_returns_limitation(self):
		result = enforce_price_constraints(100, 100, 100, 105, 10, 20)
		self.assertFalse(result["valid"])
		self.assertIn("MRP", result["error"])

	def test_maximum_movement_is_enforced(self):
		result = enforce_price_constraints(200, 100, 20, 300, 10, 10)
		self.assertEqual(result["price"], 110)

	def test_tax_inclusive_cost_margin(self):
		result = enforce_price_constraints(110, 100, 100, 200, 10, 50)
		self.assertGreaterEqual((result["price"] - 100) / result["price"] * 100, 10)


class TestElasticity(TestCase):
	def test_low_price_variation_is_rejected(self):
		observations = [{"price": 100, "quantity": 10} for _index in range(12)]
		result = estimate_elasticity(observations)
		self.assertFalse(result["valid"])
		self.assertEqual(result["confidence"], "low")

	def test_promotional_history_is_not_used_as_reliable_elasticity(self):
		observations = [
			{"price": 80 + index, "quantity": 30 - index, "promotion": index < 8} for index in range(12)
		]
		result = estimate_elasticity(observations)
		self.assertFalse(result["valid"])
		self.assertIn("Promotions", " ".join(result["reasons"]))

	def test_scenarios_never_write_and_respect_bounds(self):
		elasticity = {"valid": False, "elasticity": None, "confidence": "low"}
		result = build_price_scenarios(
			current_price=100,
			cost=70,
			mrp=108,
			baseline_quantity=50,
			minimum_margin_pct=20,
			maximum_price_change_pct=10,
			objective="maximize gross margin",
			elasticity=elasticity,
			current_stock=100,
		)
		self.assertEqual(len(result["scenarios"]), 3)
		self.assertTrue(all(scenario["suggested_price"] <= 108 for scenario in result["scenarios"]))
		self.assertTrue(all(scenario["expected_gross_margin_pct"] >= 20 for scenario in result["scenarios"]))

	def test_missing_cost_does_not_invent_profit_or_margin(self):
		result = build_price_scenarios(
			current_price=100,
			cost=0,
			mrp=120,
			baseline_quantity=20,
			minimum_margin_pct=10,
			maximum_price_change_pct=10,
			objective="maximize revenue",
			elasticity={"valid": False},
		)
		self.assertTrue(all(row["expected_gross_profit"] is None for row in result["scenarios"]))
		self.assertTrue(all(row["expected_gross_margin_pct"] is None for row in result["scenarios"]))
