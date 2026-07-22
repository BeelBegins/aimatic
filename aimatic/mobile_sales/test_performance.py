from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aimatic.mobile_sales import api, events


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


class TestMobileSalesCustomerHistory(FrappeTestCase):
	@patch("aimatic.mobile_sales.api.frappe.get_all")
	@patch("aimatic.mobile_sales.api.frappe.get_list")
	@patch("aimatic.mobile_sales.api.nowdate", return_value="2026-07-22")
	def test_history_uses_stock_uom_and_distinct_submitted_orders(self, _today, get_list, get_all):
		get_list.return_value = [
			frappe._dict(name="SO-2", transaction_date="2026-07-20"),
			frappe._dict(name="SO-1", transaction_date="2026-06-20"),
		]
		get_all.return_value = [
			frappe._dict(parent="SO-2", item_code="ITEM-1", stock_qty=24, stock_uom="Nos"),
			frappe._dict(parent="SO-1", item_code="ITEM-1", stock_qty=12, stock_uom="Nos"),
		]

		result = api._customer_item_history_by_item("CUST-1", ["ITEM-1"], months=3)["ITEM-1"]

		self.assertEqual(result["last_stock_qty"], 24)
		self.assertEqual(result["avg_stock_qty"], 18)
		self.assertEqual(result["frequency_per_month"], 0.67)
		self.assertEqual(result["trend"], "up")
		self.assertEqual(result["last_order_date"], "2026-07-20")

	@patch("aimatic.mobile_sales.api.frappe.get_all")
	@patch("aimatic.mobile_sales.api.frappe.get_list")
	def test_single_order_does_not_claim_a_usual_quantity(self, get_list, get_all):
		get_list.return_value = [frappe._dict(name="SO-1", transaction_date="2026-07-20")]
		get_all.return_value = [frappe._dict(parent="SO-1", item_code="ITEM-1", stock_qty=12, stock_uom="Nos")]
		self.assertEqual(api._customer_item_history_by_item("CUST-1", ["ITEM-1"]), {})


class TestMobileSalesAssortments(FrappeTestCase):
	@patch("aimatic.mobile_sales.api._expanded_item_groups", return_value=["Tools", "Power Tools"])
	@patch("aimatic.mobile_sales.api.frappe.get_all")
	def test_rules_are_deduplicated_and_marked_configured(self, get_all, _expanded):
		get_all.return_value = [
			frappe._dict(item="ITEM-1", item_group=None),
			frappe._dict(item="ITEM-1", item_group=None),
			frappe._dict(item=None, item_group="Tools"),
		]
		self.assertEqual(api._customer_assortment_rules("CUST-1"), {
			"configured": True,
			"items": ["ITEM-1"],
			"item_groups": ["Tools"],
			"expanded_item_groups": ["Tools", "Power Tools"],
		})

	@patch("aimatic.mobile_sales.api.frappe.get_list")
	@patch("aimatic.mobile_sales.api._expanded_item_groups", return_value=["Tools", "Power Tools"])
	def test_item_codes_resolve_explicit_items_and_group_descendants(self, _groups, get_list):
		get_list.return_value = ["ITEM-1", "ITEM-2"]
		rules = {"configured": True, "items": ["ITEM-1"], "item_groups": ["Tools"], "expanded_item_groups": ["Tools", "Power Tools"]}
		self.assertEqual(api._assortment_item_codes(rules), ["ITEM-1", "ITEM-2"])
		self.assertEqual(get_list.call_args.kwargs["or_filters"], {
			"name": ["in", ["ITEM-1"]],
			"item_group": ["in", ["Tools", "Power Tools"]],
		})

	def test_unconfigured_customer_keeps_full_catalogue(self):
		self.assertIsNone(api._assortment_item_codes({"configured": False, "items": [], "item_groups": []}))


