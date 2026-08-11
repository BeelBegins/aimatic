import frappe


def execute():
    """Add POS Profile.custom_allow_clear_cart (Check, default 1).

    Electron POS Clear Cart (button + F10) mirrors Allow Item Search: when
    unchecked, terminals hide the control and block the shortcut after the
    next POS configuration sync. Default on so existing profiles keep today's
    Clear Cart behavior until an admin intentionally disables it.
    """
    if not frappe.db.exists(
        "Custom Field", {"dt": "POS Profile", "fieldname": "custom_allow_clear_cart"}
    ):
        frappe.get_doc(
            {
                "doctype": "Custom Field",
                "dt": "POS Profile",
                "fieldname": "custom_allow_clear_cart",
                "label": "Allow Clear Cart",
                "fieldtype": "Check",
                "default": "1",
                "insert_after": "custom_allow_item_search",
                "description": (
                    "When checked, Electron POS cashiers may use Clear Cart (button and F10). "
                    "When unchecked, those controls are hidden after the terminal syncs "
                    "POS configuration. Per-row void (F8) is unaffected."
                ),
            }
        ).insert(ignore_permissions=True)

    frappe.db.commit()
