from __future__ import annotations

import frappe
from frappe import _
from frappe.desk.reportview import get_match_cond
from frappe.utils import cint, flt, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)

	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	precision = cint(frappe.db.get_default("currency_precision") or 2)
	rows = get_rows(filters)
	data = [_set_margin_values(row, precision) for row in rows]
	for row in data:
		row.currency = currency
	total_row = get_total_row(data, precision)
	total_row.currency = currency
	data.append(total_row)

	return get_columns(currency), data, get_message(), None, get_report_summary(data, currency, precision)


def validate_filters(filters):
	for fieldname, label in (
		("company", _("Company")),
		("from_date", _("From Date")),
		("to_date", _("To Date")),
	):
		if not filters.get(fieldname):
			frappe.throw(_("{0} is mandatory").format(frappe.bold(label)))

	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date must be before To Date"))


def get_rows(filters):
	branch_expression = get_branch_expression()
	conditions = [
		"`tabPOS Invoice`.`docstatus` = 1",
		"`tabPOS Invoice`.`company` = %(company)s",
		"`tabPOS Invoice`.`posting_date` BETWEEN %(from_date)s AND %(to_date)s",
	]

	for fieldname, sql_field in (
		("pos_invoice", "`tabPOS Invoice`.`name`"),
		("pos_profile", "`tabPOS Invoice`.`pos_profile`"),
		("customer", "`tabPOS Invoice`.`customer`"),
		("item_code", "pii.`item_code`"),
		("warehouse", "pii.`warehouse`"),
	):
		if filters.get(fieldname):
			conditions.append(f"{sql_field} = %({fieldname})s")

	if filters.get("branch"):
		if branch_expression == "NULL":
			frappe.throw(_("Branch is not available on POS Invoice or POS Profile"))
		conditions.append(f"{branch_expression} = %(branch)s")

	if not cint(filters.get("include_returns", 1)):
		conditions.append("`tabPOS Invoice`.`is_return` = 0")

	if not cint(filters.get("include_pending")):
		conditions.append("COALESCE(`tabPOS Invoice`.`consolidated_invoice`, '') != ''")

	permission_condition = get_match_cond("POS Invoice")
	where_clause = " AND ".join(conditions)

	# COGS comes from the submitted consolidated Sales Invoice's actual Stock
	# Ledger Entries. Packed-item SLEs are included for product bundles.
	# nosemgrep
	return frappe.db.sql(
		f"""
		SELECT
			`tabPOS Invoice`.`posting_date`,
			`tabPOS Invoice`.`posting_time`,
			`tabPOS Invoice`.`name` AS pos_invoice,
			`tabPOS Invoice`.`is_return`,
			`tabPOS Invoice`.`return_against`,
			`tabPOS Invoice`.`company`,
			{branch_expression} AS branch,
			`tabPOS Invoice`.`pos_profile`,
			`tabPOS Invoice`.`customer`,
			`tabPOS Invoice`.`consolidated_invoice`,
			pii.`name` AS pos_invoice_item,
			pii.`item_code`,
			pii.`item_name`,
			pii.`item_group`,
			pii.`warehouse`,
			pii.`stock_uom`,
			pii.`qty`,
			pii.`stock_qty`,
			pii.`base_net_amount` AS sales,
			item.`is_stock_item`,
			sii.`name` AS consolidated_item,
			(
				SELECT -SUM(sle.`stock_value_difference`)
				FROM `tabStock Ledger Entry` sle
				WHERE sle.`voucher_type` = 'Sales Invoice'
					AND sle.`voucher_no` = sii.`parent`
					AND sle.`voucher_detail_no` = sii.`name`
					AND sle.`is_cancelled` = 0
			) AS direct_cogs
		FROM `tabPOS Invoice`
		INNER JOIN `tabPOS Invoice Item` pii
			ON pii.`parent` = `tabPOS Invoice`.`name`
			AND pii.`parenttype` = 'POS Invoice'
		LEFT JOIN `tabPOS Profile` pos_profile
			ON pos_profile.`name` = `tabPOS Invoice`.`pos_profile`
		LEFT JOIN `tabItem` item
			ON item.`name` = pii.`item_code`
		LEFT JOIN `tabSales Invoice Item` sii
			ON sii.`parent` = `tabPOS Invoice`.`consolidated_invoice`
			AND sii.`pos_invoice` = `tabPOS Invoice`.`name`
			AND sii.`pos_invoice_item` = pii.`name`
			AND sii.`docstatus` = 1
		WHERE {where_clause}
			{permission_condition}
		ORDER BY
			`tabPOS Invoice`.`posting_date`,
			`tabPOS Invoice`.`posting_time`,
			`tabPOS Invoice`.`name`,
			pii.`idx`
		""",
		filters,
		as_dict=True,
	)


def get_branch_expression():
	pos_invoice_has_branch = frappe.get_meta("POS Invoice").has_field("branch")
	pos_profile_has_branch = frappe.get_meta("POS Profile").has_field("branch")

	if pos_invoice_has_branch and pos_profile_has_branch:
		return "COALESCE(NULLIF(`tabPOS Invoice`.`branch`, ''), pos_profile.`branch`)"
	if pos_invoice_has_branch:
		return "`tabPOS Invoice`.`branch`"
	if pos_profile_has_branch:
		return "pos_profile.`branch`"
	return "NULL"


