from __future__ import annotations

import frappe
from frappe.utils import flt


def _get_primary_barcodes(item_codes):
    """First Item Barcode row per item code, same convention as
    purchase_printing._get_primary_barcodes."""
    if not item_codes:
        return {}

    rows = frappe.get_all(
        "Item Barcode",
        filters={"parent": ["in", item_codes]},
        fields=["parent", "barcode", "idx"],
        order_by="parent asc, idx asc",
    )

    barcodes = {}
    for row in rows:
        if row.parent and row.barcode and row.parent not in barcodes:
            barcodes[row.parent] = row.barcode

    return barcodes


def _get_receipt_branch_context(doc):
    """Resolve the receipt header from the invoice's branch/profile masters.

    Branch owns the customer-facing branch identity and contact details;
    POS Profile may override the address for a particular terminal. Company
    values remain the fallback so older invoices continue to print complete
    headers before branch fields have been populated.
    """
    profile = frappe.db.get_value(
        "POS Profile",
        doc.pos_profile,
        ["branch", "company_address"],
        as_dict=True,
    ) if doc.pos_profile else None
    branch_name = doc.branch or (profile.branch if profile else None)
    branch = frappe.get_cached_doc("Branch", branch_name) if branch_name else None

    profile_address = profile.company_address if profile else None
    address_name = profile_address or doc.company_address
    address = (
        frappe.db.get_value(
            "Address",
            address_name,
            ["address_line1", "address_line2", "city", "state", "pincode", "phone", "email_id"],
            as_dict=True,
        )
        if address_name
        else None
    )

    company = frappe.db.get_value(
        "Company", doc.company, ["tax_id", "phone_no", "email"], as_dict=True
    ) or {}
    branch_address = branch.get("custom_receipt_address") if branch else None
    address_text = branch_address or "\n".join(
        part
        for part in (
            address.get("address_line1") if address else None,
            address.get("address_line2") if address else None,
            ", ".join(
                part
                for part in (
                    address.get("city") if address else None,
                    address.get("state") if address else None,
                )
                if part
            ),
            address.get("pincode") if address else None,
        )
        if part
    )

    phone = (branch.get("custom_receipt_phone") if branch else None) or (
        address.get("phone") if address else None
    ) or company.get("phone_no")
    email = (branch.get("custom_receipt_email") if branch else None) or (
        address.get("email_id") if address else None
    ) or company.get("email")
    tax_id = (branch.get("custom_receipt_tax_id") if branch else None) or company.get("tax_id")

    cashier_name = doc.get("custom_cashier_full_name")
    if not cashier_name and doc.get("custom_cashier_user"):
        cashier_name = frappe.db.get_value("User", doc.custom_cashier_user, "full_name")
    if not cashier_name and doc.owner:
        cashier_name = frappe.db.get_value("User", doc.owner, "full_name") or doc.owner

    return {
        "name": branch_name,
        "address": address_text,
        "phone": phone,
        "email": email,
        "tax_id": tax_id,
        "cashier_name": cashier_name,
    }


def get_pos_receipt_context(doc):
    """Everything the POS receipt print formats need beyond the invoice's
    own fields: the FBR sales-tax/service-fee amounts (matched by Sales
    Taxes and Charges row description, since there's no dedicated field for
    either), the per-item FBR totals, the receipt's applicable terms (the
    POS Profile's own Terms and Conditions if it has one, else the
    invoice's), any loyalty/gift-voucher activity tied to this specific
    invoice, and each row's printable barcode (the row's own scanned
    `barcode` first, falling back to the item master's primary Item Barcode).
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

    item_codes = [item.item_code for item in doc.items if item.item_code]
    master_barcodes = _get_primary_barcodes(item_codes)
    item_barcodes = {
        item.name: item.get("barcode") or master_barcodes.get(item.item_code) or ""
        for item in doc.items
    }

    # A duplicate/reprint (Electron's "Duplicate Receipt" action, which tags
    # its /printview request with ?is_duplicate=1) must never show Gift
    # Voucher issuance/redemption again - it's not new, and re-showing the
    # "You've Earned a Gift Voucher!" block on every reprint is misleading.
    is_duplicate_print = bool(frappe.form_dict.get("is_duplicate"))

    return {
        "item_barcodes": item_barcodes,
        "ntn": frappe.db.get_value("Company", doc.company, "tax_id"),
        "branch_info": _get_receipt_branch_context(doc),
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
        "gift_voucher_redeemed": None
        if is_duplicate_print
        else frappe.db.get_value(
            "Gift Voucher",
            {"redeemed_against_invoice": doc.name},
            ["voucher_code", "amount"],
            as_dict=True,
        ),
        "gift_voucher_issued": None
        if is_duplicate_print
        else frappe.db.get_value(
            "Gift Voucher",
            {"issued_against_invoice": doc.name},
            ["voucher_code", "amount", "expiry_date"],
            as_dict=True,
        ),
    }
