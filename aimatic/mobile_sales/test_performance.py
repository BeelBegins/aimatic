from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aimatic.mobile_sales import api


class TestMobileSalesCataloguePerformance(FrappeTestCase):
	@patch("aimatic.mobile_sales.api.frappe.get_all")
	def test_stock_prices_and_valid_uoms_are_loaded_in_batched_queries(self, get_all):
		get_all.side_effect = [
			[frappe._dict(item_code="ITEM-1", actual_qty=8, reserved_qty=2)],
			[frappe._dict(name="ITEM-1", stock_uom="Nos", sales_uom="Box")],
			[
				frappe._dict(parent="ITEM-1", uom="Box", conversion_factor=12),
				frappe._dict(parent="ITEM-1", uom="Broken", conversion_factor=0),
			],
			[
				frappe._dict(item_code="ITEM-1", uom="Nos", price_list_rate=125),
				frappe._dict(item_code="ITEM-1", uom="Box", price_list_rate=1400),
			],
		]
		stock, rates, uoms = api._item_stock_and_rates(["ITEM-1", "ITEM-2"], "Stores - TC", "Standard Selling")

		self.assertEqual(get_all.call_count, 4)
		self.assertEqual(stock["ITEM-1"].actual_qty, 8)
		self.assertEqual(rates["ITEM-1"], 125)
		self.assertNotIn("ITEM-2", rates)
		self.assertEqual(uoms["ITEM-1"]["default_uom"], "Box")
		self.assertEqual(
			{row["uom"] for row in uoms["ITEM-1"]["uoms"]},
			{"Nos", "Box"},
		)
