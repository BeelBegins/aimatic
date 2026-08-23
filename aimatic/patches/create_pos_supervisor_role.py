import frappe


def execute():
	if frappe.db.exists("Role", "POS Supervisor"):
		return

	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": "POS Supervisor",
			"desk_access": 1,
		}
	).insert(ignore_permissions=True)
