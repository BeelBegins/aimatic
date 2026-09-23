"""Selling Price Update engine — manual filters or PR / Stock Transfer Note source."""

from __future__ import annotations

import json
import math

import frappe
from frappe import _
from frappe.utils import flt, getdate

from aimatic.shelf_pricing.api import (
	_require_price_update_permission,
	_update_global_item_mrp,
)
from aimatic.shelf_pricing.utils import (
	get_or_create_branch_foodpanda_price_list,
	get_or_create_branch_price_list,
	get_selling_item_price_rate,
	upsert_item_price,
)

_MAX_ROWS = 1500
_CHUNK = 500
_MODES = ("Store Selling", "Foodpanda")
SOURCE_PURCHASE_RECEIPT = "Purchase Receipt"
SOURCE_STOCK_TRANSFER = "Stock Entry"
_SOURCE_DOCTYPES = (SOURCE_PURCHASE_RECEIPT, SOURCE_STOCK_TRANSFER)


def _chunks(values, size=_CHUNK):
	for index in range(0, len(values), size):
		yield values[index : index + size]


def _parse_json(value):
	if isinstance(value, str):
		return json.loads(value)
	return value


def _normalize_source_doctype(source_doctype):
	source_doctype = (source_doctype or "").strip()
	return source_doctype or None


def _resolve_price_list(mode, branch, *, create=False):
	"""Always the branch-owned list for this mode — never user-selected."""
	if not branch:
		frappe.throw(_("Branch is required."))

	field = "default_foodpanda_price_list" if mode == "Foodpanda" else "default_selling_price_list"
	existing = frappe.db.get_value("Branch", branch, field)
	if existing:
		return existing
	if not create:
		return None
	if mode == "Foodpanda":
		return get_or_create_branch_foodpanda_price_list(branch)
	return get_or_create_branch_price_list(branch)


def _resolve_selling_price_list_for_branch(branch, *, create=False):
	existing = frappe.db.get_value("Branch", branch, "default_selling_price_list")
	if existing:
		return existing
	if not create:
		return None
	return get_or_create_branch_price_list(branch)


def _get_submitted_source(source_doctype, source_name):
	source_doctype = _normalize_source_doctype(source_doctype)
	source_name = (source_name or "").strip()
	if not source_doctype or not source_name:
		return None, None
	if source_doctype not in _SOURCE_DOCTYPES:
		frappe.throw(_("Source must be Purchase Receipt or Stock Transfer Note."))

	doc = frappe.get_doc(source_doctype, source_name)
	if doc.docstatus != 1:
		frappe.throw(_("{0} must be submitted first.").format(source_doctype))
	if source_doctype == SOURCE_STOCK_TRANSFER and doc.purpose != "Material Transfer":
		frappe.throw(_("Stock Transfer Note must be a Material Transfer Stock Entry."))
	if source_doctype == SOURCE_PURCHASE_RECEIPT and doc.is_return:
		frappe.throw(_("Purchase Return receipts cannot drive a price update."))
	return source_doctype, doc


def _stock_transfer_target_branch(doc):
	"""Receiving branch of a Material Transfer.

	Stock Entry.branch is the *sending* branch (defaulted from the source
	warehouse/user), so prices must follow the target warehouse's branch.
	"""
	branches = []
	for row in doc.items:
		if not row.t_warehouse:
			continue
		branch = frappe.db.get_value("Warehouse", row.t_warehouse, "custom_branch")
		if branch and branch not in branches:
			branches.append(branch)
	if not branches:
		frappe.throw(
			_("Target warehouse of {0} has no Branch, so its price lists cannot be resolved.").format(doc.name)
		)
	if len(branches) > 1:
		frappe.throw(
			_("{0} transfers to more than one branch ({1}); split it before updating prices.").format(
				doc.name, ", ".join(branches)
			)
		)
	return branches[0]


def _resolve_update_branch(source_doctype, source_doc, branch):
	"""Branch whose price lists a source document updates."""
	if source_doctype == SOURCE_STOCK_TRANSFER:
		return _stock_transfer_target_branch(source_doc)
	return branch


@frappe.whitelist()
def get_branch_console_context(branch):
	"""Branch → warehouse + linked price lists for the console header."""
	_require_price_update_permission()
	branch = (branch or "").strip()
	if not branch:
		return {}

	from aimatic.branch_management.utils import get_branch_defaults

	defaults = get_branch_defaults(branch)
	return {
		"branch": branch,
		"warehouse": defaults.get("finished_goods_warehouse"),
		"selling_price_list": frappe.db.get_value("Branch", branch, "default_selling_price_list"),
		"foodpanda_price_list": frappe.db.get_value("Branch", branch, "default_foodpanda_price_list"),
	}


