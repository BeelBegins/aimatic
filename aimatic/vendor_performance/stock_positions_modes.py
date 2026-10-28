"""All-vendors and barcode modes for Vendor Stock Positions.

Imported lazily from whitelist wrappers in api.py to avoid circular imports.
"""

from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt

from aimatic.vendor_performance import api as vp


def _supplier_name_map(suppliers: list[str]) -> dict[str, str]:
	if not suppliers:
		return {}
	rows = frappe.get_all(
		"Supplier",
		filters={"name": ["in", suppliers]},
		fields=["name", "supplier_name"],
	)
	return {row.name: (row.supplier_name or row.name) for row in rows}


def _stock_definition_notes(stock_basis: str) -> dict[str, str]:
	notes = {
		"stock_incl_tax_note": _(
			"Stock Value (Incl Taxes) estimates tax-on-hand: prefer qty × last purchase rate incl tax from this "
			"supplier; else scale Bin cost by that purchase tax factor; else apply Item FBR tax rate to Bin cost."
		),
		"cogs_definition_note": _(
			"Cost of goods sold is the cost-basis value (from stock ledger valuation) of supplier-linked SKUs "
			"consumed by submitted sales in the selected window - not the retail sales revenue."
		),
	}
	if stock_basis == vp._STOCK_BASIS_BIN:
		notes["stock_definition_note"] = _(
			"Stock position shows current stock of supplier-linked SKUs valued at cost (Bin stock value), "
			"not exact remaining units by supplier origin. Shared SKUs repeat Bin qty under each linked vendor."
		)
	elif stock_basis == vp._STOCK_BASIS_FIFO:
		notes["stock_definition_note"] = _(
			"Stock rows use FIFO vendor-origin attribution from the stock ledger (remaining purchase layers "
			"assigned to each supplier). Bin warehouse qty is not used for Stock Qty / Stock Value in this mode."
		)
	else:
		notes["stock_definition_note"] = _(
			"Bin columns show linked-SKU warehouse stock at cost (may repeat across vendors). "
			"Origin Stock Qty/Value are FIFO remaining layers attributed to that supplier."
		)
	return notes


def _attach_barcodes(rows: list[dict], item_codes: list[str]) -> None:
	barcode_map = vp._fetch_barcode_map(item_codes)
	for row in rows:
		bc = barcode_map.get(row["item_code"]) or []
		row["barcode1"] = bc[0] if len(bc) > 0 else ""
		row["barcode2"] = bc[1] if len(bc) > 1 else ""


def _attach_origin_fields(row: dict, origin: dict | None = None) -> None:
	origin = origin or {}
	row["origin_stock_qty"] = flt(origin.get("origin_stock_qty"))
	row["origin_stock_value"] = flt(origin.get("origin_stock_value"))
	row["last_origin_doc"] = origin.get("last_origin_doc")
	row["last_origin_doctype"] = origin.get("last_origin_doctype")
	row["last_origin_date"] = origin.get("last_origin_date")


def _empty_bin_fields(row: dict, group_by_warehouse: bool) -> None:
	row["stock_qty"] = 0.0
	row["stock_value"] = 0.0
	row["stock_value_incl_tax"] = 0.0
	row["stock_tax_amount"] = 0.0
	row["stock_tax_basis"] = "at_cost"
	if group_by_warehouse:
		row.setdefault("warehouse", "")
		row.setdefault("branch", "")


def _enrich_rows_by_supplier(
	rows: list[dict],
	*,
	company: str,
	date_from,
	date_to,
	branch: str | None,
	warehouse_list: list[str] | None,
	principal: str | None,
) -> list[dict]:
	if not rows:
		return rows

	by_supplier: dict[str, list[dict]] = defaultdict(list)
	for row in rows:
		by_supplier[row["supplier"]].append(row)

	enriched: list[dict] = []
	for supplier, supplier_rows in by_supplier.items():
		vp._enrich_stock_position_rows(
			supplier_rows,
			supplier=supplier,
			company=company,
			date_from=date_from,
			date_to=date_to,
			branch=branch,
			warehouse_list=warehouse_list,
			principal=principal,
		)
		enriched.extend(supplier_rows)
	return enriched


