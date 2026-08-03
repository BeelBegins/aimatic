import frappe
from frappe import _
from frappe.utils import flt

from aimatic.barcode_search import resolve_item_codes
from aimatic.shelf_pricing.api import get_current_branch_sale_price


@frappe.whitelist()
def lookup_price_by_barcode(barcode: str, branch: str):
    """Read-only barcode -> branch selling price lookup for the Price Check
    console. Reuses the same read-only helpers as the shelf_pricing prefill
    (barcode_search.resolve_item_codes, get_current_branch_sale_price) rather
    than duplicating branch/price-list resolution logic.
    """
    frappe.has_permission("Item", ptype="read", throw=True)

    branch = (branch or "").strip()
    if not branch:
        frappe.throw(_("Select a branch first."))

    item_codes = resolve_item_codes(barcode)
    if not item_codes:
        return {"found": False}

    items = []
    for item_code in item_codes:
        item = frappe.db.get_value(
            "Item", item_code, ["item_name", "disabled", "custom_mrp"], as_dict=True
        )
        if not item or item.disabled:
            continue

        price = get_current_branch_sale_price(item_code, branch)
        items.append(
            {
                "item_code": item_code,
                "item_name": item.item_name,
                "rate": flt(price.get("rate")),
                "mrp": flt(item.custom_mrp),
            }
        )

    if not items:
        return {"found": False}

    return {"found": True, "items": items}
