"""Read-only data endpoints for the CEO Dashboard."""

from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, today

from aimatic.ai.basket_analysis import CALCULATION_VERSION, calculate_basket_pairs
from aimatic.sales_dashboard import api as sales_dashboard

_ALLOWED_ROLES = {"System Manager", "Sales Manager", "Accounts Manager", "Stock Manager"}
_BASKET_TRANSACTION_LIMIT = 5000
_MIN_JOINT_BASKETS = 10


def _check_access():
	if frappe.session.user == "Guest" or not (set(frappe.get_roles()) & _ALLOWED_ROLES):
		frappe.throw(_("Not permitted to view the CEO Dashboard."), frappe.PermissionError)
	for doctype in ("POS Invoice", "Bin", "Purchase Invoice"):
		if not frappe.has_permission(doctype, ptype="read"):
			frappe.throw(_("Not permitted to view executive business data."), frappe.PermissionError)


def _date_range(date_from, date_to):
	date_to = getdate(date_to) if date_to else getdate(today())
	date_from = getdate(date_from) if date_from else date_to
	return (date_to, date_from) if date_from > date_to else (date_from, date_to)


def _scope(company=None, branch=None, warehouse=None):
	_check_access()
	if warehouse:
		if not frappe.has_permission("Warehouse", ptype="read", doc=warehouse):
			frappe.throw(_("Not permitted to view this warehouse."), frappe.PermissionError)
		branch = frappe.db.get_value("Warehouse", warehouse, "custom_branch")
		if not branch:
			frappe.throw(_("This warehouse is not assigned to a Branch."), frappe.ValidationError)
	company = company or frappe.defaults.get_user_default("Company") or frappe.defaults.get_default("company")
	if not company or not frappe.db.exists("Company", company):
		frappe.throw(_("A valid Company is required."))
	if not frappe.has_permission("Company", ptype="read", doc=company):
		frappe.throw(_("Not permitted to view this company."), frappe.PermissionError)
	if branch and not frappe.has_permission("Branch", ptype="read", doc=branch):
		frappe.throw(_("Not permitted to view this branch."), frappe.PermissionError)
	currency = frappe.get_cached_value("Company", company, "default_currency")
	branches = sales_dashboard._resolve_branch_filter(company, branch)
	filters = {"company": company, "disabled": 0}
	if branches is not None:
		filters["custom_branch"] = ["in", branches or [""]]
	warehouses = [warehouse] if warehouse else frappe.get_list("Warehouse", filters=filters, pluck="name")
	return {"company": company, "currency": currency, "branches": branches, "branch": branch, "warehouse": warehouse, "warehouses": warehouses}


def _pos_filter(scope):
	if scope["branches"] is None:
		return "", {}
	if not scope["branches"]:
		return " AND 1 = 0", {}
	return " AND COALESCE(pi.branch, pp.branch) IN %(branches)s", {"branches": tuple(scope["branches"])}


@frappe.whitelist()
def get_scope(company=None):
	scope = _scope(company)
	branches = frappe.get_list("Branch", filters={"company": scope["company"]}, fields=["name"], order_by="name")
	if scope["branches"] is not None:
		branches = [row for row in branches if row.name in set(scope["branches"])]
	warehouses = frappe.get_list("Warehouse", filters={"company": scope["company"], "disabled": 0, "custom_branch": ["is", "set"]}, fields=["name", "custom_branch"], order_by="custom_branch, name")
	if scope["branches"] is not None:
		warehouses = [row for row in warehouses if row.custom_branch in set(scope["branches"])]
	return {"company": scope["company"], "currency": scope["currency"], "branches": [row.name for row in branches], "warehouses": [{"name": row.name, "branch": row.custom_branch} for row in warehouses]}


