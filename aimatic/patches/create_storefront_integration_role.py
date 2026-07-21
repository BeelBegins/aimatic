import frappe


def execute():
    if frappe.db.exists("Role", "Storefront Integration"):
        return

    frappe.get_doc(
        {
            "doctype": "Role",
            "role_name": "Storefront Integration",
            "desk_access": 0,
        }
    ).insert(ignore_permissions=True)
