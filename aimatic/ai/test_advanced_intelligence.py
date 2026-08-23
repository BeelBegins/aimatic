from unittest import TestCase

from aimatic.ai.anomaly_detection import detect_series_anomalies
from aimatic.ai.basket_analysis import calculate_basket_pairs
from aimatic.ai.customer_intelligence import score_rfm
from aimatic.ai.promotion_analysis import calculate_promotion_effect


class TestPromotionAnalysis(TestCase):
	def test_incremental_sales_and_roi_are_deterministic(self):
		result = calculate_promotion_effect(
			{"days": 10, "quantity": 100, "revenue": 1000, "margin": 300, "category_other_revenue": 500},
			{
				"quantity": 150,
				"revenue": 1350,
				"margin": 360,
				"gross_before_discount": 1500,
				"category_other_revenue": 400,
			},
			{"days": 5, "quantity": 40},
			10,
		)
		self.assertEqual(result["incremental_units"], 50)
		self.assertEqual(result["incremental_revenue"], 350)
		self.assertEqual(result["promotion_roi_pct"], 40)
		self.assertEqual(result["cannibalization"], 100)


class TestCustomerRfm(TestCase):
	def test_high_recent_frequent_customer_is_champion(self):
		rows = score_rfm(
			[
				{
					"customer": "Best",
					"last_purchase_date": "2026-07-25",
					"frequency": 20,
					"monetary_value": 5000,
				},
				{
					"customer": "Mid",
					"last_purchase_date": "2026-06-01",
					"frequency": 5,
					"monetary_value": 1000,
				},
				{
					"customer": "Dormant",
					"last_purchase_date": "2025-01-01",
					"frequency": 1,
					"monetary_value": 50,
				},
			],
			"2026-07-26",
		)
		self.assertEqual(rows[0]["customer_segment"], "Champions")
		self.assertEqual(rows[-1]["churn_risk"], "high")


class TestBasketAnalysis(TestCase):
	def test_support_confidence_and_lift(self):
		transactions = {
			"1": {"A", "B"},
			"2": {"A", "B"},
			"3": {"A"},
			"4": {"C"},
		}
		rows, quality = calculate_basket_pairs(
			transactions, minimum_transactions=4, minimum_support=0.1, minimum_confidence=0.1
		)
		self.assertFalse(quality["insufficient_data"])
		self.assertEqual(rows[0]["support"], 50)
		self.assertGreater(rows[0]["lift"], 1)

	def test_small_sample_is_rejected(self):
		rows, quality = calculate_basket_pairs({"1": {"A", "B"}}, minimum_transactions=20)
		self.assertEqual(rows, [])
		self.assertTrue(quality["insufficient_data"])


class TestAnomalyDetection(TestCase):
	def test_outlier_has_expected_range_and_severity(self):
		rows = [
			{"period": f"2026-07-{day:02d}", "branch": "A", "net_sales": 100 + day % 2}
			for day in range(1, 10)
		]
		rows.append({"period": "2026-07-10", "branch": "A", "net_sales": 500})
		anomalies = detect_series_anomalies(rows, "net_sales", minimum_periods=7, z_threshold=2.5)
		self.assertEqual(len(anomalies), 1)
		self.assertEqual(anomalies[0]["actual_value"], 500)
		self.assertIn("expected_low", anomalies[0])
		self.assertEqual(anomalies[0]["severity"], "critical")
