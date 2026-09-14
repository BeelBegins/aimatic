from __future__ import annotations

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company") or not filters.get("supplier"):
		frappe.throw(_("Company and Supplier are mandatory."))
	if not frappe.has_permission("Supplier", "read", filters.supplier):
		frappe.throw(_("Not permitted to read this Supplier."), frappe.PermissionError)
	conditions = [
		"pr.company = %(company)s",
		"pr.supplier = %(supplier)s",
		"pr.docstatus = 1",
		"ifnull(pr.custom_principal, '') = ''",
	]
	if filters.get("from_date"):
		conditions.append("pr.posting_date >= %(from_date)s")
	if filters.get("to_date"):
		conditions.append("pr.posting_date <= %(to_date)s")
	rows = frappe.db.sql(
		f"""
		with evidence as (
			select pri.item_code, pr.custom_principal principal, pr.posting_date, pr.name voucher_no
			from `tabPurchase Receipt` pr join `tabPurchase Receipt Item` pri on pri.parent=pr.name
			where pr.company=%(company)s and pr.supplier=%(supplier)s and pr.docstatus=1 and ifnull(pr.custom_principal,'')!=''
			union all
			select pii.item_code, pi.custom_principal, pi.posting_date, pi.name
			from `tabPurchase Invoice` pi join `tabPurchase Invoice Item` pii on pii.parent=pi.name
			where pi.company=%(company)s and pi.supplier=%(supplier)s and pi.docstatus=1 and ifnull(pi.custom_principal,'')!=''
		), targets as (
			select pr.name purchase_receipt, pr.posting_date, pri.name source_row, pri.item_code,
				pri.item_name, pri.qty, pri.amount, i.custom_principal item_principal
			from `tabPurchase Receipt` pr join `tabPurchase Receipt Item` pri on pri.parent=pr.name
			join `tabItem` i on i.name=pri.item_code where {" and ".join(conditions)}
		)
		select t.*, count(distinct e.principal) evidence_count,
			group_concat(distinct e.principal order by e.principal separator ', ') later_principals,
			group_concat(distinct e.voucher_no order by e.posting_date, e.voucher_no separator ', ') evidence_vouchers,
			case
				when ifnull(t.item_principal,'')!='' then 'Approved Item Mapping'
				when count(distinct e.principal)=1 then 'Unique Later Evidence'
				when count(distinct e.principal)>1 then 'Conflicting Later Evidence'
				else 'No Later Evidence'
			end status
		from targets t left join evidence e on e.item_code=t.item_code and e.posting_date>=t.posting_date
		group by t.source_row order by t.posting_date,t.purchase_receipt,t.source_row
		""",
		filters,
		as_dict=True,
	)
	if filters.get("status"):
		rows = [row for row in rows if row.status == filters.status]
	return get_columns(), rows


def get_columns():
	return [
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 190},
		{
			"label": _("Purchase Receipt"),
			"fieldname": "purchase_receipt",
			"fieldtype": "Link",
			"options": "Purchase Receipt",
			"width": 190,
		},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 240},
		{
			"label": _("Approved Item Principal"),
			"fieldname": "item_principal",
			"fieldtype": "Link",
			"options": "Principal",
			"width": 170,
		},
		{
			"label": _("Later Principal Evidence"),
			"fieldname": "later_principals",
			"fieldtype": "Data",
			"width": 220,
		},
		{
			"label": _("Evidence Documents"),
			"fieldname": "evidence_vouchers",
			"fieldtype": "Data",
			"width": 280,
		},
		{"label": _("Quantity"), "fieldname": "qty", "fieldtype": "Float", "width": 90},
		{"label": _("Net Amount"), "fieldname": "amount", "fieldtype": "Currency", "width": 120},
		{"label": _("Source Row"), "fieldname": "source_row", "fieldtype": "Data", "hidden": 1},
	]
