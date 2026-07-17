import frappe

from aimatic.purchase_history_autofill.utils import (
    apply_history_to_row,
    fetch_latest_history_rows,
    get_autofillable_fields,
)


def autofill_purchase_receipt_item_fields(doc, method=None):
    """before_validate hook on Purchase Receipt (runs after
    apply_branch_defaults, so doc.branch is already resolved by the time
    this runs). Fills empty/zero item-level purchase fields (taxes,
    discounts, trade offer, FED, advance tax, GST, MRP, ...) from the most
    recent submitted Purchase Receipt/Purchase Invoice row for the same
    Supplier + Branch + Item - see purchase_history_autofill/utils.py for
    the matching/exclusion logic.

    Fires identically whether the rows arrived via "Get Items From Purchase
    Order" or were added manually (both reach before_validate the same way
    at save time), and never touches Quantity/Rate/custom_vendor_rate -
    those are excluded upstream in utils._ALWAYS_EXCLUDE, not just skipped
    because they're already non-empty.
    """
    supplier = doc.get("supplier")
    branch = doc.get("branch")
    if not supplier or not branch:
        return

    item_codes = {row.item_code for row in (doc.items or []) if row.item_code}
    if not item_codes:
        return

    fieldtypes = get_autofillable_fields("Purchase Receipt Item")
    if not fieldtypes:
        return

    history_map = fetch_latest_history_rows(supplier, branch, item_codes, fieldtypes)
    if not history_map:
        return

    for row in doc.items:
        history = history_map.get(row.item_code)
        if history:
            apply_history_to_row(row, history)
