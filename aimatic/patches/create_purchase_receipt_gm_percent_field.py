from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    """Add read-only GM % on Purchase Receipt Item between Sale Price and
    Price After Taxes so KPOs can keep margin when adjusting shelf price.
    Formula matches the PR print layout: (sale - cost) / sale * 100.
    """
    create_custom_fields(
        {
            "Purchase Receipt Item": [
                {
                    "fieldname": "custom_gm_percent",
                    "label": "GM %",
                    "fieldtype": "Percent",
                    "insert_after": "custom_shelf_price",
                    "read_only": 1,
                    "precision": "2",
                    "description": (
                        "Gross margin % of Sale Price vs Price After Taxes: "
                        "(Sale Price - Price After Taxes) / Sale Price * 100. "
                        "Read-only; recalculated when either price changes."
                    ),
                },
            ],
        },
        update=True,
    )
