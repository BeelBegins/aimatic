"""Phase B of the szl multi-branch setup: Tax Withholding Group/Category,
the generic Mode of Payment masters, and the per-branch Cost Center ->
Warehouses -> Branch structure for S2/S4/S5/S6/S7.

Run via bench console (this directory's scripts are not an importable Python
package -- see setup_szl.md for the exact invocation):
    exec(open("apps/aimatic/ipos_data_migration/setup_szl_branches.py").read())
    main()

Must run after setup_szl_company.py (needs the Company, its root Cost
Center, and the "Withholding Tax Payable" account to already exist).

Deliberately does NOT create POS Profiles, per-branch Mode of Payment, or
per-branch stock/cash ledger accounts for these 5 branches -- confirmed with
the user as out of scope for this pass. Those get added per-branch right
before that branch's own go-live.

Safe to re-run: every step is guarded by a frappe.db.exists() check.
"""

import types

import frappe

_REF_DATA_PATH = "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/szl_reference_data.py"
_ref_ns = {}
exec(compile(open(_REF_DATA_PATH).read(), _REF_DATA_PATH, "exec"), _ref_ns)
ref = types.SimpleNamespace(**_ref_ns)

WHT_RATE_FROM_DATE = ref.WHT_RATE_FROM_DATE
WHT_RATE_TO_DATE = ref.WHT_RATE_TO_DATE


def get_or_create_wht_payable_account(company):
	"""Mirrors import_siezal_suppliers.py's get_or_create_wht_payable_account
	exactly (not imported directly -- that module calls run() unconditionally
	at import time, which would trigger an unrelated Excel-file import)."""
	abbr = frappe.get_cached_value("Company", company, "abbr")
	account_name = f"Withholding Tax Payable - {abbr}"
	if frappe.db.exists("Account", account_name):
		return account_name

	parent = frappe.db.get_value(
		"Account", {"company": company, "account_name": "Duties and Taxes", "is_group": 1}
	)
	if not parent:
		frappe.throw(f"No 'Duties and Taxes' group account found under {company}.")

	account = frappe.new_doc("Account")
	account.account_name = "Withholding Tax Payable"
	account.company = company
	account.parent_account = parent
	account.account_type = "Tax"
	account.insert(ignore_permissions=True)
	frappe.db.commit()
	return account.name


def get_or_create_wht_group(name):
	if frappe.db.exists("Tax Withholding Group", name):
		return name
	doc = frappe.new_doc("Tax Withholding Group")
	doc.group_name = name
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	print(f"Created Tax Withholding Group {doc.name}")
	return doc.name


def get_or_create_wht_category(rate, company, wht_account):
	label = "Exempt" if not rate else f"WHT {rate:g}%"
	if frappe.db.exists("Tax Withholding Category", label):
		return label

	doc = frappe.new_doc("Tax Withholding Category")
	doc.name = label
	doc.category_name = label
	doc.tax_deduction_basis = "Gross Total"
	doc.append(
		"rates",
		{
			"from_date": WHT_RATE_FROM_DATE,
			"to_date": WHT_RATE_TO_DATE,
			"tax_withholding_rate": rate,
		},
	)
	doc.append("accounts", {"company": company, "account": wht_account})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	print(f"Created Tax Withholding Category {doc.name}")
	return doc.name


def create_tax_withholding(company):
	wht_account = get_or_create_wht_payable_account(company)
	get_or_create_wht_group("Filers")
	get_or_create_wht_group("Non-Filers")
	for rate in ref.TAX_WITHHOLDING_RATES:
		get_or_create_wht_category(rate, company, wht_account)


def create_mode_of_payment_masters():
	for entry in ref.MODE_OF_PAYMENT_MASTERS:
		if frappe.db.exists("Mode of Payment", entry["name"]):
			print(f"Mode of Payment {entry['name']} already exists")
			continue
		doc = frappe.new_doc("Mode of Payment")
		doc.mode_of_payment = entry["name"]
		doc.type = entry["type"]
		doc.enabled = entry["enabled"]
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		print(f"Created Mode of Payment {doc.name}")


def _get_company_root_cost_center(company):
	root = frappe.db.get_value(
		"Cost Center", {"company": company, "is_group": 1, "parent_cost_center": ["is", "not set"]}
	)
	if not root:
		root = frappe.db.get_value("Cost Center", {"company": company, "is_group": 1}, order_by="lft asc")
	if not root:
		frappe.throw(f"No root group Cost Center found for {company}.")
	return root


