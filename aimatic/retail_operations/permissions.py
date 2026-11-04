from __future__ import annotations

import frappe

CHANNEL_ROLES = {
	"Inventory": {"Stock Manager", "Purchase Manager", "System Manager"},
	"Purchase": {"Purchase Manager", "System Manager"},
	"Sales": {"Sales Manager", "System Manager"},
	"Accounts": {"Accounts Manager", "System Manager"},
}


def channels(user=None):
	roles = set(frappe.get_roles(user or frappe.session.user))
	return [channel for channel, allowed in CHANNEL_ROLES.items() if roles & allowed]


def check(branch, channel=None, write=False):
	if frappe.session.user == "Guest" or not channels():
		frappe.throw("Operations access is not permitted", frappe.PermissionError)
	if channel and channel not in channels():
		frappe.throw("This report is not permitted for your role", frappe.PermissionError)
	if write and not set(frappe.get_roles()) & {"System Manager", "Purchase Manager", "Stock Manager"}:
		frappe.throw("Operations changes are not permitted", frappe.PermissionError)
	if not frappe.get_list("Branch", filters={"name": branch}, pluck="name", limit_page_length=1):
		frappe.throw("Branch is not permitted", frappe.PermissionError)
	doc = frappe.get_doc("Branch", branch)
	wh = frappe.db.get_value(
		"Warehouse",
		doc.finished_goods_warehouse,
		["company", "custom_branch", "disabled", "is_group"],
		as_dict=True,
	)
	if not wh or wh.company != doc.company or wh.custom_branch != branch or wh.disabled or wh.is_group:
		frappe.throw("Branch finished-goods warehouse mapping is missing or inconsistent")
	return doc


def query_conditions(user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return ""
	names = frappe.get_list("Branch", pluck="name", user=user, limit_page_length=1000)
	return "branch IN ({})".format(",".join(frappe.db.escape(n) for n in names)) if names else "1=0"


def has_permission(doc, user=None, permission_type=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	if doc.get("branch") and doc.branch not in frappe.get_list(
		"Branch", pluck="name", user=user, limit_page_length=1000
	):
		return False
	if doc.doctype == "Retail Operations Issue" and doc.channel not in channels(user):
		return False
	return None


def issue_query(user=None):
	allowed = channels(user)
	branch = query_conditions(user)
	ch = "channel IN ({})".format(",".join(frappe.db.escape(c) for c in allowed)) if allowed else "1=0"
	return " AND ".join(p for p in (branch, ch) if p)