@frappe.whitelist()
def get_vendor_source_documents(branch, vendor=None, source_doctype=None, limit=40):
	"""Latest submitted PR / Material Transfer docs for branch (+ vendor for PR)."""
	_require_price_update_permission()
	branch = (branch or "").strip()
	vendor = (vendor or "").strip() or None
	source_doctype = _normalize_source_doctype(source_doctype)
	if not branch:
		frappe.throw(_("Select a Branch first."))

	limit = min(max(int(limit or 40), 1), 100)
	documents = []

	if source_doctype in (None, SOURCE_PURCHASE_RECEIPT):
		if not vendor:
			frappe.throw(_("Select a Vendor for Purchase Receipt sources."))
		for row in frappe.get_all(
			"Purchase Receipt",
			filters={
				"docstatus": 1,
				"is_return": 0,
				"branch": branch,
				"supplier": vendor,
			},
			fields=["name", "posting_date", "modified"],
			order_by="posting_date desc, modified desc",
			limit=limit,
			ignore_permissions=True,
		):
			documents.append(
				{
					"source_doctype": SOURCE_PURCHASE_RECEIPT,
					"source_name": row.name,
					"posting_date": str(row.posting_date or ""),
					"label": f"{row.name} · {row.posting_date or ''}",
				}
			)

	if source_doctype == SOURCE_STOCK_TRANSFER:
		# Stock Entry.branch is the sender; list transfers *received* by this branch.
		received = frappe.db.sql_list(
			"""
			select distinct sed.parent
			from `tabStock Entry Detail` sed
			join `tabWarehouse` wh on wh.name = sed.t_warehouse
			where sed.docstatus = 1 and wh.custom_branch = %s
			""",
			branch,
		)
		for row in frappe.get_all(
			"Stock Entry",
			filters={
				"docstatus": 1,
				"purpose": "Material Transfer",
				"name": ["in", received or [""]],
			},
			fields=["name", "posting_date", "modified"],
			order_by="posting_date desc, modified desc",
			limit=limit,
			ignore_permissions=True,
		):
			documents.append(
				{
					"source_doctype": SOURCE_STOCK_TRANSFER,
					"source_name": row.name,
					"posting_date": str(row.posting_date or ""),
					"label": f"{row.name} · {row.posting_date or ''}",
				}
			)

	return {"documents": documents}


@frappe.whitelist()
def get_source_document_context(source_doctype, source_name):
	"""Prefill console filters from a submitted PR or Material Transfer."""
	_require_price_update_permission()
	source_doctype, doc = _get_submitted_source(source_doctype, source_name)
	if not doc:
		return {}

	context = {
		"source_doctype": source_doctype,
		"source_name": doc.name,
		"branch": (doc.get("branch") or "").strip() or None,
		"vendor": None,
		"warehouse": None,
		"company": doc.get("company"),
		"posting_date": str(doc.get("posting_date") or ""),
	}

	if source_doctype == SOURCE_PURCHASE_RECEIPT:
		context["vendor"] = doc.get("supplier")
		context["warehouse"] = doc.get("set_warehouse")
	elif source_doctype == SOURCE_STOCK_TRANSFER:
		warehouses = [row.t_warehouse for row in doc.items if row.t_warehouse]
		if warehouses:
			context["warehouse"] = warehouses[0]
		context["branch"] = _stock_transfer_target_branch(doc)

	return context


def _fetch_barcode_map(item_codes):
	barcodes = {}
	for chunk in _chunks(item_codes):
		for row in frappe.get_all(
			"Item Barcode",
			filters={"parent": ("in", chunk)},
			fields=["parent", "barcode", "idx"],
			order_by="idx asc",
		):
			if row.barcode:
				barcodes.setdefault(row.parent, []).append(row.barcode)
	return barcodes


def _fetch_item_meta(item_codes):
	meta = {}
	for chunk in _chunks(item_codes):
		for row in frappe.get_all(
			"Item",
			filters={"name": ("in", chunk)},
			fields=["name", "item_name", "stock_uom", "custom_mrp", "custom_latest_price_incl_taxes"],
		):
			meta[row.name] = row
	return meta


def _gm_percent(sale, cost):
	sale = flt(sale)
	if sale <= 0:
		return 0.0
	return flt(((sale - flt(cost)) / sale) * 100, 2)


def _sale_from_gm(cost, gm_percent):
	cost = flt(cost)
	gm = flt(gm_percent)
	if cost <= 0 or gm <= 0 or gm >= 100:
		return None
	raw = cost / (1 - gm / 100)
	return int(math.ceil(raw / 5.0) * 5)


