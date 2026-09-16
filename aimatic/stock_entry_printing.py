from __future__ import annotations

import frappe


def get_stock_entry_print_context(doc) -> dict:
	"""Compact transfer-print context with a reliable Item Barcode fallback."""
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

	def branch_for(warehouse):
		return frappe.get_cached_value("Warehouse", warehouse, "custom_branch") if warehouse else None

	source_branch = branch_for(doc.from_warehouse)
	target_branch = branch_for(doc.to_warehouse)
	price_list = (
		frappe.get_cached_value("Branch", target_branch, "default_selling_price_list")
		if target_branch
		else None
	)
	branch_prices = (
		frappe.get_all(
			"Item Price",
			filters={"item_code": ["in", item_codes], "price_list": price_list, "selling": 1},
			fields=["item_code", "uom", "custom_latest_price_incl_taxes", "custom_mrp"],
		)
		if item_codes and price_list
		else []
	)

	def price_for(row):
		for price in branch_prices:
			if price.item_code == row.item_code and price.uom == row.uom:
				return price
		return next((price for price in branch_prices if price.item_code == row.item_code), {})

	return {
		"source_branch": source_branch,
		"target_branch": target_branch,
		"price_branch": target_branch,
		"price_list": price_list,
		"item_context": {
			row.name: {
				"barcode": row.get("barcode") or primary_barcodes.get(row.item_code) or "",
				"latest_price_incl_taxes": price_for(row).get("custom_latest_price_incl_taxes", 0),
				"mrp": price_for(row).get("custom_mrp", 0),
			}
			for row in (doc.items or [])
			if row.name
		},
	}
