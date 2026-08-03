"""Keep company-specific Tax Formula account links valid after fixture sync."""

from __future__ import annotations

import frappe

# (formula_type, account fieldname, standard account name template)
_REPAIRABLE_ACCOUNTS = [
    ("gst", "gst_account", "GST - {abbr}"),
    ("advance_tax", "advance_tax_account", "Advance Tax Deducted by Suppliers - {abbr}"),
]


def _repair_formula_account(formula_type: str, fieldname: str, account_name_template: str) -> dict:
    """Point invalid/blank links of one account field at the sole company's standard account.

    Tax Formula is shared configuration and has no company field, while Account
    names are company-specific. We therefore repair links only when the site
    has exactly one company and its enabled leaf standard account exists.
    Existing valid links are deliberately preserved.
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
    standard_account = frappe.db.get_value(
        "Account",
        {
            "name": account_name_template.format(abbr=company.abbr),
            "company": company.name,
            "account_type": "Tax",
            "is_group": 0,
            "disabled": 0,
        },
        "name",
    )
    if not standard_account:
        return {"status": "skipped", "reason": "standard_account_not_found", "updated": []}

    updated = []
    formulas = frappe.get_all(
        "Tax Formula",
        filters={"formula_type": formula_type},
        fields=["name", fieldname],
        order_by="name",
    )
    for formula in formulas:
        current_value = getattr(formula, fieldname)
        if current_value and frappe.db.exists(
            "Account",
            {
                "name": current_value,
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
            fieldname,
            standard_account,
            update_modified=False,
        )
        updated.append(formula.name)

    return {"status": "updated", "account": standard_account, "updated": updated}


def repair_dangling_gst_accounts() -> dict:
    """Repair Tax Formula.gst_account links. Kept for backward compatibility."""
    return _repair_formula_account("gst", "gst_account", "GST - {abbr}")


def repair_dangling_advance_tax_accounts() -> dict:
    """Repair Tax Formula.advance_tax_account links.

    The shared fixture ships this field blank (unlike gst_account, which
    ships a placeholder), so a fixture sync wipes it to blank on every site
    and, without this repair, it never gets restored.
    """
    return _repair_formula_account(
        "advance_tax", "advance_tax_account", "Advance Tax Deducted by Suppliers - {abbr}"
    )


def repair_dangling_tax_formula_accounts() -> dict:
    """Repair every known Tax Formula account link field."""
    return {
        fieldname: _repair_formula_account(formula_type, fieldname, template)
        for formula_type, fieldname, template in _REPAIRABLE_ACCOUNTS
    }


def after_migrate() -> None:
    """Repair site-specific links after the generic fixture has been synced."""
    repair_dangling_tax_formula_accounts()
