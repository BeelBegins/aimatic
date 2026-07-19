import frappe

from aimatic.purchase_history_autofill.utils import fetch_latest_history_rows, get_autofillable_fields


@frappe.whitelist()
def preview_item_history(
    supplier: str, branch: str, item_code: str, target_doctype: str = "Purchase Receipt Item"
):
    """Live preview for the Purchase Receipt / Purchase Order forms: same
    matching logic as the before_validate hooks
    (aimatic.purchase_history_autofill.events), exposed read-only so the
    client can pre-fill blank grid cells before save for a smoother UX.
    This is a convenience only - the before_validate hook is what actually
    guarantees the fields get populated, whether or not this endpoint (or
    its client script) is ever called. target_doctype defaults to
    "Purchase Receipt Item" for back-compat with existing callers; pass
    "Purchase Order Item" from the Purchase Order form.
    """
    if not (supplier and branch and item_code):
        return {}

    fieldtypes = get_autofillable_fields(target_doctype)
    if not fieldtypes:
        return {}

    history_map = fetch_latest_history_rows(supplier, branch, [item_code], fieldtypes)
    return history_map.get(item_code, {})
