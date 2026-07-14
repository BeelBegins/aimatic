import frappe


def execute():
    custom_fields = [
        {
            "dt": "POS Profile",
            "fieldname": "custom_terminal_id",
            "label": "Terminal ID",
            "fieldtype": "Data",
            "unique": 0,
            "no_copy": 0,
            "in_list_view": 0,
            "bold": 0,
            "insert_after": "warehouse",
            "description": "Logical terminal identifier for this POS Profile, shown read-only on the POS client. Multiple physical terminals may share the same value when they share this POS Profile.",
        },
        {
            "dt": "POS Invoice",
            "fieldname": "custom_hardware_id",
            "label": "Hardware ID",
            "fieldtype": "Data",
            "unique": 0,
            "no_copy": 1,
            "in_list_view": 0,
            "bold": 0,
            "insert_after": "custom_terminal_id",
            "description": "Per-install, auto-generated UUID of the physical terminal that created this invoice. Distinct from custom_terminal_id (the shared/logical label from the POS Profile).",
        },
    ]

    for field in custom_fields:
        if not frappe.db.exists(
            "Custom Field", {"dt": field["dt"], "fieldname": field["fieldname"]}
        ):
            cf = frappe.get_doc({"doctype": "Custom Field", **field})
            cf.insert(ignore_permissions=True)

    frappe.db.commit()
