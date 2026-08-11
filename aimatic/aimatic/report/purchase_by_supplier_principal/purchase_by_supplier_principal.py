from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)
	columns = get_columns()
	data = get_data(filters)
	return columns, data


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


def get_columns():
	return [
		{
			"label": _("Supplier"),
			"fieldname": "supplier",
			"fieldtype": "Link",
			"options": "Supplier",
			"width": 220,
		},
		{
			"label": _("Principal"),
			"fieldname": "principal",
			"fieldtype": "Link",
			"options": "Principal",
			"width": 160,
		},
		{
			"label": _("Invoices"),
			"fieldname": "invoice_count",
			"fieldtype": "Int",
			"width": 90,
		},
		{
			"label": _("Net Total"),
			"fieldname": "net_total",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("Grand Total"),
			"fieldname": "grand_total",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("Outstanding"),
			"fieldname": "outstanding_amount",
			"fieldtype": "Currency",
			"width": 130,
		},
	]


def get_data(filters):
	conditions = [
		"pi.docstatus = 1",
		"pi.company = %(company)s",
		"pi.posting_date between %(from_date)s and %(to_date)s",
	]
	values = {
		"company": filters.company,
		"from_date": filters.from_date,
		"to_date": filters.to_date,
	}

	if filters.get("supplier"):
		conditions.append("pi.supplier = %(supplier)s")
		values["supplier"] = filters.supplier
	if filters.get("principal"):
		conditions.append("pi.custom_principal = %(principal)s")
		values["principal"] = filters.principal
	if filters.get("branch"):
		conditions.append("pi.branch = %(branch)s")
		values["branch"] = filters.branch
	if not filters.get("include_returns"):
		conditions.append("pi.is_return = 0")

	where_clause = " and ".join(conditions)
	rows = frappe.db.sql(
		f"""
		select
			pi.supplier,
			ifnull(pi.custom_principal, '') as principal,
			count(*) as invoice_count,
			sum(pi.net_total) as net_total,
			sum(pi.grand_total) as grand_total,
			sum(pi.outstanding_amount) as outstanding_amount
		from `tabPurchase Invoice` pi
		where {where_clause}
		group by pi.supplier, ifnull(pi.custom_principal, '')
		order by pi.supplier, principal
		""",
		values,
		as_dict=True,
	)

	for row in rows:
		row.net_total = flt(row.net_total)
		row.grand_total = flt(row.grand_total)
		row.outstanding_amount = flt(row.outstanding_amount)
	return rows