def _source_item_lines(source_doctype, doc):
	if source_doctype == SOURCE_PURCHASE_RECEIPT:
		for row in doc.items:
			if not row.item_code:
				continue
			yield {
				"item_code": row.item_code,
				"uom": row.uom,
				"cost": flt(row.custom_price_after_taxes),
				"mrp": flt(row.custom_mrp),
				"proposed_store": flt(row.custom_shelf_price),
				"proposed_fp": flt(row.custom_fp_price),
			}
		return

	for row in doc.items:
		if not row.item_code or not row.t_warehouse:
			continue
		cost = flt(row.basic_rate) or flt(getattr(row, "valuation_rate", 0))
		yield {
			"item_code": row.item_code,
			"uom": row.uom,
			"cost": cost,
			"mrp": 0,
			"proposed_store": 0,
			"proposed_fp": 0,
		}


def _build_grid_row(line, mode, target_price_list, selling_list, meta, barcodes):
	item = meta.get(line["item_code"]) or frappe._dict()
	bc = barcodes.get(line["item_code"]) or []
	uom = line["uom"] or item.stock_uom
	cost = flt(line["cost"]) or flt(item.custom_latest_price_incl_taxes)
	mrp = flt(line["mrp"]) or flt(item.custom_mrp)
	current = get_selling_item_price_rate(line["item_code"], target_price_list, uom=uom)
	store_selling = None
	if mode == "Foodpanda" and selling_list:
		store_selling = get_selling_item_price_rate(line["item_code"], selling_list, uom=uom)

	if mode == "Foodpanda":
		proposed = flt(line["proposed_fp"])
	else:
		proposed = flt(line["proposed_store"])

	new_price = proposed if proposed > 0 else current

	return {
		"item_code": line["item_code"],
		"item_name": item.item_name,
		"barcode1": bc[0] if len(bc) > 0 else "",
		"barcode2": bc[1] if len(bc) > 1 else "",
		"barcode3": bc[2] if len(bc) > 2 else "",
		"uom": uom,
		"latest_price_incl_taxes": cost,
		"mrp": mrp,
		"current_selling_price": current,
		"store_selling_price": store_selling,
		"new_selling_price": new_price,
		"current_gm_percent": _gm_percent(new_price, cost),
		"item_price": frappe.db.get_value(
			"Item Price",
			{
				"item_code": line["item_code"],
				"price_list": target_price_list,
				"selling": 1,
				"uom": uom,
			},
			"name",
		),
	}


def _rows_from_source(source_doctype, doc, mode, branch, target_price_list):
	selling_list = None
	if mode == "Foodpanda" and branch:
		selling_list = _resolve_selling_price_list_for_branch(branch, create=False)

	lines = list(_source_item_lines(source_doctype, doc))
	if len(lines) > _MAX_ROWS:
		frappe.throw(_("Source document has more than {0} item rows.").format(_MAX_ROWS))

	item_codes = list({line["item_code"] for line in lines})
	meta = _fetch_item_meta(item_codes)
	barcodes = _fetch_barcode_map(item_codes)

	rows = []
	for line in lines:
		rows.append(_build_grid_row(line, mode, target_price_list, selling_list, meta, barcodes))
	return rows, False


@frappe.whitelist()
def get_selling_price_update_rows(
	mode,
	branch=None,
	source_doctype=None,
	source_name=None,
):
	"""Load grid rows from a submitted PR or Material Transfer source document."""
	_require_price_update_permission()
	mode = (mode or "").strip()
	if mode not in _MODES:
		frappe.throw(_("Mode must be Store Selling or Foodpanda."))

	branch = (branch or "").strip()
	if not branch:
		frappe.throw(_("Select a Branch."))

	source_doctype, source_doc = _get_submitted_source(source_doctype, source_name)
	if not source_doc:
		frappe.throw(_("Select a source Purchase Receipt or Stock Transfer Note."))

	branch = _resolve_update_branch(source_doctype, source_doc, branch)
	ctx = get_source_document_context(source_doctype, source_doc.name)

	target_price_list = _resolve_price_list(mode, branch, create=False)
	if not target_price_list:
		frappe.throw(
			_("Branch {0} has no {1} Price List linked yet. Set it on the Branch before loading.").format(
				branch, _("Foodpanda") if mode == "Foodpanda" else _("Selling")
			)
		)

	rows, truncated = _rows_from_source(source_doctype, source_doc, mode, branch, target_price_list)

	return {
		"mode": mode,
		"branch": branch,
		"warehouse": ctx.get("warehouse"),
		"vendor": ctx.get("vendor"),
		"price_list": target_price_list,
		"source_doctype": source_doctype,
		"source_name": source_doc.name,
		"rows": rows,
		"truncated": truncated,
		"row_count": len(rows),
		"max_rows": _MAX_ROWS,
	}


def _mark_pr_status(source_doc, mode):
	if mode == "Foodpanda":
		frappe.db.set_value(
			"Purchase Receipt", source_doc.name, "custom_foodpanda_price_update_status", "Updated"
		)
	else:
		frappe.db.set_value(
			"Purchase Receipt", source_doc.name, "custom_branch_price_update_status", "Updated"
		)


