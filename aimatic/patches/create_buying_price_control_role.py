import frappe


def execute():
    if frappe.db.exists("Role", "Buying Price Control"):
        return

    frappe.get_doc(
        {
            "doctype": "Role",
            "role_name": "Buying Price Control",
            "desk_access": 1,
        }
    ).insert(ignore_permissions=True)
