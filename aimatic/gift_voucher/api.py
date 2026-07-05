import frappe
from frappe import _
from frappe.utils import getdate, nowdate


def _require_login():
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required"), frappe.PermissionError)


def _load_active_gift_voucher(voucher_code, customer):
    """Return the Gift Voucher doc for a code, or throw with a specific reason.

    Shared by preview_cart, submit_online_sale, and validate_gift_voucher_code
    so all three enforce the exact same eligibility rules.
    """
    voucher = frappe.db.get_value(
        "Gift Voucher",
        {"voucher_code": voucher_code},
        ["name", "customer", "status", "expiry_date", "amount", "criteria"],
        as_dict=True,
    )
    if not voucher:
        frappe.throw(_("Invalid gift voucher code"))

    if voucher.customer != customer:
        frappe.throw(_("This gift voucher does not belong to this customer"))

    if voucher.status != "Active":
        frappe.throw(_("This gift voucher is {0}").format(voucher.status))

    if getdate(voucher.expiry_date) < getdate(nowdate()):
        frappe.throw(_("This gift voucher expired on {0}").format(voucher.expiry_date))

    return frappe.get_doc("Gift Voucher", voucher.name)


@frappe.whitelist()
def list_customer_gift_vouchers(customer, company=None):
    _require_login()
    if not customer:
        frappe.throw(_("customer is required"))

    filters = {
        "customer": customer,
        "status": "Active",
        "expiry_date": [">=", nowdate()],
    }
    if company:
        filters["company"] = company

    return frappe.get_all(
        "Gift Voucher",
        filters=filters,
        fields=["name", "voucher_code", "amount", "issue_date", "expiry_date", "branch"],
        order_by="expiry_date asc",
    )


@frappe.whitelist()
def validate_gift_voucher_code(voucher_code, customer):
    _require_login()
    if not voucher_code or not customer:
        frappe.throw(_("voucher_code and customer are required"))

    voucher = _load_active_gift_voucher(voucher_code, customer)
    criteria = frappe.get_cached_doc("Gift Voucher Criteria", voucher.criteria)

    return {
        "name": voucher.name,
        "voucher_code": voucher.voucher_code,
        "amount": voucher.amount,
        "expiry_date": voucher.expiry_date,
        "minimum_redemption_value": criteria.minimum_redemption_value,
    }
