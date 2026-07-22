"""Read-only checks for the retail finance foundation."""

from collections import defaultdict

import frappe
from frappe.utils import nowdate


def _result(check_id, label, status, message, route=None, details=None):
	return {
		"id": check_id,
		"label": label,
		"status": status,
		"message": message,
		"route": route,
		"details": details or [],
	}


def _has_field(doctype, fieldname):
	return bool(frappe.get_meta(doctype).get_field(fieldname))


def check_company(company):
	doc = frappe.get_cached_doc("Company", company)
	leaf_accounts = frappe.db.count("Account", {"company": company, "is_group": 0, "disabled": 0})
	missing = [label for field, label in (
		("default_currency", "default currency"),
		("default_receivable_account", "default receivable account"),
		("default_payable_account", "default payable account"),
		("cost_center", "default cost center"),
	) if not doc.get(field)]
	if not leaf_accounts:
		missing.append("active leaf accounts")
	status = "blocked" if missing else "pass"
	message = "Company accounting foundation is configured." if not missing else "Missing: " + ", ".join(missing) + "."
	return _result("company", "Company and chart of accounts", status, message, "List/Company")


def check_stores(company):
	branch_filters = {"company": company} if _has_field("Branch", "company") else {}
	branches = frappe.get_all("Branch", filters=branch_filters, fields=["name"])
	if not branches:
		return _result("stores", "Store mappings", "blocked", "No Branch is configured for this company.", "List/Branch")

	issues = []
	for row in branches:
		branch = frappe.get_cached_doc("Branch", row.name)
		cost_center = branch.get("cost_center")
		warehouse = branch.get("finished_goods_warehouse")
		if not cost_center:
			issues.append(f"{branch.name}: cost center is missing")
		elif not frappe.db.exists("Cost Center", {"name": cost_center, "company": company, "is_group": 0, "disabled": 0}):
			issues.append(f"{branch.name}: cost center is not an enabled leaf for this company")
		if not warehouse:
			issues.append(f"{branch.name}: finished-goods warehouse is missing")
		elif not frappe.db.exists("Warehouse", {"name": warehouse, "company": company, "is_group": 0, "disabled": 0}):
			issues.append(f"{branch.name}: warehouse is not an enabled leaf for this company")
		elif _has_field("Warehouse", "custom_branch") and frappe.db.get_value("Warehouse", warehouse, "custom_branch") != branch.name:
			issues.append(f"{branch.name}: warehouse Branch mapping does not match")

	dimension_ok = frappe.db.exists("Accounting Dimension", {"document_type": "Branch", "disabled": 0})
	if not dimension_ok:
		issues.append("Branch accounting dimension is missing or disabled")
	status = "blocked" if issues else "pass"
	message = f"{len(branches)} store mapping(s) are ready." if not issues else f"{len(issues)} store setup issue(s) require attention."
	return _result("stores", "Store mappings", status, message, "List/Branch", issues)


def check_pos(company):
	if not _has_field("POS Profile", "branch"):
		return _result("pos", "POS accounting context", "blocked", "The Branch accounting dimension is not installed on POS Profile.", "List/Accounting Dimension")
	profiles = frappe.get_all("POS Profile", filters={"company": company, "disabled": 0}, fields=["name", "warehouse", "cost_center", "branch"])
	if not profiles:
		return _result("pos", "POS accounting context", "warning", "No enabled POS Profile is configured.", "List/POS Profile")
	issues = []
	for profile in profiles:
		if not profile.branch:
			issues.append(f"{profile.name}: Branch is missing")
		if not profile.warehouse:
			issues.append(f"{profile.name}: warehouse is missing")
		elif not frappe.db.exists("Warehouse", {"name": profile.warehouse, "company": company, "is_group": 0, "disabled": 0}):
			issues.append(f"{profile.name}: warehouse is not an enabled leaf")
		elif profile.branch and _has_field("Warehouse", "custom_branch") and frappe.db.get_value("Warehouse", profile.warehouse, "custom_branch") != profile.branch:
			issues.append(f"{profile.name}: warehouse does not belong to the selected Branch")
		if not profile.cost_center:
			issues.append(f"{profile.name}: cost center is missing")
		elif not frappe.db.exists("Cost Center", {"name": profile.cost_center, "company": company, "is_group": 0, "disabled": 0}):
			issues.append(f"{profile.name}: cost center is not an enabled leaf")
	status = "blocked" if issues else "pass"
	message = f"{len(profiles)} enabled POS Profile(s) have accounting context." if not issues else f"{len(issues)} POS mapping issue(s) require attention."
	return _result("pos", "POS accounting context", status, message, "List/POS Profile", issues)


