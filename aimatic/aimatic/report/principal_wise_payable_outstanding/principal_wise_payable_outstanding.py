from __future__ import annotations

import frappe
from erpnext.accounts.report.accounts_receivable.accounts_receivable import ReceivablePayableReport
from frappe import _

from aimatic.principal_reporting import (
	UNALLOCATED_PRINCIPAL,
	UNCLASSIFIED_PRINCIPAL,
	get_approved_allocation_shares,
	split_by_principal,
)

NUMERIC_FIELDS = (
	"invoiced",
	"paid",
	"credit_note",
	"outstanding",
	"range0",
	"range1",
	"range2",
	"range3",
	"range4",
	"range5",
	"future_amount",
	"remaining_balance",
	"invoiced_in_account_currency",
	"paid_in_account_currency",
	"credit_note_in_account_currency",
	"outstanding_in_account_currency",
	"invoice_grand_total",
	"total_due",
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company") or not filters.get("report_date"):
		frappe.throw(_("Company and As On Date are mandatory."))
	standard_filters = frappe._dict(filters.copy())
	standard_filters.party_type = "Supplier"
	standard_filters.party = [filters.supplier] if filters.get("supplier") else []
	standard_filters.ageing_based_on = "Due Date"
	columns, rows, _message, _chart, _summary, skip_total = ReceivablePayableReport(standard_filters).run(
		{"account_type": "Payable", "naming_by": ["Buying Settings", "supp_master_name"]}
	)
	invoice_names = [row.voucher_no for row in rows if row.get("voucher_type") == "Purchase Invoice"]
	invoice_meta = {}
	if invoice_names:
		invoice_meta = {
			row.name: row
			for row in frappe.get_all(
				"Purchase Invoice",
				filters={"name": ["in", invoice_names]},
				fields=["name", "custom_principal", "rounded_total", "grand_total"],
			)
		}
	allocations = get_approved_allocation_shares(invoice_names)
	data = []
	for row in rows:
		row = frappe._dict(row)
		if row.get("voucher_type") != "Purchase Invoice":
			row.principal = UNALLOCATED_PRINCIPAL
			data.append(row)
			continue
		meta = invoice_meta.get(row.voucher_no) or frappe._dict()
		row.principal = meta.get("custom_principal") or UNCLASSIFIED_PRINCIPAL
		total = meta.get("rounded_total") or meta.get("grand_total") or row.get("invoiced")
		data.extend(split_by_principal(row, allocations.get(row.voucher_no), total, NUMERIC_FIELDS))

	if filters.get("principal"):
		data = [row for row in data if row.principal == filters.principal]
	principal_column = {
		"label": _("Principal"),
		"fieldname": "principal",
		"fieldtype": "Data",
		"width": 180,
	}
	insert_at = next((idx + 1 for idx, col in enumerate(columns) if col.get("fieldname") == "party"), 3)
	columns.insert(insert_at, principal_column)
	return columns, data, None, None, None, skip_total
