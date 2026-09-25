"""Assign S5/S6 POS cashiers, rename FP terminals, and add S5 HD.

Hard-targets ``szl``. Dry-run by default. Does not print or store passwords;
pass them only to ``main(apply=True, passwords={email: ...})``.

    ns = {}
    path = "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/assign_s5_s6_pos_users.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()
"""

from __future__ import annotations

import frappe
from frappe.utils.password import check_password, update_password

TARGET_SITE = "szl"
COMPANY = "Siezal Supermarket"
ROLE = "POS User"

S5_BRANCH = "S5 - Sector C"
S6_BRANCH = "S6 - Khalid Block"
S5_SELLING = "S5 - Sector C Selling Price List"
S5_FOODPANDA = "S5 - Sector C Foodpanda Price List"
S6_SELLING = "S6 - Khalid Block Selling Price List"
S6_FOODPANDA = "S6 - Khalid Block Foodpanda Price List"
S5_CASH = "1115 - Cash in Hand - S5SC - SSM"
S6_CASH = "1114 - Cash in Hand - S6KB - SSM"

RENAMES = (
	("S5 Food Panda", "S5 FP1", "S5FP1"),
	("S6 Food Panda", "S6 FP1", "S6FP1"),
)

HD_PROFILE = "S5 HD"
HD_CUSTOMER = "S5 Home Delivery"
HD_TEMPLATE_PROFILE = "S5 Counter 1"
HD_TEMPLATE_CUSTOMER = "S5 Walk in Customer"
HD_TERMINAL_ID = "S5HD"

STAFF = (
	{
		"email": "nafees@aimatic.tech",
		"first_name": "Nafees",
		"branch": S5_BRANCH,
		"profile": "S5 Counter 1",
		"customer": "S5 Walk in Customer",
		"price_list": S5_SELLING,
		"cash": S5_CASH,
	},
	{
		"email": "fps5@aimatic.tech",
		"first_name": "S5 FP1",
		"branch": S5_BRANCH,
		"profile": "S5 FP1",
		"customer": "S5 Food Panda",
		"price_list": S5_FOODPANDA,
		"cash": S5_CASH,
	},
	{
		"email": "hds5@aimatic.tech",
		"first_name": "S5 HD",
		"branch": S5_BRANCH,
		"profile": HD_PROFILE,
		"customer": HD_CUSTOMER,
		"price_list": S5_SELLING,
		"cash": S5_CASH,
	},
	{
		"email": "israr@aimatic.tech",
		"first_name": "Israr",
		"branch": S6_BRANCH,
		"profile": "S6 Counter 1",
		"customer": "S6 Walk in Customer",
		"price_list": S6_SELLING,
		"cash": S6_CASH,
	},
	{
		"email": "fps6@aimatic.tech",
		"first_name": "S6 FP1",
		"branch": S6_BRANCH,
		"profile": "S6 FP1",
		"customer": "S6 Food Panda",
		"price_list": S6_FOODPANDA,
		"cash": S6_CASH,
	},
)

PROTECTED_PROFILES = {
	"S1 Food Panda",
	"S1GT Counter 1",
	"S1GT Counter 2",
	"S1GT Counter 3",
	"S1GT Counter 4",
	"S4 Counter 1",
	"S4 Food Panda",
	"S7 Counter 1",
	"S7 Food Panda",
}


def _assert_site():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing S5/S6 POS users: expected {TARGET_SITE}, got {site}")
	frappe.set_user("Administrator")


def _profile_users(name: str) -> list[str]:
	if not frappe.db.exists("POS Profile", name):
		return []
	return [row.user for row in frappe.get_all("POS Profile User", filters={"parent": name}, fields=["user"])]


def _ensure_customer(name: str, price_list: str, template_name: str, apply: bool):
	if frappe.db.exists("Customer", name):
		current = frappe.db.get_value("Customer", name, "default_price_list")
		if current != price_list and apply:
			doc = frappe.get_doc("Customer", name)
			doc.default_price_list = price_list
			doc.save(ignore_permissions=True)
		return {"customer": name, "existed": True, "price_list": price_list if apply else current}
	if not apply:
		return {"customer": name, "existed": False, "would_create": True}
	template = frappe.get_doc("Customer", template_name)
	doc = frappe.new_doc("Customer")
	doc.customer_name = name
	doc.customer_type = template.customer_type or "Individual"
	doc.customer_group = template.customer_group or "Individual"
	doc.territory = template.territory or "Pakistan"
	doc.default_price_list = price_list
	doc.insert(ignore_permissions=True)
	if doc.default_price_list != price_list:
		frappe.throw(f"{doc.name} missing default_price_list {price_list}")
	return {"customer": doc.name, "existed": False, "created": True}