def _fan_out_bin_rows(
	stock_rows: list[dict],
	links_by_item: dict[str, list[str]],
	supplier_names: dict[str, str],
	group_by_warehouse: bool,
) -> list[dict]:
	fanout = []
	for row in stock_rows:
		suppliers = links_by_item.get(row["item_code"]) or []
		for supplier in suppliers:
			entry = dict(row)
			entry["supplier"] = supplier
			entry["supplier_name"] = supplier_names.get(supplier) or supplier
			_attach_origin_fields(entry)
			fanout.append(entry)
	return fanout


def _build_fifo_only_rows(
	origin_map: dict[tuple[str, str], dict],
	links: set[tuple[str, str]],
	supplier_names: dict[str, str],
	item_names: dict[str, str],
	group_by_warehouse: bool,
) -> list[dict]:
	rows = []
	for (supplier, item_code), origin in origin_map.items():
		if links and (supplier, item_code) not in links:
			continue
		if flt(origin.get("origin_stock_qty")) <= 0.0001 and flt(origin.get("origin_stock_value")) <= 0.0001:
			continue
		entry = {
			"supplier": supplier,
			"supplier_name": supplier_names.get(supplier) or supplier,
			"item_code": item_code,
			"item_name": item_names.get(item_code) or item_code,
		}
		_empty_bin_fields(entry, group_by_warehouse)
		_attach_origin_fields(entry, origin)
		# FIFO-only mode surfaces origin qty/value in the primary stock columns too.
		entry["stock_qty"] = flt(origin.get("origin_stock_qty"))
		entry["stock_value"] = flt(origin.get("origin_stock_value"))
		rows.append(entry)
	rows.sort(
		key=lambda r: (
			-(flt(r.get("origin_stock_value")) or flt(r.get("stock_value"))),
			-(flt(r.get("origin_stock_qty")) or flt(r.get("stock_qty"))),
			r.get("supplier") or "",
			r.get("item_code") or "",
		)
	)
	return rows


