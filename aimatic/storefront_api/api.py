"""Read-only catalog/price/stock feed for an external online shopping website.

This is a separate integration surface from aimatic.shopping (our own
OAuth2/PKCE Customer Shopping product where checkout happens inside Frappe).
Here the external site owns its own database/checkout entirely and only
pulls master data on a schedule. Auth is plain standard Frappe API Key/Secret
token auth for a dedicated "Storefront Integration" role user - see
require_storefront_role() in utils.py for the authorization boundary, and
API.md in this directory for the full external-facing contract.
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import flt, now_datetime

from aimatic.storefront_api.utils import (
	envelope,
	get_branch_warehouses,
	paginate,
	require_storefront_role,
	resolve_branch_price_list,
)

_SYNC_STATUS_DOCTYPES = {
	"Item": "Item",
	"Item Price": "Item Price",
	"Bin": "Bin",
}


@frappe.whitelist()
@rate_limit(limit=120, seconds=60)
def get_sync_status():
	"""Cheap poll-first endpoint. Call this before the heavier list endpoints
	below and only fetch a resource whose max_modified moved since your last
	successful sync.
	"""
	require_storefront_role()
	max_modified = {}
	for label, doctype in _SYNC_STATUS_DOCTYPES.items():
		max_modified[label] = frappe.db.sql(
			f"SELECT MAX(modified) FROM `tab{doctype}`",
		)[0][0]
	return {"server_time": now_datetime(), "max_modified": max_modified}


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def get_branches():
	require_storefront_role()
	branches = frappe.get_all(
		"Branch",
		fields=["name", "branch", "company", "cost_center", "default_selling_price_list"],
	)
	for row in branches:
		warehouses = get_branch_warehouses(row.name)
		finished_goods, rejected = frappe.db.get_value(
			"Branch", row.name, ["finished_goods_warehouse", "rejected_warehouse"]
		)
		for w in warehouses:
			w["is_default"] = w.name == finished_goods
			w["is_rejected"] = w.name == rejected
		row["warehouses"] = warehouses
	return branches


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def get_item_groups():
	require_storefront_role()
	return frappe.get_all(
		"Item Group",
		fields=["name", "item_group_name", "parent_item_group", "is_group", "lft", "rgt"],
		order_by="lft asc",
	)


@frappe.whitelist()
@rate_limit(limit=60, seconds=60)
def get_items(modified_after=None, limit_start=0, limit_page_length=500):
	require_storefront_role()
	start, page_length = paginate(limit_start, limit_page_length)

	filters = {"is_sales_item": 1}
	conditions = ""
	params = {"limit": page_length + 1, "offset": start}
	if modified_after:
		conditions = "AND modified > %(modified_after)s"
		params["modified_after"] = modified_after

	rows = frappe.db.sql(
		f"""
		SELECT
			name AS item_code, item_name, description, item_group, brand,
			stock_uom, image, disabled, custom_mrp, modified
		FROM `tabItem`
		WHERE is_sales_item = 1
		{conditions}
		ORDER BY modified ASC, name ASC
		LIMIT %(limit)s OFFSET %(offset)s
		""",
		params,
		as_dict=True,
	)

	result = envelope(rows, start, page_length)
	item_codes = [r.item_code for r in result["rows"]]
	barcodes_by_item = {}
	if item_codes:
		barcode_rows = frappe.get_all(
			"Item Barcode",
			filters={"parent": ["in", item_codes]},
			fields=["parent", "barcode", "barcode_type", "uom"],
			order_by="parent asc, idx asc",
		)
		for b in barcode_rows:
			barcodes_by_item.setdefault(b.parent, []).append(
				{"barcode": b.barcode, "barcode_type": b.barcode_type, "uom": b.uom}
			)
	for row in result["rows"]:
		row["barcodes"] = barcodes_by_item.get(row.item_code, [])

	return result


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def get_deleted_items(since):
	require_storefront_role()
	if not since:
		frappe.throw(_("since is required"), frappe.ValidationError)
	rows = frappe.get_all(
		"Deleted Document",
		filters={"deleted_doctype": "Item", "creation": [">", since]},
		fields=["deleted_name AS item_code", "creation AS deleted_at"],
		order_by="creation asc",
	)
	return rows


@frappe.whitelist()
@rate_limit(limit=60, seconds=60)
def get_price_list(branch, modified_after=None, limit_start=0, limit_page_length=500):
	require_storefront_role()
	start, page_length = paginate(limit_start, limit_page_length)
	price_list = resolve_branch_price_list(branch)

	conditions = ""
	params = {
		"price_list": price_list,
		"limit": page_length + 1,
		"offset": start,
	}
	if modified_after:
		conditions = "AND modified > %(modified_after)s"
		params["modified_after"] = modified_after

	rows = frappe.db.sql(
		f"""
		SELECT
			item_code, price_list_rate, currency, custom_mrp,
			valid_from, valid_upto, modified
		FROM `tabItem Price`
		WHERE price_list = %(price_list)s AND selling = 1
			AND (valid_from IS NULL OR valid_from <= CURDATE())
			AND (valid_upto IS NULL OR valid_upto >= CURDATE())
		{conditions}
		ORDER BY modified ASC, item_code ASC
		LIMIT %(limit)s OFFSET %(offset)s
		""",
		params,
		as_dict=True,
	)

	result = envelope(rows, start, page_length)
	result["price_list"] = price_list
	return result


@frappe.whitelist()
@rate_limit(limit=60, seconds=60)
def get_stock_levels(branch=None, warehouse=None, modified_after=None, limit_start=0, limit_page_length=500):
	require_storefront_role()
	start, page_length = paginate(limit_start, limit_page_length)

	if warehouse:
		warehouses = [warehouse]
	elif branch:
		warehouses = [w.name for w in get_branch_warehouses(branch)]
	else:
		frappe.throw(_("branch or warehouse is required"), frappe.ValidationError)

	if not warehouses:
		return {"rows": [], "next_start": None, "has_more": False}

	conditions = ""
	params = {
		"warehouses": warehouses,
		"limit": page_length + 1,
		"offset": start,
	}
	if modified_after:
		conditions = "AND modified > %(modified_after)s"
		params["modified_after"] = modified_after

	rows = frappe.db.sql(
		f"""
		SELECT item_code, warehouse, actual_qty, reserved_qty, modified
		FROM `tabBin`
		WHERE warehouse IN %(warehouses)s
		{conditions}
		ORDER BY modified ASC, item_code ASC
		LIMIT %(limit)s OFFSET %(offset)s
		""",
		params,
		as_dict=True,
	)
	for row in rows:
		row["available_qty"] = max(flt(row.actual_qty) - flt(row.reserved_qty), 0)

	return envelope(rows, start, page_length)
