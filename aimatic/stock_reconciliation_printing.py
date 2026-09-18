from __future__ import annotations

import frappe


def get_stock_reconciliation_print_context(doc) -> dict:
	"""Compact reconciliation-print context: barcode fallback and a single
	consolidated warehouse/branch line when every row shares one warehouse."""
	item_codes = [row.item_code for row in (doc.items or []) if row.item_code]
	primary_barcodes: dict[str, str] = {}
	if item_codes:
		for barcode in frappe.get_all(
			"Item Barcode",
			filters={"parent": ["in", item_codes]},
			fields=["parent", "barcode", "idx"],
			order_by="parent asc, idx asc",
		):
			if barcode.parent and barcode.barcode and barcode.parent not in primary_barcodes:
				primary_barcodes[barcode.parent] = barcode.barcode

	row_warehouses = {row.warehouse for row in (doc.items or []) if row.warehouse}
	single_warehouse = next(iter(row_warehouses)) if len(row_warehouses) == 1 else None
	consolidated_warehouse = doc.set_warehouse or single_warehouse
	branch = (
		frappe.get_cached_value("Warehouse", consolidated_warehouse, "custom_branch")
		if consolidated_warehouse
		else None
	)

	return {
		"consolidated_warehouse": consolidated_warehouse,
		"branch": branch,
		"show_warehouse_column": bool(consolidated_warehouse is None and row_warehouses),
		"item_context": {
			row.name: {"barcode": row.get("barcode") or primary_barcodes.get(row.item_code) or ""}
			for row in (doc.items or [])
			if row.name
		},
	}
