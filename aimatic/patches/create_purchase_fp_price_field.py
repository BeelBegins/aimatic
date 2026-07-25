from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    """Add custom_fp_price (Currency) to Purchase Order/Receipt/Invoice Item.

    Foodpanda pricing previously piggybacked on custom_mrp - this gives it
    its own dedicated field instead, decoupled from MRP. Added identically
    to all three Item doctypes even though only Purchase Receipt's
    apply_foodpanda_price_update actually reads it (Purchase Order/Invoice
    get it purely for schema consistency with the rest of the purchase
    pipeline, matching the existing pattern where custom_mrp/
    custom_shelf_price already exist identically across all three).
    """
    create_custom_fields(
        {
            "Purchase Order Item": [
                {
                    "fieldname": "custom_fp_price",
                    "label": "FP Price",
                    "fieldtype": "Currency",
                    "insert_after": "custom_mrp",
                    "description": (
                        "Foodpanda-specific selling price for this item. Separate from "
                        "MRP/Shelf Price - only this field feeds the branch's Foodpanda "
                        "Price List update on Purchase Receipt submission."
                    ),
                },
            ],
            "Purchase Receipt Item": [
                {
                    "fieldname": "custom_fp_price",
                    "label": "FP Price",
                    "fieldtype": "Currency",
                    "insert_after": "custom_shelf_price",
                    "description": (
                        "Foodpanda-specific selling price for this item. Separate from "
                        "MRP/Shelf Price - only this field feeds the branch's Foodpanda "
                        "Price List update on Purchase Receipt submission."
                    ),
                },
            ],
            "Purchase Invoice Item": [
                {
                    "fieldname": "custom_fp_price",
                    "label": "FP Price",
                    "fieldtype": "Currency",
                    "insert_after": "custom_shelf_price",
                    "description": (
                        "Foodpanda-specific selling price for this item. Separate from "
                        "MRP/Shelf Price - only this field feeds the branch's Foodpanda "
                        "Price List update on Purchase Receipt submission."
                    ),
                },
            ],
        },
        update=False,
    )
