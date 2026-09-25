from __future__ import annotations

import frappe
from frappe import _

from aimatic.branch_transfer_reports import (
	get_currency,
	get_summary_cards,
	get_transfer_rows,
	validate_filters,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)

	rows = get_transfer_rows(filters)
	currency = get_currency(filters.company)
	for row in rows:
		row.currency = currency
	return get_columns(currency), rows, None, None, get_summary_cards(rows, currency)


def get_columns(currency=None):
	return [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
		{"label": _("Stock Entry"), "fieldname": "stock_entry", "fieldtype": "Link", "options": "Stock Entry", "width": 150},
		{"label": _("From Branch"), "fieldname": "from_branch", "fieldtype": "Link", "options": "Branch", "width": 150},
		{"label": _("From Warehouse"), "fieldname": "from_warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 160},
		{"label": _("To Branch"), "fieldname": "to_branch", "fieldtype": "Link", "options": "Branch", "width": 150},
		{"label": _("To Warehouse"), "fieldname": "to_warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 160},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 190},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 120},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 80},
		{"label": _("UOM"), "fieldname": "uom", "fieldtype": "Link", "options": "UOM", "width": 70},
		{"label": _("Rate"), "fieldname": "basic_rate", "fieldtype": "Currency", "options": "currency", "width": 100},
		{"label": _("Value"), "fieldname": "amount", "fieldtype": "Currency", "options": "currency", "width": 110},
		{"label": _("Created By"), "fieldname": "created_by", "fieldtype": "Link", "options": "User", "width": 170},
	]
