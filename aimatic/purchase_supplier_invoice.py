"""Prefill Supplier Invoice No across PO → PR → PI.

PO and PR store `custom_supplier_invoice_no`. Purchase Invoice uses the
standard `bill_no` (same label). Child documents are prefilled from the
parent when empty; the user can still change the value.
"""

from __future__ import annotations

import frappe


def _first_linked_value(doc, link_field: str, parent_doctype: str, parent_field: str):
	seen = set()
	for row in getattr(doc, "items", None) or []:
		parent_name = row.get(link_field) if hasattr(row, "get") else getattr(row, link_field, None)
		if not parent_name or parent_name in seen:
			continue
		seen.add(parent_name)
		value = frappe.db.get_value(parent_doctype, parent_name, parent_field)
		if value:
			return value
	return None


def resolve_supplier_invoice_no_for_receipt(doc) -> str | None:
	"""PO.custom_supplier_invoice_no → PR.custom_supplier_invoice_no."""
	return _first_linked_value(doc, "purchase_order", "Purchase Order", "custom_supplier_invoice_no")


def resolve_supplier_invoice_no_for_invoice(doc) -> str | None:
	"""Prefer PR, then PO, into PI.bill_no."""
	from_pr = _first_linked_value(doc, "purchase_receipt", "Purchase Receipt", "custom_supplier_invoice_no")
	if from_pr:
		return from_pr
	return _first_linked_value(doc, "purchase_order", "Purchase Order", "custom_supplier_invoice_no")


def prefill_purchase_receipt_supplier_invoice(doc, method=None):
	"""before_validate: fill PR Supplier Invoice No from linked PO if blank."""
	if getattr(doc, "docstatus", 0) != 0:
		return
	if getattr(doc, "custom_supplier_invoice_no", None):
		return
	value = resolve_supplier_invoice_no_for_receipt(doc)
	if value:
		doc.custom_supplier_invoice_no = value


def prefill_purchase_invoice_supplier_invoice(doc, method=None):
	"""before_validate: fill PI bill_no from linked PR/PO if blank."""
	if getattr(doc, "docstatus", 0) != 0:
		return
	if getattr(doc, "bill_no", None):
		return
	value = resolve_supplier_invoice_no_for_invoice(doc)
	if value:
		doc.bill_no = value


def _prefill_mapped_receipt(doc, source_name: str):
	if not doc or doc.get("custom_supplier_invoice_no"):
		return doc
	value = frappe.db.get_value("Purchase Order", source_name, "custom_supplier_invoice_no")
	if value:
		doc.custom_supplier_invoice_no = value
	return doc


def _prefill_mapped_invoice_from_receipt(doc, source_name: str):
	if not doc or doc.get("bill_no"):
		return doc
	value = frappe.db.get_value("Purchase Receipt", source_name, "custom_supplier_invoice_no")
	if not value:
		# PR may itself only have it on linked PO — follow once.
		po_names = frappe.db.sql(
			"""
			select distinct purchase_order
			from `tabPurchase Receipt Item`
			where parent = %s and ifnull(purchase_order, '') != ''
			""",
			source_name,
			pluck=True,
		)
		for po_name in po_names or []:
			value = frappe.db.get_value("Purchase Order", po_name, "custom_supplier_invoice_no")
			if value:
				break
	if value:
		doc.bill_no = value
	return doc


def _prefill_mapped_invoice_from_order(doc, source_name: str):
	if not doc or doc.get("bill_no"):
		return doc
	value = frappe.db.get_value("Purchase Order", source_name, "custom_supplier_invoice_no")
	if value:
		doc.bill_no = value
	return doc


@frappe.whitelist()
def make_purchase_receipt(source_name, target_doc=None, args=None):
	from erpnext.buying.doctype.purchase_order.purchase_order import (
		make_purchase_receipt as _make,
	)

	from aimatic.purchase_principal import apply_principal_on_mapped_receipt

	doc = _make(source_name, target_doc=target_doc, args=args)
	doc = _prefill_mapped_receipt(doc, source_name)
	return apply_principal_on_mapped_receipt(doc, source_name)


@frappe.whitelist()
def make_purchase_invoice_from_po(source_name, target_doc=None, args=None):
	from erpnext.buying.doctype.purchase_order.purchase_order import (
		make_purchase_invoice as _make,
	)

	from aimatic.purchase_principal import apply_principal_on_mapped_invoice_from_order

	doc = _make(source_name, target_doc=target_doc, args=args)
	doc = _prefill_mapped_invoice_from_order(doc, source_name)
	return apply_principal_on_mapped_invoice_from_order(doc, source_name)


@frappe.whitelist()
def make_purchase_invoice_from_pr(source_name, target_doc=None, args=None):
	from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
		make_purchase_invoice as _make,
	)

	from aimatic.purchase_principal import apply_principal_on_mapped_invoice_from_receipt

	doc = _make(source_name, target_doc=target_doc, args=args)
	doc = _prefill_mapped_invoice_from_receipt(doc, source_name)
	return apply_principal_on_mapped_invoice_from_receipt(doc, source_name)
