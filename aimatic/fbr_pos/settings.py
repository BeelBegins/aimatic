import frappe
from frappe import _


def get_fbr_settings(company, branch):
    if not company:
        frappe.throw(_("Company is required for FBR integration"))

    if not branch:
        frappe.throw(_("Branch is required for FBR integration"))

    settings_name = frappe.db.get_value(
        "FBR Integration Settings",
        {
            "company": company,
            "branch": branch,
            "enabled": 1,
            "enable_fbr_for_pos": 1,
        },
        "name",
    )

    if not settings_name:
        frappe.throw(
            _(
                "Enabled FBR Integration Settings not found for "
                "Company {0} and Branch {1}"
            ).format(
                frappe.bold(company),
                frappe.bold(branch),
            )
        )

    settings = frappe.get_doc(
        "FBR Integration Settings",
        settings_name,
    )

    environment = settings.environment or "Sandbox"

    if environment == "Production":
        api_url = settings.pos_production_url
    else:
        api_url = settings.pos_sandbox_url

    if not api_url:
        frappe.throw(
            _("FBR POS API URL is missing for {0}")
            .format(frappe.bold(environment))
        )

    if not settings.pos_id:
        frappe.throw(_("FBR POS ID is missing"))

    submission_mode = settings.submission_mode or "Preview Only"

    if submission_mode == "Real Time":
        token = settings.get_password(
            "security_token",
            raise_exception=False,
        )

        if not token:
            frappe.throw(
                _("FBR Security Token is required for Real Time mode")
            )
    else:
        token = settings.get_password(
            "security_token",
            raise_exception=False,
        )

    return {
        "name": settings.name,
        "company": settings.company,
        "branch": settings.branch,
        "branch_code": settings.branch_code,
        "business_name": settings.business_name,
        "seller_ntn": settings.seller_ntn,
        "seller_strn": settings.seller_strn,
        "environment": environment,
        "submission_mode": submission_mode,
        "api_url": api_url,
        "pos_id": settings.pos_id,
        "security_token": token,
        "request_timeout": settings.request_timeout or 30,
        "maximum_retries": settings.maximum_retries or 3,
        "send_in_background": settings.send_in_background,
        "verify_ssl": settings.verify_ssl,
        "sales_tax_account": settings.sales_tax_account,
        "fbr_pos_fee_account": settings.fbr_pos_fee_account,
        "fbr_pos_fee_amount": settings.fbr_pos_fee_amount or 0,
        "default_payment_mode": settings.default_payment_mode or 1,
        "block_submit_on_failure": settings.block_submit_on_failure,
        "store_full_response": settings.store_full_response,
    }