def build_multi_vendor_stock_positions(
	*,
	company: str | None = None,
	lookback_days: int = 30,
	from_date=None,
	to_date=None,
	principal: str | None = None,
	branch: str | None = None,
	warehouse: str | None = None,
	group_by_warehouse: int | bool = 0,
	stock_basis: str | None = None,
	item_codes: list[str] | None = None,
	row_limit: int | None = None,
	mode: str = "all_vendors",
	unresolved_barcodes: list[str] | None = None,
	resolved_barcodes: dict[str, str] | None = None,
):
	company, currency = vp._resolve_company_context(company)
	lookback_days, date_from, date_to = vp._get_date_window(lookback_days, from_date, to_date)
	principal = (principal or "").strip() or None
	branch, warehouse_list = vp._resolve_branch_warehouse_scope(branch, warehouse)
	vp._check_branch_permission(branch)
	group_by_warehouse = bool(cint(group_by_warehouse))
	stock_basis = vp._normalize_stock_basis(stock_basis)
	row_limit = max(
		1,
		min(cint(row_limit or vp._MAX_ALL_STOCK_POSITION_ROWS), vp._MAX_ALL_STOCK_POSITION_ROWS),
	)

	links = vp._get_supplier_item_links(
		company=company,
		principal=principal,
		item_codes=item_codes,
	)
	if not links:
		notes = _stock_definition_notes(stock_basis)
		return {
			"mode": mode,
			"company": company,
			"currency": currency,
			"date_from": str(date_from),
			"date_to": str(date_to),
			"lookback_days": lookback_days,
			"principal": principal,
			"group_by_warehouse": group_by_warehouse,
			"stock_basis": stock_basis,
			"include_supplier_columns": True,
			"include_origin_columns": stock_basis in (vp._STOCK_BASIS_FIFO, vp._STOCK_BASIS_BOTH),
			"fifo_too_large": False,
			"sle_row_count": 0,
			"unresolved_barcodes": unresolved_barcodes or [],
			"resolved_barcodes": resolved_barcodes or {},
			**notes,
			"item_sources_note": (
				_(
					"Supplier-linked SKUs are resolved from Item Supplier, Item Default, and actual submitted purchase history, filtered to Item Principal {0}."
				).format(principal)
				if principal
				else _(
					"Supplier-linked SKUs are resolved from Item Supplier, Item Default, and actual submitted purchase history."
				)
			),
			"summary": {
				"linked_item_count": 0,
				"linked_supplier_count": 0,
				"stock_item_count": 0,
				"warehouse_count": 0,
				"stock_qty": 0,
				"stock_value": 0,
				"stock_value_incl_tax": 0,
				"stock_tax_amount": 0,
				"origin_stock_qty": 0,
				"origin_stock_value": 0,
				"sales_qty": 0,
				"sales_amount": 0,
				"sales_doc_count": 0,
				"cogs_qty": 0,
				"cogs_amount": 0,
				"gross_margin_amount": 0,
				"gross_margin_pct": 0,
				"truncated": False,
				"total_row_count": 0,
				"row_count": 0,
			},
			"stock_items": [],
		}

	link_pairs = {(row["supplier"], row["item_code"]) for row in links}
	links_by_item: dict[str, list[str]] = defaultdict(list)
	for supplier, item_code in sorted(link_pairs):
		if supplier not in links_by_item[item_code]:
			links_by_item[item_code].append(supplier)

	linked_item_codes = sorted(links_by_item.keys())
	suppliers = sorted({supplier for supplier, _item in link_pairs})
	supplier_names = _supplier_name_map(suppliers)

	fifo_too_large = False
	sle_row_count = 0
	origin_map: dict[tuple[str, str], dict] = {}
	need_fifo = stock_basis in (vp._STOCK_BASIS_FIFO, vp._STOCK_BASIS_BOTH)
	if need_fifo:
		fifo_too_large, sle_row_count, origin_map = vp._get_fifo_origin_by_supplier_item(
			company=company,
			item_codes=linked_item_codes,
			warehouse_list=warehouse_list,
		)

	stock_rows: list[dict] = []
	stock_summary = {
		"item_count": 0,
		"warehouse_count": 0,
		"stock_qty": 0,
		"stock_value": 0,
		"stock_value_incl_tax": 0,
		"stock_tax_amount": 0,
		"truncated": False,
		"total_row_count": 0,
		"by_item": {},
	}

	if stock_basis in (vp._STOCK_BASIS_BIN, vp._STOCK_BASIS_BOTH):
		stock_summary, bin_rows = vp._get_stock_positions(
			item_codes=linked_item_codes,
			company=company,
			warehouse_list=warehouse_list,
			group_by_warehouse=group_by_warehouse,
			row_limit=vp._MAX_ALL_STOCK_POSITION_ROWS,
		)
		stock_rows = _fan_out_bin_rows(
			bin_rows,
			links_by_item=links_by_item,
			supplier_names=supplier_names,
			group_by_warehouse=group_by_warehouse,
		)
		if stock_basis == vp._STOCK_BASIS_BOTH and not fifo_too_large:
			for row in stock_rows:
				origin = origin_map.get((row["supplier"], row["item_code"]))
				_attach_origin_fields(row, origin)
		elif stock_basis == vp._STOCK_BASIS_BOTH and fifo_too_large:
			for row in stock_rows:
				_attach_origin_fields(row)

	if stock_basis == vp._STOCK_BASIS_FIFO:
		if fifo_too_large:
			stock_rows = []
		else:
			item_names = {
				row.name: row.item_name
				for row in frappe.get_all(
					"Item",
					filters={"name": ["in", linked_item_codes]},
					fields=["name", "item_name"],
				)
			}
			stock_rows = _build_fifo_only_rows(
				origin_map=origin_map,
				links=link_pairs,
				supplier_names=supplier_names,
				item_names=item_names,
				group_by_warehouse=group_by_warehouse,
			)

	total_row_count = len(stock_rows)
	truncated = total_row_count > row_limit or bool(stock_summary.get("truncated"))
	stock_rows = stock_rows[:row_limit]

	row_item_codes = list({row["item_code"] for row in stock_rows})
	_attach_barcodes(stock_rows, row_item_codes)

	stock_rows = _enrich_rows_by_supplier(
		stock_rows,
		company=company,
		date_from=date_from,
		date_to=date_to,
		branch=branch,
		warehouse_list=warehouse_list,
		principal=principal,
	)

	# Recompute incl-tax summary from enriched rows (per-supplier last purchase).
	stock_value_incl_tax = sum(flt(row.get("stock_value_incl_tax")) for row in stock_rows)
	stock_tax_amount = sum(flt(row.get("stock_tax_amount")) for row in stock_rows)
	origin_stock_qty = sum(flt(row.get("origin_stock_qty")) for row in stock_rows)
	origin_stock_value = sum(flt(row.get("origin_stock_value")) for row in stock_rows)

	sales_summary = vp._get_sales_summary(
		item_codes=linked_item_codes,
		company=company,
		date_from=date_from,
		date_to=date_to,
		branch=branch,
	)
	cogs_summary = vp._get_cogs_summary(
		item_codes=linked_item_codes,
		company=company,
		date_from=date_from,
		date_to=date_to,
		warehouse_list=warehouse_list,
	)
	sales_amount = flt(sales_summary.get("sales_amount"))
	cogs_amount = flt(cogs_summary.get("cogs_amount"))
	gross_margin_amount = sales_amount - cogs_amount
	gross_margin_pct = (gross_margin_amount / sales_amount * 100) if sales_amount else 0

	notes = _stock_definition_notes(stock_basis)
	fifo_note = ""
	if need_fifo and fifo_too_large:
		fifo_note = _(
			"FIFO origin was skipped because this item set has {0} stock ledger rows (limit {1}). "
			"Narrow branch/warehouse or item paste, or use Bin basis."
		).format(sle_row_count, vp._MAX_ORIGIN_STOCK_SLE_ROWS)

	return {
		"mode": mode,
		"company": company,
		"currency": currency,
		"date_from": str(date_from),
		"date_to": str(date_to),
		"lookback_days": lookback_days,
		"principal": principal,
		"group_by_warehouse": group_by_warehouse,
		"stock_basis": stock_basis,
		"include_supplier_columns": True,
		"include_origin_columns": stock_basis in (vp._STOCK_BASIS_FIFO, vp._STOCK_BASIS_BOTH),
		"fifo_too_large": fifo_too_large,
		"sle_row_count": sle_row_count,
		"fifo_note": fifo_note,
		"unresolved_barcodes": unresolved_barcodes or [],
		"resolved_barcodes": resolved_barcodes or {},
		**notes,
		"item_sources_note": (
			_(
				"Supplier-linked SKUs are resolved from Item Supplier, Item Default, and actual submitted purchase history, filtered to Item Principal {0}."
			).format(principal)
			if principal
			else _(
				"Supplier-linked SKUs are resolved from Item Supplier, Item Default, and actual submitted purchase history."
			)
		),
		"summary": {
			"linked_item_count": len(linked_item_codes),
			"linked_supplier_count": len(suppliers),
			"stock_item_count": cint(stock_summary.get("item_count"))
			if stock_basis != vp._STOCK_BASIS_FIFO
			else len({row["item_code"] for row in stock_rows}),
			"warehouse_count": cint(stock_summary.get("warehouse_count")),
			"stock_qty": sum(flt(row.get("stock_qty")) for row in stock_rows),
			"stock_value": sum(flt(row.get("stock_value")) for row in stock_rows),
			"stock_value_incl_tax": stock_value_incl_tax,
			"stock_tax_amount": stock_tax_amount,
			"origin_stock_qty": origin_stock_qty,
			"origin_stock_value": origin_stock_value,
			"sales_qty": flt(sales_summary.get("sales_qty")),
			"sales_amount": sales_amount,
			"sales_doc_count": cint(sales_summary.get("doc_count")),
			"cogs_qty": flt(cogs_summary.get("cogs_qty")),
			"cogs_amount": cogs_amount,
			"gross_margin_amount": gross_margin_amount,
			"gross_margin_pct": gross_margin_pct,
			"truncated": truncated,
			"total_row_count": total_row_count,
			"row_count": len(stock_rows),
		},
		"stock_items": stock_rows,
	}


