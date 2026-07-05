import frappe
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
