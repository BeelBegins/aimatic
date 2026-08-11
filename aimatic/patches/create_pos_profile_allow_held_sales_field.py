import frappe


def execute():
    """Add POS Profile.custom_allow_held_sales (Check, default 0).

    Electron POS Held Sales (Hold Sale button, Ctrl+H, Hold Current Sale at
    shift close) is unchecked by default: those controls are hidden after the
    terminal syncs POS configuration. Unlike Allow Item Search / Allow Clear
    Cart, this one defaults OFF, not ON — held sales must be explicitly
    enabled per profile. Existing held drafts remain resumable/deletable
    regardless of this setting, so nothing gets stranded if it's turned off
    after cashiers already have holds pending.
    """
    if not frappe.db.exists(
        "Custom Field", {"dt": "POS Profile", "fieldname": "custom_allow_held_sales"}
    ):
        frappe.get_doc(
            {
                "doctype": "Custom Field",
                "dt": "POS Profile",
                "fieldname": "custom_allow_held_sales",
                "label": "Allow Held Sales",
                "fieldtype": "Check",
                "default": "0",
                "insert_after": "custom_allow_clear_cart",
                "description": (
                    "When checked, Electron POS cashiers may hold and resume sales "
                    "(Hold Sale, Ctrl+H, Hold Current Sale at shift close). Unchecked "
                    "by default: those controls are hidden after the terminal syncs "
                    "POS configuration. Existing held drafts remain resumable/deletable "
                    "either way so nothing gets stranded."
                ),
            }
        ).insert(ignore_permissions=True)

    frappe.db.commit()
