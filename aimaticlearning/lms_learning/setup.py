import frappe

LMS_LEARNING_ROLES = (
	("LMS Content Reviewer", 1),
	("LMS Learner Analytics", 0),
)


def after_install():
	create_roles()


def create_roles():
	for role_name, desk_access in LMS_LEARNING_ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		frappe.get_doc(
			{"doctype": "Role", "role_name": role_name, "desk_access": desk_access}
		).insert(ignore_permissions=True)