class TestMobileSalesDeliveryRules(FrappeTestCase):
	@patch("aimatic.mobile_sales.api.frappe.get_all")
	def test_locations_only_expose_addresses_linked_to_customer(self, get_all):
		get_all.side_effect = [
			[
				frappe._dict(
					name="LOC-1",
					location_name="Main Warehouse",
					address="ADDR-1",
					is_default=1,
					instructions="Use gate two",
					minimum_order_value=5000,
					monday=1,
					tuesday=0,
					wednesday=1,
					thursday=0,
					friday=0,
					saturday=0,
					sunday=0,
				),
				frappe._dict(name="LOC-2", location_name="Unlinked", address="ADDR-2", is_default=0),
			],
			["ADDR-1"],
			[
				frappe._dict(
					name="ADDR-1",
					address_title="Customer",
					address_line1="Plot 10",
					address_line2=None,
					city="Karachi",
					county=None,
					state="Sindh",
					pincode="74000",
					country="Pakistan",
					phone="021-0000000",
					email_id=None,
				),
			],
		]

		locations = api._customer_delivery_locations("CUST-1")

		self.assertEqual(len(locations), 1)
		self.assertEqual(locations[0]["name"], "LOC-1")
		self.assertEqual(locations[0]["delivery_days"], ["Monday", "Wednesday"])
		self.assertEqual(locations[0]["address"], "Plot 10, Karachi, Sindh, 74000, Pakistan")
		self.assertEqual(locations[0]["minimum_order_value"], 5000)

	@patch("aimatic.mobile_sales.api._customer_delivery_locations")
	def test_default_location_is_selected_server_side(self, locations):
		locations.return_value = [
			{"name": "LOC-1", "is_default": False},
			{"name": "LOC-2", "is_default": True},
		]
		self.assertEqual(api._delivery_location_rule("CUST-1")["name"], "LOC-2")
		self.assertEqual(api._delivery_location_rule("CUST-1", "LOC-1")["name"], "LOC-1")

	def test_unavailable_delivery_day_is_rejected(self):
		rule = {"location_name": "Main Warehouse", "delivery_days": ["Monday", "Wednesday"]}
		api._validate_delivery_date(rule, "2026-07-22")
		with self.assertRaises(frappe.ValidationError):
			api._validate_delivery_date(rule, "2026-07-23")


class TestMobileSalesDiscountApprovals(FrappeTestCase):
	@patch("aimatic.mobile_sales.api._discount_authority", return_value=5)
	def test_discount_context_marks_only_excess_as_pending(self, _authority):
		self.assertFalse(api._discount_context(5)["discount_requires_approval"])
		self.assertTrue(api._discount_context(5.01)["discount_requires_approval"])
		self.assertEqual(api._discount_context(4)["discount_authority_percent"], 5)

	@patch("aimatic.mobile_sales.api._discount_authority", return_value=0)
	def test_discount_percent_is_bounded(self, _authority):
		with self.assertRaises(frappe.ValidationError):
			api._discount_context(-1)
		with self.assertRaises(frappe.ValidationError):
			api._discount_context(101)

	@patch("aimatic.mobile_sales.events.frappe.db.get_value")
	def test_pending_mobile_discount_blocks_sales_order_submission(self, get_value):
		get_value.return_value = frappe._dict(status="Pending", requested_percent=12)
		doc = frappe._dict(name="SO-1", additional_discount_percentage=12)
		with self.assertRaises(frappe.ValidationError):
			events.before_submit_sales_order(doc)

	@patch("aimatic.mobile_sales.events.frappe.db.get_value")
	def test_approved_discount_cannot_be_increased_after_decision(self, get_value):
		get_value.return_value = frappe._dict(status="Approved", requested_percent=10)
		with self.assertRaises(frappe.ValidationError):
			events.before_submit_sales_order(frappe._dict(name="SO-1", additional_discount_percentage=11))
		events.before_submit_sales_order(frappe._dict(name="SO-1", additional_discount_percentage=10))


class TestMobileSalesPhaseThree(FrappeTestCase):
	def test_order_proof_accepts_png_and_rejects_disguised_content(self):
		valid = "data:image/png;base64,iVBORw0KGgo="
		self.assertTrue(api._signature_bytes(valid).startswith(b"\x89PNG"))
		with self.assertRaises(frappe.ValidationError):
			api._signature_bytes("data:image/png;base64,dGV4dA==")

	def test_order_proof_coordinates_are_bounded(self):
		self.assertEqual(api._proof_coordinates("33.6844", "73.0479", "8.5"), (33.6844, 73.0479, 8.5))
		with self.assertRaises(frappe.ValidationError):
			api._proof_coordinates(91, 73, 1)

	def test_route_optimizer_uses_nearest_next_visit(self):
		rows = [
			frappe._dict(name="far", planned_latitude=33.9, planned_longitude=73.2),
			frappe._dict(name="near", planned_latitude=33.69, planned_longitude=73.05),
		]
		self.assertEqual([row.name for row in api._optimize_visit_route(rows, 33.6844, 73.0479)], ["near", "far"])

	@patch("aimatic.mobile_sales.api.frappe.db.sql")
	def test_manager_metrics_only_count_submitted_company_orders(self, sql):
		sql.return_value = [frappe._dict(orders=4, revenue=10000)]
		result = api._manager_order_metrics("Test Company", "2026-07-01", "2026-07-31", "Stores - TC")
		self.assertEqual(result, {"orders": 4, "revenue": 10000, "average_order": 2500})
		query = sql.call_args.args[0]
		self.assertIn("docstatus = 1", query)
		self.assertIn("company = %(company)s", query)
		self.assertEqual(sql.call_args.args[1]["warehouse"], "Stores - TC")
