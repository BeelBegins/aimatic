"""Read-only cache access for the existing POS gross-margin report roles."""

import frappe

ROLES = ("System Manager", "Accounts Manager", "Accounts User", "Sales Manager", "Stock Manager")
DOCTYPE = "Insights POS Margin Daily"


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			continue
		filters = {"parent": DOCTYPE, "role": role, "permlevel": 0}
		name = frappe.db.get_value("Custom DocPerm", filters, "name")
		if name:
			for field in ("read", "select", "report"):
				if not frappe.db.get_value("Custom DocPerm", name, field):
					frappe.db.set_value("Custom DocPerm", name, field, 1)
			continue
		frappe.get_doc(
			{
				"doctype": "Custom DocPerm",
				"parent": DOCTYPE,
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": role,
				"permlevel": 0,
				"read": 1,
				"select": 1,
				"report": 1,
				"write": 0,
				"create": 0,
				"delete": 0,
				"submit": 0,
				"cancel": 0,
				"amend": 0,
				"export": 0,
				"import": 0,
				"share": 0,
				"print": 0,
				"email": 0,
			}
		).insert(ignore_permissions=True)
	frappe.clear_cache(doctype=DOCTYPE)
