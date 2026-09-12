"""One-off: backfill the last 7 szl Payment Entries missing cost_center.

These postdate the S7 Empire Heights cutover (2026-09-06), so the earlier
date-based rule (ops_backfill_pre_s7_supplier_payments_20260912) doesn't
apply to them - a second branch now exists. User confirmed (2026-09-12)
both creators only ever work S1; runtime evidence agrees: User Permission
restricts both `mzaman@aimatic.tech` and `smir@aimatic.tech` to Branch
S1 - Ghouri Town VIP, with no S7 permission at all, so neither could have
been posting for S7.

Scope: the exact 7 named Payment Entries (+ their GL Entry rows) below,
owned by those two users, missing cost_center -> S1 - Ghouri Town VIP - SSM.
Nothing else.

Run:
  bench --site szl execute aimatic.ops_backfill_s1_only_users_payments_20260912.run --kwargs "{'dry_run': 1}"
  bench --site szl execute aimatic.ops_backfill_s1_only_users_payments_20260912.run

Rollback: restore DB backup 20260912_233859-szl-database.sql.gz (taken
immediately before this ran).
"""

from __future__ import annotations

import frappe

COMPANY = "Siezal Supermarket"
S1_COST_CENTER = "S1 - Ghouri Town VIP - SSM"
S1_ONLY_OWNERS = ("mzaman@aimatic.tech", "smir@aimatic.tech")
TARGET_PAYMENT_ENTRIES = (
    "ACC-PAY-2026-00244",
    "ACC-PAY-2026-00253",
    "ACC-PAY-2026-00255",
    "ACC-PAY-2026-00281",
    "ACC-PAY-2026-00299",
    "ACC-PAY-2026-00306",
    "ACC-PAY-2026-00307",
)


def _count(sql, values=None):
    return int(frappe.db.sql(sql, values or {})[0][0])


def _preview():
    return {
        "payment_entry": _count(
            """
            select count(*) from `tabPayment Entry`
            where name in %(names)s and coalesce(cost_center, '') = ''
            """,
            {"names": TARGET_PAYMENT_ENTRIES},
        ),
        "gl_entry": _count(
            """
            select count(*) from `tabGL Entry`
            where voucher_type = 'Payment Entry' and is_cancelled = 0
              and voucher_no in %(names)s and coalesce(cost_center, '') = ''
            """,
            {"names": TARGET_PAYMENT_ENTRIES},
        ),
    }


def run(dry_run=0):
    dry_run = int(dry_run or 0)

    if frappe.db.get_value("Cost Center", S1_COST_CENTER, "company") != COMPANY:
        frappe.throw(f"Cost Center {S1_COST_CENTER} not found under {COMPANY}")

    rows = frappe.db.sql(
        """
        select name, owner from `tabPayment Entry` where name in %(names)s
        """,
        {"names": TARGET_PAYMENT_ENTRIES},
        as_dict=True,
    )
    if len(rows) != len(TARGET_PAYMENT_ENTRIES):
        frappe.throw(f"Expected {len(TARGET_PAYMENT_ENTRIES)} Payment Entries, found {len(rows)}")
    for row in rows:
        if row.owner not in S1_ONLY_OWNERS:
            frappe.throw(f"{row.name} owner {row.owner} is not in the verified S1-only owner list - stopping")
    for user in S1_ONLY_OWNERS:
        branches = frappe.db.sql(
            """
            select for_value from `tabUser Permission`
            where user = %(user)s and allow = 'Branch'
            """,
            {"user": user},
        )
        if [b[0] for b in branches] != ["S1 - Ghouri Town VIP"]:
            frappe.throw(f"{user} Branch User Permission is not exactly S1-only as expected - stopping")

    before = _preview()
    if dry_run:
        return {"dry_run": 1, "before": before}

    frappe.db.sql(
        """
        update `tabPayment Entry`
        set cost_center = %(cc)s
        where name in %(names)s and coalesce(cost_center, '') = ''
        """,
        {"cc": S1_COST_CENTER, "names": TARGET_PAYMENT_ENTRIES},
    )
    frappe.db.sql(
        """
        update `tabGL Entry`
        set cost_center = %(cc)s
        where voucher_type = 'Payment Entry' and is_cancelled = 0
          and voucher_no in %(names)s and coalesce(cost_center, '') = ''
        """,
        {"cc": S1_COST_CENTER, "names": TARGET_PAYMENT_ENTRIES},
    )

    frappe.db.commit()
    after = _preview()
    return {
        "dry_run": 0,
        "backup": "20260912_233859-szl-database.sql.gz",
        "before": before,
        "after": after,
    }
