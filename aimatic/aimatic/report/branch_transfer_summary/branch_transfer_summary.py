from __future__ import annotations

import frappe
from frappe import _

from aimatic.branch_transfer_reports import (
	get_currency,
	get_summary_cards,
	get_transfer_rows,
	summarise,
	validate_filters,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)

	rows = get_transfer_rows(filters)
	currency = get_currency(filters.company)
	message = _("Submitted Material Transfers between two different branches. Open Branch Transfer Detail for the lines.")
	return get_columns(currency), summarise(rows), message, None, get_summary_cards(rows, currency)


def get_columns(currency=None):
	return [
		{"label": _("From Branch"), "fieldname": "from_branch", "fieldtype": "Link", "options": "Branch", "width": 170},
		{"label": _("To Branch"), "fieldname": "to_branch", "fieldtype": "Link", "options": "Branch", "width": 170},
		{"label": _("Transfers"), "fieldname": "transfers", "fieldtype": "Int", "width": 90},
		{"label": _("Items"), "fieldname": "item_count", "fieldtype": "Int", "width": 80},
		{"label": _("Qty (Stock UOM)"), "fieldname": "qty", "fieldtype": "Float", "width": 120},
		{"label": _("Value"), "fieldname": "value", "fieldtype": "Currency", "options": "currency", "width": 130},
		{"label": _("First Transfer"), "fieldname": "first_date", "fieldtype": "Date", "width": 105},
		{"label": _("Last Transfer"), "fieldname": "last_date", "fieldtype": "Date", "width": 105},
	]
