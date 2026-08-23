from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    """Add GM % on Purchase Receipt Item between Sale Price and Price After
    Taxes. Editable: KPOs enter margin (20/15/10) to drive rounded Sale Price.
    Formula matches the PR print layout: (sale - cost) / sale * 100.
    (Originally shipped read_only; unlock_purchase_receipt_gm_percent + fixture
    keep it editable — do not set read_only=1 here or a re-run with update=True
    would lock the field again.)
    """
    create_custom_fields(
        {
            "Purchase Receipt Item": [
                {
                    "fieldname": "custom_gm_percent",
                    "label": "GM %",
                    "fieldtype": "Percent",
                    "insert_after": "custom_shelf_price",
                    "read_only": 0,
                    "precision": "2",
                    "description": (
                        "Gross margin % of Sale Price vs Price After Taxes: "
                        "(Sale Price - Price After Taxes) / Sale Price * 100. "
                        "Edit to set Sale Price = round(Price After Taxes / (1 - GM%/100))."
                    ),
                },
            ],
        },
        update=True,
    )
