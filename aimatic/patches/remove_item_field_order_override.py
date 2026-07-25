import frappe


PROPERTY_SETTER = "Item-main-field_order"


def execute():
    if frappe.db.exists("Property Setter", PROPERTY_SETTER):
        frappe.delete_doc("Property Setter", PROPERTY_SETTER, ignore_permissions=True)

    frappe.clear_cache(doctype="Item")
