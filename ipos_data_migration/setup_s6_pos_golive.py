"""S6 Khalid Block POS go-live masters on live szl.

Mirrors S7 Empire Heights: Cash account, Mode of Payment, walk-in Customer
(with default_price_list set), Food Panda customer, Counter + Foodpanda POS
Profiles. Copies the S7 profile then overrides branch accounts, including
``account_for_change_amount`` (S7 cash must not remain on S6). Idempotent.

    ns = {}
    path = "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/setup_s6_pos_golive.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()
"""

from __future__ import annotations

import frappe

TARGET_SITE = "szl"
COMPANY = "Siezal Supermarket"
BRANCH = "S6 - Khalid Block"
WAREHOUSE = "S6 - Khalid Block - SSM"
COST_CENTER = "S6 - Khalid Block - SSM"
SELLING_PRICE_LIST = "S6 - Khalid Block Selling Price List"
FOODPANDA_PRICE_LIST = "S6 - Khalid Block Foodpanda Price List"
CASH_ACCOUNT_NUMBER = "1114"
CASH_ACCOUNT_NAME = "Cash in Hand - S6KB"
PARENT_CASH = "1100 - Cash In Hand - SSM"
MODE_OF_PAYMENT = "Cash - S6KB"
WALK_IN = "S6 Walk in Customer"
FOODPANDA_CUSTOMER = "S6 Food Panda"
COUNTER_PROFILE = "S6 Counter 1"
FOODPANDA_PROFILE = "S6 Food Panda"
TEMPLATE_CASH_ACCOUNT = "1113 - Cash in Hand - S4WC - SSM"
TEMPLATE_COUNTER = "S4 Counter 1"
TEMPLATE_FOODPANDA = "S4 Food Panda"


def _assert_site():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing S6 POS go-live: expected {TARGET_SITE}, got {site}")
	for doctype, name in (
		("Branch", BRANCH),
		("Warehouse", WAREHOUSE),
		("Cost Center", COST_CENTER),
		("Price List", SELLING_PRICE_LIST),
		("Price List", FOODPANDA_PRICE_LIST),
		("Account", PARENT_CASH),
		("Account", TEMPLATE_CASH_ACCOUNT),
	):
		if not frappe.db.exists(doctype, name):
			frappe.throw(f"Missing {doctype} {name}")


def _ensure_cash_account() -> str:
	existing = frappe.db.get_value(
		"Account",
		{"company": COMPANY, "account_name": CASH_ACCOUNT_NAME, "is_group": 0},
		"name",
	)
	if existing:
		print("exists cash account", existing)
		return existing
	if frappe.db.exists("Account", {"company": COMPANY, "account_number": CASH_ACCOUNT_NUMBER}):
		frappe.throw(f"Account number {CASH_ACCOUNT_NUMBER} already used")
	template = frappe.get_doc("Account", TEMPLATE_CASH_ACCOUNT)
	account = frappe.new_doc("Account")
	account.account_name = CASH_ACCOUNT_NAME
	account.company = COMPANY
	account.parent_account = PARENT_CASH
	account.is_group = 0
	account.account_number = CASH_ACCOUNT_NUMBER
	account.root_type = template.root_type
	account.report_type = template.report_type
	account.account_type = template.account_type
	account.account_currency = template.account_currency
	account.insert(ignore_permissions=True)
	print("created cash account", account.name)
	return account.name


def _ensure_mode_of_payment(cash_account: str):
	if not frappe.db.exists("Mode of Payment", MODE_OF_PAYMENT):
		mop = frappe.new_doc("Mode of Payment")
		mop.mode_of_payment = MODE_OF_PAYMENT
		mop.type = "Cash"
		mop.enabled = 1
		mop.append("accounts", {"company": COMPANY, "default_account": cash_account})
		mop.insert(ignore_permissions=True)
		print("created mop", mop.name)
	else:
		mop = frappe.get_doc("Mode of Payment", MODE_OF_PAYMENT)
		if not any(row.company == COMPANY for row in mop.accounts):
			mop.append("accounts", {"company": COMPANY, "default_account": cash_account})
			mop.save(ignore_permissions=True)
		print("exists mop", mop.name)
	return MODE_OF_PAYMENT


