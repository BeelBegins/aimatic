import frappe


def execute():
    """Allow KPOs to edit GM % so Sale Price can be driven from margin
    (20 / 15 / 10 → rounded shelf). Field was created read-only.
    """
    name = "Purchase Receipt Item-custom_gm_percent"
    if not frappe.db.exists("Custom Field", name):
        return

    frappe.db.set_value(
        "Custom Field",
        name,
        {
            "read_only": 0,
            "description": (
                "Gross margin % of Sale Price vs Price After Taxes: "
                "(Sale Price - Price After Taxes) / Sale Price * 100. "
                "Edit to set Sale Price = round(Price After Taxes / (1 - GM%/100))."
            ),
        },
        update_modified=False,
    )
