import frappe

# Vendor Performance (and other Desk Link fields to Branch) need select/read on
# Branch. Branch-restricted Accounts/Purchase users already have a Branch User
# Permission, but without this they cannot pick that same branch in the filter.
ROLES = (
	"Accounts User",
	"Accounts Manager",
	"Purchase Master Manager",
)


def _ensure_branch_select(role: str) -> None:
	filters = {"parent": "Branch", "role": role, "permlevel": 0}
	if frappe.db.exists("Custom DocPerm", filters):
		row = frappe.get_all(
			"Custom DocPerm",
			filters=filters,
			fields=["name", "read", "select"],
			limit=1,
		)[0]
		updates = {}
		if not row.read:
			updates["read"] = 1
		if not row.select:
			updates["select"] = 1
		if updates:
			frappe.db.set_value("Custom DocPerm", row.name, updates, update_modified=False)
		return
	frappe.get_doc(
		{
			"doctype": "Custom DocPerm",
			"parent": "Branch",
			"parenttype": "DocType",
			"parentfield": "permissions",
			"role": role,
			"permlevel": 0,
			"read": 1,
			"select": 1,
			"write": 0,
			"create": 0,
			"delete": 0,
			"submit": 0,
			"cancel": 0,
			"amend": 0,
			"report": 0,
			"export": 0,
			"import": 0,
			"share": 0,
			"print": 0,
			"email": 0,
		}
	).insert(ignore_permissions=True)


def execute():
	for role in ROLES:
		if frappe.db.exists("Role", role):
			_ensure_branch_select(role)
	frappe.clear_cache()
