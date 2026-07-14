from __future__ import annotations

import frappe
from frappe.utils import flt


def get_pos_receipt_context(doc):
    """Everything the POS receipt print formats need beyond the invoice's
    own fields: the FBR sales-tax/service-fee amounts (matched by Sales
    Taxes and Charges row description, since there's no dedicated field for
    either), the per-item FBR totals, the receipt's applicable terms (the
    POS Profile's own Terms and Conditions if it has one, else the
    invoice's), and any loyalty/gift-voucher activity tied to this specific
    invoice.
    """
    fbr_sales_tax = 0.0
    fbr_pos_fee = 0.0
    for tax in doc.taxes:
        description = tax.description or ""
        if "Sales Tax" in description:
            fbr_sales_tax = tax.tax_amount
        if "Service Fee" in description or "POS Fee" in description:
            fbr_pos_fee = tax.tax_amount

    fbr_excl_total = 0.0
    fbr_tax_total = 0.0
    item_disc_total = 0.0
    for item in doc.items:
        fbr_excl_total += flt(item.custom_fbr_value_excluding_tax)
        fbr_tax_total += flt(item.custom_fbr_sales_tax)
        item_disc_total += flt(item.discount_amount)

    receipt_terms = None
    if doc.pos_profile:
        tc_name = frappe.db.get_value("POS Profile", doc.pos_profile, "tc_name")
        if tc_name:
            receipt_terms = frappe.db.get_value("Terms and Conditions", tc_name, "terms")
    receipt_terms = receipt_terms or doc.terms

    return {
        "ntn": frappe.db.get_value("Company", doc.company, "tax_id"),
        "receipt_terms": receipt_terms,
        "fbr_sales_tax": fbr_sales_tax,
        "fbr_pos_fee": fbr_pos_fee,
        "fbr_excl_total": fbr_excl_total,
        "fbr_tax_total": fbr_tax_total,
        "item_disc_total": item_disc_total,
        "loyalty_earned": frappe.db.get_value(
            "Loyalty Point Entry",
            {"invoice": doc.name},
            ["loyalty_points", "purchase_amount"],
            as_dict=True,
        ),
        "gift_voucher_redeemed": frappe.db.get_value(
            "Gift Voucher",
            {"redeemed_against_invoice": doc.name},
            ["voucher_code", "amount"],
            as_dict=True,
        ),
        "gift_voucher_issued": frappe.db.get_value(
            "Gift Voucher",
            {"issued_against_invoice": doc.name},
            ["voucher_code", "amount", "expiry_date"],
            as_dict=True,
        ),
    }
