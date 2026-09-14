from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate

from aimatic.principal_reporting import UNCLASSIFIED_PRINCIPAL


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)
	warehouse_filters = {"company": filters.company, "is_group": 0, "disabled": 0}
	if filters.get("branch"):
		warehouse_filters["custom_branch"] = filters.branch
	warehouses = frappe.get_list("Warehouse", filters=warehouse_filters, pluck="name")
	values = {**filters, "warehouses": tuple(warehouses), "unclassified": UNCLASSIFIED_PRINCIPAL}
	data = defaultdict(_empty_row)
	_merge(data, _get_purchases(filters, values))
	_merge(data, _get_sales(filters, values))
	if warehouses:
		_merge(data, _get_cogs(filters, values))
		_merge(data, _get_stock_values(filters, values))
		_merge(data, _get_expiry_damage(filters, values))
	rows = []
	for principal, row in sorted(data.items()):
		row["principal"] = principal
		row["gross_margin"] = flt(row["sales"]) - flt(row["cogs"])
		row["gross_margin_percent"] = row["gross_margin"] / row["sales"] * 100 if row["sales"] else 0
		row["average_stock_value"] = (flt(row["opening_stock_value"]) + flt(row["closing_stock_value"])) / 2
		row["stock_turnover"] = row["cogs"] / row["average_stock_value"] if row["average_stock_value"] else 0
		rows.append(row)
	return get_columns(), rows


def validate_filters(filters):
	for fieldname in ("company", "from_date", "to_date"):
		if not filters.get(fieldname):
			frappe.throw(_("Company, From Date and To Date are mandatory."))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date must be before To Date."))


def _empty_row():
	return {
		fieldname: 0.0
		for fieldname in (
			"purchases",
			"purchase_returns",
			"sales",
			"cogs",
			"opening_stock_value",
			"closing_stock_value",
			"expiry_damage",
		)
	}


def _merge(target, rows):
	for row in rows:
		principal = row.pop("principal")
		for fieldname, value in row.items():
			target[principal][fieldname] += flt(value)


def _principal_expr(alias="i"):
	return f"coalesce(nullif({alias}.custom_principal, ''), %(unclassified)s)"


def _get_purchases(filters, values):
	conditions = [
		"pi.docstatus = 1",
		"pi.company = %(company)s",
		"pi.posting_date between %(from_date)s and %(to_date)s",
	]
	if filters.get("supplier"):
		conditions.append("pi.supplier = %(supplier)s")
	if filters.get("branch"):
		conditions.append("pi.branch = %(branch)s")
	if filters.get("principal"):
		conditions.append("i.custom_principal = %(principal)s")
	return frappe.db.sql(
		f"""select {_principal_expr()} principal,
			sum(case when pi.is_return = 0 then pii.base_net_amount else 0 end) purchases,
			-sum(case when pi.is_return = 1 then pii.base_net_amount else 0 end) purchase_returns
		from `tabPurchase Invoice Item` pii join `tabPurchase Invoice` pi on pi.name=pii.parent
		join `tabItem` i on i.name=pii.item_code where {" and ".join(conditions)} group by principal""",
		values,
		as_dict=True,
	)


def _get_sales(filters, values):
	conditions = [
		"si.docstatus = 1",
		"si.company = %(company)s",
		"si.posting_date between %(from_date)s and %(to_date)s",
	]
	if filters.get("branch"):
		conditions.append("si.branch = %(branch)s")
	if filters.get("principal"):
		conditions.append("i.custom_principal = %(principal)s")
	standard = frappe.db.sql(
		f"""select {_principal_expr()} principal, sum(sii.base_net_amount) sales
		from `tabSales Invoice Item` sii join `tabSales Invoice` si on si.name=sii.parent
		join `tabItem` i on i.name=sii.item_code where ifnull(si.is_pos,0)=0 and {" and ".join(conditions)} group by principal""",
		values,
		as_dict=True,
	)
	pos_conditions = [condition.replace("si.", "pos.") for condition in conditions]
	pos = frappe.db.sql(
		f"""select {_principal_expr()} principal, sum(pii.base_net_amount) sales
		from `tabPOS Invoice Item` pii join `tabPOS Invoice` pos on pos.name=pii.parent
		join `tabItem` i on i.name=pii.item_code where {" and ".join(pos_conditions)} group by principal""",
		values,
		as_dict=True,
	)
	return standard + pos