def _ensure_hd_profile(apply: bool):
	if frappe.db.exists("POS Profile", HD_PROFILE):
		doc = frappe.get_doc("POS Profile", HD_PROFILE)
		needed = {
			"branch": S5_BRANCH,
			"customer": HD_CUSTOMER,
			"selling_price_list": S5_SELLING,
			"account_for_change_amount": S5_CASH,
			"custom_terminal_id": HD_TERMINAL_ID,
			"custom_is_foodpanda_profile": 0,
			"disabled": 0,
		}
		changed = [field for field, value in needed.items() if doc.get(field) != value]
		if apply and changed:
			for field, value in needed.items():
				doc.set(field, value)
			doc.save(ignore_permissions=True)
		return {"profile": HD_PROFILE, "existed": True, "changed": changed}
	if not apply:
		return {"profile": HD_PROFILE, "existed": False, "would_create": True}
	template = frappe.get_doc("POS Profile", HD_TEMPLATE_PROFILE)
	doc = frappe.copy_doc(template)
	doc.name = HD_PROFILE
	doc.applicable_for_users = []
	doc.branch = S5_BRANCH
	doc.customer = HD_CUSTOMER
	doc.selling_price_list = S5_SELLING
	doc.account_for_change_amount = S5_CASH
	doc.custom_terminal_id = HD_TERMINAL_ID
	doc.custom_is_foodpanda_profile = 0
	doc.disabled = 0
	doc.insert(ignore_permissions=True, set_name=HD_PROFILE)
	return {"profile": doc.name, "existed": False, "created": True}


def _rename_foodpanda_profiles(apply: bool):
	actions = []
	for old, new, terminal_id in RENAMES:
		if frappe.db.exists("POS Profile", new):
			if apply:
				doc = frappe.get_doc("POS Profile", new)
				if doc.custom_terminal_id != terminal_id:
					doc.custom_terminal_id = terminal_id
					doc.save(ignore_permissions=True)
			actions.append({"from": old, "to": new, "status": "already_named"})
			continue
		if not frappe.db.exists("POS Profile", old):
			frappe.throw(f"Missing POS Profile {old} (cannot rename to {new})")
		if old in PROTECTED_PROFILES or new in PROTECTED_PROFILES:
			frappe.throw(f"Refusing rename involving protected profile {old}->{new}")
		si = frappe.db.count("Sales Invoice", {"pos_profile": old})
		pi = frappe.db.count("POS Invoice", {"pos_profile": old})
		if si or pi:
			frappe.throw(f"Refusing rename {old}: {si} Sales Invoice / {pi} POS Invoice")
		if apply:
			frappe.rename_doc("POS Profile", old, new, force=True)
			doc = frappe.get_doc("POS Profile", new)
			doc.custom_terminal_id = terminal_id
			doc.disabled = 0
			doc.save(ignore_permissions=True)
		actions.append({"from": old, "to": new, "status": "renamed" if apply else "would_rename"})
	return actions


def _ensure_user(spec: dict, apply: bool, password: str | None):
	email = spec["email"]
	existed = bool(frappe.db.exists("User", email))
	if existed:
		other = frappe.get_all(
			"User Permission",
			filters={"user": email, "allow": "Branch", "for_value": ["!=", spec["branch"]]},
			pluck="for_value",
		)
		if other:
			frappe.throw(f"{email} already has other Branch permissions: {', '.join(other)}")
		roles = frappe.get_roles(email)
		if apply:
			user = frappe.get_doc("User", email)
			user.enabled = 1
			user.user_type = "System User"
			user.first_name = spec["first_name"]
			user.custom_branch = spec["branch"]
			user.send_welcome_email = 0
			if ROLE not in roles:
				user.add_roles(ROLE)
			user.flags.ignore_password_policy = True
			user.save(ignore_permissions=True)
			if password:
				update_password(email, password, logout_all_sessions=False)
	elif apply:
		user = frappe.new_doc("User")
		user.email = email
		user.first_name = spec["first_name"]
		user.enabled = 1
		user.user_type = "System User"
		user.send_welcome_email = 0
		user.custom_branch = spec["branch"]
		user.flags.no_welcome_mail = True
		user.flags.ignore_password_policy = True
		user.append("roles", {"role": ROLE})
		user.insert(ignore_permissions=True)
		if not password:
			frappe.throw(f"Refusing to create {email} without a password")
		update_password(email, password, logout_all_sessions=False)
	perm = frappe.db.get_value(
		"User Permission",
		{"user": email, "allow": "Branch", "for_value": spec["branch"]},
		"name",
	)
	if apply and not perm:
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": email,
				"allow": "Branch",
				"for_value": spec["branch"],
				"is_default": 1,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
	elif apply and perm:
		frappe.db.set_value(
			"User Permission",
			perm,
			{"is_default": 1, "apply_to_all_doctypes": 1},
		)
	password_ok = None
	if apply and password:
		password_ok = check_password(email, password) == email
		if not password_ok:
			frappe.throw(f"Password verify failed for {email}")
	return {
		"email": email,
		"existed": existed,
		"branch": spec["branch"],
		"profile": spec["profile"],
		"password_ok": password_ok,
	}


