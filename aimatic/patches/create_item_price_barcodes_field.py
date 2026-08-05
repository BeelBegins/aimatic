import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from aimatic.item_pricing.barcodes import (
    ITEM_PRICE_BARCODE_FIELD,
    backfill_item_price_barcodes,
)


def execute():
    create_custom_fields(
        {"Item Price": [ITEM_PRICE_BARCODE_FIELD]},
        update=False,
    )
    frappe.clear_cache(doctype="Item Price")
    backfill_item_price_barcodes()
