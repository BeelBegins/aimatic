import frappe
from frappe.utils import flt


def returned_qty_by_row(original_name):
    """Absolute returned quantity per original POS Invoice Item row.

    Sums quantities from all submitted return POS Invoices linked to the
    original via the standard ``pos_invoice_item`` reference set by make_return_doc.
    Keyed by the original row's own ``name`` (matches ``original.items`` row names).
    """
    result = {}
    returns = frappe.get_all(
        "POS Invoice",
        filters={"return_against": original_name, "is_return": 1, "docstatus": 1},
        pluck="name",
    )
    if not returns:
        return result
    rows = frappe.get_all(
        "POS Invoice Item",
        filters={"parent": ("in", returns), "parenttype": "POS Invoice"},
        fields=["pos_invoice_item", "qty"],
    )
    for r in rows:
        key = r.get("pos_invoice_item")
        if not key:
            continue
        result[key] = flt(result.get(key, 0)) + abs(flt(r.get("qty")))
    return result
