from __future__ import annotations

import frappe
from frappe.utils import flt


def _get_primary_barcodes(item_codes: list[str]) -> dict[str, str]:
	if not item_codes:
		return {}

	rows = frappe.get_all(
		"Item Barcode",
		filters={"parent": ["in", item_codes]},
		fields=["parent", "barcode", "idx"],
		order_by="parent asc, idx asc",
	)

	barcodes: dict[str, str] = {}
	for row in rows:
		if row.parent and row.barcode and row.parent not in barcodes:
			barcodes[row.parent] = row.barcode

	return barcodes


def _fetch_purchase_history(doctype: str, item_codes: list[str], current_name: str | None) -> list[dict]:
	if not item_codes:
		return []

	child_table = f"tab{doctype} Item"
	parent_table = f"tab{doctype}"
	filters: dict[str, object] = {"item_codes": tuple(item_codes)}
	exclude_current = ""

	if current_name:
		filters["current_name"] = current_name
		exclude_current = "AND parent.name != %(current_name)s"

	return frappe.db.sql(
		f"""
        SELECT
            child.item_code,
            child.rate,
            child.custom_price_after_taxes,
            child.custom_shelf_price,
            child.custom_mrp,
            parent.posting_date,
            parent.posting_time,
            parent.creation,
            parent.name,
            child.idx
        FROM `{child_table}` child
        INNER JOIN `{parent_table}` parent ON parent.name = child.parent
        WHERE parent.docstatus = 1
          AND child.item_code IN %(item_codes)s
          {exclude_current}
        ORDER BY
            child.item_code ASC,
            parent.posting_date DESC,
            IFNULL(parent.posting_time, '00:00:00') DESC,
            parent.creation DESC,
            parent.name DESC,
            child.idx DESC
        """,
		filters,
		as_dict=True,
	)


def _build_latest_history_map(
	item_codes: list[str], current_doctype: str, current_name: str | None
) -> dict[str, dict]:
	rows = []
	rows.extend(
		_fetch_purchase_history(
			"Purchase Receipt",
			item_codes,
			current_name if current_doctype == "Purchase Receipt" else None,
		)
	)
	rows.extend(
		_fetch_purchase_history(
			"Purchase Invoice",
			item_codes,
			current_name if current_doctype == "Purchase Invoice" else None,
		)
	)

	latest: dict[str, dict] = {}
	for row in sorted(
		rows,
		key=lambda r: (
			r.item_code or "",
			r.posting_date or "",
			r.posting_time or "",
			r.creation or "",
			r.name or "",
			r.idx or 0,
		),
		reverse=True,
	):
		if row.item_code and row.item_code not in latest:
			latest[row.item_code] = row

	return latest


def populate_old_purchase_snapshot(doc, method=None):
	item_codes = [row.item_code for row in (doc.items or []) if row.item_code]
	if not item_codes:
		return

	latest_history = _build_latest_history_map(item_codes, doc.doctype, doc.name)
	for row in doc.items or []:
		history = latest_history.get(row.item_code) or {}
		row.custom_old_price_after_tax = flt(history.get("custom_price_after_taxes"))


def get_purchase_print_item_context(doc) -> dict[str, dict]:
	item_codes = [row.item_code for row in (doc.items or []) if row.item_code]
	if not item_codes:
		return {}

	barcodes = _get_primary_barcodes(item_codes)
	latest_history = _build_latest_history_map(item_codes, doc.doctype, doc.name)

	context: dict[str, dict] = {}
	for row in doc.items or []:
		if not row.name:
			continue

		history = latest_history.get(row.item_code) or {}
		context[row.name] = {
			"barcode": row.get("barcode") or barcodes.get(row.item_code) or row.item_code or "",
			"old_cost_price": flt(
				row.get("custom_old_price_after_tax")
				or history.get("custom_price_after_taxes")
				or history.get("rate")
			),
			"old_sale_price": flt(
				history.get("custom_shelf_price") or history.get("custom_mrp") or history.get("rate")
			),
		}

	return context
