"""One-off: backfill Branch on S1GT Counter 4 POS history that posted without it.

Root cause: POS Invoice Branch was taken from the API session user's Branch
User Permission, not the POS Profile. Counter 4 cashier mawais@ had no Branch
permission; close_shift then consolidated as Administrator, so Sales Invoice /
GL Income-Expense rows also lacked Branch (cost center was filled from the
profile). Forward fix is offline_pos._apply_pos_profile_accounting_context.

Scope (Siezal Supermarket / S1GT Counter 4 only):
  - POS Invoice (+ Item, Sales Taxes and Charges)
  - consolidated is_pos Sales Invoice (+ Item, taxes)
  - GL Entry and Payment Ledger Entry for those Sales Invoices

Run:
  bench --site szl execute aimatic.ops_backfill_counter4_branch.run
  bench --site szl execute aimatic.ops_backfill_counter4_branch.run --kwargs "{'dry_run': 1}"

Rollback: restore DB backup 20260823_042448-szl-database.sql.gz (taken immediately
before the live run), or re-null Branch only for names listed in the run return.
"""

from __future__ import annotations

import frappe

BRANCH = "S1 - Ghouri Town VIP"
COST_CENTER = "S1 - Ghouri Town VIP - SSM"
POS_PROFILE = "S1GT Counter 4"
COMPANY = "Siezal Supermarket"


def _count(sql, values=None):
    return int(frappe.db.sql(sql, values or {})[0][0])


def _preview():
    values = {
        "branch": BRANCH,
        "profile": POS_PROFILE,
        "company": COMPANY,
        "cost_center": COST_CENTER,
    }
    return {
        "pos_invoice": _count(
            """
            select count(*) from `tabPOS Invoice`
            where pos_profile = %(profile)s and docstatus = 1
              and coalesce(branch, '') = ''
            """,
            values,
        ),
        "pos_invoice_item": _count(
            """
            select count(*) from `tabPOS Invoice Item` pi
            inner join `tabPOS Invoice` p on p.name = pi.parent
            where p.pos_profile = %(profile)s and p.docstatus = 1
              and coalesce(pi.branch, '') = ''
            """,
            values,
        ),
        "pos_tax": _count(
            """
            select count(*) from `tabSales Taxes and Charges` t
            inner join `tabPOS Invoice` p on p.name = t.parent
            where p.pos_profile = %(profile)s and p.docstatus = 1
              and coalesce(t.branch, '') = ''
            """,
            values,
        ),
        "sales_invoice": _count(
            """
            select count(*) from `tabSales Invoice`
            where company = %(company)s and pos_profile = %(profile)s
              and docstatus = 1 and is_pos = 1 and coalesce(branch, '') = ''
            """,
            values,
        ),
        "sales_invoice_item": _count(
            """
            select count(*) from `tabSales Invoice Item` sii
            inner join `tabSales Invoice` si on si.name = sii.parent
            where si.company = %(company)s and si.pos_profile = %(profile)s
              and si.docstatus = 1 and si.is_pos = 1
              and coalesce(sii.branch, '') = ''
            """,
            values,
        ),
        "sales_tax": _count(
            """
            select count(*) from `tabSales Taxes and Charges` t
            inner join `tabSales Invoice` si on si.name = t.parent
            where si.company = %(company)s and si.pos_profile = %(profile)s
              and si.docstatus = 1 and si.is_pos = 1
              and coalesce(t.branch, '') = ''
            """,
            values,
        ),
        "gl_entry": _count(
            """
            select count(*) from `tabGL Entry` gle
            inner join `tabSales Invoice` si on si.name = gle.voucher_no
            where gle.voucher_type = 'Sales Invoice' and gle.is_cancelled = 0
              and si.company = %(company)s and si.pos_profile = %(profile)s
              and si.docstatus = 1 and si.is_pos = 1
              and coalesce(gle.branch, '') = ''
            """,
            values,
        ),
        "payment_ledger": _count(
            """
            select count(*) from `tabPayment Ledger Entry` ple
            inner join `tabSales Invoice` si on si.name = ple.against_voucher_no
            where ple.against_voucher_type = 'Sales Invoice'
              and ple.company = %(company)s and si.pos_profile = %(profile)s
              and si.docstatus = 1 and si.is_pos = 1
              and coalesce(ple.branch, '') = ''
            """,
            values,
        ),
        "reporting_missing_branch_gle": _count(
            """
            select count(*) from `tabGL Entry` gle
            inner join `tabAccount` account on account.name = gle.account
            where gle.company = %(company)s and gle.is_cancelled = 0
              and gle.posting_date between '2026-07-01' and '2027-06-30'
              and account.root_type in ('Income', 'Expense')
              and coalesce(gle.branch, '') = ''
            """,
            values,
        ),
    }


