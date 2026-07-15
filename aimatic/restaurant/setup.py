import frappe


RESTAURANT_ROLES = (
	("Restaurant Waiter", 0),
	("Restaurant Manager", 1),
	("Kitchen User", 0),
)


def create_roles():
	for role_name, desk_access in RESTAURANT_ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": desk_access}).insert(
			ignore_permissions=True
		)