@frappe.whitelist()
def get_snapshot(company=None, date_from=None, date_to=None, branch=None, warehouse=None):
	scope = _scope(company, branch, warehouse)
	date_from, date_to = _date_range(date_from, date_to)
	sales = sales_dashboard._get_range_kpis(scope["company"], date_from, date_to, scope["branches"])
	branch_rows = sales_dashboard._get_branch_comparison(scope["company"], date_from, date_to, scope["branches"])
	customers = sales_dashboard._get_top_customers(scope["company"], date_from, date_to, scope["branches"])
	stock_filter = ""
	stock_params = {"company": scope["company"]}
	if scope["warehouses"]:
		stock_filter = " AND b.warehouse IN %(warehouses)s"
		stock_params["warehouses"] = tuple(scope["warehouses"])
	elif scope["branches"] is not None:
		stock_filter = " AND 1 = 0"
	stock = frappe.db.sql(f"""SELECT COALESCE(SUM(b.stock_value), 0) AS stock_value,
		COALESCE(SUM(CASE WHEN b.actual_qty < 0 THEN 1 ELSE 0 END), 0) AS negative_stock_skus
		FROM `tabBin` b INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
		WHERE w.company = %(company)s AND w.disabled = 0 {stock_filter}""", stock_params, as_dict=True)[0]
	overdue = frappe.db.sql("""SELECT COALESCE(SUM(outstanding_amount), 0) AS amount FROM `tabPurchase Invoice`
		WHERE docstatus = 1 AND company = %(company)s AND outstanding_amount > 0.005 AND due_date < CURDATE()""", {"company": scope["company"]}, as_dict=True)[0]
	pos_filter, pos_params = _pos_filter(scope)
	failures = frappe.db.sql(f"""SELECT COUNT(*) AS failed FROM `tabPOS Invoice` pi LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
		WHERE pi.docstatus = 1 AND pi.company = %(company)s AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
		AND pi.custom_fbr_status = 'Failed' {pos_filter}""", {"company": scope["company"], "date_from": date_from, "date_to": date_to, **pos_params}, as_dict=True)[0]
	stock_by_branch = {row.branch: row.stock_value for row in frappe.db.sql(f"""SELECT w.custom_branch AS branch, COALESCE(SUM(b.stock_value), 0) AS stock_value
		FROM `tabBin` b INNER JOIN `tabWarehouse` w ON w.name = b.warehouse WHERE w.company = %(company)s AND w.disabled = 0 {stock_filter} GROUP BY w.custom_branch""", stock_params, as_dict=True)}
	return {"company": scope["company"], "currency": scope["currency"], "date_from": str(date_from), "date_to": str(date_to), "scope": {"branch": scope["branch"], "warehouse": scope["warehouse"]},
		"kpis": {**sales, "stock_value": flt(stock.stock_value), "negative_stock_skus": cint(stock.negative_stock_skus), "overdue_supplier_bills": flt(overdue.amount), "failed_tax_invoices": cint(failures.failed)},
		"branches": [{**row, "tickets": 0, "failed_tax_invoices": 0, "stock_value": flt(stock_by_branch.get(row["branch"], 0))} for row in branch_rows],
		"top_customers": customers,
		"notes": {"overdue_supplier_bills": _("Company-wide payable; Purchase Invoice branch attribution is not assumed."), "stock_value": _("Current on-hand Bin value; it is a snapshot, not a date-range movement total.")}}


@frappe.whitelist()
def get_basket_report(company=None, date_from=None, date_to=None, branch=None, warehouse=None):
	scope = _scope(company, branch, warehouse)
	date_from, date_to = _date_range(date_from, date_to)
	pos_filter, pos_params = _pos_filter(scope)
	params = {"company": scope["company"], "date_from": date_from, "date_to": date_to, "limit": _BASKET_TRANSACTION_LIMIT, **pos_params}
	rows = frappe.db.sql(f"""SELECT selected.name AS transaction_id, pii.item_code, MAX(COALESCE(i.item_name, pii.item_name, pii.item_code)) AS item_name
		FROM (SELECT pi.name FROM `tabPOS Invoice` pi LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
			WHERE pi.docstatus = 1 AND pi.is_return = 0 AND pi.company = %(company)s AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s {pos_filter}
			ORDER BY pi.posting_date DESC, pi.posting_time DESC, pi.name DESC LIMIT %(limit)s) selected
		INNER JOIN `tabPOS Invoice Item` pii ON pii.parent = selected.name AND pii.docstatus = 1 LEFT JOIN `tabItem` i ON i.name = pii.item_code
		WHERE pii.stock_qty > 0 GROUP BY selected.name, pii.item_code""", params, as_dict=True)
	transactions, names = defaultdict(set), {}
	for row in rows:
		transactions[row.transaction_id].add(row.item_code)
		names[row.item_code] = row.item_name or row.item_code
	# 1% support returns nothing on a supermarket assortment — measured on szl,
	# the strongest pair of 168k reaches 0.98%. The absolute floor is what keeps
	# a short date range from ranking a one-basket coincidence at the top.
	pairs, quality = calculate_basket_pairs(transactions, minimum_transactions=100, minimum_support=0.003, minimum_confidence=0.10, limit=30, minimum_joint_transactions=_MIN_JOINT_BASKETS)
	for pair in pairs:
		pair["item_a_name"] = names.get(pair["item_a"], pair["item_a"])
		pair["item_b_name"] = names.get(pair["item_b"], pair["item_b"])
	return {"pairs": pairs, "transaction_count": len(transactions), "bounded": len(transactions) >= _BASKET_TRANSACTION_LIMIT, "calculation_version": CALCULATION_VERSION,
		"warning": _("At least 100 sales transactions are required for an item-affinity result.") if quality["insufficient_data"] else None}
