import frappe


def execute():
    if frappe.db.exists("Role", "POS User"):
        return

    frappe.get_doc({
        "doctype": "Role",
        "role_name": "POS User",
        "desk_access": 1,
    }).insert(ignore_permissions=True)