def _warehouse_conditions(filters, date_condition):
	conditions = ["sle.warehouse in %(warehouses)s", date_condition]
	if filters.get("principal"):
		conditions.append("i.custom_principal = %(principal)s")
	return conditions


def _get_cogs(filters, values):
	conditions = _warehouse_conditions(filters, "sle.posting_date between %(from_date)s and %(to_date)s")
	conditions.append("sle.voucher_type in ('Sales Invoice', 'POS Invoice')")
	return frappe.db.sql(
		f"""select {_principal_expr()} principal, -sum(sle.stock_value_difference) cogs
		from `tabStock Ledger Entry` sle join `tabItem` i on i.name=sle.item_code
		join `tabWarehouse` w on w.name=sle.warehouse where sle.is_cancelled=0 and {" and ".join(conditions)} group by principal""",
		values,
		as_dict=True,
	)


def _get_stock_values(filters, values):
	conditions = _warehouse_conditions(filters, "sle.posting_date <= %(to_date)s")
	return frappe.db.sql(
		f"""select {_principal_expr()} principal,
			sum(case when sle.posting_date < %(from_date)s then sle.stock_value_difference else 0 end) opening_stock_value,
			sum(sle.stock_value_difference) closing_stock_value
		from `tabStock Ledger Entry` sle join `tabItem` i on i.name=sle.item_code
		join `tabWarehouse` w on w.name=sle.warehouse where sle.is_cancelled=0 and {" and ".join(conditions)} group by principal""",
		values,
		as_dict=True,
	)


def _get_expiry_damage(filters, values):
	conditions = _warehouse_conditions(filters, "sle.posting_date between %(from_date)s and %(to_date)s")
	conditions.extend(
		[
			"sle.voucher_type = 'Stock Entry'",
			"(se.stock_entry_type like '%%expir%%' or se.stock_entry_type like '%%damag%%')",
		]
	)
	return frappe.db.sql(
		f"""select {_principal_expr()} principal, -sum(least(sle.stock_value_difference, 0)) expiry_damage
		from `tabStock Ledger Entry` sle join `tabStock Entry` se on se.name=sle.voucher_no and se.docstatus=1
		join `tabItem` i on i.name=sle.item_code join `tabWarehouse` w on w.name=sle.warehouse
		where sle.is_cancelled=0 and {" and ".join(conditions)} group by principal""",
		values,
		as_dict=True,
	)


def get_columns():
	return [
		{"label": _("Principal"), "fieldname": "principal", "fieldtype": "Data", "width": 180},
		{"label": _("Purchases"), "fieldname": "purchases", "fieldtype": "Currency", "width": 130},
		{
			"label": _("Purchase Returns"),
			"fieldname": "purchase_returns",
			"fieldtype": "Currency",
			"width": 140,
		},
		{"label": _("Sales"), "fieldname": "sales", "fieldtype": "Currency", "width": 130},
		{"label": _("COGS"), "fieldname": "cogs", "fieldtype": "Currency", "width": 130},
		{"label": _("Gross Margin"), "fieldname": "gross_margin", "fieldtype": "Currency", "width": 140},
		{"label": _("GM %"), "fieldname": "gross_margin_percent", "fieldtype": "Percent", "width": 90},
		{"label": _("Expiry / Damage"), "fieldname": "expiry_damage", "fieldtype": "Currency", "width": 140},
		{
			"label": _("Opening Stock Value"),
			"fieldname": "opening_stock_value",
			"fieldtype": "Currency",
			"width": 150,
		},
		{
			"label": _("Closing Stock Value"),
			"fieldname": "closing_stock_value",
			"fieldtype": "Currency",
			"width": 150,
		},
		{
			"label": _("Average Stock Value"),
			"fieldname": "average_stock_value",
			"fieldtype": "Currency",
			"width": 150,
		},
		{
			"label": _("Stock Turnover"),
			"fieldname": "stock_turnover",
			"fieldtype": "Float",
			"precision": 2,
			"width": 110,
		},
	]
