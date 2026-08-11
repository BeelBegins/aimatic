import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aimatic.purchase_supplier_invoice import (
	prefill_purchase_invoice_supplier_invoice,
	prefill_purchase_receipt_supplier_invoice,
	resolve_supplier_invoice_no_for_invoice,
	resolve_supplier_invoice_no_for_receipt,
)


class TestSupplierInvoicePrefill(unittest.TestCase):
	@patch("aimatic.purchase_supplier_invoice.frappe.db.get_value")
	def test_receipt_resolves_from_purchase_order(self, get_value):
		get_value.return_value = "INV-100"
		doc = SimpleNamespace(
			items=[SimpleNamespace(purchase_order="PO-1")],
		)

		self.assertEqual(resolve_supplier_invoice_no_for_receipt(doc), "INV-100")
		get_value.assert_called_with(
			"Purchase Order", "PO-1", "custom_supplier_invoice_no"
		)

	@patch("aimatic.purchase_supplier_invoice.frappe.db.get_value")
	def test_invoice_prefers_receipt_over_order(self, get_value):
		def _side_effect(doctype, name, field):
			if doctype == "Purchase Receipt":
				return "FROM-PR"
			return "FROM-PO"

		get_value.side_effect = _side_effect
		doc = SimpleNamespace(
			items=[
				SimpleNamespace(purchase_receipt="PR-1", purchase_order="PO-1"),
			],
		)

		self.assertEqual(resolve_supplier_invoice_no_for_invoice(doc), "FROM-PR")

	@patch("aimatic.purchase_supplier_invoice.frappe.db.get_value")
	def test_invoice_falls_back_to_purchase_order(self, get_value):
		def _side_effect(doctype, name, field):
			if doctype == "Purchase Receipt":
				return None
			return "FROM-PO"

		get_value.side_effect = _side_effect
		doc = SimpleNamespace(
			items=[
				SimpleNamespace(purchase_receipt="PR-1", purchase_order="PO-1"),
			],
		)

		self.assertEqual(resolve_supplier_invoice_no_for_invoice(doc), "FROM-PO")

	@patch("aimatic.purchase_supplier_invoice.resolve_supplier_invoice_no_for_receipt")
	def test_receipt_hook_skips_when_already_set(self, resolve):
		doc = SimpleNamespace(docstatus=0, custom_supplier_invoice_no="KEEP", items=[])
		prefill_purchase_receipt_supplier_invoice(doc)
		resolve.assert_not_called()
		self.assertEqual(doc.custom_supplier_invoice_no, "KEEP")

	@patch("aimatic.purchase_supplier_invoice.resolve_supplier_invoice_no_for_receipt")
	def test_receipt_hook_fills_blank(self, resolve):
		resolve.return_value = "INV-200"
		doc = SimpleNamespace(docstatus=0, custom_supplier_invoice_no="", items=[])
		prefill_purchase_receipt_supplier_invoice(doc)
		self.assertEqual(doc.custom_supplier_invoice_no, "INV-200")

	@patch("aimatic.purchase_supplier_invoice.resolve_supplier_invoice_no_for_invoice")
	def test_invoice_hook_skips_submitted(self, resolve):
		doc = SimpleNamespace(docstatus=1, bill_no="", items=[])
		prefill_purchase_invoice_supplier_invoice(doc)
		resolve.assert_not_called()
		self.assertEqual(doc.bill_no, "")

	@patch("aimatic.purchase_supplier_invoice.resolve_supplier_invoice_no_for_invoice")
	def test_invoice_hook_fills_blank_bill_no(self, resolve):
		resolve.return_value = "INV-300"
		doc = SimpleNamespace(docstatus=0, bill_no=None, items=[])
		prefill_purchase_invoice_supplier_invoice(doc)
		self.assertEqual(doc.bill_no, "INV-300")