def run(dry_run=0):
    """Stamp Branch on Counter 4 POS history. dry_run=1 counts only."""
    dry_run = int(dry_run or 0)

    if not frappe.db.exists("Branch", BRANCH):
        frappe.throw(f"Branch {BRANCH} does not exist")
    if frappe.db.get_value("Branch", BRANCH, "cost_center") != COST_CENTER:
        frappe.throw(f"Branch {BRANCH} cost_center is not {COST_CENTER}")
    if not frappe.db.exists("POS Profile", POS_PROFILE):
        frappe.throw(f"POS Profile {POS_PROFILE} does not exist")
    if frappe.db.get_value("POS Profile", POS_PROFILE, "branch") != BRANCH:
        frappe.throw(f"POS Profile {POS_PROFILE} branch is not {BRANCH}")

    before = _preview()
    if dry_run:
        return {
            "dry_run": 1,
            "branch": BRANCH,
            "pos_profile": POS_PROFILE,
            "before": before,
        }

    values = {
        "branch": BRANCH,
        "profile": POS_PROFILE,
        "company": COMPANY,
    }

    # Parents first, then children / ledgers keyed by pos_profile.
    frappe.db.sql(
        """
        update `tabPOS Invoice`
        set branch = %(branch)s
        where pos_profile = %(profile)s and docstatus = 1
          and coalesce(branch, '') = ''
        """,
        values,
    )
    frappe.db.sql(
        """
        update `tabPOS Invoice Item` pi
        inner join `tabPOS Invoice` p on p.name = pi.parent
        set pi.branch = %(branch)s
        where p.pos_profile = %(profile)s and p.docstatus = 1
          and coalesce(pi.branch, '') = ''
        """,
        values,
    )
    frappe.db.sql(
        """
        update `tabSales Taxes and Charges` t
        inner join `tabPOS Invoice` p on p.name = t.parent
        set t.branch = %(branch)s
        where p.pos_profile = %(profile)s and p.docstatus = 1
          and coalesce(t.branch, '') = ''
        """,
        values,
    )

    frappe.db.sql(
        """
        update `tabSales Invoice`
        set branch = %(branch)s
        where company = %(company)s and pos_profile = %(profile)s
          and docstatus = 1 and is_pos = 1 and coalesce(branch, '') = ''
        """,
        values,
    )
    frappe.db.sql(
        """
        update `tabSales Invoice Item` sii
        inner join `tabSales Invoice` si on si.name = sii.parent
        set sii.branch = %(branch)s
        where si.company = %(company)s and si.pos_profile = %(profile)s
          and si.docstatus = 1 and si.is_pos = 1
          and coalesce(sii.branch, '') = ''
        """,
        values,
    )
    frappe.db.sql(
        """
        update `tabSales Taxes and Charges` t
        inner join `tabSales Invoice` si on si.name = t.parent
        set t.branch = %(branch)s
        where si.company = %(company)s and si.pos_profile = %(profile)s
          and si.docstatus = 1 and si.is_pos = 1
          and coalesce(t.branch, '') = ''
        """,
        values,
    )

    frappe.db.sql(
        """
        update `tabGL Entry` gle
        inner join `tabSales Invoice` si on si.name = gle.voucher_no
        set gle.branch = %(branch)s
        where gle.voucher_type = 'Sales Invoice' and gle.is_cancelled = 0
          and si.company = %(company)s and si.pos_profile = %(profile)s
          and si.docstatus = 1 and si.is_pos = 1
          and coalesce(gle.branch, '') = ''
        """,
        values,
    )
    frappe.db.sql(
        """
        update `tabPayment Ledger Entry` ple
        inner join `tabSales Invoice` si on si.name = ple.against_voucher_no
        set ple.branch = %(branch)s
        where ple.against_voucher_type = 'Sales Invoice'
          and ple.company = %(company)s and si.pos_profile = %(profile)s
          and si.docstatus = 1 and si.is_pos = 1
          and coalesce(ple.branch, '') = ''
        """,
        values,
    )

    frappe.db.commit()
    after = _preview()
    return {
        "dry_run": 0,
        "branch": BRANCH,
        "pos_profile": POS_PROFILE,
        "backup": "20260823_042448-szl-database.sql.gz",
        "before": before,
        "after": after,
    }
