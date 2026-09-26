from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

from aimatic.price_export.api import require_export_permission


def execute(filters=None):
	require_export_permission()
	filters = frappe._dict(filters or {})
	branch = (filters.get("branch") or "").strip()
	if not branch:
		frappe.throw(_("Branch is required."))
	if not frappe.has_permission("Branch", ptype="read", doc=branch):
		frappe.throw(_("Not permitted to view this branch."), frappe.PermissionError)

	price_list = frappe.db.get_value("Branch", branch, "default_selling_price_list")
	if not price_list:
		frappe.throw(_("Branch {0} has no Selling Price List linked.").format(branch))

	company = frappe.db.get_value("Branch", branch, "company")
	currency = frappe.get_cached_value("Company", company, "default_currency") if company else None
	rows = get_rows(branch, price_list)
	_add_barcodes(rows)
	for row in rows:
		row.stock_on_hand = flt(row.stock_on_hand)
		row.selling_price = flt(row.selling_price)
		row.currency = currency

	return (
		get_columns(currency),
		rows,
		_(
			"Stock on hand is summed across enabled warehouses tagged to the selected branch. "
			"Selling Price is read from the branch's dedicated Selling Price List; missing and zero rates are included."
		),
		None,
		get_report_summary(rows),
	)


def get_rows(branch, price_list):
	# Bin.actual_qty is the current on-hand source of truth. Collapse duplicate
	# Item Price rows and use the highest rate so only zero/missing prices appear.
	return frappe.db.sql(
		"""
		WITH branch_stock AS (
			SELECT b.`item_code`, SUM(b.`actual_qty`) AS `stock_on_hand`,
				GROUP_CONCAT(DISTINCT b.`warehouse` ORDER BY b.`warehouse` SEPARATOR ', ') AS `warehouses`
			FROM `tabBin` b
			INNER JOIN `tabWarehouse` wh ON wh.`name` = b.`warehouse`
			WHERE wh.`custom_branch` = %(branch)s AND wh.`disabled` = 0
			GROUP BY b.`item_code`
			HAVING SUM(b.`actual_qty`) > 0
		), branch_prices AS (
			SELECT ip.`item_code`, ip.`uom`, MAX(ip.`price_list_rate`) AS `selling_price`, COUNT(*) AS `price_rows`
			FROM `tabItem Price` ip
			WHERE ip.`price_list` = %(price_list)s AND ip.`selling` = 1
			GROUP BY ip.`item_code`, ip.`uom`
		)
		SELECT bs.`item_code`, item.`item_name`, item.`item_group`, item.`stock_uom`, bs.`warehouses`,
			bs.`stock_on_hand`, COALESCE(bp.`selling_price`, 0) AS `selling_price`,
			CASE WHEN bp.`item_code` IS NULL THEN 'Missing Price' ELSE 'Zero Price' END AS `price_status`,
			COALESCE(bp.`price_rows`, 0) AS `price_rows`, %(branch)s AS `branch`, %(price_list)s AS `price_list`
		FROM branch_stock bs
		INNER JOIN `tabItem` item ON item.`name` = bs.`item_code`
		LEFT JOIN branch_prices bp ON bp.`item_code` = bs.`item_code` AND bp.`uom` = item.`stock_uom`
		WHERE bp.`item_code` IS NULL OR bp.`selling_price` <= 0
		ORDER BY bs.`item_code`
		""",
		{"branch": branch, "price_list": price_list},
		as_dict=True,
	)


def _add_barcodes(rows):
	item_codes = [row.item_code for row in rows if row.item_code]
	if not item_codes:
		return
	barcodes = {}
	for row in frappe.get_all(
		"Item Barcode",
		filters={"parent": ["in", item_codes]},
		fields=["parent", "barcode", "idx"],
		order_by="parent asc, idx asc",
		ignore_permissions=True,
	):
		if row.barcode:
			barcodes.setdefault(row.parent, []).append(row.barcode)
	for row in rows:
		values = barcodes.get(row.item_code, [])
		row.barcode1 = values[0] if len(values) > 0 else ""
		row.barcode2 = values[1] if len(values) > 1 else ""
		row.barcode3 = values[2] if len(values) > 2 else ""


def get_report_summary(rows):
	return [
		{"label": _("Items"), "value": len(rows), "indicator": "Red", "datatype": "Int"},
		{"label": _("Stock On Hand"), "value": flt(sum(flt(row.stock_on_hand) for row in rows)), "indicator": "Blue", "datatype": "Float"},
		{"label": _("Missing Prices"), "value": sum(row.price_status == "Missing Price" for row in rows), "indicator": "Orange", "datatype": "Int"},
		{"label": _("Zero Prices"), "value": sum(row.price_status == "Zero Price" for row in rows), "indicator": "Red", "datatype": "Int"},
	]


def get_columns(currency=None):
	return [
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 160},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 210},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 130},
		{"label": _("Barcode 1"), "fieldname": "barcode1", "fieldtype": "Data", "width": 130},
		{"label": _("Barcode 2"), "fieldname": "barcode2", "fieldtype": "Data", "width": 130},
		{"label": _("Barcode 3"), "fieldname": "barcode3", "fieldtype": "Data", "width": 130},
		{"label": _("UOM"), "fieldname": "stock_uom", "fieldtype": "Link", "options": "UOM", "width": 75},
		{"label": _("Warehouses"), "fieldname": "warehouses", "fieldtype": "Data", "width": 260},
		{"label": _("Stock On Hand"), "fieldname": "stock_on_hand", "fieldtype": "Float", "width": 115},
		{"label": _("Selling Price"), "fieldname": "selling_price", "fieldtype": "Currency", "options": "currency", "width": 115},
		{"label": _("Price Status"), "fieldname": "price_status", "fieldtype": "Data", "width": 110},
		{"label": _("Price Rows"), "fieldname": "price_rows", "fieldtype": "Int", "width": 85},
		{"label": _("Selling Price List"), "fieldname": "price_list", "fieldtype": "Link", "options": "Price List", "width": 220},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "hidden": 1, "default": currency},
	]
