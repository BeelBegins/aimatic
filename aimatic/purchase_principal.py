"""Principal tagging on purchase documents for multi-company distributors.

Rules:
- Allowed Principals live on Supplier.custom_principals (no default).
- When the supplier has any allowed Principal, custom_principal is required
  and must be one of that list.
- When the supplier has none, custom_principal must stay blank.
- PO → PR → PI copies custom_principal when the child field is blank.
"""

from __future__ import annotations

import frappe
from frappe import _


def get_allowed_principals(supplier: str | None) -> list[str]:
	if not supplier:
		return []
	rows = frappe.get_all(
		"Supplier Principal",
		filters={"parent": supplier, "parenttype": "Supplier"},
		fields=["principal"],
		order_by="idx asc",
	)
	return [r.principal for r in rows if r.principal]


def validate_purchase_principal(doc, method=None):
	"""validate: enforce principal rules on PO / PR / PI."""
	allowed = get_allowed_principals(getattr(doc, "supplier", None))
	principal = (getattr(doc, "custom_principal", None) or "").strip()

	if allowed:
		if not principal:
			frappe.throw(
				_("Principal is required for supplier {0}.").format(
					frappe.bold(doc.supplier)
				)
			)
		if principal not in allowed:
			frappe.throw(
				_("Principal {0} is not allowed for supplier {1}.").format(
					frappe.bold(principal), frappe.bold(doc.supplier)
				)
			)
		return

	if principal:
		frappe.throw(
			_(
				"Supplier {0} has no Principals configured. Clear Principal or "
				"add allowed Principals on the Supplier."
			).format(frappe.bold(doc.supplier))
		)


def _first_linked_principal(doc, link_field: str, parent_doctype: str):
	seen = set()
	for row in getattr(doc, "items", None) or []:
		parent_name = (
			row.get(link_field)
			if hasattr(row, "get")
			else getattr(row, link_field, None)
		)
		if not parent_name or parent_name in seen:
			continue
		seen.add(parent_name)
		value = frappe.db.get_value(parent_doctype, parent_name, "custom_principal")
		if value:
			return value
	return None


def resolve_principal_for_receipt(doc) -> str | None:
	return _first_linked_principal(doc, "purchase_order", "Purchase Order")


def resolve_principal_for_invoice(doc) -> str | None:
	from_pr = _first_linked_principal(doc, "purchase_receipt", "Purchase Receipt")
	if from_pr:
		return from_pr
	return _first_linked_principal(doc, "purchase_order", "Purchase Order")


def prefill_purchase_receipt_principal(doc, method=None):
	if getattr(doc, "docstatus", 0) != 0:
		return
	if getattr(doc, "custom_principal", None):
		return
	value = resolve_principal_for_receipt(doc)
	if value:
		doc.custom_principal = value


def prefill_purchase_invoice_principal(doc, method=None):
	if getattr(doc, "docstatus", 0) != 0:
		return
	if getattr(doc, "custom_principal", None):
		return
	value = resolve_principal_for_invoice(doc)
	if value:
		doc.custom_principal = value


def _prefill_mapped_principal(doc, source_doctype: str, source_name: str):
	if not doc or doc.get("custom_principal"):
		return doc
	value = frappe.db.get_value(source_doctype, source_name, "custom_principal")
	if value:
		doc.custom_principal = value
	return doc


def apply_principal_on_mapped_receipt(doc, source_name: str):
	return _prefill_mapped_principal(doc, "Purchase Order", source_name)


def apply_principal_on_mapped_invoice_from_order(doc, source_name: str):
	return _prefill_mapped_principal(doc, "Purchase Order", source_name)


def apply_principal_on_mapped_invoice_from_receipt(doc, source_name: str):
	doc = _prefill_mapped_principal(doc, "Purchase Receipt", source_name)
	if doc and doc.get("custom_principal"):
		return doc
	# PR may itself only inherit from PO — follow once via item links.
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
		value = frappe.db.get_value("Purchase Order", po_name, "custom_principal")
		if value:
			doc.custom_principal = value
			break
	return doc


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_principal_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link search: only Principals allowed on the selected Supplier."""
	supplier = (filters or {}).get("supplier")
	allowed = get_allowed_principals(supplier)
	if not allowed:
		return []

	return frappe.db.sql(
		"""
		select name, principal_name
		from `tabPrincipal`
		where disabled = 0
		  and name in %(allowed)s
		  and (name like %(txt)s or principal_name like %(txt)s)
		order by principal_name
		limit %(start)s, %(page_len)s
		""",
		{
			"allowed": tuple(allowed),
			"txt": f"%{txt}%",
			"start": int(start or 0),
			"page_len": int(page_len or 20),
		},
	)
