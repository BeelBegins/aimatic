# Copyright (c) 2026, Ai Matic and contributors
# For license information, please see license.txt

import frappe

from aimatic.price_export.api import get_branch_price_sheet_rows, require_export_permission


def get_columns(max_barcodes):
	barcode_columns = [
		{"label": f"Barcode {i}", "fieldname": f"barcode{i}", "fieldtype": "Data", "width": 140}
		for i in range(1, max_barcodes + 1)
	]
	return [
		{"label": "Item Code", "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": "Item Name", "fieldname": "item_name", "fieldtype": "Data", "width": 220},
		*barcode_columns,
		{"label": "UOM", "fieldname": "uom", "fieldtype": "Link", "options": "UOM", "width": 80},
		{"label": "Current Selling Price", "fieldname": "selling_price", "fieldtype": "Currency", "width": 140},
		{"label": "Foodpanda Price", "fieldname": "foodpanda_price", "fieldtype": "Currency", "width": 130},
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

	max_barcodes = max((len(row["_barcodes"]) for row in data), default=0)
	for row in data:
		barcodes = row.pop("_barcodes")
		for i in range(max_barcodes):
			row[f"barcode{i + 1}"] = barcodes[i] if i < len(barcodes) else ""

	columns = get_columns(max_barcodes)
	return columns, data
