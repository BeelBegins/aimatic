"""Backfill Purchase Invoice bill_no from parent PR/PO Supplier Invoice No.

Only fills blank bill_no. Prefer Purchase Receipt.custom_supplier_invoice_no,
then Purchase Order.custom_supplier_invoice_no. Idempotent.
"""

from __future__ import annotations

import frappe


def _invoice_names_needing_pr_source():
	return frappe.db.sql(
		"""
		select distinct pi.name, pr.custom_supplier_invoice_no as value
		from `tabPurchase Invoice` pi
		inner join `tabPurchase Invoice Item` pii on pii.parent = pi.name
		inner join `tabPurchase Receipt` pr on pr.name = pii.purchase_receipt
		where pi.docstatus < 2
		  and ifnull(pi.bill_no, '') = ''
		  and ifnull(pr.custom_supplier_invoice_no, '') != ''
		""",
		as_dict=True,
	)


def _invoice_names_needing_po_source():
	return frappe.db.sql(
		"""
		select distinct pi.name, po.custom_supplier_invoice_no as value
		from `tabPurchase Invoice` pi
		inner join `tabPurchase Invoice Item` pii on pii.parent = pi.name
		inner join `tabPurchase Order` po on po.name = pii.purchase_order
		left join `tabPurchase Receipt` pr on pr.name = pii.purchase_receipt
		where pi.docstatus < 2
		  and ifnull(pi.bill_no, '') = ''
		  and ifnull(po.custom_supplier_invoice_no, '') != ''
		  and ifnull(pr.custom_supplier_invoice_no, '') = ''
		""",
		as_dict=True,
	)


def _receipt_names_needing_po_source():
	return frappe.db.sql(
		"""
		select distinct pr.name, po.custom_supplier_invoice_no as value
		from `tabPurchase Receipt` pr
		inner join `tabPurchase Receipt Item` pri on pri.parent = pr.name
		inner join `tabPurchase Order` po on po.name = pri.purchase_order
		where pr.docstatus < 2
		  and ifnull(pr.custom_supplier_invoice_no, '') = ''
		  and ifnull(po.custom_supplier_invoice_no, '') != ''
		""",
		as_dict=True,
	)


def backfill_supplier_invoice_numbers() -> dict:
	"""Fill blank PR/PI supplier invoice fields from parents. Returns counts."""
	updated_pr = 0
	updated_pi = 0

	for row in _receipt_names_needing_po_source():
		frappe.db.set_value(
			"Purchase Receipt",
			row.name,
			"custom_supplier_invoice_no",
			row.value,
			update_modified=False,
		)
		updated_pr += 1

	seen_pi = set()
	for row in _invoice_names_needing_pr_source() + _invoice_names_needing_po_source():
		if row.name in seen_pi:
			continue
		seen_pi.add(row.name)
		frappe.db.set_value(
			"Purchase Invoice",
			row.name,
			"bill_no",
			row.value,
			update_modified=False,
		)
		updated_pi += 1

	return {"purchase_receipt": updated_pr, "purchase_invoice": updated_pi}


def execute():
	counts = backfill_supplier_invoice_numbers()
	frappe.logger("aimatic").info(
		f"backfill_supplier_invoice_from_parents: {counts}"
	)
