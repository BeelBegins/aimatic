"""One-off: backfill cost_center gaps found on szl 2026-09-12.

Root causes:
  1. `fbr_pos.accounting.add_fbr_sales_tax_row` / `add_fbr_pos_fee_row` append
     the GST and FBR POS Service Fee tax rows without a `cost_center`, on every
     Sales Invoice / POS Invoice, since go-live. Forward-fixed alongside this
     script by setting `"cost_center": doc.cost_center` in both append calls.
  2. Historical-only: `offline_pos._apply_pos_profile_accounting_context` (the
     Branch/Cost Center stamp) was deployed 2026-08-23 (see the Counter 4
     Branch backfill note in current-state.md). POS Invoices from
     2026-08-03..2026-08-22 on the four affected S1 profiles predate that fix
     and have a blank `cost_center` header (and therefore blank tax rows too).
     Confirmed 0 missing on every date from 2026-08-23 onward and on S7 — not
     an active/ongoing gap, purely historical.
  3. Manual entries: two Payment Entries never had a cost centre selected
     (`ACC-PAY-2026-00001`/`-00002`, 2026-08-03, `Cash - S1GT`), and one has
     the wrong one (`ACC-PAY-2026-00254`, 2026-09-09, `Cash - S7EH` mode of
     payment but `S1 - Ghouri Town VIP` cost centre).

Deliberately OUT of scope (needs a business decision, not a formula):
  supplier cheque/bank Payment Entries missing cost_center (Creditors/Bank
  Account GL rows), and the ~13 other go-live vouchers beyond the two cash
  ones above. Do not touch those here.

Run:
  bench --site szl execute aimatic.ops_backfill_cost_center_20260912.run --kwargs "{'dry_run': 1}"
  bench --site szl execute aimatic.ops_backfill_cost_center_20260912.run

Rollback: restore DB backup 20260912_230922-szl-database.sql.gz (taken
immediately before this ran), or re-null cost_center only for the rows this
script touches (see `after` counts in the return value for scope).
"""

from __future__ import annotations

import frappe

COMPANY = "Siezal Supermarket"

S1_COST_CENTER = "S1 - Ghouri Town VIP - SSM"
S7_COST_CENTER = "S7 - Empire Heights - SSM"

S1_POS_PROFILES = ["S1GT Counter 1", "S1GT Counter 2", "S1GT Counter 4", "S1 Food Panda"]

TAX_ACCOUNTS = ["GST - SSM", "Fbr Pos Service Fee - SSM"]

CASH_ACCOUNT_BY_MOP = {
    "Cash - S1GT": "1111 - Cash in Hand - S1GT - SSM",
    "Cash - S7EH": "1112 - Cash in Hand - S7EH - SSM",
}
COST_CENTER_BY_MOP = {
    "Cash - S1GT": S1_COST_CENTER,
    "Cash - S7EH": S7_COST_CENTER,
}

MISTAGGED_CASH_PAYMENT_ENTRY = "ACC-PAY-2026-00254"


def _count(sql, values=None):
    return int(frappe.db.sql(sql, values or {})[0][0])


def _preview():
    return {
        "pos_invoice_header": _count(
            """
            select count(*) from `tabPOS Invoice`
            where docstatus = 1 and pos_profile in %(profiles)s
              and coalesce(cost_center, '') = ''
            """,
            {"profiles": tuple(S1_POS_PROFILES)},
        ),
        "pos_invoice_tax_rows": _count(
            """
            select count(*) from `tabSales Taxes and Charges` t
            inner join `tabPOS Invoice` p on p.name = t.parent
            where t.parenttype = 'POS Invoice' and p.docstatus = 1
              and t.account_head in %(accounts)s
              and coalesce(t.cost_center, '') = ''
            """,
            {"accounts": tuple(TAX_ACCOUNTS)},
        ),
        "sales_invoice_tax_rows": _count(
            """
            select count(*) from `tabSales Taxes and Charges` t
            inner join `tabSales Invoice` si on si.name = t.parent
            where t.parenttype = 'Sales Invoice' and si.docstatus = 1
              and t.account_head in %(accounts)s
              and coalesce(t.cost_center, '') = ''
            """,
            {"accounts": tuple(TAX_ACCOUNTS)},
        ),
        "sales_invoice_gl_entry": _count(
            """
            select count(*) from `tabGL Entry`
            where voucher_type = 'Sales Invoice' and is_cancelled = 0
              and account in %(accounts)s
              and coalesce(cost_center, '') = ''
            """,
            {"accounts": tuple(TAX_ACCOUNTS)},
        ),
        "cash_payment_entry": _count(
            """
            select count(*) from `tabPayment Entry`
            where docstatus = 1 and mode_of_payment in %(mops)s
              and coalesce(cost_center, '') = ''
            """,
            {"mops": tuple(COST_CENTER_BY_MOP)},
        ),
        "cash_gl_entry": _count(
            """
            select count(*) from `tabGL Entry` gle
            inner join `tabPayment Entry` pe on pe.name = gle.voucher_no
            where gle.voucher_type = 'Payment Entry' and gle.is_cancelled = 0
              and pe.mode_of_payment in %(mops)s
              and gle.account in %(cash_accounts)s
              and coalesce(gle.cost_center, '') = ''
            """,
            {
                "mops": tuple(COST_CENTER_BY_MOP),
                "cash_accounts": tuple(CASH_ACCOUNT_BY_MOP.values()),
            },
        ),
        "mistagged_payment_entry": _count(
            """
            select count(*) from `tabPayment Entry`
            where name = %(name)s and cost_center = %(wrong)s
            """,
            {"name": MISTAGGED_CASH_PAYMENT_ENTRY, "wrong": S1_COST_CENTER},
        ),
        "mistagged_gl_entry": _count(
            """
            select count(*) from `tabGL Entry`
            where voucher_type = 'Payment Entry' and voucher_no = %(name)s
              and account = %(account)s and cost_center = %(wrong)s
            """,
            {
                "name": MISTAGGED_CASH_PAYMENT_ENTRY,
                "account": CASH_ACCOUNT_BY_MOP["Cash - S7EH"],
                "wrong": S1_COST_CENTER,
            },
        ),
    }