def build_stock_positions_by_barcodes(
	*,
	barcodes_text: str | list | None,
	company: str | None = None,
	lookback_days: int = 30,
	from_date=None,
	to_date=None,
	principal: str | None = None,
	branch: str | None = None,
	warehouse: str | None = None,
	group_by_warehouse: int | bool = 0,
	stock_basis: str | None = None,
	row_limit: int | None = None,
):
	tokens = vp._parse_barcode_tokens(barcodes_text)
	if not tokens:
		frappe.throw(_("Paste at least one barcode or item code."))

	resolved, unresolved = vp._resolve_barcodes_to_items(tokens)
	item_codes = list(dict.fromkeys(resolved.values()))
	return build_multi_vendor_stock_positions(
		company=company,
		lookback_days=lookback_days,
		from_date=from_date,
		to_date=to_date,
		principal=principal,
		branch=branch,
		warehouse=warehouse,
		group_by_warehouse=group_by_warehouse,
		stock_basis=stock_basis,
		item_codes=item_codes,
		row_limit=row_limit,
		mode="by_barcodes",
		unresolved_barcodes=unresolved,
		resolved_barcodes=resolved,
	)


def apply_stock_basis_to_single_vendor(
	payload: dict,
	*,
	supplier: str,
	company: str,
	item_codes: list[str],
	warehouse_list: list[str] | None,
	stock_basis: str,
) -> dict:
	"""Attach FIFO origin fields / swap primary qty for single-vendor payloads."""
	stock_basis = vp._normalize_stock_basis(stock_basis)
	payload["stock_basis"] = stock_basis
	payload["include_supplier_columns"] = False
	payload["include_origin_columns"] = stock_basis in (vp._STOCK_BASIS_FIFO, vp._STOCK_BASIS_BOTH)
	payload["fifo_too_large"] = False
	payload["sle_row_count"] = 0
	payload["fifo_note"] = ""
	payload["mode"] = "single_vendor"

	rows = payload.get("stock_items") or []
	for row in rows:
		_attach_origin_fields(row)

	if stock_basis == vp._STOCK_BASIS_BIN:
		notes = _stock_definition_notes(stock_basis)
		payload["stock_definition_note"] = notes["stock_definition_note"]
		return payload

	too_large, sle_row_count, origin_map = vp._get_fifo_origin_by_supplier_item(
		company=company,
		item_codes=item_codes,
		warehouse_list=warehouse_list,
	)
	payload["fifo_too_large"] = too_large
	payload["sle_row_count"] = sle_row_count
	if too_large:
		payload["fifo_note"] = _(
			"FIFO origin was skipped because this item set has {0} stock ledger rows (limit {1}). "
			"Narrow branch/warehouse, or use Bin basis."
		).format(sle_row_count, vp._MAX_ORIGIN_STOCK_SLE_ROWS)
		notes = _stock_definition_notes(vp._STOCK_BASIS_BIN if stock_basis == vp._STOCK_BASIS_FIFO else stock_basis)
		payload["stock_definition_note"] = notes["stock_definition_note"]
		if stock_basis == vp._STOCK_BASIS_FIFO:
			payload["stock_items"] = []
			summary = payload.get("summary") or {}
			summary["row_count"] = 0
			summary["stock_qty"] = 0
			summary["stock_value"] = 0
			summary["stock_value_incl_tax"] = 0
			summary["stock_tax_amount"] = 0
			summary["origin_stock_qty"] = 0
			summary["origin_stock_value"] = 0
			payload["summary"] = summary
		return payload

	if stock_basis == vp._STOCK_BASIS_BOTH:
		for row in rows:
			origin = origin_map.get((supplier, row["item_code"]))
			_attach_origin_fields(row, origin)
		summary = payload.get("summary") or {}
		summary["origin_stock_qty"] = sum(flt(r.get("origin_stock_qty")) for r in rows)
		summary["origin_stock_value"] = sum(flt(r.get("origin_stock_value")) for r in rows)
		payload["summary"] = summary
	elif stock_basis == vp._STOCK_BASIS_FIFO:
		item_names = {row.get("item_code"): row.get("item_name") for row in rows}
		# Also include origin-only items not in the Bin set.
		for (origin_supplier, item_code), origin in origin_map.items():
			if origin_supplier != supplier:
				continue
			item_names.setdefault(item_code, item_code)
		fifo_rows = []
		for (origin_supplier, item_code), origin in origin_map.items():
			if origin_supplier != supplier:
				continue
			if flt(origin.get("origin_stock_qty")) <= 0.0001 and flt(origin.get("origin_stock_value")) <= 0.0001:
				continue
			entry = {
				"item_code": item_code,
				"item_name": item_names.get(item_code) or item_code,
				"stock_qty": flt(origin.get("origin_stock_qty")),
				"stock_value": flt(origin.get("origin_stock_value")),
				"stock_value_incl_tax": flt(origin.get("origin_stock_value")),
				"stock_tax_amount": 0.0,
				"stock_tax_basis": "at_cost",
			}
			if payload.get("group_by_warehouse"):
				entry["warehouse"] = ""
				entry["branch"] = ""
			_attach_origin_fields(entry, origin)
			fifo_rows.append(entry)

		fifo_rows.sort(
			key=lambda r: (
				-flt(r.get("origin_stock_value")),
				-flt(r.get("origin_stock_qty")),
				r.get("item_code") or "",
			)
		)
		_attach_barcodes(fifo_rows, [r["item_code"] for r in fifo_rows])
		fifo_rows = vp._enrich_stock_position_rows(
			fifo_rows,
			supplier=supplier,
			company=company,
			date_from=payload.get("date_from"),
			date_to=payload.get("date_to"),
			branch=payload.get("branch"),
			warehouse_list=warehouse_list,
			principal=payload.get("principal"),
		)
		# For FIFO-only, primary stock already equals origin; incl-tax may overwrite from last purchase.
		for row in fifo_rows:
			row["stock_qty"] = flt(row.get("origin_stock_qty"))
			row["stock_value"] = flt(row.get("origin_stock_value"))

		payload["stock_items"] = fifo_rows
		summary = payload.get("summary") or {}
		summary["row_count"] = len(fifo_rows)
		summary["total_row_count"] = len(fifo_rows)
		summary["stock_item_count"] = len(fifo_rows)
		summary["stock_qty"] = sum(flt(r.get("stock_qty")) for r in fifo_rows)
		summary["stock_value"] = sum(flt(r.get("stock_value")) for r in fifo_rows)
		summary["stock_value_incl_tax"] = sum(flt(r.get("stock_value_incl_tax")) for r in fifo_rows)
		summary["stock_tax_amount"] = sum(flt(r.get("stock_tax_amount")) for r in fifo_rows)
		summary["origin_stock_qty"] = summary["stock_qty"]
		summary["origin_stock_value"] = summary["stock_value"]
		payload["summary"] = summary

	notes = _stock_definition_notes(stock_basis)
	payload["stock_definition_note"] = notes["stock_definition_note"]
	return payload


