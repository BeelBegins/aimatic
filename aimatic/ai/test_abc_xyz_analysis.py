from unittest import TestCase

from aimatic.ai.abc_xyz_analysis import classify_abc, classify_xyz, validate_abc_thresholds


class TestAbcClassification(TestCase):
	def test_default_thresholds_use_cumulative_contribution(self):
		rows = [
			{"item_code": "A", "net_sales": 80},
			{"item_code": "B", "net_sales": 15},
			{"item_code": "C", "net_sales": 5},
		]
		classified = classify_abc(rows, "net_sales")
		self.assertEqual([row["abc_class"] for row in classified], ["A", "B", "C"])
		self.assertEqual(classified[-1]["cumulative_contribution_pct"], 100)

	def test_first_item_crossing_threshold_remains_in_that_class(self):
		rows = [
			{"item_code": "A", "net_sales": 70},
			{"item_code": "B", "net_sales": 20},
			{"item_code": "C", "net_sales": 10},
		]
		classified = classify_abc(rows, "net_sales")
		self.assertEqual([row["abc_class"] for row in classified], ["A", "A", "B"])

	def test_negative_margin_does_not_inflate_contribution(self):
		rows = [
			{"item_code": "A", "margin": 100},
			{"item_code": "Loss", "margin": -50},
		]
		classified = classify_abc(rows, "margin")
		self.assertEqual(classified[0]["sales_contribution_pct"], 100)
		self.assertEqual(classified[1]["sales_contribution_pct"], 0)

	def test_threshold_validation(self):
		self.assertEqual(validate_abc_thresholds(80, 95), (0.8, 0.95))
		with self.assertRaises(ValueError):
			validate_abc_thresholds(95, 90)


class TestXyzClassification(TestCase):
	def test_stable_demand_is_x(self):
		result = classify_xyz([10, 11, 9, 10, 10, 10, 11, 9, 10, 10, 11, 9])
		self.assertEqual(result["xyz_class"], "X")
		self.assertLess(result["coefficient_of_variation"], 0.5)

	def test_moderately_seasonal_demand_is_y(self):
		result = classify_xyz([5, 10, 15, 5, 10, 15, 5, 10, 15, 5, 10, 15])
		self.assertEqual(result["xyz_class"], "Y")

	def test_intermittent_demand_is_z(self):
		result = classify_xyz([0, 0, 20, 0, 0, 15, 0, 0, 25, 0, 0, 10])
		self.assertEqual(result["xyz_class"], "Z")
		self.assertEqual(result["active_selling_periods"], 4)

	def test_new_item_is_not_claimed_stable(self):
		result = classify_xyz([10, 10])
		self.assertEqual(result["xyz_class"], "Insufficient")
		self.assertLess(result["confidence"], 50)
