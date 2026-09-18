from __future__ import annotations

import frappe
from frappe import _
from frappe.desk.reportview import get_match_cond
from frappe.utils import cint, flt, getdate

DOCSTATUS_LABEL = {0: _("Draft"), 1: _("Submitted"), 2: _("Cancelled")}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)

	currency = frappe.get_cached_value("Company", filters.company, "default_currency") if filters.company else None
	precision = cint(frappe.db.get_default("currency_precision") or 2)

	rows = get_rows(filters)
	_apply_barcode_fallback(rows)
	data = [_set_row_values(row, precision) for row in rows]
	for row in data:
		row.currency = currency

	return get_columns(currency), data, None, None, get_report_summary(data, currency, precision)


def validate_filters(filters):
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("From Date and To Date are mandatory"))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date must be before To Date"))


def get_rows(filters):
	conditions = ["sr.`posting_date` BETWEEN %(from_date)s AND %(to_date)s"]

	if cint(filters.get("include_cancelled")):
		conditions.append("sr.`docstatus` IN (1, 2)")
	else:
		conditions.append("sr.`docstatus` = 1")

	for fieldname, sql_field in (
		("company", "sr.`company`"),
		("name", "sr.`name`"),
		("branch", "sr.`branch`"),
		("item_code", "sri.`item_code`"),
		("warehouse", "sri.`warehouse`"),
	):
		if filters.get(fieldname):
			conditions.append(f"{sql_field} = %({fieldname})s")

	permission_condition = get_match_cond("Stock Reconciliation")
	where_clause = " AND ".join(conditions)

	# nosemgrep
	return frappe.db.sql(
		f"""
		SELECT
			sr.`posting_date`,
			sr.`posting_time`,
			sr.`name` AS reconciliation_no,
			sr.`docstatus`,
			sr.`company`,
			sr.`branch`,
			sr.`purpose`,
			sr.`cost_center`,
			sr.`difference_amount`,
			sri.`name` AS row_name,
			sri.`item_code`,
			sri.`item_name`,
			sri.`barcode`,
			sri.`warehouse`,
			sri.`stock_uom`,
			sri.`current_qty`,
			sri.`qty`,
			sri.`quantity_difference`,
			sri.`valuation_rate`,
			sri.`amount_difference`
		FROM `tabStock Reconciliation` sr
		INNER JOIN `tabStock Reconciliation Item` sri
			ON sri.`parent` = sr.`name` AND sri.`parenttype` = 'Stock Reconciliation'
		WHERE {where_clause}
			{permission_condition}
		ORDER BY sr.`posting_date`, sr.`posting_time`, sr.`name`, sri.`idx`
		""",
		filters,
		as_dict=True,
	)


def _apply_barcode_fallback(rows):
	"""Same fallback as the Stock Reconciliation Compact print layout: a row's
	own scanned barcode first, else the Item's primary Item Barcode."""

	missing_item_codes = {row.item_code for row in rows if row.item_code and not row.barcode}
	if not missing_item_codes:
		return

	primary_barcodes: dict[str, str] = {}
	for barcode in frappe.get_all(
		"Item Barcode",
		filters={"parent": ["in", list(missing_item_codes)]},
		fields=["parent", "barcode", "idx"],
		order_by="parent asc, idx asc",
	):
		if barcode.parent and barcode.barcode and barcode.parent not in primary_barcodes:
			primary_barcodes[barcode.parent] = barcode.barcode

	for row in rows:
		if not row.barcode:
			row.barcode = primary_barcodes.get(row.item_code, "")


def _set_row_values(row, precision):
	row = frappe._dict(row)
	row.status = DOCSTATUS_LABEL.get(row.docstatus, "")
	row.qty_diff = flt(row.quantity_difference, precision)
	row.amount_difference = flt(row.amount_difference, precision)
	row.valuation_rate = flt(row.valuation_rate, precision)
	return row


def get_report_summary(data, currency, precision):
	rows = [row for row in data if row.get("docstatus") == 1]
	documents = {row.reconciliation_no for row in rows}
	dates = {row.posting_date for row in rows}
	total_qty_diff = flt(sum(row.qty_diff for row in rows), precision)
	total_amount_diff = flt(sum(row.amount_difference for row in rows), precision)

	return [
		{"label": _("Documents"), "value": len(documents), "indicator": "Blue", "datatype": "Int"},
		{"label": _("Dates Covered"), "value": len(dates), "indicator": "Blue", "datatype": "Int"},
		{
			"label": _("Total Qty Difference"),
			"value": total_qty_diff,
			"indicator": "Orange" if total_qty_diff else "Green",
			"datatype": "Float",
		},
		{
			"label": _("Total Amount Difference"),
			"value": total_amount_diff,
			"indicator": "Orange" if total_amount_diff else "Green",
			"datatype": "Currency",
			"currency": currency,
		},
	]


def get_columns(currency):
	return [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 90},
		{
			"label": _("Reconciliation No"),
			"fieldname": "reconciliation_no",
			"fieldtype": "Link",
			"options": "Stock Reconciliation",
			"width": 165,
		},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 90},
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 130},
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 160,
		},
		{"label": _("Purpose"), "fieldname": "purpose", "fieldtype": "Data", "width": 110},
		{"label": _("Barcode"), "fieldname": "barcode", "fieldtype": "Data", "width": 120},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 180},
		{"label": _("UOM"), "fieldname": "stock_uom", "fieldtype": "Link", "options": "UOM", "width": 70},
		{"label": _("Before Qty"), "fieldname": "current_qty", "fieldtype": "Float", "width": 90},
		{"label": _("Physical Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 95},
		{"label": _("Qty Diff"), "fieldname": "qty_diff", "fieldtype": "Float", "width": 85},
		{
			"label": _("Rate"),
			"fieldname": "valuation_rate",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
		{
			"label": _("Amount Diff"),
			"fieldname": "amount_difference",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 110,
		},
		{
			"label": _("Cost Center"),
			"fieldname": "cost_center",
			"fieldtype": "Link",
			"options": "Cost Center",
			"width": 150,
		},
		{
			"label": _("Currency"),
			"fieldname": "currency",
			"fieldtype": "Data",
			"hidden": 1,
			"default": currency,
		},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "hidden": 1},
	]
