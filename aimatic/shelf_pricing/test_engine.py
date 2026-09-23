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


class TestStockTransferTargetBranch(unittest.TestCase):
	def _doc(self, *warehouses):
		from types import SimpleNamespace

		return SimpleNamespace(
			name="MAT-STE-1",
			branch="Sender",
			items=[SimpleNamespace(t_warehouse=w) for w in warehouses],
		)

	@patch("aimatic.shelf_pricing.engine.frappe")
	def test_price_update_branch_is_receiving_branch_not_stock_entry_branch(self, frappe):
		from aimatic.shelf_pricing.engine import SOURCE_STOCK_TRANSFER, _resolve_update_branch

		frappe.db.get_value.return_value = "Receiver"
		doc = self._doc("Receiver WH")
		self.assertEqual(_resolve_update_branch(SOURCE_STOCK_TRANSFER, doc, "Sender"), "Receiver")

	@patch("aimatic.shelf_pricing.engine.frappe")
	def test_purchase_receipt_keeps_requested_branch(self, frappe):
		from aimatic.shelf_pricing.engine import SOURCE_PURCHASE_RECEIPT, _resolve_update_branch

		self.assertEqual(_resolve_update_branch(SOURCE_PURCHASE_RECEIPT, object(), "Branch A"), "Branch A")

	@patch("aimatic.shelf_pricing.engine.frappe")
	def test_multi_branch_transfer_is_rejected(self, frappe):
		from aimatic.shelf_pricing.engine import _stock_transfer_target_branch

		frappe.db.get_value.side_effect = ["B1", "B2"]
		frappe.throw.side_effect = Exception("blocked")
		with self.assertRaises(Exception):
			_stock_transfer_target_branch(self._doc("W1", "W2"))
