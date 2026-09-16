import unittest
from unittest.mock import patch

from aimatic.shelf_pricing.engine import _gm_percent, _sale_from_gm


class TestSellingPriceUpdateHelpers(unittest.TestCase):
	def test_gm_percent_from_sale_and_cost(self):
		self.assertEqual(_gm_percent(100, 80), 20.0)
		self.assertEqual(_gm_percent(0, 80), 0.0)

	def test_sale_from_gm_rounds_up_to_nearest_five(self):
		self.assertEqual(_sale_from_gm(80, 20), 100)
		self.assertEqual(_sale_from_gm(82, 20), 105)
		self.assertEqual(_sale_from_gm(100, 10), 115)
		self.assertIsNone(_sale_from_gm(80, 0))
		self.assertIsNone(_sale_from_gm(80, 100))


class TestFoodpandaFloorValidation(unittest.TestCase):
	@patch("aimatic.shelf_pricing.engine.upsert_item_price")
	@patch("aimatic.shelf_pricing.engine.get_selling_item_price_rate", return_value=120.0)
	@patch("aimatic.shelf_pricing.engine._resolve_selling_price_list_for_branch", return_value="Selling")
	@patch("aimatic.shelf_pricing.engine._resolve_price_list", return_value="FP List")
	@patch("aimatic.shelf_pricing.engine._require_price_update_permission")
	@patch("aimatic.shelf_pricing.engine.frappe")
	def test_rejects_foodpanda_below_store_selling(
		self, frappe, _perm, _plist, _selling, _rate, _upsert
	):
		from aimatic.shelf_pricing.engine import apply_selling_price_updates

		frappe.db.get_value.return_value = 90
		frappe.throw.side_effect = Exception("blocked")

		with self.assertRaises(Exception):
			apply_selling_price_updates(
				"Foodpanda",
				"Branch A",
				[{"item_code": "ITEM-1", "uom": "Pcs", "new_selling_price": 100, "mrp": 90}],
			)

		_upsert.assert_not_called()

	@patch("aimatic.shelf_pricing.engine.upsert_item_price")
	@patch("aimatic.shelf_pricing.engine.get_selling_item_price_rate", return_value=100.0)
	@patch("aimatic.shelf_pricing.engine._resolve_selling_price_list_for_branch", return_value="Selling")
	@patch("aimatic.shelf_pricing.engine._resolve_price_list", return_value="FP List")
	@patch("aimatic.shelf_pricing.engine._require_price_update_permission")
	@patch("aimatic.shelf_pricing.engine.frappe")
	def test_accepts_foodpanda_at_or_above_floors(
		self, frappe, _perm, _plist, _selling, _rate, upsert
	):
		from aimatic.shelf_pricing.engine import apply_selling_price_updates

		frappe.db.get_value.return_value = 110
		result = apply_selling_price_updates(
			"Foodpanda",
			"Branch A",
			[{"item_code": "ITEM-1", "uom": "Pcs", "new_selling_price": 120, "mrp": 110}],
		)
		self.assertEqual(result["updated"], 1)
		upsert.assert_called_once()
