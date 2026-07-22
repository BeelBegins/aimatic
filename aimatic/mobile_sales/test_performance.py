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


class TestMobileSalesReorderFeed(FrappeTestCase):
	@patch("aimatic.mobile_sales.api._require_sales_user")
	@patch("aimatic.mobile_sales.api.frappe.get_doc")
	@patch("aimatic.mobile_sales.api._submitted_order_rows")
	def test_recent_candidates_are_distinct_and_permission_checked(self, rows, get_doc, _require):
		rows.return_value = [
			frappe._dict(name="SO-1", customer="CUST-1"),
			frappe._dict(name="SO-2", customer="CUST-1"),
			frappe._dict(name="SO-3", customer="CUST-2"),
		]
		docs = {
			"SO-1": frappe._dict(name="SO-1", customer="CUST-1", customer_name="One", transaction_date="2026-07-20", currency="PKR", grand_total=1200, items=[frappe._dict(item_code="ITEM-1", item_name="Item One", uom="Box", qty=2)]),
			"SO-3": frappe._dict(name="SO-3", customer="CUST-2", customer_name="Two", transaction_date="2026-07-19", currency="PKR", grand_total=900, items=[frappe._dict(item_code="ITEM-2", item_name="Item Two", uom="Nos", qty=3)]),
		}
		for doc in docs.values():
			doc.check_permission = lambda permission: self.assertEqual(permission, "read")
		get_doc.side_effect = lambda _doctype, name: docs[name]

		result = api.get_recent_reorder_candidates(limit=3)

		self.assertEqual([row["customer"] for row in result["orders"]], ["CUST-1", "CUST-2"])
		self.assertEqual(result["orders"][0]["items"][0], {"item_code": "ITEM-1", "item_name": "Item One", "uom": "Box", "qty": 2.0})
		rows.assert_called_once_with(limit=9)

	@patch("aimatic.mobile_sales.api._require_sales_user")
	@patch("aimatic.mobile_sales.api._customer_doc")
	@patch("aimatic.mobile_sales.api._submitted_order_rows", return_value=[])
	def test_customer_last_order_returns_null_without_history(self, rows, customer_doc, _require):
		customer_doc.return_value = frappe._dict(name="CUST-NEW")
		self.assertEqual(api.get_customer_last_order("CUST-NEW"), {"order": None})
		rows.assert_called_once_with(customer="CUST-NEW", limit=1)
