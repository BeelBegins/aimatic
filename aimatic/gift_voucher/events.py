import frappe
from frappe import _
from frappe.utils import add_days, cint, flt

from aimatic.fbr_pos.payload_builder import get_invoice_branch
from aimatic.gift_voucher.code_generator import generate_voucher_code


def _find_matching_criteria(company, branch, grand_total):
    matches = frappe.get_all(
        "Gift Voucher Criteria",
        filters={
            "company": company,
            "branch": branch,
            "enabled": 1,
            "min_value": ["<=", grand_total],
            "max_value": [">=", grand_total],
        },
        fields=["name", "percentage", "validity_days"],
        order_by="min_value desc",
        limit=1,
    )
    return matches[0] if matches else None


def on_submit_issue_gift_voucher(doc, method=None):
    """Auto-issue a Gift Voucher when a (non-return) sale's grand total falls
    into a configured Gift Voucher Criteria bracket for its company/branch.
    """
    if cint(getattr(doc, "is_return", 0)):
        return

    branch = get_invoice_branch(doc)
    grand_total = flt(doc.grand_total, 2)

    match = _find_matching_criteria(doc.company, branch, grand_total)
    if not match:
        return

    amount = flt(grand_total * flt(match.percentage) / 100.0, 2)
    if amount <= 0:
        return

    frappe.get_doc({
        "doctype": "Gift Voucher",
        "voucher_code": generate_voucher_code(),
        "customer": doc.customer,
        "company": doc.company,
        "branch": branch,
        "criteria": match.name,
        "amount": amount,
        "issued_against_invoice": doc.name,
        "issue_date": doc.posting_date,
        "expiry_date": add_days(doc.posting_date, cint(match.validity_days)),
        "status": "Active",
    }).insert(ignore_permissions=True)


def validate_pos_profile_no_manual_gift_voucher_payment(doc, method=None):
    """
    Gift Voucher redemption is a server-only "Gift Voucher" Mode of Payment
    row appended by offline_pos.api's submit flow after validating a real
    voucher code - it must never be a mode a cashier can pick manually in the
    POS terminal's Payment screen, since nothing then stops them entering any
    amount with no real voucher behind it. Confirmed live on siezal (2026-07-16):
    all 4 S1GT counters had "Gift Voucher" in their payment list, which
    offline_pos.api._validate_and_set_payments's own allowed-modes check would
    have silently accepted as a legitimate cashier-selected payment mode.
    """
    for row in doc.payments or []:
        if row.mode_of_payment == "Gift Voucher":
            frappe.throw(
                _(
                    "'Gift Voucher' cannot be added to a POS Profile's payment "
                    "methods - it is a server-only mode applied automatically "
                    "when a valid gift voucher code is redeemed, never a mode a "
                    "cashier selects manually."
                )
            )


def on_cancel_gift_voucher(doc, method=None):
    """Undo issuance/redemption tied to a cancelled invoice, either direction."""
    for name in frappe.get_all(
        "Gift Voucher",
        filters={"issued_against_invoice": doc.name, "status": "Active"},
        pluck="name",
    ):
        frappe.db.set_value("Gift Voucher", name, "status", "Cancelled")

    for name in frappe.get_all(
        "Gift Voucher",
        filters={"redeemed_against_invoice": doc.name, "status": "Redeemed"},
        pluck="name",
    ):
        frappe.db.set_value(
            "Gift Voucher",
            name,
            {"status": "Active", "redeemed_against_invoice": None, "redeemed_on": None},
        )
