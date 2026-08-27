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
