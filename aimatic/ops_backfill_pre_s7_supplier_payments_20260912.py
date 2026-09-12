"""One-off: backfill cost_center on szl supplier Payment Entries that predate
the S7 Empire Heights cutover (2026-09-06).

Context: `ops_backfill_cost_center_20260912` deliberately left supplier
cheque/bank Payment Entries out of scope, since with two live branches there
is no single formula for which branch a payment belongs to. User confirmed
(2026-09-12) that every posting before the S7 go-live belongs to S1, and
runtime evidence agrees: `Branch` shows S2/S4/S5/S6/S7 all created
2026-07-27 as unprovisioned master data, but the only POS Profiles with any
GL activity before 2026-09-06 are the four S1 ones (S1GT Counter 1/2/4, S1
Food Panda) — S7's profiles start exactly 2026-09-06. So "before S7" and
"S1" are the same set on this data.

Scope:
  - Payment Entry (+ its GL Entry rows) missing cost_center, posting_date <
    2026-09-06, mode_of_payment not already handled by the cash-branch
    backfill (i.e. not Cash - S1GT / Cash - S7EH) -> S1 - Ghouri Town VIP - SSM.
  - One additional row on/after 2026-09-06 (`ACC-PAY-2026-00289`) resolved by
    its own linked Purchase Invoice's branch (S1 - Ghouri Town VIP), not by
    the date rule -> same cost centre, for an independently verified reason.

Explicitly NOT touched: the other ~7 Payment Entries dated on/after
2026-09-06 with no resolvable branch evidence. Recorded in
docs/reference/known-issues.md; still needs a per-voucher decision.

Run:
  bench --site szl execute aimatic.ops_backfill_pre_s7_supplier_payments_20260912.run --kwargs "{'dry_run': 1}"
  bench --site szl execute aimatic.ops_backfill_pre_s7_supplier_payments_20260912.run

Rollback: restore DB backup 20260912_232841-szl-database.sql.gz (taken
immediately before this ran).
"""

from __future__ import annotations

import frappe

COMPANY = "Siezal Supermarket"
S1_COST_CENTER = "S1 - Ghouri Town VIP - SSM"
S7_CUTOVER_DATE = "2026-09-06"
ALREADY_HANDLED_MOPS = ("Cash - S1GT", "Cash - S7EH")
EXTRA_RESOLVED_PAYMENT_ENTRY = "ACC-PAY-2026-00289"


def _count(sql, values=None):
    return int(frappe.db.sql(sql, values or {})[0][0])


def _preview():
    return {
        "pre_s7_payment_entry": _count(
            """
            select count(*) from `tabPayment Entry`
            where docstatus = 1 and coalesce(cost_center, '') = ''
              and mode_of_payment not in %(mops)s
              and posting_date < %(cutover)s
            """,
            {"mops": ALREADY_HANDLED_MOPS, "cutover": S7_CUTOVER_DATE},
        ),
        "pre_s7_gl_entry": _count(
            """
            select count(*) from `tabGL Entry` gle
            inner join `tabPayment Entry` pe on pe.name = gle.voucher_no
            where gle.voucher_type = 'Payment Entry' and gle.is_cancelled = 0
              and coalesce(gle.cost_center, '') = ''
              and pe.mode_of_payment not in %(mops)s
              and pe.posting_date < %(cutover)s
            """,
            {"mops": ALREADY_HANDLED_MOPS, "cutover": S7_CUTOVER_DATE},
        ),
        "extra_resolved_payment_entry": _count(
            """
            select count(*) from `tabPayment Entry`
            where name = %(name)s and coalesce(cost_center, '') = ''
            """,
            {"name": EXTRA_RESOLVED_PAYMENT_ENTRY},
        ),
        "extra_resolved_gl_entry": _count(
            """
            select count(*) from `tabGL Entry`
            where voucher_type = 'Payment Entry' and voucher_no = %(name)s
              and is_cancelled = 0 and coalesce(cost_center, '') = ''
            """,
            {"name": EXTRA_RESOLVED_PAYMENT_ENTRY},
        ),
    }


def run(dry_run=0):
    dry_run = int(dry_run or 0)

    if frappe.db.get_value("Cost Center", S1_COST_CENTER, "company") != COMPANY:
        frappe.throw(f"Cost Center {S1_COST_CENTER} not found under {COMPANY}")

    linked_pi_cost_centers = frappe.db.sql(
        """
        select distinct pi.cost_center
        from `tabPayment Entry Reference` per
        inner join `tabPurchase Invoice` pi on pi.name = per.reference_name
        where per.parent = %(name)s and per.reference_doctype = 'Purchase Invoice'
        """,
        {"name": EXTRA_RESOLVED_PAYMENT_ENTRY},
    )
    if not linked_pi_cost_centers or len(linked_pi_cost_centers) != 1:
        frappe.throw(
            f"{EXTRA_RESOLVED_PAYMENT_ENTRY} does not resolve to exactly one linked "
            f"Purchase Invoice cost_center as expected - stopping: {linked_pi_cost_centers}"
        )
    if linked_pi_cost_centers[0][0] != S1_COST_CENTER:
        frappe.throw(
            f"{EXTRA_RESOLVED_PAYMENT_ENTRY}'s linked Purchase Invoice cost_center is "
            f"{linked_pi_cost_centers[0][0]}, not {S1_COST_CENTER} as expected - stopping."
        )

    before = _preview()
    if dry_run:
        return {"dry_run": 1, "before": before}

    frappe.db.sql(
        """
        update `tabPayment Entry`
        set cost_center = %(cc)s
        where docstatus = 1 and coalesce(cost_center, '') = ''
          and mode_of_payment not in %(mops)s
          and posting_date < %(cutover)s
        """,
        {"cc": S1_COST_CENTER, "mops": ALREADY_HANDLED_MOPS, "cutover": S7_CUTOVER_DATE},
    )
    frappe.db.sql(
        """
        update `tabGL Entry` gle
        inner join `tabPayment Entry` pe on pe.name = gle.voucher_no
        set gle.cost_center = pe.cost_center
        where gle.voucher_type = 'Payment Entry' and gle.is_cancelled = 0
          and coalesce(gle.cost_center, '') = ''
          and pe.mode_of_payment not in %(mops)s
          and pe.posting_date < %(cutover)s
        """,
        {"mops": ALREADY_HANDLED_MOPS, "cutover": S7_CUTOVER_DATE},
    )

    frappe.db.sql(
        """
        update `tabPayment Entry`
        set cost_center = %(cc)s
        where name = %(name)s and coalesce(cost_center, '') = ''
        """,
        {"cc": S1_COST_CENTER, "name": EXTRA_RESOLVED_PAYMENT_ENTRY},
    )
    frappe.db.sql(
        """
        update `tabGL Entry`
        set cost_center = %(cc)s
        where voucher_type = 'Payment Entry' and voucher_no = %(name)s
          and is_cancelled = 0 and coalesce(cost_center, '') = ''
        """,
        {"cc": S1_COST_CENTER, "name": EXTRA_RESOLVED_PAYMENT_ENTRY},
    )

    frappe.db.commit()
    after = _preview()
    return {
        "dry_run": 0,
        "backup": "20260912_232841-szl-database.sql.gz",
        "before": before,
        "after": after,
    }
