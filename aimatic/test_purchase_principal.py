import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from aimatic.purchase_principal import (
	get_allowed_principals,
	guess_principal_from_items,
	prefill_purchase_invoice_principal,
	prefill_purchase_receipt_principal,
	resolve_principal_for_invoice,
	resolve_principal_for_receipt,
	validate_item_principals,
	validate_purchase_principal,
)


class TestPurchasePrincipal(unittest.TestCase):
	@patch("aimatic.purchase_principal.frappe.get_all")
	@patch("aimatic.purchase_principal.frappe.db.get_value", return_value=1)
	@patch("aimatic.purchase_principal.frappe.get_meta")
	def test_item_principal_enforcement_accepts_matching_items(self, get_meta, _get_value, get_all):
		get_meta.return_value.has_field.return_value = True
		get_all.return_value = [["ITEM-1", "ABBOTT"], ["ITEM-2", "ABBOTT"]]
		doc = SimpleNamespace(
			supplier="SUP-1",
			items=[SimpleNamespace(item_code="ITEM-1"), SimpleNamespace(item_code="ITEM-2")],
		)
		validate_item_principals(doc, "ABBOTT")

	@patch("aimatic.purchase_principal.frappe.get_all")
	@patch("aimatic.purchase_principal.frappe.db.get_value", return_value=1)
	@patch("aimatic.purchase_principal.frappe.get_meta")
	def test_item_principal_enforcement_rejects_missing_mapping(self, get_meta, _get_value, get_all):
		get_meta.return_value.has_field.return_value = True
		get_all.return_value = [["ITEM-1", None]]
		doc = SimpleNamespace(supplier="SUP-1", items=[SimpleNamespace(item_code="ITEM-1")])
		with self.assertRaises(frappe.ValidationError):
			validate_item_principals(doc, "ABBOTT")

	@patch("aimatic.purchase_principal.frappe.get_all")
	@patch("aimatic.purchase_principal.frappe.db.get_value", return_value=1)
	@patch("aimatic.purchase_principal.frappe.get_meta")
	def test_item_principal_enforcement_rejects_mismatch(self, get_meta, _get_value, get_all):
		get_meta.return_value.has_field.return_value = True
		get_all.return_value = [["ITEM-1", "SHIELD"]]
		doc = SimpleNamespace(supplier="SUP-1", items=[SimpleNamespace(item_code="ITEM-1")])
		with self.assertRaises(frappe.ValidationError):
			validate_item_principals(doc, "ABBOTT")

	@patch("aimatic.purchase_principal.frappe.get_all")
	def test_get_allowed_principals(self, get_all):
		get_all.return_value = [
			SimpleNamespace(principal="UNILEVER"),
			SimpleNamespace(principal="DETTOL"),
			SimpleNamespace(principal=""),
		]
		self.assertEqual(get_allowed_principals("SUP-1"), ["UNILEVER", "DETTOL"])
		get_all.assert_called_once()

	@patch("aimatic.purchase_principal._apply_guessed_principal", return_value=None)
	@patch("aimatic.purchase_principal.get_allowed_principals", return_value=["UNILEVER", "DETTOL"])
	def test_validate_requires_principal_when_allowed(self, _allowed, _guess):
		doc = SimpleNamespace(supplier="SUP-1", custom_principal="")
		with self.assertRaises(frappe.ValidationError):
			validate_purchase_principal(doc)

	@patch("aimatic.purchase_principal.get_allowed_principals", return_value=["UNILEVER", "DETTOL"])
	def test_validate_rejects_foreign_principal(self, _allowed):
		doc = SimpleNamespace(supplier="SUP-1", custom_principal="RECKITT", items=[])
		with self.assertRaises(frappe.ValidationError):
			validate_purchase_principal(doc)

	@patch("aimatic.purchase_principal.validate_item_principals")
	@patch("aimatic.purchase_principal.get_allowed_principals", return_value=["UNILEVER", "DETTOL"])
	def test_validate_accepts_allowed_principal(self, _allowed, _item):
		doc = SimpleNamespace(supplier="SUP-1", custom_principal="UNILEVER", items=[])
		validate_purchase_principal(doc)

	@patch("aimatic.purchase_principal.get_allowed_principals", return_value=[])
	def test_validate_rejects_principal_when_supplier_has_none(self, _allowed):
		doc = SimpleNamespace(supplier="SUP-2", custom_principal="UNILEVER", items=[])
		with self.assertRaises(frappe.ValidationError):
			validate_purchase_principal(doc)

	@patch("aimatic.purchase_principal._apply_guessed_principal", return_value=None)
	@patch("aimatic.purchase_principal.get_allowed_principals", return_value=[])
	def test_validate_allows_blank_when_supplier_has_none(self, _allowed, _guess):
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
		doc = SimpleNamespace(items=[SimpleNamespace(purchase_receipt="PR-1", purchase_order="PO-1")])
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

	@patch("aimatic.purchase_principal.get_allowed_principals", return_value=["ABBOTT", "SHIELD"])
	@patch("aimatic.purchase_principal.frappe.get_all")
	def test_guess_from_item_mapping_when_all_agree(self, get_all, _allowed):
		get_all.return_value = [
			SimpleNamespace(name="ITEM-1", custom_principal="ABBOTT"),
			SimpleNamespace(name="ITEM-2", custom_principal="ABBOTT"),
		]
		doc = SimpleNamespace(
			supplier="SUP-1",
			company="CO",
			items=[SimpleNamespace(item_code="ITEM-1"), SimpleNamespace(item_code="ITEM-2")],
		)
		self.assertEqual(guess_principal_from_items(doc), "ABBOTT")

	@patch("aimatic.purchase_principal.get_allowed_principals", return_value=["ABBOTT", "SHIELD"])
	@patch("aimatic.purchase_principal.frappe.get_all")
	def test_guess_rejects_mixed_item_mapping(self, get_all, _allowed):
		get_all.return_value = [
			SimpleNamespace(name="ITEM-1", custom_principal="ABBOTT"),
			SimpleNamespace(name="ITEM-2", custom_principal="SHIELD"),
		]
		doc = SimpleNamespace(
			supplier="SUP-1",
			company="CO",
			items=[SimpleNamespace(item_code="ITEM-1"), SimpleNamespace(item_code="ITEM-2")],
		)
		self.assertIsNone(guess_principal_from_items(doc))

	@patch("aimatic.purchase_principal.get_allowed_principals", return_value=["ABBOTT", "SHIELD"])
	@patch("aimatic.purchase_principal._historical_principals_by_item")
	@patch("aimatic.purchase_principal.frappe.get_all", return_value=[])
	def test_guess_from_unique_item_history(self, _get_all, history, _allowed):
		history.return_value = {"ITEM-1": {"ABBOTT"}, "ITEM-2": {"ABBOTT"}}
		doc = SimpleNamespace(
			supplier="SUP-1",
			company="CO",
			items=[SimpleNamespace(item_code="ITEM-1"), SimpleNamespace(item_code="ITEM-2")],
		)
		self.assertEqual(guess_principal_from_items(doc), "ABBOTT")

	@patch("aimatic.purchase_principal.resolve_principal_for_receipt", return_value=None)
	@patch("aimatic.purchase_principal.guess_principal_from_items", return_value="ABBOTT")
	def test_receipt_prefill_falls_back_to_item_guess(self, _guess, _resolve):
		doc = SimpleNamespace(docstatus=0, custom_principal="", items=[])
		prefill_purchase_receipt_principal(doc)
		self.assertEqual(doc.custom_principal, "ABBOTT")
