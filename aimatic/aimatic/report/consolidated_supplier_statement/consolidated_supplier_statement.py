from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from aimatic.aimatic.report.principal_wise_payable_outstanding.principal_wise_payable_outstanding import (
	execute as payable_execute,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company") or not filters.get("supplier") or not filters.get("report_date"):
		frappe.throw(_("Company, Legal Supplier and As On Date are mandatory."))
	if not frappe.has_permission("Supplier", "read", filters.supplier):
		frappe.throw(_("Not permitted to read this Supplier."), frappe.PermissionError)
	_columns, payable_rows, *_rest = payable_execute(filters)
	by_principal = defaultdict(lambda: {"invoiced": 0, "paid": 0, "credit_note": 0, "outstanding": 0})
	for row in payable_rows:
		bucket = by_principal[row.principal]
		for fieldname in bucket:
			bucket[fieldname] += flt(row.get(fieldname))
	totals = {
		fieldname: sum(values[fieldname] for values in by_principal.values())
		for fieldname in ("invoiced", "paid", "credit_note", "outstanding")
	}
	ledger_balance = frappe.db.sql(
		"""
		select coalesce(sum(amount), 0)
		from `tabPayment Ledger Entry`
		where delinked = 0 and company = %(company)s and party_type = 'Supplier'
			and party = %(supplier)s and posting_date <= %(report_date)s
		""",
		filters,
	)[0][0]
	difference = flt(flt(ledger_balance) - flt(totals["outstanding"]), 2)
	data = [
		{
			"row_type": "Legal Supplier Total",
			"principal": filters.supplier,
			**totals,
			"outstanding": ledger_balance,
			"bold": 1,
		},
	]
	for principal, values in sorted(by_principal.items()):
		data.append({"row_type": "Principal / Ledger Bucket", "principal": principal, **values, "indent": 1})
	data.append(
		{
			"row_type": "Reconciliation Difference",
			"principal": "Payment Ledger minus breakdown",
			"outstanding": difference,
			"bold": 1,
		}
	)
	return get_columns(), data


def get_columns():
	return [
		{"label": _("Level"), "fieldname": "row_type", "fieldtype": "Data", "width": 180},
		{"label": _("Supplier / Principal"), "fieldname": "principal", "fieldtype": "Data", "width": 260},
		{"label": _("Invoiced"), "fieldname": "invoiced", "fieldtype": "Currency", "width": 140},
		{"label": _("Allocated Payments"), "fieldname": "paid", "fieldtype": "Currency", "width": 150},
		{"label": _("Returns / Credits"), "fieldname": "credit_note", "fieldtype": "Currency", "width": 150},
		{"label": _("Outstanding"), "fieldname": "outstanding", "fieldtype": "Currency", "width": 150},
	]
