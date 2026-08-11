import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from aimatic.purchase_principal import (
	get_allowed_principals,
	prefill_purchase_invoice_principal,
	prefill_purchase_receipt_principal,
	resolve_principal_for_invoice,
	resolve_principal_for_receipt,
	validate_purchase_principal,
)


class TestPurchasePrincipal(unittest.TestCase):
	@patch("aimatic.purchase_principal.frappe.get_all")
	def test_get_allowed_principals(self, get_all):
		get_all.return_value = [
			SimpleNamespace(principal="UNILEVER"),
			SimpleNamespace(principal="DETTOL"),
			SimpleNamespace(principal=""),
		]
		self.assertEqual(get_allowed_principals("SUP-1"), ["UNILEVER", "DETTOL"])
		get_all.assert_called_once()

	@patch("aimatic.purchase_principal.get_allowed_principals", return_value=["UNILEVER", "DETTOL"])
	def test_validate_requires_principal_when_allowed(self, _allowed):
		doc = SimpleNamespace(supplier="SUP-1", custom_principal="")
		with self.assertRaises(frappe.ValidationError):
			validate_purchase_principal(doc)

	@patch("aimatic.purchase_principal.get_allowed_principals", return_value=["UNILEVER", "DETTOL"])
	def test_validate_rejects_foreign_principal(self, _allowed):
		doc = SimpleNamespace(supplier="SUP-1", custom_principal="RECKITT")
		with self.assertRaises(frappe.ValidationError):
			validate_purchase_principal(doc)

	@patch("aimatic.purchase_principal.get_allowed_principals", return_value=["UNILEVER", "DETTOL"])
	def test_validate_accepts_allowed_principal(self, _allowed):
		doc = SimpleNamespace(supplier="SUP-1", custom_principal="UNILEVER")
		validate_purchase_principal(doc)

	@patch("aimatic.purchase_principal.get_allowed_principals", return_value=[])
	def test_validate_rejects_principal_when_supplier_has_none(self, _allowed):
		doc = SimpleNamespace(supplier="SUP-2", custom_principal="UNILEVER")
		with self.assertRaises(frappe.ValidationError):
			validate_purchase_principal(doc)

	@patch("aimatic.purchase_principal.get_allowed_principals", return_value=[])
	def test_validate_allows_blank_when_supplier_has_none(self, _allowed):
		doc = SimpleNamespace(supplier="SUP-2", custom_principal="")
		validate_purchase_principal(doc)

	@patch("aimatic.purchase_principal.frappe.db.get_value", return_value="UNILEVER")
	def test_receipt_resolves_from_po(self, get_value):
		doc = SimpleNamespace(items=[SimpleNamespace(purchase_order="PO-1")])
		self.assertEqual(resolve_principal_for_receipt(doc), "UNILEVER")
		get_value.assert_called_with("Purchase Order", "PO-1", "custom_principal")

	@patch("aimatic.purchase_principal.frappe.db.get_value")
	def test_invoice_prefers_pr_over_po(self, get_value):
		def _side_effect(doctype, name, field):
			if doctype == "Purchase Receipt":
				return "FROM-PR"
			return "FROM-PO"

		get_value.side_effect = _side_effect
		doc = SimpleNamespace(
			items=[SimpleNamespace(purchase_receipt="PR-1", purchase_order="PO-1")]
		)
		self.assertEqual(resolve_principal_for_invoice(doc), "FROM-PR")

	@patch("aimatic.purchase_principal.resolve_principal_for_receipt", return_value="UNILEVER")
	def test_receipt_prefill_fills_blank(self, resolve):
		doc = SimpleNamespace(docstatus=0, custom_principal="", items=[])
		prefill_purchase_receipt_principal(doc)
		self.assertEqual(doc.custom_principal, "UNILEVER")

	@patch("aimatic.purchase_principal.resolve_principal_for_receipt")
	def test_receipt_prefill_skips_when_set(self, resolve):
		doc = SimpleNamespace(docstatus=0, custom_principal="KEEP", items=[])
		prefill_purchase_receipt_principal(doc)
		resolve.assert_not_called()
		self.assertEqual(doc.custom_principal, "KEEP")

	@patch("aimatic.purchase_principal.resolve_principal_for_invoice", return_value="DETTOL")
	def test_invoice_prefill_fills_blank(self, resolve):
		doc = SimpleNamespace(docstatus=0, custom_principal="", items=[])
		prefill_purchase_invoice_principal(doc)
		self.assertEqual(doc.custom_principal, "DETTOL")
