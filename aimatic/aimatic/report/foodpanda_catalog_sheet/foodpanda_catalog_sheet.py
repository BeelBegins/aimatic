# Copyright (c) 2026, Ai Matic and contributors
# For license information, please see license.txt

import frappe

from aimatic.foodpanda_integration.catalog_sheet import (
	get_foodpanda_catalog_sheet_rows,
	require_catalog_sheet_permission,
)

COLUMNS = [
	{
		"label": "Match Status",
		"fieldname": "match_status",
		"fieldtype": "Data",
		"width": 130,
	},
	{
		"label": "Foodpanda SKU",
		"fieldname": "foodpanda_sku",
		"fieldtype": "Data",
		"width": 140,
	},
	{
		"label": "Foodpanda Barcode",
		"fieldname": "foodpanda_barcode",
		"fieldtype": "Data",
		"width": 150,
	},
	{
		"label": "Item Code",
		"fieldname": "item_code",
		"fieldtype": "Link",
		"options": "Item",
		"width": 150,
	},
	{
		"label": "Item Name",
		"fieldname": "item_name",
		"fieldtype": "Data",
		"width": 200,
	},
	{
		"label": "ERPNext Barcodes",
		"fieldname": "erpnext_barcodes",
		"fieldtype": "Data",
		"width": 160,
	},
	{
		"label": "Our Foodpanda Price (Editable)",
		"fieldname": "foodpanda_price",
		"fieldtype": "Currency",
		"width": 180,
		"editable": 1,
	},
	{
		"label": "Stock Qty",
		"fieldname": "stock_qty",
		"fieldtype": "Float",
		"width": 100,
	},
	{
		"label": "Remote Price",
		"fieldname": "remote_price",
		"fieldtype": "Currency",
		"width": 110,
	},
	{
		"label": "Remote Active",
		"fieldname": "remote_active",
		"fieldtype": "Check",
		"width": 100,
	},
	{
		"label": "Portal Active (Editable)",
		"fieldname": "portal_active",
		"fieldtype": "Check",
		"width": 130,
		"editable": 1,
	},
	{
		"label": "Sync Status",
		"fieldname": "sync_status",
		"fieldtype": "Data",
		"width": 100,
	},
	{
		"label": "Foodpanda Product",
		"fieldname": "foodpanda_product",
		"fieldtype": "Link",
		"options": "Foodpanda Product",
		"width": 140,
	},
	{
		"label": "Last Error",
		"fieldname": "last_error",
		"fieldtype": "Data",
		"width": 220,
	},
	{
		"label": "Matched On",
		"fieldname": "matched_barcode",
		"fieldtype": "Data",
		"width": 120,
	},
]


def execute(filters=None):
	require_catalog_sheet_permission()
	filters = filters or {}
	outlet = filters.get("outlet")
	if not outlet:
		frappe.throw(frappe._("Foodpanda Outlet is required."))
	if not frappe.db.exists("Foodpanda Outlet", outlet):
		frappe.throw(frappe._("Foodpanda Outlet not found."))
	if not frappe.has_permission("Foodpanda Outlet", ptype="read", doc=outlet):
		frappe.throw(frappe._("Not permitted to view this outlet."), frappe.PermissionError)

	data = get_foodpanda_catalog_sheet_rows(outlet)
	search = (filters.get("item_search") or "").strip().lower()
	match_status = filters.get("match_status")
	sync_status = filters.get("sync_status")
	price_status = filters.get("price_status")

	def include(row):
		if search:
			hay = " ".join(
				[
					row.get("foodpanda_sku") or "",
					row.get("foodpanda_barcode") or "",
					row.get("item_code") or "",
					row.get("item_name") or "",
					row.get("erpnext_barcodes") or "",
				]
			).lower()
			if search not in hay:
				return False
		if match_status and match_status != "All":
			status = row.get("match_status") or ""
			if match_status == "Linked" and not status.startswith("Linked"):
				return False
			if match_status == "Not Linked" and status not in {"Not Linked", "No Barcode", "Ambiguous"}:
				return False
			if match_status == "No Barcode" and status != "No Barcode":
				return False
			if match_status == "Ambiguous" and status != "Ambiguous":
				return False
			if match_status == "Match Ready" and status != "Match Ready":
				return False
		if sync_status and sync_status != "All" and (row.get("sync_status") or "") != sync_status:
			return False
		if price_status == "With Price" and not row.get("foodpanda_price"):
			return False
		if price_status == "Missing Price" and row.get("foodpanda_price"):
			return False
		return True

	data = [row for row in data if include(row)]
	return COLUMNS, data
