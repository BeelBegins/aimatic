# Copyright (c) 2026, Ai Matic and contributors
# For license information, please see license.txt

"""Desk report rows for Foodpanda catalog: linked, unmatched, and sync state.

Built from the latest Partner catalog export (or empty if none) joined to
Foodpanda Product / Item Price / Bin — so users manage the catalog in ERPNext
like Branch Price Sheet, not only via Excel downloads.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt

from aimatic.branch_management.utils import get_branch_defaults
from aimatic.foodpanda_integration import catalog as catalog_module
from aimatic.foodpanda_integration import catalog_export
from aimatic.shelf_pricing.utils import get_or_create_branch_foodpanda_price_list


def require_catalog_sheet_permission():
	allowed = {"System Manager", "Buying Price Control"}
	if not allowed.intersection(frappe.get_roles()):
		frappe.throw(
			frappe._("You need the Buying Price Control role to view the Foodpanda Catalog Sheet."),
			frappe.PermissionError,
		)


def _erpnext_barcodes(item_code):
	return ", ".join(frappe.get_all("Item Barcode", filters={"parent": item_code}, pluck="barcode") or [])


def _stock_map(item_codes, branch):
	warehouse = (get_branch_defaults(branch) or {}).get("finished_goods_warehouse")
	if not warehouse or not item_codes:
		return {}
	rows = frappe.db.sql(
		"""
		select item_code, actual_qty, reserved_qty
		from `tabBin`
		where warehouse = %s and item_code in %s
		""",
		(warehouse, tuple(item_codes)),
		as_dict=True,
	)
	out = {}
	for row in rows:
		out[row.item_code] = max(flt(row.actual_qty) - flt(row.reserved_qty), 0)
	return out


def _price_map(item_codes, price_list):
	if not item_codes or not price_list:
		return {}
	rows = frappe.db.sql(
		"""
		select item_code, price_list_rate
		from `tabItem Price`
		where price_list = %s and selling = 1 and item_code in %s
		""",
		(price_list, tuple(item_codes)),
		as_dict=True,
	)
	return {row.item_code: flt(row.price_list_rate, 2) for row in rows}


def get_foodpanda_catalog_sheet_rows(outlet_name):
	"""Return one row per Foodpanda catalog SKU for the outlet."""
	outlet = frappe.get_doc("Foodpanda Outlet", outlet_name)
	branch = outlet.branch
	price_list = get_or_create_branch_foodpanda_price_list(branch)

	remote_products = list(catalog_export.iter_cached_export_products(outlet_name))
	if not remote_products:
		# Fall back to paginated GET so the sheet still works before first export.
		remote_products = list(catalog_module.iter_remote_catalog_products(outlet_name, source="get"))

	barcode_index = catalog_module._build_item_barcode_index()
	product_fields = [
		"name",
		"item_code",
		"foodpanda_product_id",
		"sync_status",
		"last_error",
		"last_synced",
	]
	if frappe.db.has_column("Foodpanda Product", "portal_active"):
		product_fields.append("portal_active")
	products = frappe.get_all(
		"Foodpanda Product",
		filters={"outlet": outlet_name},
		fields=product_fields,
	)
	by_sku = {str(p.foodpanda_product_id).strip(): p for p in products if p.foodpanda_product_id}
	by_item = {p.item_code: p for p in products if p.item_code}

	item_codes = list({p.item_code for p in products if p.item_code})
	# Also resolve barcode matches that are not yet stored as Foodpanda Product.
	resolved_for_stock = set(item_codes)
	match_cache = {}
	for remote in remote_products:
		sku = str(remote.get("sku") or "").strip()
		barcodes = remote.get("barcodes") or []
		unique_items, matched_variant = catalog_module._resolve_items_for_remote_barcodes(
			barcodes, barcode_index
		)
		match_cache[sku] = (unique_items, matched_variant)
		resolved_for_stock.update(unique_items)

	item_codes = list(resolved_for_stock)
	item_names = {}
	if item_codes:
		for row in frappe.db.sql(
			"select name, item_name from `tabItem` where name in %s",
			(item_codes,),
			as_dict=True,
		):
			item_names[row.name] = row.item_name or ""

	prices = _price_map(item_codes, price_list)
	stocks = _stock_map(item_codes, branch)

	rows = []
	seen_skus = set()
	for remote in remote_products:
		sku = str(remote.get("sku") or "").strip()
		if not sku or sku in seen_skus:
			continue
		seen_skus.add(sku)
		barcodes = remote.get("barcodes") or []
		if not isinstance(barcodes, list):
			barcodes = [barcodes] if barcodes else []
		barcodes = [str(b).strip() for b in barcodes if b]
		unique_items, matched_variant = match_cache.get(sku, ([], None))
		product = by_sku.get(sku)

		if not barcodes:
			match_status = "No Barcode"
			item_code = product.item_code if product else ""
		elif len(unique_items) > 1:
			match_status = "Ambiguous"
			item_code = product.item_code if product else ""
		elif len(unique_items) == 1:
			item_code = unique_items[0]
			match_status = "Linked" if product and product.foodpanda_product_id == sku else "Match Ready"
			if not product:
				product = by_item.get(item_code)
		else:
			match_status = "Not Linked"
			item_code = product.item_code if product else ""

		if product and product.sync_status == "Failed":
			# Keep Failed visible even when linked.
			display_sync = "Failed"
		elif product:
			display_sync = product.sync_status or ""
		else:
			display_sync = ""

		our_price = prices.get(item_code) if item_code else None
		stock_qty = stocks.get(item_code, 0) if item_code else None
		remote_active = 1 if remote.get("active") else 0
		if product and getattr(product, "portal_active", None) is not None:
			portal_active = 1 if int(product.portal_active) else 0
		else:
			portal_active = remote_active
		# Match Ready / missing local price: show remote price so the grid is ready to save.
		display_price = (
			our_price
			if our_price is not None
			else (flt(remote.get("price"), 2) if remote.get("price") is not None else None)
		)
		rows.append(
			{
				"match_status": match_status,
				"foodpanda_sku": sku,
				"foodpanda_barcode": ", ".join(barcodes),
				"matched_barcode": matched_variant or "",
				"foodpanda_title": remote.get("title") or "",
				"remote_price": flt(remote.get("price"), 2) if remote.get("price") is not None else None,
				"remote_active": remote_active,
				"portal_active": portal_active,
				"_loaded_portal_active": portal_active,
				"item_code": item_code or "",
				"item_name": item_names.get(item_code, "") if item_code else "",
				"erpnext_barcodes": _erpnext_barcodes(item_code) if item_code else "",
				"foodpanda_price": display_price if display_price is not None else "",
				"_loaded_foodpanda_price": our_price if our_price is not None else None,
				"stock_qty": stock_qty if stock_qty is not None else "",
				"sync_status": display_sync,
				"last_error": (product.last_error or "")[:240] if product else "",
				"foodpanda_product": product.name if product else "",
				"last_synced": product.last_synced if product else "",
				"branch": branch,
				"outlet": outlet_name,
				"price_list": price_list,
			}
		)

	# Include linked Foodpanda Products that were missing from the remote dump.
	for product in products:
		sku = str(product.foodpanda_product_id or "").strip()
		if sku and sku in seen_skus:
			continue
		if not sku:
			continue
		item_code = product.item_code
		our_price = prices.get(item_code)
		portal_active = 1
		if getattr(product, "portal_active", None) is not None:
			portal_active = 1 if int(product.portal_active) else 0
		rows.append(
			{
				"match_status": "Linked (not in latest export)",
				"foodpanda_sku": sku,
				"foodpanda_barcode": "",
				"matched_barcode": "",
				"foodpanda_title": "",
				"remote_price": None,
				"remote_active": "",
				"portal_active": portal_active,
				"_loaded_portal_active": portal_active,
				"item_code": item_code,
				"item_name": item_names.get(item_code, ""),
				"erpnext_barcodes": _erpnext_barcodes(item_code),
				"foodpanda_price": our_price if our_price is not None else "",
				"_loaded_foodpanda_price": our_price if our_price is not None else None,
				"stock_qty": stocks.get(item_code, 0),
				"sync_status": product.sync_status or "",
				"last_error": (product.last_error or "")[:240],
				"foodpanda_product": product.name,
				"last_synced": product.last_synced,
				"branch": branch,
				"outlet": outlet_name,
				"price_list": price_list,
			}
		)

	return rows


def link_match_ready_products(outlet_name, rows=None):
	"""Promote Match Ready barcode matches to Foodpanda Product SKU mappings.

	Does not create products on Foodpanda — only stores the remote catalog SKU
	so later PUT calls address the existing portal row.
	"""
	from frappe.utils import cint

	rows = rows if rows is not None else get_foodpanda_catalog_sheet_rows(outlet_name)
	linked = 0
	item_codes = []
	for row in rows:
		if (row.get("match_status") or "") != "Match Ready":
			continue
		item_code = (row.get("item_code") or "").strip()
		sku = (row.get("foodpanda_sku") or "").strip()
		if not item_code or not sku:
			continue
		product = catalog_module.get_or_create_foodpanda_product(item_code, outlet_name)
		updates = {}
		if product.foodpanda_product_id != sku:
			updates["foodpanda_product_id"] = sku
			updates["sync_status"] = "Pending"
			updates["last_error"] = ""
		if (
			frappe.db.has_column("Foodpanda Product", "portal_active")
			and getattr(product, "portal_active", None) is None
		):
			updates["portal_active"] = cint(
				row.get("portal_active") if row.get("portal_active") != "" else row.get("remote_active") or 1
			)
		if updates:
			product.db_set(updates)
			linked += 1
		item_codes.append(item_code)
	return {"linked": linked, "item_codes": list(dict.fromkeys(item_codes))}


def apply_portal_active_updates(outlet_name, active_updates):
	"""Persist Catalog Sheet portal_active edits onto Foodpanda Product."""
	from frappe.utils import cint

	if isinstance(active_updates, str):
		active_updates = frappe.parse_json(active_updates)
	if not active_updates:
		return {"updated": 0, "item_codes": []}
	if not frappe.db.has_column("Foodpanda Product", "portal_active"):
		frappe.throw(
			frappe._("Portal Active field is not installed yet. Run bench migrate on this site, then retry.")
		)

	updated = 0
	item_codes = []
	for row in active_updates:
		if not isinstance(row, dict):
			continue
		item_code = str(row.get("item_code") or "").strip()
		sku = str(row.get("foodpanda_sku") or "").strip()
		if not item_code:
			continue
		product = catalog_module.get_or_create_foodpanda_product(item_code, outlet_name)
		values = {"portal_active": 1 if cint(row.get("portal_active")) else 0}
		if sku and product.foodpanda_product_id != sku:
			values["foodpanda_product_id"] = sku
			values["sync_status"] = "Pending"
			values["last_error"] = ""
		elif cint(product.portal_active) != values["portal_active"]:
			values["sync_status"] = "Pending"
		product.db_set(values)
		updated += 1
		item_codes.append(item_code)
	return {"updated": updated, "item_codes": list(dict.fromkeys(item_codes))}


def seed_missing_foodpanda_prices_from_remote(outlet_name, rows=None):
	"""For Match Ready rows with no local Foodpanda price, copy remote price."""
	rows = rows if rows is not None else get_foodpanda_catalog_sheet_rows(outlet_name)
	updates = []
	for row in rows:
		if (row.get("match_status") or "") != "Match Ready":
			continue
		item_code = (row.get("item_code") or "").strip()
		if not item_code:
			continue
		if row.get("_loaded_foodpanda_price") is not None:
			continue
		remote_price = flt(row.get("remote_price") or row.get("foodpanda_price"))
		if remote_price <= 0:
			continue
		updates.append(
			{
				"item_code": item_code,
				"price": remote_price,
				"old_price": 0,
			}
		)
	return updates


@frappe.whitelist()
def apply_catalog_sheet_updates(
	outlet,
	price_updates=None,
	active_updates=None,
	link_match_ready=1,
	seed_remote_prices=1,
	push=1,
	push_item_codes=None,
):
	"""Save sheet edits, link Match Ready SKUs, and optionally push to Foodpanda.

	Used by Foodpanda Catalog Sheet so Match Ready rows become Linked with
	local Foodpanda prices and portal active/inactive in one Desk action.
	"""
	from frappe.utils import cint

	from aimatic.price_export.api import save_foodpanda_grid_prices

	require_catalog_sheet_permission()
	if not outlet or not frappe.db.exists("Foodpanda Outlet", outlet):
		frappe.throw(frappe._("Foodpanda Outlet not found."))
	if not frappe.has_permission("Foodpanda Outlet", ptype="write", doc=outlet):
		frappe.throw(frappe._("Not permitted to update this outlet."), frappe.PermissionError)

	outlet_doc = frappe.get_doc("Foodpanda Outlet", outlet)
	branch = outlet_doc.branch
	if not branch:
		frappe.throw(frappe._("Outlet has no branch."))

	sheet_rows = get_foodpanda_catalog_sheet_rows(outlet)
	push_codes = []

	if cint(link_match_ready):
		link_result = link_match_ready_products(outlet, sheet_rows)
	else:
		link_result = {"linked": 0, "item_codes": []}
	push_codes.extend(link_result.get("item_codes") or [])

	if isinstance(price_updates, str):
		price_updates = frappe.parse_json(price_updates)
	price_updates = list(price_updates or [])
	if cint(seed_remote_prices):
		existing = {str(row.get("item_code")) for row in price_updates if isinstance(row, dict)}
		for row in seed_missing_foodpanda_prices_from_remote(outlet, sheet_rows):
			if row["item_code"] not in existing:
				price_updates.append(row)
				existing.add(row["item_code"])

	price_result = {"created": 0, "updated": 0, "unchanged": 0}
	if price_updates:
		price_result = save_foodpanda_grid_prices(branch, price_updates)
		push_codes.extend(
			str(row.get("item_code")).strip()
			for row in price_updates
			if isinstance(row, dict) and row.get("item_code")
		)

	active_result = apply_portal_active_updates(outlet, active_updates)
	push_codes.extend(active_result.get("item_codes") or [])

	if isinstance(push_item_codes, str):
		push_item_codes = frappe.parse_json(push_item_codes)
	if push_item_codes:
		push_codes.extend(str(code).strip() for code in push_item_codes if str(code).strip())

	push_codes = list(dict.fromkeys([code for code in push_codes if code]))
	job = None
	if cint(push):
		if not outlet_doc.catalog_sync_enabled:
			frappe.throw(frappe._("Catalog sync is not enabled for this outlet."))
		# Only push items touched by this Apply (linked / price / active).
		# Full-catalog push remains on "Update prices & stock".
		if push_codes:
			job = catalog_module.start_bulk_push(outlet, item_codes=push_codes)

	mapped_count = frappe.db.count(
		"Foodpanda Product",
		{"outlet": outlet, "foodpanda_product_id": ("is", "set")},
	)
	outlet_doc.db_set({"mapped_sku_count": mapped_count})

	return {
		"linked": link_result.get("linked") or 0,
		"prices": price_result,
		"active_updated": active_result.get("updated") or 0,
		"push_item_count": len(push_codes),
		"job": job,
	}
