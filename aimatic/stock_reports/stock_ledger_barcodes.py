"""Add Item barcodes to the standard Stock Ledger report without editing ERPNext.

Patches ``erpnext.stock.report.stock_ledger.stock_ledger.execute`` once per
process so Desk URL ``/desk/query-report/Stock%20Ledger`` keeps working.
"""

from __future__ import annotations

import frappe
from frappe import _

from aimatic.item_pricing.barcodes import BARCODE_SEPARATOR

_patched = False


def patch_stock_ledger_report():
	global _patched
	if _patched:
		return

	from erpnext.stock.report.stock_ledger import stock_ledger as sl

	if getattr(sl, "_aimatic_barcodes_patched", False):
		_patched = True
		return

	original_execute = sl.execute

	def execute(filters=None):
		columns, data = original_execute(filters)
		return _with_barcodes(columns, data)

	sl.execute = execute
	sl._aimatic_barcodes_patched = True
	_patched = True


def _with_barcodes(columns, data):
	_insert_barcodes_column(columns)
	item_codes = {
		row.get("item_code")
		for row in (data or [])
		if row and row.get("item_code") and not str(row.get("item_code")).startswith("'")
	}
	barcodes_by_item = _barcodes_by_item(item_codes)
	for row in data or []:
		if not row:
			continue
		row["barcodes"] = barcodes_by_item.get(row.get("item_code"), "")
	return columns, data


def _insert_barcodes_column(columns):
	if any(col.get("fieldname") == "barcodes" for col in columns):
		return

	barcode_col = {
		"label": _("Barcodes"),
		"fieldname": "barcodes",
		"fieldtype": "Data",
		"width": 150,
	}
	for index, col in enumerate(columns):
		if col.get("fieldname") == "item_name":
			columns.insert(index + 1, barcode_col)
			return
	columns.insert(2, barcode_col)


def _barcodes_by_item(item_codes):
	if not item_codes:
		return {}

	rows = frappe.get_all(
		"Item Barcode",
		filters={"parent": ("in", list(item_codes)), "parenttype": "Item"},
		fields=["parent", "barcode"],
		order_by="parent asc, idx asc",
	)
	grouped = {}
	for row in rows:
		if row.barcode:
			grouped.setdefault(row.parent, []).append(row.barcode)
	return {item: BARCODE_SEPARATOR.join(codes) for item, codes in grouped.items()}