def create_cost_center(company, branch_cfg, root_cost_center):
	name = branch_cfg["branch_name"]
	full_name = f"{name} - {ref.COMPANY_ABBR}"
	if frappe.db.exists("Cost Center", full_name):
		return full_name

	doc = frappe.new_doc("Cost Center")
	doc.cost_center_name = name
	doc.company = company
	doc.parent_cost_center = root_cost_center
	doc.is_group = 0
	doc.insert(ignore_permissions=True)
	print(f"Created Cost Center {doc.name}")
	return doc.name


def create_branch_stub(company, branch_cfg):
	"""Insert a bare Branch (just `branch` + `company`) first -- Warehouse.
	custom_branch is a Link field, and Frappe validates that a Link target
	actually exists at insert time, so the Branch must exist before any
	Warehouse can reference it. Still triggers initialize_branch_selling_
	price_list (Branch's after_insert hook), which doesn't depend on cost_
	center/warehouse being set."""
	name = branch_cfg["branch_name"]
	if frappe.db.exists("Branch", name):
		return name

	doc = frappe.new_doc("Branch")
	doc.branch = name
	doc.company = company
	# cost_center/finished_goods_warehouse are mandatory custom fields, but
	# can't be set yet -- their own Warehouse.custom_branch Link requires
	# this Branch to already exist. Filled in by link_branch() below.
	doc.insert(ignore_permissions=True, ignore_mandatory=True)
	print(f"Created Branch {doc.name} (stub -- triggers Selling + Foodpanda Price Lists)")
	return doc.name


def create_warehouses(company, branch_cfg):
	name = branch_cfg["branch_name"]
	fg_full_name = f"{name} - {ref.COMPANY_ABBR}"
	rejected_name = f"Rejected {name}"
	rejected_full_name = f"{rejected_name} - {ref.COMPANY_ABBR}"

	if not frappe.db.exists("Warehouse", fg_full_name):
		doc = frappe.new_doc("Warehouse")
		doc.warehouse_name = name
		doc.company = company
		doc.custom_branch = name
		doc.insert(ignore_permissions=True)
		fg_full_name = doc.name
		print(f"Created Warehouse {doc.name}")

	if not frappe.db.exists("Warehouse", rejected_full_name):
		doc = frappe.new_doc("Warehouse")
		doc.warehouse_name = rejected_name
		doc.company = company
		doc.custom_branch = name
		doc.insert(ignore_permissions=True)
		rejected_full_name = doc.name
		print(f"Created Warehouse {doc.name}")

	return fg_full_name, rejected_full_name


def link_branch(branch_name, cost_center, fg_warehouse, rejected_warehouse):
	doc = frappe.get_doc("Branch", branch_name)
	changed = False
	for fieldname, value in (
		("cost_center", cost_center),
		("finished_goods_warehouse", fg_warehouse),
		("rejected_warehouse", rejected_warehouse),
	):
		if doc.get(fieldname) != value:
			doc.set(fieldname, value)
			changed = True
	if changed:
		doc.save(ignore_permissions=True)
		print(f"Linked Branch {branch_name} to its Cost Center/Warehouses")
	else:
		print(f"Branch {branch_name} already linked")


def setup_branch(company, root_cost_center, branch_cfg):
	try:
		create_branch_stub(company, branch_cfg)
		cost_center = create_cost_center(company, branch_cfg, root_cost_center)
		fg_warehouse, rejected_warehouse = create_warehouses(company, branch_cfg)
		link_branch(branch_cfg["branch_name"], cost_center, fg_warehouse, rejected_warehouse)
		frappe.db.commit()
	except Exception as exc:
		frappe.db.rollback()
		print(f"FAILED branch setup for {branch_cfg['branch_name']} -> {exc}")


def main():
	company = ref.COMPANY_NAME
	if not frappe.db.exists("Company", company):
		frappe.throw(f"Company {company} does not exist -- run setup_szl_company.main() first.")

	# Suppress doc-event Notification alerts during this one-time setup --
	# see setup_szl_company.py's main() for why.
	previous_in_patch = frappe.flags.in_patch
	frappe.flags.in_patch = True
	try:
		create_tax_withholding(company)
		create_mode_of_payment_masters()

		root_cost_center = _get_company_root_cost_center(company)
		for branch_cfg in ref.BRANCHES:
			setup_branch(company, root_cost_center, branch_cfg)
	finally:
		frappe.flags.in_patch = previous_in_patch

	print("Phase B (branches) complete.")
