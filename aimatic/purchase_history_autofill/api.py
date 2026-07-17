import frappe

from aimatic.purchase_history_autofill.utils import fetch_latest_history_rows, get_autofillable_fields


@frappe.whitelist()
def preview_item_history(supplier: str, branch: str, item_code: str):
    """Live preview for the Purchase Receipt form: same matching logic as
    the before_validate hook (aimatic.purchase_history_autofill.events),
    exposed read-only so the client can pre-fill blank grid cells before
    save for a smoother UX. This is a convenience only - the before_validate
    hook is what actually guarantees the fields get populated, whether or
    not this endpoint (or its client script) is ever called.
    """
    if not (supplier and branch and item_code):
        return {}

    fieldtypes = get_autofillable_fields("Purchase Receipt Item")
    if not fieldtypes:
        return {}

    history_map = fetch_latest_history_rows(supplier, branch, [item_code], fieldtypes)
    return history_map.get(item_code, {})
