import frappe


def execute():
    """Create the Item Group loyalty rate custom fields.

    custom_loyalty_rate_configured distinguishes "no override, inherit from
    the nearest ancestor Item Group" from "explicitly configured, including
    an explicit zero" for custom_loyalty_points_rate, which a bare numeric
    field can't represent on its own (it always defaults to 0).
    See aimatic.loyalty.item_group_rate for the cascade lookup that reads
    these fields.
    """
    custom_fields = [
        {
            "dt": "Item Group",
            "fieldname": "custom_loyalty_rate_configured",
            "label": "Loyalty Rate Configured",
            "fieldtype": "Check",
            "insert_after": "taxes",
            "description": (
                "Check to set an explicit loyalty points rate on this Item Group "
                "(including an explicit zero, to award no points here). Leave "
                "unchecked to inherit the rate from the nearest ancestor Item "
                "Group that has this checked."
            ),
        },
        {
            "dt": "Item Group",
            "fieldname": "custom_loyalty_points_rate",
            "label": "Loyalty Points per Rs 100",
            "fieldtype": "Float",
            "precision": "2",
            "insert_after": "custom_loyalty_rate_configured",
            "depends_on": "eval:doc.custom_loyalty_rate_configured",
            "description": (
                "Points earned per Rs 100 of a POS Invoice line's customer-facing "
                "(tax-inclusive) amount, for this Item Group and any descendant "
                "that does not itself override it."
            ),
        },
    ]

    for field in custom_fields:
        if not frappe.db.exists(
            "Custom Field", {"dt": field["dt"], "fieldname": field["fieldname"]}
        ):
            cf = frappe.get_doc({"doctype": "Custom Field", **field})
            cf.insert(ignore_permissions=True)

    frappe.db.commit()