def _ensure_customer(name: str, price_list: str, template_name: str):
	if frappe.db.exists("Customer", name):
		doc = frappe.get_doc("Customer", name)
		if doc.default_price_list != price_list:
			doc.default_price_list = price_list
			doc.save(ignore_permissions=True)
			print("updated customer price list", name, price_list)
		else:
			print("exists customer", name)
		return name
	template = frappe.get_doc("Customer", template_name)
	doc = frappe.new_doc("Customer")
	doc.customer_name = name
	doc.customer_type = template.customer_type or "Individual"
	doc.customer_group = template.customer_group or "Individual"
	doc.territory = template.territory or "Pakistan"
	doc.default_price_list = price_list
	doc.insert(ignore_permissions=True)
	print("created customer", doc.name, "default_price_list", doc.default_price_list)
	if doc.default_price_list != price_list:
		frappe.throw(f"{doc.name} missing default_price_list {price_list}")
	return doc.name


def _copy_profile(template_name: str, name: str, payments, **overrides):
	if frappe.db.exists("POS Profile", name):
		doc = frappe.get_doc("POS Profile", name)
		changed = []
		for field, value in overrides.items():
			if doc.get(field) != value:
				doc.set(field, value)
				changed.append(field)
		if changed:
			doc.save(ignore_permissions=True)
			print("updated profile", name, changed)
		else:
			print("exists profile", name)
		return name
	template = frappe.get_doc("POS Profile", template_name)
	doc = frappe.copy_doc(template)
	doc.name = name
	doc.applicable_for_users = []
	doc.payments = []
	for field, value in overrides.items():
		doc.set(field, value)
	for payment in payments:
		doc.append("payments", payment)
	doc.insert(ignore_permissions=True, set_name=name)
	print("created profile", doc.name)
	return doc.name


def main():
	_assert_site()
	cash_account = _ensure_cash_account()
	_ensure_mode_of_payment(cash_account)
	_ensure_customer(WALK_IN, SELLING_PRICE_LIST, "S4 Walk in Customer")
	_ensure_customer(FOODPANDA_CUSTOMER, FOODPANDA_PRICE_LIST, "S4 Food Panda")
	_copy_profile(
		TEMPLATE_COUNTER,
		COUNTER_PROFILE,
		payments=[
			{"mode_of_payment": "Credit Card", "default": 0},
			{"mode_of_payment": MODE_OF_PAYMENT, "default": 1},
		],
		company=COMPANY,
		warehouse=WAREHOUSE,
		branch=BRANCH,
		cost_center=COST_CENTER,
		write_off_cost_center=COST_CENTER,
		selling_price_list=SELLING_PRICE_LIST,
		customer=WALK_IN,
		account_for_change_amount=cash_account,
		custom_terminal_id="S6KB-1",
		custom_is_foodpanda_profile=0,
		custom_fbr_optional=1,
		disabled=1,
	)
	_copy_profile(
		TEMPLATE_FOODPANDA,
		FOODPANDA_PROFILE,
		payments=[{"mode_of_payment": "Food Panda Credit", "default": 1}],
		company=COMPANY,
		warehouse=WAREHOUSE,
		branch=BRANCH,
		cost_center=COST_CENTER,
		write_off_cost_center=COST_CENTER,
		selling_price_list=FOODPANDA_PRICE_LIST,
		customer=FOODPANDA_CUSTOMER,
		account_for_change_amount=cash_account,
		custom_terminal_id="S6FP",
		custom_is_foodpanda_profile=1,
		custom_fbr_optional=1,
		disabled=1,
	)
	walk_in_pl = frappe.db.get_value("Customer", WALK_IN, "default_price_list")
	fp_pl = frappe.db.get_value("Customer", FOODPANDA_CUSTOMER, "default_price_list")
	if walk_in_pl != SELLING_PRICE_LIST:
		frappe.throw(f"Walk-in default_price_list is {walk_in_pl!r}, expected {SELLING_PRICE_LIST}")
	if fp_pl != FOODPANDA_PRICE_LIST:
		frappe.throw(f"Foodpanda customer default_price_list is {fp_pl!r}, expected {FOODPANDA_PRICE_LIST}")
	frappe.db.commit()
	print(
		{
			"cash_account": cash_account,
			"mode_of_payment": MODE_OF_PAYMENT,
			"walk_in": WALK_IN,
			"walk_in_price_list": walk_in_pl,
			"foodpanda_customer": FOODPANDA_CUSTOMER,
			"foodpanda_customer_price_list": fp_pl,
			"counter": COUNTER_PROFILE,
			"foodpanda_profile": FOODPANDA_PROFILE,
			"fbr_pos_id_unchanged": frappe.db.get_value(
				"FBR Integration Settings",
				"Siezal Supermarket-S6 - Khalid Block",
				"pos_id",
			),
		}
	)
