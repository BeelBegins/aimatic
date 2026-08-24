# Copyright (c) 2026, Ai Matic and contributors
# For license information, please see license.txt

"""Stock Aging with History — ages purchase quantity into buckets.

History Debit/Credit + live PR/PI SLE. Cost is a single end-of-row reference
only — aging itself is quantity-based.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)
	ranges = get_ranges(filters)
	columns = get_columns(ranges)
	data = get_data(filters, ranges)
	return columns, data


def validate_filters(filters):
	for fieldname, label in (
		("company", _("Company")),
		("branch", _("Branch")),
		("as_on_date", _("As On Date")),
	):
		if not filters.get(fieldname):
			frappe.throw(_("{0} is mandatory").format(frappe.bold(label)))
	if not frappe.has_permission("Branch", ptype="read", doc=filters.branch):
		frappe.throw(_("Not permitted to view this branch."), frappe.PermissionError)


def get_ranges(filters):
	r1 = max(1, cint(filters.get("range1") or 30))
	r2 = max(r1 + 1, cint(filters.get("range2") or 60))
	r3 = max(r2 + 1, cint(filters.get("range3") or 90))
	return (r1, r2, r3)


def bucket_key(days: int, ranges: tuple[int, int, int]) -> str:
	r1, r2, r3 = ranges
	if days <= r1:
		return "bucket_1"
	if days <= r2:
		return "bucket_2"
	if days <= r3:
		return "bucket_3"
	return "bucket_4"


def get_columns(ranges):
	r1, r2, r3 = ranges
	return [
		{
			"label": _("Item"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 160,
		},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 220},
		{"label": _("Source ItemCode"), "fieldname": "item_code_raw", "fieldtype": "Data", "width": 140},
		{
			"label": _("0-{0} Qty").format(r1),
			"fieldname": "bucket_1",
			"fieldtype": "Float",
			"width": 110,
		},
		{
			"label": _("{0}-{1} Qty").format(r1 + 1, r2),
			"fieldname": "bucket_2",
			"fieldtype": "Float",
			"width": 110,
		},
		{
			"label": _("{0}-{1} Qty").format(r2 + 1, r3),
			"fieldname": "bucket_3",
			"fieldtype": "Float",
			"width": 110,
		},
		{
			"label": _("{0}+ Qty").format(r3 + 1),
			"fieldname": "bucket_4",
			"fieldtype": "Float",
			"width": 110,
		},
		{"label": _("Total Qty"), "fieldname": "total_qty", "fieldtype": "Float", "width": 110},
		{"label": _("History Qty"), "fieldname": "history_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Live Qty"), "fieldname": "live_qty", "fieldtype": "Float", "width": 100},
		{
			"label": _("Cost (ref)"),
			"fieldname": "total_cost",
			"fieldtype": "Currency",
			"width": 120,
		},
		{"label": _("Unmatched"), "fieldname": "unmatched", "fieldtype": "Check", "width": 90},
	]


def _blank_row(item_code=None, item_name=None, item_code_raw=None, unmatched=0):
	return frappe._dict(
		item_code=item_code,
		item_name=item_name,
		item_code_raw=item_code_raw,
		bucket_1=0.0,
		bucket_2=0.0,
		bucket_3=0.0,
		bucket_4=0.0,
		total_qty=0.0,
		history_qty=0.0,
		live_qty=0.0,
		total_cost=0.0,
		unmatched=unmatched,
	)


def _accumulate(row, key: str, qty: float, cost: float, layer: str):
	qty = flt(qty)
	cost = flt(cost)
	row[key] = flt(row.get(key)) + qty
	row.total_qty = flt(row.total_qty) + qty
	row.total_cost = flt(row.total_cost) + cost
	if layer == "history":
		row.history_qty = flt(row.history_qty) + qty
	else:
		row.live_qty = flt(row.live_qty) + qty


def _get_branch_warehouses(branch: str) -> list[str]:
	return frappe.get_all(
		"Warehouse",
		filters={"custom_branch": branch, "is_group": 0},
		pluck="name",
	)


def _history_rows(filters, as_on):
	conditions = [
		"bih.company = %(company)s",
		"bih.branch = %(branch)s",
		"bih.posting_date <= %(as_on_date)s",
		"bih.transaction_type IN ('Debit', 'Credit')",
	]
	values = {
		"company": filters.company,
		"branch": filters.branch,
		"as_on_date": as_on,
	}

	if filters.get("item_code"):
		conditions.append("bih.item_code = %(item_code)s")
		values["item_code"] = filters.item_code
	if filters.get("item_group"):
		conditions.append(
			"EXISTS (SELECT 1 FROM `tabItem` i WHERE i.name = bih.item_code AND i.item_group = %(item_group)s)"
		)
		values["item_group"] = filters.item_group
	if not cint(filters.get("include_unmatched")):
		conditions.append("bih.match_status != 'Unmatched'")
		conditions.append("bih.item_code IS NOT NULL")

	where = " AND ".join(conditions)
	return frappe.db.sql(
		f"""
		SELECT
			bih.item_code,
			bih.item_code_raw,
			bih.item_name,
			bih.match_status,
			bih.transaction_type,
			bih.posting_date,
			bih.qty,
			bih.cost_total
		FROM `tabBranch Item History` bih
		WHERE {where}
		""",
		values,
		as_dict=True,
	)


def _live_purchase_rows(filters, as_on, warehouse_list: list[str] | None):
	"""Inbound purchase qty/value from submitted Purchase Receipt / Purchase Invoice stock."""
	conditions = [
		"sle.is_cancelled = 0",
		"sle.company = %(company)s",
		"sle.posting_date <= %(as_on_date)s",
		"sle.voucher_type IN ('Purchase Receipt', 'Purchase Invoice')",
		"sle.actual_qty != 0",
	]
	values = {
		"company": filters.company,
		"as_on_date": as_on,
		"branch": filters.branch,
	}

	conditions.append(
		"""
		(
			EXISTS (
				SELECT 1 FROM `tabPurchase Receipt` pr
				WHERE sle.voucher_type = 'Purchase Receipt'
				  AND pr.name = sle.voucher_no
				  AND pr.docstatus = 1
				  AND pr.branch = %(branch)s
			)
			OR EXISTS (
				SELECT 1 FROM `tabPurchase Invoice` pi
				WHERE sle.voucher_type = 'Purchase Invoice'
				  AND pi.name = sle.voucher_no
				  AND pi.docstatus = 1
				  AND pi.branch = %(branch)s
			)
			OR (
				%(has_warehouses)s = 1
				AND sle.warehouse IN %(warehouses)s
			)
		)
		"""
	)
	values["has_warehouses"] = 1 if warehouse_list else 0
	values["warehouses"] = tuple(warehouse_list) if warehouse_list else ("",)

	if filters.get("warehouse"):
		conditions.append("sle.warehouse = %(warehouse)s")
		values["warehouse"] = filters.warehouse
	if filters.get("item_code"):
		conditions.append("sle.item_code = %(item_code)s")
		values["item_code"] = filters.item_code
	if filters.get("item_group"):
		conditions.append(
			"EXISTS (SELECT 1 FROM `tabItem` i WHERE i.name = sle.item_code AND i.item_group = %(item_group)s)"
		)
		values["item_group"] = filters.item_group

	where = " AND ".join(conditions)
	return frappe.db.sql(
		f"""
		SELECT
			sle.item_code,
			i.item_name,
			sle.posting_date,
			sle.actual_qty AS qty_signed,
			sle.stock_value_difference AS cost_signed
		FROM `tabStock Ledger Entry` sle
		LEFT JOIN `tabItem` i ON i.name = sle.item_code
		WHERE {where}
		""",
		values,
		as_dict=True,
	)


def get_data(filters, ranges):
	as_on = getdate(filters.as_on_date)
	by_key: dict[str, frappe._dict] = {}

	for row in _history_rows(filters, as_on):
		item_key = row.item_code or f"UNMATCHED::{row.item_code_raw}"
		out = by_key.get(item_key)
		if not out:
			out = _blank_row(
				item_code=row.item_code,
				item_name=row.item_name,
				item_code_raw=row.item_code_raw,
				unmatched=1 if row.match_status == "Unmatched" or not row.item_code else 0,
			)
			by_key[item_key] = out

		days = max(0, (as_on - getdate(row.posting_date)).days)
		key = bucket_key(days, ranges)
		# Debit = purchase (+); Credit = purchase return (−)
		sign = 1.0 if row.transaction_type == "Debit" else -1.0
		_accumulate(out, key, sign * flt(row.qty), sign * flt(row.cost_total), "history")

	if cint(filters.get("include_live")):
		if filters.get("warehouse"):
			warehouses = [filters.warehouse]
		else:
			warehouses = _get_branch_warehouses(filters.branch) or None

		for row in _live_purchase_rows(filters, as_on, warehouses):
			item_key = row.item_code
			out = by_key.get(item_key)
			if not out:
				out = _blank_row(item_code=row.item_code, item_name=row.item_name)
				by_key[item_key] = out
			elif not out.item_name and row.item_name:
				out.item_name = row.item_name

			days = max(0, (as_on - getdate(row.posting_date)).days)
			key = bucket_key(days, ranges)
			_accumulate(out, key, flt(row.qty_signed), flt(row.cost_signed), "live")

	rows = list(by_key.values())
	rows.sort(key=lambda r: (-flt(r.total_qty), r.item_code or "", r.item_code_raw or ""))
	return rows
