import frappe
from frappe.utils import add_days, cint, flt, getdate

from aimatic.loyalty.item_group_rate import get_item_group_loyalty_rate
from aimatic.pos_shared import returned_qty_by_row


def _compute_item_group_points(invoice, returned_qty=None):
    """Item-group-weighted points for an invoice's remaining (non-returned) rows."""
    returned_qty = returned_qty or {}
    total_points = 0.0
    eligible_amount = 0.0

    for row in invoice.items:
        sold_qty = abs(flt(row.qty))
        if sold_qty <= 0:
            continue

        already_returned = flt(returned_qty.get(row.name, 0))
        remaining_ratio = max(0.0, (sold_qty - already_returned) / sold_qty)
        if remaining_ratio <= 0:
            continue

        row_eligible_amount = flt(row.amount, 2) * remaining_ratio
        eligible_amount += row_eligible_amount

        rate = get_item_group_loyalty_rate(row.item_group)
        if rate:
            total_points += row_eligible_amount * rate / 100.0

    return cint(total_points), flt(eligible_amount, 2)


def _correct_loyalty_point_entry(invoice, returned_qty=None):
    """Overwrite the Loyalty Point Entry core's own on_submit already created/
    recalculated, replacing its flat collection_factor points with the
    item-group-weighted total. Loyalty Point Entry is not submittable, so this
    is a plain in-place update -- no cancel/amend needed.
    """
    if not getattr(invoice, "loyalty_program", None):
        return

    entry_name = frappe.db.get_value(
        "Loyalty Point Entry",
        {"invoice_type": "POS Invoice", "invoice": invoice.name},
        "name",
    )
    if not entry_name:
        return

    points, eligible_amount = _compute_item_group_points(invoice, returned_qty)

    from erpnext.accounts.doctype.loyalty_program.loyalty_program import (
        get_loyalty_program_details_with_points,
    )

    lp_details = get_loyalty_program_details_with_points(
        invoice.customer,
        company=invoice.company,
        current_transaction_amount=eligible_amount,
        loyalty_program=invoice.loyalty_program,
        expiry_date=invoice.posting_date,
        include_expired_entry=True,
    )

    # Mirrors core's own eligibility guard in Sales Invoice.make_loyalty_point_entry.
    if not (
        lp_details
        and getdate(lp_details.from_date) <= getdate(invoice.posting_date)
        and (not lp_details.to_date or getdate(lp_details.to_date) >= getdate(invoice.posting_date))
    ):
        return

    frappe.db.set_value(
        "Loyalty Point Entry",
        entry_name,
        {
            "loyalty_points": points,
            "purchase_amount": eligible_amount,
            "expiry_date": add_days(invoice.posting_date, lp_details.expiry_duration),
        },
    )


def on_submit_correct_loyalty_points(doc, method=None):
    """Registered as a POS Invoice on_submit doc_event -- runs strictly after
    core's own on_submit (which creates/recalculates the Loyalty Point Entry).

    On a return, core recalculates the entry belonging to the ORIGINAL invoice
    (return_against), not the return itself, so we correct that one instead,
    recomputing points from the original's rows minus whatever has been
    returned so far (including this return, since it's already docstatus=1 by
    the time this hook runs).
    """
    if cint(getattr(doc, "is_return", 0)):
        if not doc.return_against or not doc.loyalty_program:
            return
        original = frappe.get_doc("POS Invoice", doc.return_against)
        _correct_loyalty_point_entry(original, returned_qty_by_row(original.name))
    else:
        _correct_loyalty_point_entry(doc)
