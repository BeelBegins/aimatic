import frappe


def execute():
    """Add POS Profile.custom_fbr_optional (Check, default 0).

    aimatic.fbr_pos.settings.get_fbr_settings() hard-throws whenever a
    company+branch has no enabled FBR Integration Settings row - this makes
    the entire POS terminal unable to sell anything for that branch, not just
    unable to e-invoice. This field is an explicit per-profile opt-in: when
    checked, aimatic.fbr_pos.settings.resolve_fbr_settings() lets the sale
    complete with no GST/FBR-fee tax rows and no FBR submission instead of
    blocking. Left unchecked by default so every existing profile keeps
    today's hard-block behavior unchanged.
    """
    if not frappe.db.exists(
        "Custom Field", {"dt": "POS Profile", "fieldname": "custom_fbr_optional"}
    ):
        frappe.get_doc(
            {
                "doctype": "Custom Field",
                "dt": "POS Profile",
                "fieldname": "custom_fbr_optional",
                "label": "Allow Sale Without FBR",
                "fieldtype": "Check",
                "default": "0",
                "insert_after": "custom_terminal_id",
                "description": (
                    "When checked, POS sales on this profile complete without GST/FBR fee "
                    "tax rows and without FBR submission if no enabled FBR Integration "
                    "Settings exist for this profile's company and branch, instead of "
                    "blocking the sale."
                ),
            }
        ).insert(ignore_permissions=True)

    frappe.db.commit()
