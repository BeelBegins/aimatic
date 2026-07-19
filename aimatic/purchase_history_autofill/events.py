import frappe

from aimatic.purchase_history_autofill.utils import (
    apply_history_to_row,
    fetch_latest_history_rows,
    get_autofillable_fields,
)


def _autofill_item_fields(doc, child_doctype):
    """Shared body for the per-doctype before_validate hooks below. Fills
    empty/zero item-level purchase fields (taxes, discounts, trade offer,
    FED, advance tax, GST, MRP, ...) from the most recent submitted
    Purchase Receipt/Purchase Invoice row for the same Supplier + Branch +
    Item - see purchase_history_autofill/utils.py for the matching/exclusion
    logic. Never touches Quantity/Rate/custom_vendor_rate - those are
    excluded upstream in utils._ALWAYS_EXCLUDE, not just skipped because
    they're already non-empty.
    """
    supplier = doc.get("supplier")
    branch = doc.get("branch")
    if not supplier or not branch:
        return

    item_codes = {row.item_code for row in (doc.items or []) if row.item_code}
    if not item_codes:
        return

    fieldtypes = get_autofillable_fields(child_doctype)
    if not fieldtypes:
        return

    history_map = fetch_latest_history_rows(supplier, branch, item_codes, fieldtypes)
    if not history_map:
        return

    for row in doc.items:
        history = history_map.get(row.item_code)
        if history:
            apply_history_to_row(row, history)


def autofill_purchase_receipt_item_fields(doc, method=None):
    """before_validate hook on Purchase Receipt (runs after
    apply_branch_defaults, so doc.branch is already resolved by the time
    this runs). Fires identically whether rows arrived via "Get Items From
    Purchase Order" or were added manually (both reach before_validate the
    same way at save time). See _autofill_item_fields for the shared logic.
    """
    _autofill_item_fields(doc, "Purchase Receipt Item")


def autofill_purchase_order_item_fields(doc, method=None):
    """before_validate hook on Purchase Order (runs after
    apply_branch_defaults, so doc.branch is already resolved by the time
    this runs). History is still sourced from submitted Purchase
    Receipt/Purchase Invoice rows (_SOURCE_DOCTYPES in utils.py), not prior
    Purchase Orders - actual received/billed terms are the reliable source,
    not other quoted-but-not-yet-fulfilled POs. See _autofill_item_fields
    for the shared logic.
    """
    _autofill_item_fields(doc, "Purchase Order Item")
