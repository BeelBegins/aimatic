from __future__ import annotations

import frappe
from frappe import _

from aimatic.principal_reporting import UNCLASSIFIED_PRINCIPAL


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Company is mandatory."))
	warehouse_filters = {"company": filters.company, "is_group": 0, "disabled": 0}
	if filters.get("branch"):
		warehouse_filters["custom_branch"] = filters.branch
	if filters.get("warehouse"):
		warehouse_filters["name"] = filters.warehouse
	warehouses = frappe.get_list("Warehouse", filters=warehouse_filters, pluck="name")
	if not warehouses:
		return get_columns(), []
	conditions = ["b.warehouse in %(warehouses)s"]
	if filters.get("principal"):
		conditions.append("i.custom_principal = %(principal)s")
	if not filters.get("include_zero_stock"):
		conditions.append("(b.actual_qty != 0 or b.stock_value != 0)")
	data = frappe.db.sql(
		f"""
		select
			coalesce(nullif(i.custom_principal, ''), %(unclassified)s) principal,
			b.item_code, i.item_name, w.custom_branch branch, b.warehouse,
			b.actual_qty quantity, b.stock_value,
			last_purchase.last_purchase_date
		from `tabBin` b
		join `tabItem` i on i.name = b.item_code
		join `tabWarehouse` w on w.name = b.warehouse
		left join (
			select pri.item_code, pri.warehouse, max(pr.posting_date) last_purchase_date
			from `tabPurchase Receipt Item` pri
			join `tabPurchase Receipt` pr on pr.name = pri.parent and pr.docstatus = 1
			where pr.company = %(company)s
			group by pri.item_code, pri.warehouse
		) last_purchase on last_purchase.item_code = b.item_code and last_purchase.warehouse = b.warehouse
		where {" and ".join(conditions)}
		order by principal, i.item_name, w.custom_branch, b.warehouse
		""",
		{**filters, "warehouses": tuple(warehouses), "unclassified": UNCLASSIFIED_PRINCIPAL},
		as_dict=True,
	)
	return get_columns(), data


def get_columns():
	return [
		{"label": _("Principal"), "fieldname": "principal", "fieldtype": "Data", "width": 170},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 240},
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 180},
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 220,
		},
		{"label": _("Quantity"), "fieldname": "quantity", "fieldtype": "Float", "width": 110},
		{"label": _("Stock Value"), "fieldname": "stock_value", "fieldtype": "Currency", "width": 140},
		{
			"label": _("Last Purchase Date"),
			"fieldname": "last_purchase_date",
			"fieldtype": "Date",
			"width": 130,
		},
	]
