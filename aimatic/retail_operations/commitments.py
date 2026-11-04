"""Explicit receipt matching; never fuzzy-consume somebody else's order."""

from __future__ import annotations

import frappe
from frappe.utils import flt


def receipt_qty(item_row):
	row = frappe.db.get_value("Purchase Receipt Item", item_row, ["parent", "stock_qty"], as_dict=True)
	if not row or frappe.db.get_value("Purchase Receipt", row.parent, "docstatus") != 1:
		return 0
	returns = frappe.db.sql(
		"""SELECT COALESCE(SUM(pri.stock_qty),0) FROM `tabPurchase Receipt Item` pri
        JOIN `tabPurchase Receipt` pr ON pr.name=pri.parent
        WHERE pr.docstatus=1 AND pr.is_return=1 AND pri.purchase_receipt_item=%s""",
		item_row,
	)[0][0]
	return max(flt(row.stock_qty) + flt(returns), 0)


def received(doc, line):
	if line.purchase_order_item:
		native = frappe.db.get_value(
			"Purchase Order Item",
			line.purchase_order_item,
			["parent", "received_qty", "conversion_factor"],
			as_dict=True,
		)
		if native and frappe.db.get_value("Purchase Order", native.parent, "docstatus") == 1:
			return max(flt(native.received_qty) * flt(native.conversion_factor), 0)
		return 0
	keys = {r.purchase_receipt_item for r in doc.get("receipts") or [] if r.item_code == line.item_code}
	if line.purchase_receipt_item:
		keys.add(line.purchase_receipt_item)
	return sum(receipt_qty(key) for key in keys if key)


def validate_allocations(doc):
	from aimatic.retail_operations.permissions import check

	check(doc.branch, "Purchase", write=True)
	item_codes = [r.item_code for r in doc.items]
	if len(item_codes) != len(set(item_codes)):
		frappe.throw("Combine repeated item lines within this purchasing commitment")
	seen = set()
	for allocation in doc.get("receipts") or []:
		key = allocation.purchase_receipt_item
		if not key or key in seen:
			frappe.throw("Each matched receipt row can appear only once")
		seen.add(key)
		if frappe.db.exists("Retail Commitment Item", {"purchase_receipt_item": key}):
			frappe.throw("This receipt row is already matched through a direct commitment link")
		native = frappe.db.get_value(
			"Purchase Receipt Item", key, ["parent", "item_code", "warehouse"], as_dict=True
		)
		parent = frappe.get_doc("Purchase Receipt", native.parent) if native else None
		if (
			not native
			or parent.docstatus != 1
			or parent.is_return
			or parent.supplier != doc.supplier
			or parent.company != doc.company
			or parent.branch != doc.branch
			or native.warehouse != doc.warehouse
			or native.item_code not in item_codes
		):
			frappe.throw("Receipt allocation must match vendor, branch, warehouse and a commitment item")
		allocation.item_code = native.item_code
		line = next(r for r in doc.items if r.item_code == native.item_code)
		if line.purchase_order_item or line.purchase_receipt_item:
			frappe.throw("Use receipt allocations or a native PO/direct receipt link for an item, not both")
	total = 0
	done = 0
	for line in doc.items:
		line.received_qty = min(received(doc, line), flt(line.qty))
		total += flt(line.qty)
		done += flt(line.received_qty)
	doc.fulfilment_status = (
		"Received" if total and done >= total else ("Partially received" if done else "Awaiting receipt")
	)
