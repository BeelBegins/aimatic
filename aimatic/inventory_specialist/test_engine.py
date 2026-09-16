from unittest import TestCase

from aimatic.inventory_specialist.engine import calculate_lead_time_days, calculate_recommendations


class TestInventorySpecialist(TestCase):
	def test_only_usable_selling_supplier_items_are_recommended(self):
		rows = calculate_recommendations([
			{"item_code": "A", "supplier": "Vendor", "stock_qty": 12, "sales_qty": 168, "history_days": 28},
			{"item_code": "B", "supplier": "Vendor", "stock_qty": -1, "sales_qty": 100, "history_days": 28},
			{"item_code": "C", "stock_qty": 0, "sales_qty": 100, "history_days": 28},
		])
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["item_code"], "A")
		self.assertEqual(rows[0]["suggested_qty"], 30)

	def test_lead_time_never_invents_a_value(self):
		self.assertEqual(calculate_lead_time_days([0, 0])["status"], "Learning")
		self.assertEqual(calculate_lead_time_days([2, 4, 8])["lead_time_days"], 4)