def run(dry_run=0):
    """Backfill the cost_center gaps described above. dry_run=1 counts only."""
    dry_run = int(dry_run or 0)

    if frappe.db.get_value("Cost Center", S1_COST_CENTER, "company") != COMPANY:
        frappe.throw(f"Cost Center {S1_COST_CENTER} not found under {COMPANY}")
    if frappe.db.get_value("Cost Center", S7_COST_CENTER, "company") != COMPANY:
        frappe.throw(f"Cost Center {S7_COST_CENTER} not found under {COMPANY}")
    for profile in S1_POS_PROFILES:
        if frappe.db.get_value("POS Profile", profile, "cost_center") != S1_COST_CENTER:
            frappe.throw(f"POS Profile {profile} cost_center is not {S1_COST_CENTER}")

    before = _preview()
    if dry_run:
        return {"dry_run": 1, "before": before}

    # 1. Historical POS Invoice header stamp (must run before the POS Invoice
    #    tax-row copy below, so pre-fix invoices' tax rows inherit a value).
    frappe.db.sql(
        """
        update `tabPOS Invoice`
        set cost_center = %(cc)s
        where docstatus = 1 and pos_profile in %(profiles)s
          and coalesce(cost_center, '') = ''
        """,
        {"cc": S1_COST_CENTER, "profiles": tuple(S1_POS_PROFILES)},
    )

    # 2. Tax rows: copy from each row's own parent invoice's cost_center.
    frappe.db.sql(
        """
        update `tabSales Taxes and Charges` t
        inner join `tabPOS Invoice` p on p.name = t.parent
        set t.cost_center = p.cost_center
        where t.parenttype = 'POS Invoice' and p.docstatus = 1
          and t.account_head in %(accounts)s
          and coalesce(t.cost_center, '') = ''
        """,
        {"accounts": tuple(TAX_ACCOUNTS)},
    )
    frappe.db.sql(
        """
        update `tabSales Taxes and Charges` t
        inner join `tabSales Invoice` si on si.name = t.parent
        set t.cost_center = si.cost_center
        where t.parenttype = 'Sales Invoice' and si.docstatus = 1
          and t.account_head in %(accounts)s
          and coalesce(t.cost_center, '') = ''
        """,
        {"accounts": tuple(TAX_ACCOUNTS)},
    )

    # 3. Consolidated Sales Invoice GL Entry rows for the same two tax accounts.
    frappe.db.sql(
        """
        update `tabGL Entry` gle
        inner join `tabSales Invoice` si on si.name = gle.voucher_no
        set gle.cost_center = si.cost_center
        where gle.voucher_type = 'Sales Invoice' and gle.is_cancelled = 0
          and gle.account in %(accounts)s
          and coalesce(gle.cost_center, '') = ''
        """,
        {"accounts": tuple(TAX_ACCOUNTS)},
    )

    # 4. Cash Payment Entries missing cost_center, by mode-of-payment -> branch.
    for mop, cc in COST_CENTER_BY_MOP.items():
        frappe.db.sql(
            """
            update `tabPayment Entry`
            set cost_center = %(cc)s
            where docstatus = 1 and mode_of_payment = %(mop)s
              and coalesce(cost_center, '') = ''
            """,
            {"cc": cc, "mop": mop},
        )
    frappe.db.sql(
        """
        update `tabGL Entry` gle
        inner join `tabPayment Entry` pe on pe.name = gle.voucher_no
        set gle.cost_center = pe.cost_center
        where gle.voucher_type = 'Payment Entry' and gle.is_cancelled = 0
          and pe.mode_of_payment in %(mops)s
          and gle.account in %(cash_accounts)s
          and coalesce(gle.cost_center, '') = ''
        """,
        {
            "mops": tuple(COST_CENTER_BY_MOP),
            "cash_accounts": tuple(CASH_ACCOUNT_BY_MOP.values()),
        },
    )

    # 5. The one mis-tagged cash payment: S1 cost centre on an S7EH cash entry.
    frappe.db.sql(
        """
        update `tabPayment Entry`
        set cost_center = %(right)s
        where name = %(name)s and cost_center = %(wrong)s
        """,
        {
            "right": S7_COST_CENTER,
            "name": MISTAGGED_CASH_PAYMENT_ENTRY,
            "wrong": S1_COST_CENTER,
        },
    )
    frappe.db.sql(
        """
        update `tabGL Entry`
        set cost_center = %(right)s
        where voucher_type = 'Payment Entry' and voucher_no = %(name)s
          and account = %(account)s and cost_center = %(wrong)s
        """,
        {
            "right": S7_COST_CENTER,
            "name": MISTAGGED_CASH_PAYMENT_ENTRY,
            "account": CASH_ACCOUNT_BY_MOP["Cash - S7EH"],
            "wrong": S1_COST_CENTER,
        },
    )

    frappe.db.commit()
    after = _preview()
    return {
        "dry_run": 0,
        "backup": "20260912_230922-szl-database.sql.gz",
        "before": before,
        "after": after,
    }