def check_cash_bank(company):
	accounts = frappe.get_all("Account", filters={"company": company, "is_group": 0, "disabled": 0, "account_type": ["in", ["Cash", "Bank"]]}, fields=["name", "account_type"])
	by_type = defaultdict(int)
	for account in accounts:
		by_type[account.account_type] += 1
	mappings = frappe.db.count("Mode of Payment Account", {"company": company, "default_account": ["is", "set"]})
	missing = [kind for kind in ("Cash", "Bank") if not by_type[kind]]
	if not mappings:
		missing.append("Mode of Payment account mappings")
	status = "warning" if missing else "pass"
	message = "Cash, bank, and payment mappings are available." if not missing else "Missing or incomplete: " + ", ".join(missing) + "."
	return _result("cash_bank", "Cash and bank controls", status, message, "List/Mode of Payment")


def check_receivables_payables(company):
	missing = []
	for account_type, label in (("Receivable", "receivable account"), ("Payable", "payable account")):
		if not frappe.db.exists("Account", {"company": company, "account_type": account_type, "is_group": 0, "disabled": 0}):
			missing.append(label)
	status = "blocked" if missing else "pass"
	message = "Receivable and payable control accounts are available." if not missing else "Missing: " + ", ".join(missing) + "."
	return _result("receivables_payables", "Receivable and payable ledgers", status, message, "List/Account")


def check_inventory(company):
	stock_items = frappe.db.count("Item", {"is_stock_item": 1, "disabled": 0})
	warehouse_filters = {"company": company, "is_group": 0, "disabled": 0}
	if _has_field("Warehouse", "custom_branch"):
		warehouse_filters["custom_branch"] = ["is", "set"]
	warehouses = frappe.db.count("Warehouse", warehouse_filters)
	valuation_method = frappe.db.get_single_value("Stock Settings", "valuation_method")
	missing = []
	if not stock_items:
		missing.append("active stock items")
	if not warehouses:
		missing.append("branch-mapped leaf warehouse")
	if not valuation_method:
		missing.append("default valuation method")
	status = "blocked" if missing else "pass"
	message = f"{stock_items} stock item(s) and {warehouses} branch warehouse(s) are available." if not missing else "Missing: " + ", ".join(missing) + "."
	return _result("inventory", "Inventory valuation foundation", status, message, "query-report/Stock Balance")


def check_tax(company):
	settings = frappe.db.count("FBR Integration Settings", {"company": company, "enabled": 1})
	tax_accounts = frappe.db.count("Account", {"company": company, "is_group": 0, "disabled": 0, "account_type": "Tax"})
	if settings and tax_accounts:
		return _result("tax", "Tax configuration", "pass", f"{settings} enabled FBR setting(s) and {tax_accounts} tax account(s) are configured.", "List/FBR Integration Settings")
	return _result("tax", "Tax configuration", "warning", "Tax applicability or configuration needs review; this does not change existing tax records.", "List/FBR Integration Settings")


def check_reporting(company):
	from erpnext.accounts.utils import get_fiscal_year

	if not _has_field("GL Entry", "branch"):
		return _result("reporting", "Forward branch reporting", "blocked", "The Branch accounting dimension is not installed on GL Entry.", "List/Accounting Dimension")
	fiscal_year = get_fiscal_year(nowdate(), company=company, raise_on_missing=False)
	if not fiscal_year:
		return _result("reporting", "Forward branch reporting", "warning", "No fiscal year covers today, so forward reporting cannot be checked.", "List/Fiscal Year")
	date_from, date_to = fiscal_year[1], fiscal_year[2]
	missing_branch = frappe.db.sql(
		"""
		select count(*)
		from `tabGL Entry` gle
		inner join `tabAccount` account on account.name = gle.account
		where gle.company = %(company)s
		  and gle.is_cancelled = 0
		  and gle.posting_date between %(date_from)s and %(date_to)s
		  and account.root_type in ('Income', 'Expense')
		  and coalesce(gle.branch, '') = ''
		""",
		{"company": company, "date_from": date_from, "date_to": date_to},
	)[0][0]
	if missing_branch:
		return _result("reporting", "Forward branch reporting", "warning", f"{missing_branch} current-fiscal-year income/expense GL row(s) have no Branch. Opening entries and unavailable history are not backfilled.", "query-report/General Ledger")
	return _result("reporting", "Forward branch reporting", "pass", "Current-fiscal-year income and expense GL rows are Branch-tagged. Opening entries remain the accepted cutover baseline.", "query-report/General Ledger")


CHECKS = {
	"company": check_company,
	"stores": check_stores,
	"pos": check_pos,
	"cash_bank": check_cash_bank,
	"receivables_payables": check_receivables_payables,
	"inventory": check_inventory,
	"tax": check_tax,
	"reporting": check_reporting,
}


def run_checks(company):
	return {key: check(company) for key, check in CHECKS.items()}
