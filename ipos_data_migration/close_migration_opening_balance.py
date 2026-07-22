"""Final, cross-cutting step for a new site's iPOS migration -- run once after
BOTH the item import (import_<site>_items.py) and the supplier import
(import_<site>_suppliers.py) are complete.

Why this exists: both imports route their one-time opening entries through the
same suspense account, "Temporary Opening" (Asset / Balance Sheet,
account_type "Temporary") -- opening stock credits it, vendor opening
balances debit/credit it against Payable. See import.md's "Opening-stock GL
posting" note for why Stock Adjustment (Expense / P&L) must never be used for
this instead. Once every opening entry for the site is posted, Temporary
Opening should be holding exactly the net difference between recorded opening
assets and recorded opening liabilities -- that residual IS the business's
opening equity contribution (or drawdown), and belongs in Opening Balance
Equity (Equity / Balance Sheet), not left sitting in a suspense account
indefinitely.

This script does NOT run automatically as part of either import -- it must be
run by hand, once, after confirming both imports are fully done for the site
(re-running it after that is safe/idempotent: if Temporary Opening nets to
zero, it does nothing).
"""

import frappe

# --- Edit these before each run -------------------------------------------------
COMPANY = "Siezal Super Market"
POSTING_DATE = None  # None = today; set explicitly to match the migration's cutover date


def resolve_company_branch(company):
    """Journal Entry has no aimatic.branch_management hook (see
    import_siezal_suppliers.py's resolve_company_branch for the full
    rationale) -- this closing entry must set branch/cost_center itself."""
    branches = frappe.get_all("Branch", filters={"company": company}, pluck="name", order_by="name")
    if not branches:
        frappe.throw(f"No Branch configured for {company}.")
    if len(branches) > 1:
        frappe.throw(
            f"{company} has multiple Branches ({', '.join(branches)}) -- pick which one this "
            "closing entry belongs to explicitly before running."
        )
    branch = branches[0]
    cost_center = frappe.get_cached_value("Branch", branch, "cost_center")
    if not cost_center:
        frappe.throw(f"Branch {branch} has no Cost Center configured.")
    return branch, cost_center


def execute():
    posting_date = POSTING_DATE or frappe.utils.today()
    branch, cost_center = resolve_company_branch(COMPANY)

    temp_opening_account = frappe.db.get_value(
        "Account", {"company": COMPANY, "account_name": "Temporary Opening", "is_group": 0}
    )
    equity_account = frappe.db.get_value(
        "Account", {"company": COMPANY, "account_name": "Opening Balance Equity", "is_group": 0}
    )
    if not temp_opening_account:
        frappe.throw(f"No 'Temporary Opening' account found under {COMPANY}.")
    if not equity_account:
        frappe.throw(f"No 'Opening Balance Equity' account found under {COMPANY}.")

    net = frappe.db.sql(
        """
        select sum(debit - credit) as net_debit
        from `tabGL Entry`
        where account = %s and docstatus = 1 and is_cancelled = 0
        """,
        (temp_opening_account,),
    )[0][0] or 0
    net = frappe.utils.flt(net, 2)

    if not net:
        print(f"{temp_opening_account} already nets to zero -- nothing to close.")
        return

    je = frappe.new_doc("Journal Entry")
    je.voucher_type = "Opening Entry"
    je.company = COMPANY
    je.posting_date = posting_date
    je.is_opening = "Yes"
    je.user_remark = "Close residual Temporary Opening balance from iPOS migration to Opening Balance Equity"

    amount = abs(net)
    if net > 0:
        # Temporary Opening net debit -> recorded opening liabilities exceed
        # recorded opening assets; the residual is a drawdown against equity.
        je.append(
            "accounts",
            {
                "account": temp_opening_account,
                "credit_in_account_currency": amount,
                "branch": branch,
                "cost_center": cost_center,
            },
        )
        je.append(
            "accounts",
            {
                "account": equity_account,
                "debit_in_account_currency": amount,
                "branch": branch,
                "cost_center": cost_center,
            },
        )
    else:
        # Temporary Opening net credit -> recorded opening assets (e.g.
        # opening stock) exceed recorded opening liabilities; the residual is
        # a net equity contribution.
        je.append(
            "accounts",
            {
                "account": temp_opening_account,
                "debit_in_account_currency": amount,
                "branch": branch,
                "cost_center": cost_center,
            },
        )
        je.append(
            "accounts",
            {
                "account": equity_account,
                "credit_in_account_currency": amount,
                "branch": branch,
                "cost_center": cost_center,
            },
        )

    je.insert(ignore_permissions=True)
    je.submit()
    frappe.db.commit()
    print(f"Closed {temp_opening_account} residual of {net} to {equity_account} via {je.name}")