def _set_margin_values(row, precision):
	row = frappe._dict(row)
	row.sales = flt(row.sales, precision)
	row.selling_rate = flt(abs(row.sales / row.qty), precision) if flt(row.qty) else 0

	if row.direct_cogs is not None:
		row.cogs = flt(row.direct_cogs, precision)
		row.cogs_rate = flt(abs(row.cogs / row.stock_qty), precision) if flt(row.stock_qty) else 0
		row.gross_margin = flt(row.sales - row.cogs, precision)
		row.gross_margin_percentage = (
			flt(row.gross_margin / abs(row.sales) * 100, precision) if row.sales else 0
		)
		row.cogs_status = _("Stock Ledger")
		row.has_ledger_cogs = 1
	elif not row.consolidated_invoice:
		row.cogs_status = _("Pending POS Closing")
		row.has_ledger_cogs = 0
	elif not row.consolidated_item:
		row.cogs_status = _("Consolidated Item Missing")
		row.has_ledger_cogs = 0
	elif cint(row.is_stock_item):
		row.cogs_status = _("Stock Ledger Missing")
		row.has_ledger_cogs = 0
	else:
		row.cogs_status = _("Non-stock Item")
		row.has_ledger_cogs = 0

	row.transaction_type = _("Return") if cint(row.is_return) else _("Sale")
	return row


def get_total_row(data, precision):
	covered = [row for row in data if row.get("has_ledger_cogs")]
	total_sales = flt(sum(row.sales for row in covered), precision)
	total_cogs = flt(sum(row.cogs for row in covered), precision)
	total_margin = flt(total_sales - total_cogs, precision)

	return frappe._dict(
		{
			"pos_invoice": _("Total"),
			"item_name": _("COGS-covered rows"),
			"sales": total_sales,
			"cogs": total_cogs,
			"gross_margin": total_margin,
			"gross_margin_percentage": (
				flt(total_margin / abs(total_sales) * 100, precision) if total_sales else 0
			),
			"is_total": 1,
		}
	)


def get_report_summary(data, currency, precision):
	rows = [row for row in data if not row.get("is_total")]
	covered = [row for row in rows if row.get("has_ledger_cogs")]
	total_sales = flt(sum(row.sales for row in covered), precision)
	total_cogs = flt(sum(row.cogs for row in covered), precision)
	total_margin = flt(total_sales - total_cogs, precision)
	missing = len(rows) - len(covered)

	return [
		{
			"label": _("Sales (COGS-covered)"),
			"value": total_sales,
			"indicator": "Blue",
			"datatype": "Currency",
			"currency": currency,
		},
		{
			"label": _("COGS"),
			"value": total_cogs,
			"indicator": "Orange",
			"datatype": "Currency",
			"currency": currency,
		},
		{
			"label": _("Gross Margin"),
			"value": total_margin,
			"indicator": "Green",
			"datatype": "Currency",
			"currency": currency,
		},
		{
			"label": _("Gross Margin %"),
			"value": flt(total_margin / abs(total_sales) * 100, precision) if total_sales else 0,
			"indicator": "Green",
			"datatype": "Percent",
		},
		{
			"label": _("Rows Without Ledger COGS"),
			"value": missing,
			"indicator": "Red" if missing else "Green",
			"datatype": "Int",
		},
	]


def get_message():
	return _(
		"Sales is the POS item net amount in company currency. COGS is the signed "
		"stock-value change posted by the consolidated Sales Invoice, including "
		"packed items. Returns reverse Sales and COGS. Blank margin values mean "
		"ledger COGS is not yet available; pending POS Closing rows are excluded by default."
	)


def get_columns(currency):
	return [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 90},
		{"label": _("POS Invoice"), "fieldname": "pos_invoice", "fieldtype": "Link", "options": "POS Invoice", "width": 150},
		{"label": _("Type"), "fieldname": "transaction_type", "fieldtype": "Data", "width": 70},
		{"label": _("Return Against"), "fieldname": "return_against", "fieldtype": "Link", "options": "POS Invoice", "width": 140},
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 120},
		{"label": _("POS Profile"), "fieldname": "pos_profile", "fieldtype": "Link", "options": "POS Profile", "width": 130},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 130},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 180},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 120},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 150},
		{"label": _("Stock UOM"), "fieldname": "stock_uom", "fieldtype": "Link", "options": "UOM", "width": 90},
		{"label": _("Qty"), "fieldname": "stock_qty", "fieldtype": "Float", "width": 80},
		{"label": _("Selling Rate"), "fieldname": "selling_rate", "fieldtype": "Currency", "options": "currency", "width": 105},
		{"label": _("Sales"), "fieldname": "sales", "fieldtype": "Currency", "options": "currency", "width": 110},
		{"label": _("COGS Rate"), "fieldname": "cogs_rate", "fieldtype": "Currency", "options": "currency", "width": 105},
		{"label": _("COGS"), "fieldname": "cogs", "fieldtype": "Currency", "options": "currency", "width": 110},
		{"label": _("Gross Margin"), "fieldname": "gross_margin", "fieldtype": "Currency", "options": "currency", "width": 120},
		{"label": _("Gross Margin %"), "fieldname": "gross_margin_percentage", "fieldtype": "Percent", "width": 115},
		{"label": _("COGS Status"), "fieldname": "cogs_status", "fieldtype": "Data", "width": 160},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "hidden": 1, "default": currency},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "hidden": 1},
		{"label": _("Consolidated Invoice"), "fieldname": "consolidated_invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 150},
	]