def build_xlsx_data(payload: dict) -> tuple[list[list], str]:
	"""Shared Excel matrix + filename stem for all Vendor Stock Positions modes."""
	group_by_warehouse = bool(payload.get("group_by_warehouse"))
	include_supplier = bool(payload.get("include_supplier_columns"))
	include_origin = bool(payload.get("include_origin_columns"))
	currency = payload.get("currency") or ""

	headers = []
	if include_supplier:
		headers.extend(["Supplier", "Supplier Name"])
	headers.extend(["Item Code", "Item Name", "Barcode 1", "Barcode 2"])
	if group_by_warehouse:
		headers.extend(["Warehouse", "Branch"])
	headers.extend(
		[
			"Stock Qty",
			f"Stock Value (at Cost) ({currency})",
			f"Stock Value (Incl Taxes) ({currency})",
			f"Tax on Stock ({currency})",
		]
	)
	if include_origin:
		headers.extend(
			[
				"Origin Stock Qty",
				f"Origin Stock Value ({currency})",
			]
		)
	headers.extend(
		[
			"Total Purchase Qty",
			f"Total Purchase Amount Excl Tax ({currency})",
			f"Purchase Tax / GST ({currency})",
			f"Total Purchase Amount Incl Tax ({currency})",
			"Total Sale Qty",
			f"Total Sale Amount ({currency})",
			f"Cost of Goods Sold ({currency})",
			f"Gross Margin ({currency})",
			"Adjustment Qty",
			f"Adjustment Value ({currency})",
			"Last Purchase Date",
			"Last Purchase Doc",
			f"Last Purchase Rate Excl Tax ({currency})",
			f"Last Purchase Rate Incl Tax ({currency})",
			"Last Sale Date",
			"Last Sale Doc",
			f"Last Sale Rate ({currency})",
		]
	)

	data = [headers]
	for row in payload.get("stock_items") or []:
		line = []
		if include_supplier:
			line.extend([row.get("supplier") or "", row.get("supplier_name") or ""])
		line.extend(
			[
				row.get("item_code") or "",
				row.get("item_name") or "",
				row.get("barcode1") or "",
				row.get("barcode2") or "",
			]
		)
		if group_by_warehouse:
			line.extend([row.get("warehouse") or "", row.get("branch") or ""])
		line.extend(
			[
				flt(row.get("stock_qty")),
				flt(row.get("stock_value")),
				flt(row.get("stock_value_incl_tax")),
				flt(row.get("stock_tax_amount")),
			]
		)
		if include_origin:
			line.extend(
				[
					flt(row.get("origin_stock_qty")),
					flt(row.get("origin_stock_value")),
				]
			)
		line.extend(
			[
				flt(row.get("purchase_qty_in_window")),
				flt(row.get("purchase_amount_in_window")),
				flt(row.get("purchase_tax_amount_in_window")),
				flt(row.get("purchase_amount_incl_tax_in_window")),
				flt(row.get("sales_qty_in_window")),
				flt(row.get("sales_amount_in_window")),
				flt(row.get("cogs_amount_in_window")),
				flt(row.get("gross_margin_in_window")),
				flt(row.get("adjustment_qty_in_window")),
				flt(row.get("adjustment_value_in_window")),
				row.get("last_purchase_date") or "",
				row.get("last_purchase_doc") or "",
				flt(row.get("last_purchase_rate")) if row.get("last_purchase_rate") else "",
				flt(row.get("last_purchase_rate_incl_tax")) if row.get("last_purchase_rate_incl_tax") else "",
				row.get("last_sale_date") or "",
				row.get("last_sale_doc") or "",
				flt(row.get("last_sale_rate")) if row.get("last_sale_rate") else "",
			]
		)
		data.append(line)

	unresolved = payload.get("unresolved_barcodes") or []
	if unresolved:
		data.append([])
		data.append(["Unresolved Barcodes / Item Codes"])
		for token in unresolved:
			data.append([token])

	mode = payload.get("mode") or "single_vendor"
	if mode == "all_vendors":
		filename = "vendor_stock_positions_all_vendors"
	elif mode == "by_barcodes":
		filename = "vendor_stock_positions_by_barcodes"
	else:
		safe_supplier = (payload.get("supplier") or "supplier").replace("/", "-").replace(" ", "_")
		filename = f"vendor_stock_positions_{safe_supplier}"
	return data, filename
