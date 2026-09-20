import unittest
from unittest.mock import patch

from aimatic.shelf_pricing.utils import get_selling_item_price_rate


class TestGetSellingItemPriceRate(unittest.TestCase):
	@patch("aimatic.shelf_pricing.utils.frappe")
	def test_matches_requested_uom(self, frappe):
		frappe.db.get_value.side_effect = [250.0]

		rate = get_selling_item_price_rate("ITEM-1", "Branch Selling", uom="Pack")

		self.assertEqual(rate, 250.0)
		frappe.db.get_value.assert_called_once_with(
			"Item Price",
			{
				"item_code": "ITEM-1",
				"price_list": "Branch Selling",
				"selling": 1,
				"uom": "Pack",
			},
			"price_list_rate",
		)

	@patch("aimatic.shelf_pricing.utils.frappe")
	def test_falls_back_to_stock_uom_when_uom_omitted(self, frappe):
		frappe.db.get_value.side_effect = ["Pcs", 99.0]

		rate = get_selling_item_price_rate("ITEM-1", "Branch Selling")

		self.assertEqual(rate, 99.0)
		self.assertEqual(
			frappe.db.get_value.call_args_list[0].args,
			("Item", "ITEM-1", "stock_uom"),
		)
		self.assertEqual(
			frappe.db.get_value.call_args_list[1].args[1]["uom"],
			"Pcs",
		)

	@patch("aimatic.shelf_pricing.utils.frappe")
	def test_does_not_return_other_uom_price(self, frappe):
		# Exact UOM miss → 0 (no silent Pack→Pcs fallback)
		frappe.db.get_value.return_value = None

		rate = get_selling_item_price_rate("ITEM-1", "Branch Selling", uom="Pack")

		self.assertEqual(rate, 0.0)

	@patch("aimatic.shelf_pricing.utils.frappe")
	def test_missing_inputs(self, frappe):
		self.assertEqual(get_selling_item_price_rate("", "List"), 0.0)
		self.assertEqual(get_selling_item_price_rate("ITEM", ""), 0.0)
		frappe.db.get_value.assert_not_called()


if __name__ == "__main__":
	unittest.main()


class TestUpsertItemPriceUom(unittest.TestCase):
	"""Regression: a Pcs shelf price must never land on a Box row (szl, Sep 2026)."""

	@patch("aimatic.shelf_pricing.utils.log_price_update")
	@patch("aimatic.shelf_pricing.utils.assert_uom_price_sane")
	@patch("aimatic.shelf_pricing.utils.frappe")
	def test_lookup_is_scoped_to_row_uom(self, frappe, _guard, _log):
		from aimatic.shelf_pricing.utils import upsert_item_price

		frappe.db.get_value.side_effect = ["PCS-ROW", frappe._dict(price_list_rate=90, custom_mrp=100)]

		upsert_item_price("ITEM-1", "S1 List", "MAT-PRE-1", rate=99, uom="Pcs")

		row_lookup = frappe.db.get_value.call_args_list[0]
		self.assertEqual(row_lookup.args[1]["uom"], "Pcs")
		frappe.db.set_value.assert_called_once_with("Item Price", "PCS-ROW", {"price_list_rate": 99})

	@patch("aimatic.shelf_pricing.utils.log_price_update")
	@patch("aimatic.shelf_pricing.utils.assert_uom_price_sane")
	@patch("aimatic.shelf_pricing.utils.frappe")
	def test_guard_runs_before_a_rate_write(self, frappe, guard, _log):
		from aimatic.shelf_pricing.utils import upsert_item_price

		frappe.db.get_value.side_effect = ["BOX-ROW", frappe._dict(price_list_rate=2660, custom_mrp=0)]

		upsert_item_price("ITEM-1", "S1 List", "MAT-PRE-1", rate=99, uom="Box")

		guard.assert_called_once_with("ITEM-1", "Box", "S1 List", 99)
