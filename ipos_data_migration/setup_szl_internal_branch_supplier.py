"""Phase C of the szl multi-branch setup: the Inter-Branch Payable account
tree and the Internal Branch Supplier record used to book stock/money
movement between S1 (on the separate `siezal` site) and any of this site's
branches (S2/S4/S5/S6/S7), since a real ERPNext Material Transfer is not
possible across two separate Frappe site databases.

Run via bench console (this directory's scripts are not an importable Python
package -- see setup_szl.md for the exact invocation):
    exec(open("apps/aimatic/ipos_data_migration/setup_szl_internal_branch_supplier.py").read())
    main()

Must run after setup_szl_branches.py (all 5 Branches must already exist, so
each gets its own Inter-Branch Payable account).

IMPORTANT -- read before using this Supplier on a real transaction: no
default Party Account is set on this Supplier, deliberately. Every Purchase
Invoice/Receipt against "Internal Branch - S1 (Ghouri Town VIP)" must have
its `credit_to` field explicitly set to the correct branch's own
"Inter-Branch Payable - <code> - SSM" account -- never rely on a Company-
level default, and never post to the normal Trade Creditors account. Do not
apply purchase tax or Withholding Tax to these transactions. See
setup_szl.md for the full rationale.

Safe to re-run: every step is guarded by a frappe.db.exists() check.
"""

import types

import frappe

_REF_DATA_PATH = "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/szl_reference_data.py"
_ref_ns = {}
exec(compile(open(_REF_DATA_PATH).read(), _REF_DATA_PATH, "exec"), _ref_ns)
ref = types.SimpleNamespace(**_ref_ns)


def _get_group_account(company, account_name):
	return frappe.db.get_value("Account", {"company": company, "account_name": account_name, "is_group": 1})


def _get_leaf_account(company, account_name):
	return frappe.db.get_value("Account", {"company": company, "account_name": account_name, "is_group": 0})


def create_inter_branch_group_account(company):
	# Existence check is by account_name field, not a guessed full document
	# name -- Account's autoname includes the account_number prefix
	# ("2140 - Inter-Branch Accounts - SSM"), which a plain
	# "<name> - <abbr>" guess would miss, causing a duplicate-insert on re-run.
	account_name = ref.INTER_BRANCH_GROUP_ACCOUNT_NAME
	existing = frappe.db.get_value(
		"Account", {"company": company, "account_name": account_name, "is_group": 1}
	)
	if existing:
		print(f"Account {existing} already exists")
		return existing

	parent = _get_group_account(company, ref.INTER_BRANCH_GROUP_PARENT_NAME)
	if not parent:
		frappe.throw(f"No group account '{ref.INTER_BRANCH_GROUP_PARENT_NAME}' found under {company}.")

	doc = frappe.new_doc("Account")
	doc.account_name = account_name
	doc.company = company
	doc.parent_account = parent
	doc.root_type = "Liability"
	doc.is_group = 1
	doc.account_number = ref.INTER_BRANCH_GROUP_ACCOUNT_NUMBER
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	print(f"Created group Account {doc.name}")
	return doc.name


def create_inter_branch_payable_accounts(company, group_account):
	accounts = {}
	for branch_cfg in ref.BRANCHES:
		store_code = branch_cfg["store_code"]
		account_name = f"Inter-Branch Payable - {store_code}"
		existing = _get_leaf_account(company, account_name)
		if existing:
			accounts[store_code] = existing
			continue

		doc = frappe.new_doc("Account")
		doc.account_name = account_name
		doc.company = company
		doc.parent_account = group_account
		doc.root_type = "Liability"
		doc.is_group = 0
		doc.account_type = "Payable"
		doc.account_number = ref.INTER_BRANCH_PAYABLE_NUMBERS[store_code]
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		accounts[store_code] = doc.name
		print(f"Created Account {doc.name}")

	return accounts


def create_supplier_group_tree():
	root_name = "All Supplier Groups"
	if not frappe.db.exists("Supplier Group", root_name):
		doc = frappe.new_doc("Supplier Group")
		doc.supplier_group_name = root_name
		doc.is_group = 1
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		print(f"Created Supplier Group {doc.name}")

	group_name = ref.INTERNAL_BRANCH_SUPPLIER_GROUP
	if frappe.db.exists("Supplier Group", group_name):
		print(f"Supplier Group {group_name} already exists")
		return group_name

	doc = frappe.new_doc("Supplier Group")
	doc.supplier_group_name = group_name
	doc.parent_supplier_group = root_name
	doc.is_group = 0
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	print(f"Created Supplier Group {doc.name}")
	return doc.name


def create_internal_branch_supplier(supplier_group):
	name = ref.INTERNAL_BRANCH_SUPPLIER_NAME
	if frappe.db.exists("Supplier", name):
		print(f"Supplier {name} already exists")
		return name

	doc = frappe.new_doc("Supplier")
	doc.supplier_name = name
	doc.supplier_group = supplier_group
	doc.supplier_type = "Company"
	doc.supplier_details = ref.INTERNAL_BRANCH_SUPPLIER_DESCRIPTION
	# Deliberately no tax_withholding_group/tax_withholding_category and no
	# default Party Account -- see module docstring.
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	print(f"Created Supplier {doc.name}")
	return doc.name


def main():
	company = ref.COMPANY_NAME
	if not frappe.db.exists("Company", company):
		frappe.throw(f"Company {company} does not exist -- run setup_szl_company.main() first.")
	missing_branches = [
		b["branch_name"] for b in ref.BRANCHES if not frappe.db.exists("Branch", b["branch_name"])
	]
	if missing_branches:
		frappe.throw(f"Branches not yet set up: {missing_branches} -- run setup_szl_branches.main() first.")

	# Suppress doc-event Notification alerts during this one-time setup --
	# see setup_szl_company.py's main() for why.
	previous_in_patch = frappe.flags.in_patch
	frappe.flags.in_patch = True
	try:
		group_account = create_inter_branch_group_account(company)
		create_inter_branch_payable_accounts(company, group_account)
		supplier_group = create_supplier_group_tree()
		create_internal_branch_supplier(supplier_group)
	finally:
		frappe.flags.in_patch = previous_in_patch

	print("Phase C (Internal Branch Supplier) complete.")
