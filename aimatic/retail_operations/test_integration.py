"""Real-schema integration tests. Run only on the explicitly verified retail test site."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import frappe

from aimatic.retail_operations import api, service


class IntegrationTest(unittest.TestCase):
	def setUp(self):
		if frappe.local.site != "siezal":
			self.skipTest("Requires SZL retail test site")
		frappe.set_user("Administrator")
		frappe.db.savepoint("retail_operations_test")
		self.branch = frappe.get_doc("Branch", "S1 - Ghouri Town VIP")
		self.other = "S5 - Sector C"
		self.supplier = frappe.db.get_value("Supplier", {"disabled": 0}, "name")
		self.item = (
			frappe.get_doc(
				dict(
					doctype="Item",
					item_code="TEST-RETAIL-OPERATIONS-ONLY",
					item_name="Test Operations Item",
					item_group=frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
					stock_uom="Pcs",
					is_stock_item=1,
				)
			)
			.insert(ignore_permissions=True)
			.name
		)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="retail_operations_test")

	def user(self, role):
		email = "retail-" + role.lower().replace(" ", "-") + "@example.invalid"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				dict(
					doctype="User",
					email=email,
					first_name="Operations Test",
					enabled=1,
					send_welcome_email=0,
					roles=[{"role": role}],
				)
			).insert(ignore_permissions=True)
		frappe.get_doc(
			dict(
				doctype="User Permission",
				user=email,
				allow="Branch",
				for_value=self.branch.name,
				apply_to_all_doctypes=1,
			)
		).insert(ignore_permissions=True)
		return email

	def test_permissions_and_channel_redaction(self):
		snap = {
			"branch": self.branch.name,
			"cutoff": str(frappe.utils.now_datetime()),
			"business_date": str(frappe.utils.today()),
			"reports": {"Purchase": {"vendors": []}, "Accounts": {"secret": "ACCOUNT-SENTINEL"}},
			"errors": {},
		}
		run = service.write_internal(
			frappe.get_doc(
				dict(
					doctype="Retail Operations Run",
					branch=self.branch.name,
					company=self.branch.company,
					snapshot=service.pack_snapshot(snap),
					cutoff=frappe.utils.now_datetime(),
					status="Complete",
				)
			)
		)
		user = self.user("Purchase Manager")
		frappe.set_user(user)
		allowed = api.report(self.branch.name, "Purchase")
		self.assertNotIn("ACCOUNT-SENTINEL", json.dumps(allowed))
		with self.assertRaises(frappe.PermissionError):
			api.report(self.branch.name, "Accounts")
		with self.assertRaises(frappe.PermissionError):
			api.report(self.other, "Purchase")
		visible = frappe.get_list("Retail Purchase Commitment", pluck="branch")
		self.assertNotIn(self.other, visible)

	def test_recorded_order_is_idempotent_and_branch_scoped(self):
		args = dict(
			branch=self.branch.name,
			supplier=self.supplier,
			items=[{"item_code": self.item, "qty": 12}],
			external_reference="test-external-order",
		)
		first = api.record_order(**args)
		self.assertEqual(first, api.record_order(**args))
		doc = frappe.get_doc("Retail Purchase Commitment", first)
		self.assertEqual(doc.warehouse, self.branch.finished_goods_warehouse)
		doc.warehouse = frappe.db.get_value("Branch", self.other, "finished_goods_warehouse")
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_auto_request_is_native_submitted_and_not_duplicated(self):
		rule = frappe.get_doc(
			dict(
				doctype="Retail Replenishment Rule",
				branch=self.branch.name,
				company=self.branch.company,
				item_code=self.item,
				supplier=self.supplier,
				enabled=1,
				reviewed=1,
				automatic_request=1,
			)
		).insert()
		frappe.db.set_value("Branch", self.branch.name, "custom_operations_auto_requests", 1)
		row = dict(item_code=self.item, stock_uom="Pcs", suggested_qty=5, auto_eligible=True, flags=[])
		with (
			patch(
				"aimatic.retail_operations.service.settings",
				return_value=frappe._dict(enabled=1, automatic_requests=1),
			),
			patch("aimatic.retail_operations.service.data.inventory", return_value={"rows": [row]}),
			patch.object(frappe.db, "commit"),
		):
			first = service.generate_requests(self.branch.name)
			second = service.generate_requests(self.branch.name)
		self.assertEqual(len(first), 1)
		self.assertEqual(second, [])
		mr = frappe.get_doc("Material Request", first[0])
		self.assertEqual(mr.docstatus, 1)
		self.assertEqual(mr.branch, self.branch.name)
		self.assertEqual(mr.items[0].warehouse, self.branch.finished_goods_warehouse)
		self.assertEqual(mr.custom_operations_vendor, self.supplier)

	def test_transfer_request_preserves_source_and_destination(self):
		source = frappe.db.get_value("Branch", self.other, "finished_goods_warehouse")
		frappe.get_doc(
			dict(
				doctype="Retail Replenishment Rule",
				branch=self.branch.name,
				company=self.branch.company,
				item_code=self.item,
				source_warehouse=source,
				enabled=1,
				reviewed=1,
				automatic_request=1,
			)
		).insert()
		frappe.db.set_value("Branch", self.branch.name, "custom_operations_auto_requests", 1)
		row = dict(item_code=self.item, stock_uom="Pcs", suggested_qty=3, auto_eligible=True, flags=[])
		with (
			patch(
				"aimatic.retail_operations.service.settings",
				return_value=frappe._dict(enabled=1, automatic_requests=1),
			),
			patch("aimatic.retail_operations.service.data.inventory", return_value={"rows": [row]}),
			patch.object(frappe.db, "commit"),
		):
			result = service.generate_requests(self.branch.name)
		self.assertEqual(len(result), 1)
		mr = frappe.get_doc("Material Request", result[0])
		self.assertEqual(mr.material_request_type, "Material Transfer")
		self.assertEqual(mr.items[0].from_warehouse, source)
		self.assertEqual(mr.items[0].warehouse, self.branch.finished_goods_warehouse)

	def test_partial_receipt_matching_accumulates_without_double_counting(self):
		from aimatic.retail_operations.commitments import received

		doc = frappe._dict(
			receipts=[
				frappe._dict(item_code=self.item, purchase_receipt_item="r1"),
				frappe._dict(item_code=self.item, purchase_receipt_item="r2"),
			]
		)
		line = frappe._dict(item_code=self.item, purchase_order_item=None, purchase_receipt_item=None)
		with patch(
			"aimatic.retail_operations.commitments.receipt_qty",
			side_effect=lambda key: {"r1": 4, "r2": 6}[key],
		):
			self.assertEqual(received(doc, line), 10)
		# A repeated receipt key cannot inflate the calculation even before validation.
		doc.receipts.append(frappe._dict(item_code=self.item, purchase_receipt_item="r2"))
		with patch(
			"aimatic.retail_operations.commitments.receipt_qty",
			side_effect=lambda key: {"r1": 4, "r2": 6}[key],
		):
			self.assertEqual(received(doc, line), 10)

	def test_issue_baseline_deduplication_and_resolution(self):
		run = service.write_internal(
			frappe.get_doc(
				dict(
					doctype="Retail Operations Run",
					branch=self.branch.name,
					company=self.branch.company,
					snapshot=service.pack_snapshot({}),
					cutoff=frappe.utils.now_datetime(),
					status="Complete",
				)
			)
		)
		row = dict(
			item_code=self.item,
			item_name="Test item",
			status="Low cover",
			available_qty=2,
			daily_demand=3,
			flags=[],
		)
		snap = dict(
			branch=self.branch.name, company=self.branch.company, reports={"Inventory": {"rows": [row]}}
		)
		service.sync_issues(snap, run.name)
		key = service.fingerprint([self.branch.name, "Inventory", self.item])
		first = frappe.get_doc(
			"Retail Operations Issue",
			frappe.db.get_value("Retail Operations Issue", {"issue_key": key}, "name"),
		)
		first.status = "Acknowledged"
		first.reason = "Buyer checking"
		first.save()
		service.sync_issues(snap, run.name)
		first.reload()
		self.assertEqual(first.status, "Acknowledged")
		self.assertEqual(frappe.db.count("Retail Operations Issue", {"issue_key": key}), 1)
		snap["reports"]["Inventory"]["rows"] = []
		service.sync_issues(snap, run.name)
		first.reload()
		self.assertEqual(first.status, "Resolved")

	def test_shared_destinations_copy_without_returning_secrets(self):
		address = "https://discord.com/api/webhooks/123/example-placeholder"
		source = frappe.get_doc("Branch", self.branch.name)
		source.custom_discord_inventory = address
		source.save()
		result = api.copy_branch_channels(self.branch.name, [self.other])
		self.assertEqual(result[0]["channels_copied"], 1)
		self.assertNotIn(address, json.dumps(result))
		self.assertEqual(
			frappe.get_doc("Branch", self.other).get_password("custom_discord_inventory"), address
		)
		self.assertEqual(api.copy_branch_channels(self.branch.name, [self.other])[0]["channels_copied"], 0)

	def test_same_day_dates_are_not_a_delivery_estimate(self):
		from aimatic.retail_operations.calculations import cadence

		result = cadence([dict(date="2026-10-01", qty=2, purchase_order="P1")])
		self.assertIsNone(result["lead_time_days"])

	def test_snapshot_compression_preserves_evidence(self):
		value = {"branch": self.branch.name, "rows": [{"item": "A", "qty": 1}] * 1000}
		packed = service.pack_snapshot(value)
		self.assertLess(len(packed), len(json.dumps(value)))
		self.assertEqual(service.unpack_snapshot(packed), value)


def run():
	if frappe.local.site != "siezal":
		raise RuntimeError("Integration tests must not run on production")
	from aimatic.retail_operations import test_calculations, test_delivery

	suite = unittest.TestSuite()
	loader = unittest.TestLoader()
	for module in (test_calculations, test_delivery):
		suite.addTests(loader.loadTestsFromModule(module))
	suite.addTests(loader.loadTestsFromTestCase(IntegrationTest))
	result = unittest.TextTestRunner(verbosity=2).run(suite)
	frappe.db.rollback()
	if not result.wasSuccessful():
		raise RuntimeError("Retail operations tests failed")
	return {"tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors)}
