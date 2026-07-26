from unittest import TestCase

from aimatic.ai.demand_forecasting import (
	build_forecast,
	evaluate_forecast,
	forecast_confidence,
	select_forecast_model,
)


class TestDemandForecasting(TestCase):
	def test_metrics_are_deterministic(self):
		metrics = evaluate_forecast([10, 20, 30], [12, 18, 33])
		self.assertEqual(metrics["mae"], 2.3333)
		self.assertEqual(metrics["bias"], 1.0)
		self.assertEqual(metrics["wape"], 11.6667)

	def test_new_item_uses_explicit_fallback(self):
		result = select_forecast_model([4, 6], 12)
		self.assertTrue(result["insufficient_data"])
		self.assertEqual(result["fallback_method"], "naive")
		self.assertIsNone(result["metrics"]["wape"])

	def test_stable_series_selects_and_forecasts_nonnegative(self):
		result = build_forecast([10, 11, 10, 9, 10, 11, 10, 9, 10], 4, 7)
		self.assertEqual(len(result["forecast"]), 4)
		self.assertTrue(all(value >= 0 for value in result["forecast"]))

	def test_seasonal_candidate_is_backtested_when_history_is_sufficient(self):
		history = [10, 20, 30, 40] * 6
		result = select_forecast_model(history, 4)
		self.assertIn("seasonal_naive", result["candidate_scores"])
		self.assertIn("seasonal_average", result["candidate_scores"])

	def test_intermittent_candidate_is_considered(self):
		history = [0, 0, 8, 0, 0, 10, 0, 0, 9, 0, 0, 11]
		result = select_forecast_model(history, 12)
		self.assertIn("tsb_intermittent", result["candidate_scores"])

	def test_confidence_intervals_are_ordered(self):
		result = build_forecast([5, 8, 6, 9, 7, 8, 6, 10], 5, 7)
		for lower, point, upper in zip(
			result["lower_bound"], result["forecast"], result["upper_bound"]
		):
			self.assertLessEqual(lower, point)
			self.assertLessEqual(point, upper)

	def test_forecast_confidence_penalizes_zero_and_stockout_periods(self):
		strong = forecast_confidence(24, 10, 0, 0, 0)
		weak = forecast_confidence(6, 80, 4, 30, 2)
		self.assertGreater(strong["score"], weak["score"])
		self.assertEqual(weak["label"], "low")

	def test_forecast_quantity_is_not_negative_after_returns(self):
		result = build_forecast([4, -8, 3, 0, 5, -1], 3, 7)
		self.assertTrue(all(value >= 0 for value in result["forecast"]))
