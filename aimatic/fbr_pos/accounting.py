import frappe
from frappe import _
from frappe.utils import cint, flt

from aimatic.fbr_pos.settings import resolve_fbr_settings
from aimatic.fbr_pos.payload_builder import get_invoice_branch


def get_fbr_settings_from_doc(doc):
    branch = get_invoice_branch(doc)
    return resolve_fbr_settings(doc, doc.company, branch)


def get_total_fbr_tax(doc):
    total_tax = 0

    for row in doc.items:
        total_tax += flt(row.get("custom_fbr_sales_tax"))

    return flt(total_tax, 2)


def get_total_fbr_value_excluding_tax(doc):
    total_value = 0

    for row in doc.items:
        total_value += flt(row.get("custom_fbr_value_excluding_tax"))

    return flt(total_value, 2)


def clear_fbr_tax_rows(doc):
    new_taxes = []

    for tax in doc.get("taxes") or []:
        description = (tax.description or "").strip()

        if description in [
            "FBR Sales Tax",
            "FBR POS Service Fee",
        ]:
            continue

        new_taxes.append(tax)

    doc.set("taxes", new_taxes)


def add_fbr_sales_tax_row(doc, settings):
    """
    GST is included inside item selling price.

    We use On Net Total + included_in_print_rate.
    This lets ERPNext split:
    Sales before GST
    GST payable
    without increasing customer price.
    """

    total_fbr_tax = get_total_fbr_tax(doc)
    total_excluding_tax = get_total_fbr_value_excluding_tax(doc)

    if total_fbr_tax <= 0:
        return

    if total_excluding_tax <= 0:
        frappe.throw(_("FBR value excluding tax is missing"))

    if not settings.get("sales_tax_account"):
        frappe.throw(
            _("Sales Tax Account is missing in FBR Integration Settings")
        )

    effective_rate = flt(
        (total_fbr_tax / total_excluding_tax) * 100,
        6,
    )

    doc.append("taxes", {
        "charge_type": "On Net Total",
        "account_head": settings["sales_tax_account"],
        "description": "FBR Sales Tax",
        "rate": effective_rate,
        "included_in_print_rate": 1,
    })


def add_fbr_pos_fee_row(doc, settings):
    """
    FBR POS service fee is extra.
    This increases customer payable total by Rs. 1.

    The fee applies to sales only. On a return/credit note we add neither a
    positive fee nor a negative refund of the original fee: the return total is
    merchandise + return tax only. Normal sale behaviour is unchanged.
    """

    if cint(getattr(doc, "is_return", 0)):
        return

    fee_amount = flt(settings.get("fbr_pos_fee_amount"), 2)

    if fee_amount <= 0:
        return

    if not settings.get("fbr_pos_fee_account"):
        frappe.throw(
            _("FBR POS Service Fee Account is missing in FBR Integration Settings")
        )

    doc.append("taxes", {
        "charge_type": "Actual",
        "account_head": settings["fbr_pos_fee_account"],
        "description": "FBR POS Service Fee",
        "tax_amount": fee_amount,
        "included_in_print_rate": 0,
    })


def adjust_cash_payment_to_grand_total(doc):
    payments = doc.get("payments") or []

    if not payments:
        return

    # Return invoices carry explicit negative refund payments set by the refund
    # endpoint; do not rewrite them with sale-oriented logic.
    if cint(getattr(doc, "is_return", 0)):
        return

    # The S1 Food Panda credit-sale marker (offline_pos.api._validate_and_set_payments)
    # is deliberately a single zero-amount row pointing at a Receivable-type account
    # (1311 - Food Panda Receivable), so the invoice stays fully outstanding until
    # Accounts posts a real Payment Entry. Force-filling it to grand_total here (the
    # "single implicit payment" branch below) turns it into a nonzero payment against
    # a Receivable account, which then fails ERPNext's own "Customer is required
    # against Receivable account" GL check at consolidation time -- confirmed live on
    # szl (2026-08-03), see the CLAUDE.md offline_pos note. Leave it at 0 untouched.
    if (
        len(payments) == 1
        and (payments[0].mode_of_payment or "").strip() == "Food Panda Credit"
        and flt(payments[0].amount) == 0
    ):
        return

    grand_total = flt(doc.grand_total, 2)
    total_paid = flt(sum(flt(p.amount) for p in payments), 2)

    if total_paid > 0 and total_paid >= flt(grand_total - 0.005, 2):
        # Payments were explicitly set (multi-payment or exact match from submission API).
        # Preserve the individual amounts; compute change from any excess.
        doc.paid_amount = total_paid
        doc.base_paid_amount = total_paid
        change = flt(max(0.0, total_paid - grand_total), 2)
        doc.change_amount = change
        doc.base_change_amount = change
    else:
        # Single implicit payment (e.g. added by set_missing_values): align it to grand_total.
        primary = payments[0]
        primary.amount = grand_total
        primary.base_amount = grand_total
        doc.paid_amount = grand_total
        doc.base_paid_amount = grand_total
        doc.change_amount = 0
        doc.base_change_amount = 0


def apply_fbr_accounting_rows(doc):
    settings = get_fbr_settings_from_doc(doc)

    clear_fbr_tax_rows(doc)

    if settings is not None:
        add_fbr_sales_tax_row(doc, settings)
        add_fbr_pos_fee_row(doc, settings)

    doc.run_method("calculate_taxes_and_totals")

    adjust_cash_payment_to_grand_total(doc)