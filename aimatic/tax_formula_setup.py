"""Keep company-specific Tax Formula account links valid after fixture sync."""

from __future__ import annotations

import frappe


def repair_dangling_gst_accounts() -> dict:
    """Point invalid GST formulas at the sole company's standard GST account.

    Tax Formula is shared configuration and has no company field, while Account
    names are company-specific. We therefore repair links only when the site
    has exactly one company and its enabled leaf ``GST - <abbr>`` tax account
    exists. Existing valid links are deliberately preserved.
    """
    companies = frappe.get_all(
        "Company",
        fields=["name", "abbr"],
        order_by="name",
        limit_page_length=2,
    )
    if len(companies) != 1:
        return {"status": "skipped", "reason": "site_does_not_have_exactly_one_company", "updated": []}

    company = companies[0]
    gst_account = frappe.db.get_value(
        "Account",
        {
            "name": f"GST - {company.abbr}",
            "company": company.name,
            "account_type": "Tax",
            "is_group": 0,
            "disabled": 0,
        },
        "name",
    )
    if not gst_account:
        return {"status": "skipped", "reason": "standard_gst_account_not_found", "updated": []}

    updated = []
    formulas = frappe.get_all(
        "Tax Formula",
        filters={"formula_type": "gst"},
        fields=["name", "gst_account"],
        order_by="name",
    )
    for formula in formulas:
        if formula.gst_account and frappe.db.exists(
            "Account",
            {
                "name": formula.gst_account,
                "company": company.name,
                "account_type": "Tax",
                "is_group": 0,
                "disabled": 0,
            },
        ):
            continue

        frappe.db.set_value(
            "Tax Formula",
            formula.name,
            "gst_account",
            gst_account,
            update_modified=False,
        )
        updated.append(formula.name)

    return {"status": "updated", "gst_account": gst_account, "updated": updated}


def after_migrate() -> None:
    """Repair site-specific links after the generic fixture has been synced."""
    repair_dangling_gst_accounts()
