from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aimatic.mobile_sales import api


class TestMobileSalesCataloguePerformance(FrappeTestCase):
	@patch("aimatic.mobile_sales.api.frappe.get_all")
	def test_stock_and_prices_are_loaded_in_two_batched_queries(self, get_all):
		get_all.side_effect = [
			[frappe._dict(item_code="ITEM-1", actual_qty=8, reserved_qty=2)],
			[frappe._dict(item_code="ITEM-1", price_list_rate=125)],
		]
		stock, rates = api._item_stock_and_rates(["ITEM-1", "ITEM-2"], "Stores - TC", "Standard Selling")

		self.assertEqual(get_all.call_count, 2)
		self.assertEqual(stock["ITEM-1"].actual_qty, 8)
		self.assertEqual(rates["ITEM-1"], 125)
		self.assertNotIn("ITEM-2", rates)