def _attach_profile_user(spec: dict, apply: bool):
	profile_name = spec["profile"]
	if profile_name in PROTECTED_PROFILES:
		frappe.throw(f"Refusing to attach a user to protected profile {profile_name}")
	if not apply:
		return {"profile": profile_name, "user": spec["email"], "would_attach": True}
	doc = frappe.get_doc("POS Profile", profile_name)
	if doc.branch != spec["branch"]:
		frappe.throw(f"{profile_name} branch is {doc.branch!r}, expected {spec['branch']}")
	if doc.account_for_change_amount != spec["cash"]:
		frappe.throw(
			f"{profile_name} change account is {doc.account_for_change_amount!r}, expected {spec['cash']}"
		)
	if doc.customer != spec["customer"]:
		doc.customer = spec["customer"]
	if doc.selling_price_list != spec["price_list"]:
		doc.selling_price_list = spec["price_list"]
	existing = [row.user for row in doc.applicable_for_users]
	if spec["email"] not in existing:
		doc.append("applicable_for_users", {"user": spec["email"], "default": 1})
	else:
		for row in doc.applicable_for_users:
			if row.user == spec["email"]:
				row.default = 1
	doc.save(ignore_permissions=True)
	return {"profile": profile_name, "user": spec["email"], "users": [row.user for row in doc.applicable_for_users]}


def _protected_snapshot():
	return {
		name: _profile_users(name)
		for name in sorted(PROTECTED_PROFILES)
		if frappe.db.exists("POS Profile", name)
	}


def main(apply: bool = False, passwords: dict | None = None):
	_assert_site()
	passwords = passwords or {}
	before_protected = _protected_snapshot()
	plan = {
		"site": TARGET_SITE,
		"apply": apply,
		"renames": _rename_foodpanda_profiles(apply),
		"hd_customer": _ensure_customer(HD_CUSTOMER, S5_SELLING, HD_TEMPLATE_CUSTOMER, apply),
		"s6_hd_customer": _ensure_customer(
			"S6 Home Delivery", S6_SELLING, "S6 Walk in Customer", apply
		),
		"hd_profile": _ensure_hd_profile(apply),
		"staff": [],
		"protected_users_before": before_protected,
	}
	if apply:
		missing = [row["email"] for row in STAFF if row["email"] not in passwords]
		if missing:
			frappe.throw("Refusing apply: missing passwords for named cashiers")
	for spec in STAFF:
		plan["staff"].append(
			{
				"user": _ensure_user(spec, apply, passwords.get(spec["email"])),
				"profile": _attach_profile_user(spec, apply) if apply else {
					"profile": spec["profile"],
					"user": spec["email"],
					"would_attach": True,
				},
			}
		)
	if apply:
		frappe.db.commit()
		after_protected = _protected_snapshot()
		if after_protected != before_protected:
			frappe.throw(f"Protected POS profile users changed: {before_protected} -> {after_protected}")
		plan["protected_users_after"] = after_protected
		plan["verify"] = []
		for spec in STAFF:
			user = frappe.get_doc("User", spec["email"])
			roles = frappe.get_roles(spec["email"])
			perm = frappe.get_all(
				"User Permission",
				filters={"user": spec["email"], "allow": "Branch"},
				fields=["for_value", "is_default"],
			)
			profile = frappe.get_doc("POS Profile", spec["profile"])
			plan["verify"].append(
				{
					"email": spec["email"],
					"enabled": int(user.enabled or 0),
					"roles_has_pos_user": ROLE in roles,
					"custom_branch": user.custom_branch,
					"branch_perms": perm,
					"profile": profile.name,
					"profile_customer": profile.customer,
					"profile_price_list": profile.selling_price_list,
					"profile_change_account": profile.account_for_change_amount,
					"profile_disabled": int(profile.disabled or 0),
					"attached": spec["email"] in [row.user for row in profile.applicable_for_users],
				}
			)
	print(plan)
	if not apply:
		print("DRY_RUN — no users, renames, or POS Profile Users written.")
	return plan
