# Copyright (c) 2026, Ai Matic and contributors
# For license information, please see license.txt

import frappe

from aimatic.price_export.api import get_branch_price_sheet_rows, require_export_permission
from aimatic.price_export.foodpanda_sftp import resolve_foodpanda_active


def get_columns(max_barcodes):
	barcode_columns = [
		{"label": f"Barcode {i}", "fieldname": f"barcode{i}", "fieldtype": "Data", "width": 140}
		for i in range(1, max_barcodes + 1)
	]
	return [
		{
			"label": "Item Code",
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 160,
		},
		{"label": "Item Name", "fieldname": "item_name", "fieldtype": "Data", "width": 220},
		*barcode_columns,
		{"label": "UOM", "fieldname": "uom", "fieldtype": "Link", "options": "UOM", "width": 80},
		{
			"label": "Current Selling Price",
			"fieldname": "selling_price",
			"fieldtype": "Currency",
			"width": 140,
		},
		{"label": "MRP", "fieldname": "mrp", "fieldtype": "Currency", "width": 110},
		{
			"label": "Foodpanda Price (Editable)",
			"fieldname": "foodpanda_price",
			"fieldtype": "Currency",
			"width": 165,
			"editable": 1,
		},
		{"label": "FP Active", "fieldname": "foodpanda_active", "fieldtype": "Check", "width": 85},
		{
			"label": "FP Available Qty",
			"fieldname": "available_qty",
			"fieldtype": "Float",
			"width": 120,
		},
		{"label": "Stock In Hand", "fieldname": "stock_in_hand", "fieldtype": "Float", "width": 110},
		{
			"label": "Cost Price (Excl. Taxes)",
			"fieldname": "cost_price_excl_tax",
			"fieldtype": "Currency",
			"width": 150,
		},
		{
			"label": "Cost Price (Incl. Taxes)",
			"fieldname": "cost_price_incl_tax",
			"fieldtype": "Currency",
			"width": 150,
		},
		{"label": "Box Price", "fieldname": "box_price", "fieldtype": "Currency", "width": 110},
	]


def execute(filters=None):
	require_export_permission()

	filters = filters or {}
	branch = filters.get("branch")
	if not branch:
		frappe.throw(frappe._("Branch is required."))
	if not frappe.has_permission("Branch", ptype="read", doc=branch):
		frappe.throw(frappe._("Not permitted to view this branch."), frappe.PermissionError)

	data = get_branch_price_sheet_rows(branch)
	item_search = (filters.get("item_search") or "").strip().lower()
	availability = filters.get("availability")
	price_status = filters.get("foodpanda_price_status")
	inactive_if_qty_lte = filters.get("inactive_if_qty_lte")

	def include_row(row):
		if item_search:
			haystack = " ".join(
				[row.get("item_code") or "", row.get("item_name") or "", *row.get("_barcodes", [])]
			).lower()
			if item_search not in haystack:
				return False
		if availability == "In Stock" and row.get("available_qty", 0) <= 0:
			return False
		if availability == "Out of Stock" and row.get("available_qty", 0) > 0:
			return False
		if price_status == "With Price" and not row.get("foodpanda_price"):
			return False
		if price_status == "Missing Price" and row.get("foodpanda_price"):
			return False
		return True

	data = [row for row in data if include_row(row)]

	max_barcodes = max((len(row["_barcodes"]) for row in data), default=0)
	for row in data:
		barcodes = row.pop("_barcodes")
		for i in range(max_barcodes):
			row[f"barcode{i + 1}"] = barcodes[i] if i < len(barcodes) else ""
		# Preview matches CSV/SFTP active when the report filter is set.
		row["foodpanda_active"] = resolve_foodpanda_active(row.get("available_qty"), inactive_if_qty_lte)

	columns = get_columns(max_barcodes)
	return columns, data
