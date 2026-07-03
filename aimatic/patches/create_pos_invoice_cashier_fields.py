import frappe


def execute():
    """Create custom fields on POS Invoice for offline/queued-sale cashier attribution.

    Populated by submit_online_sale when Electron forwards the cashier identity and
    offline-auth context recorded at pos_cashier_login time.
    """
    custom_fields = [
        {
            "dt": "POS Invoice",
            "fieldname": "custom_cashier_user",
            "label": "Cashier",
            "fieldtype": "Link",
            "options": "User",
            "no_copy": 1,
            "in_list_view": 0,
            "bold": 0,
            "insert_after": "custom_terminal_refund_id",
            "description": "Cashier who rang up the sale, as verified by pos_cashier_login. May differ from the terminal's own API user.",
        },
        {
            "dt": "POS Invoice",
            "fieldname": "custom_cashier_full_name",
            "label": "Cashier Full Name",
            "fieldtype": "Data",
            "no_copy": 1,
            "in_list_view": 0,
            "bold": 0,
            "insert_after": "custom_cashier_user",
            "description": "Cashier display name at time of sale, sent by the terminal.",
        },
        {
            "dt": "POS Invoice",
            "fieldname": "custom_offline_authenticated",
            "label": "Offline Authenticated",
            "fieldtype": "Check",
            "no_copy": 1,
            "in_list_view": 0,
            "bold": 0,
            "insert_after": "custom_cashier_full_name",
            "description": "Set when the sale was rung up while the terminal was offline, using a cached cashier login.",
        },
        {
            "dt": "POS Invoice",
            "fieldname": "custom_offline_auth_method",
            "label": "Offline Auth Method",
            "fieldtype": "Data",
            "no_copy": 1,
            "in_list_view": 0,
            "bold": 0,
            "insert_after": "custom_offline_authenticated",
            "description": "How the cashier was authenticated locally on the terminal (e.g. pin, password) when offline.",
        },
        {
            "dt": "POS Invoice",
            "fieldname": "custom_local_offline_session_id",
            "label": "Local Offline Session ID",
            "fieldtype": "Data",
            "no_copy": 1,
            "in_list_view": 0,
            "bold": 0,
            "insert_after": "custom_offline_auth_method",
            "description": "Electron-generated identifier for the local offline cashier session that queued this sale.",
        },
    ]

    for field in custom_fields:
        if not frappe.db.exists(
            "Custom Field", {"dt": field["dt"], "fieldname": field["fieldname"]}
        ):
            cf = frappe.get_doc({"doctype": "Custom Field", **field})
            cf.insert(ignore_permissions=True)

    frappe.db.commit()
