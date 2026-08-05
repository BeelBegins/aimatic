import frappe


BARCODE_SEPARATOR = ", "
BACKFILL_CHUNK_SIZE = 1000

ITEM_PRICE_BARCODE_FIELD = {
    "fieldname": "custom_barcodes",
    "label": "Barcodes",
    "fieldtype": "Small Text",
    "insert_after": "item_description",
    "read_only": 1,
    "description": (
        "All barcodes from the linked Item, in Item Barcode row order. "
        "Available in Item Price data exports."
    ),
}


def get_item_barcodes(item_code):
    """Return an Item's non-empty barcodes in child-row order."""
    if not item_code:
        return []

    return frappe.get_all(
        "Item Barcode",
        filters={"parent": item_code, "parenttype": "Item"},
        pluck="barcode",
        order_by="idx asc",
    )


def format_item_barcodes(item_code):
    return BARCODE_SEPARATOR.join(filter(None, get_item_barcodes(item_code)))


def set_item_price_barcodes(doc, method=None):
    """Populate Item Price before core validation/save and data export."""
    doc.custom_barcodes = format_item_barcodes(doc.item_code)


def sync_item_barcodes_to_prices(doc, method=None):
    """Refresh every Item Price row after the Item's barcode table changes."""
    if not frappe.db.has_column("Item Price", "custom_barcodes"):
        return

    frappe.db.set_value(
        "Item Price",
        {"item_code": doc.name},
        "custom_barcodes",
        format_item_barcodes(doc.name),
        update_modified=False,
    )


def backfill_item_price_barcodes():
    """Populate the export field on Item Price rows that predate the feature."""
    item_prices = frappe.get_all(
        "Item Price",
        fields=["name", "item_code"],
        order_by="item_code asc, name asc",
    )
    for start in range(0, len(item_prices), BACKFILL_CHUNK_SIZE):
        price_chunk = item_prices[start : start + BACKFILL_CHUNK_SIZE]
        item_codes = list({row.item_code for row in price_chunk if row.item_code})
        barcode_rows = frappe.get_all(
            "Item Barcode",
            filters={"parent": ("in", item_codes), "parenttype": "Item"},
            fields=["parent", "barcode", "idx"],
            order_by="parent asc, idx asc",
        )

        barcodes_by_item = {}
        for row in barcode_rows:
            if row.barcode:
                barcodes_by_item.setdefault(row.parent, []).append(row.barcode)

        updates = {
            row.name: {
                "custom_barcodes": BARCODE_SEPARATOR.join(
                    barcodes_by_item.get(row.item_code, [])
                )
            }
            for row in price_chunk
        }
        frappe.db.bulk_update(
            "Item Price",
            updates,
            chunk_size=BACKFILL_CHUNK_SIZE,
            update_modified=False,
        )