@frappe.whitelist()
def apply_selling_price_updates(mode, branch, rows, source_doctype=None, source_name=None):
	"""Apply edited New Selling Price rows to the resolved branch list."""
	_require_price_update_permission()
	mode = (mode or "").strip()
	if mode not in _MODES:
		frappe.throw(_("Mode must be Store Selling or Foodpanda."))

	branch = (branch or "").strip()
	if not branch:
		frappe.throw(_("Branch is required to apply price updates."))

	source_doctype, source_doc = _get_submitted_source(source_doctype, source_name)
	branch = _resolve_update_branch(source_doctype, source_doc, branch)
	audit_ref = source_doc.name if source_doctype == SOURCE_PURCHASE_RECEIPT and source_doc else None
	posting_date = getdate(source_doc.posting_date) if source_doc else None

	rows = _parse_json(rows) or []
	if not isinstance(rows, list):
		frappe.throw(_("Rows must be a list."))
	if len(rows) > _MAX_ROWS:
		frappe.throw(_("A maximum of {0} rows can be applied at once.").format(_MAX_ROWS))

	target_price_list = _resolve_price_list(mode, branch, create=False)
	if not target_price_list:
		frappe.throw(
			_(
				"Branch {0} has no {1} Price List linked yet. Set it on the Branch before applying."
			).format(branch, _("Foodpanda") if mode == "Foodpanda" else _("Selling"))
		)
	selling_list = (
		_resolve_selling_price_list_for_branch(branch, create=False) if mode == "Foodpanda" else None
	)
	if mode == "Foodpanda" and not selling_list:
		frappe.throw(
			_("Branch {0} has no Selling Price List linked — set Store Selling prices before Foodpanda.").format(
				branch
			)
		)

	updated = skipped = 0
	errors = []

	for index, row in enumerate(rows, start=1):
		if not isinstance(row, dict):
			errors.append(_("Row {0}: invalid payload.").format(index))
			continue

		item_code = (row.get("item_code") or "").strip()
		uom = (row.get("uom") or "").strip() or None
		new_price = flt(row.get("new_selling_price"))
		if not item_code:
			errors.append(_("Row {0}: Item is required.").format(index))
			continue
		if new_price <= 0:
			skipped += 1
			continue

		mrp = flt(row.get("mrp"))
		if mrp <= 0:
			mrp = flt(frappe.db.get_value("Item", item_code, "custom_mrp"))

		if mode == "Foodpanda":
			store_selling = get_selling_item_price_rate(item_code, selling_list, uom=uom)
			if mrp > 0 and new_price < mrp:
				errors.append(
					_("{0} ({1}): Foodpanda price {2} cannot be less than MRP {3}.").format(
						item_code, uom or "", new_price, mrp
					)
				)
				continue
			if store_selling > 0 and new_price < store_selling:
				errors.append(
					_("{0} ({1}): Foodpanda price {2} cannot be less than Store Selling {3}.").format(
						item_code, uom or "", new_price, store_selling
					)
				)
				continue
			upsert_item_price(
				item_code,
				target_price_list,
				purchase_receipt=audit_ref,
				branch=branch,
				rate=new_price,
				mrp=new_price,
				uom=uom,
			)
		else:
			cost = flt(row.get("latest_price_incl_taxes"))
			if cost > 0 and new_price < cost:
				errors.append(
					_("{0} ({1}): Selling price {2} cannot be less than cost {3}.").format(
						item_code, uom or "", new_price, cost
					)
				)
				continue
			upsert_item_price(
				item_code,
				target_price_list,
				purchase_receipt=audit_ref,
				branch=branch,
				rate=new_price,
				mrp=mrp or None,
				uom=uom,
			)
			if audit_ref and mrp > 0 and posting_date:
				_update_global_item_mrp(item_code, mrp, audit_ref, posting_date)
		updated += 1

	if errors and not updated:
		frappe.throw("<br>".join(errors[:30]))

	if source_doc and source_doctype == SOURCE_PURCHASE_RECEIPT and updated:
		_mark_pr_status(source_doc, mode)

	return {
		"status": "Updated",
		"mode": mode,
		"branch": branch,
		"price_list": target_price_list,
		"source_doctype": source_doctype,
		"source_name": source_doc.name if source_doc else None,
		"updated": updated,
		"skipped": skipped,
		"errors": errors[:30],
		"error_count": len(errors),
	}


@frappe.whitelist()
def preview_sale_from_gm(cost, gm_percent):
	"""Helper for the GM % column — whole-rupee selling price from cost + GM%."""
	_require_price_update_permission()
	price = _sale_from_gm(cost, gm_percent)
	return {"new_selling_price": price, "ok": price is not None}
