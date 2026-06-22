import frappe


def execute():
    custom_fields = [
        {
            "dt": "POS Invoice",
            "fieldname": "custom_terminal_invoice_id",
            "label": "Terminal Invoice ID",
            "fieldtype": "Data",
            "unique": 1,
            "no_copy": 1,
            "in_list_view": 0,
            "bold": 0,
            "insert_after": "custom_fbr_usin",
            "description": "Unique ID assigned by the POS terminal. Used for idempotent submission.",
        },
        {
            "dt": "POS Invoice",
            "fieldname": "custom_terminal_id",
            "label": "Terminal ID",
            "fieldtype": "Data",
            "unique": 0,
            "no_copy": 1,
            "in_list_view": 0,
            "bold": 0,
            "insert_after": "custom_terminal_invoice_id",
            "description": "Hardware terminal identifier.",
        },
    ]

    for field in custom_fields:
        if not frappe.db.exists(
            "Custom Field", {"dt": field["dt"], "fieldname": field["fieldname"]}
        ):
            cf = frappe.get_doc({"doctype": "Custom Field", **field})
            cf.insert(ignore_permissions=True)

    frappe.db.commit()
