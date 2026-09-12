"""One-off: fix a scoping bug in ops_backfill_cost_center_20260912.

That script's cash-Payment-Entry GL backfill restricted the UPDATE to
`gle.account in (cash accounts)`, so it only stamped the cash-account leg of
each two-line Payment Entry GL posting and missed the counter-party leg
(e.g. `2110 - Creditors - SSM`), leaving 5 GL Entry rows blank
(ACC-PAY-2026-00001/-00037/-00063/-00155/-00221, all `Cash - S1GT`).

General, not just those 5: copies each Payment Entry's own (already-set,
already-verified) cost_center onto ANY of its still-blank GL Entry rows,
regardless of account. Safe re-run: guarded on the blank case, uses a value
already committed to the parent doc, touches no financial amount.

Run:
  bench --site szl execute aimatic.ops_backfill_pe_other_leg_cost_center_20260912.run --kwargs "{'dry_run': 1}"
  bench --site szl execute aimatic.ops_backfill_pe_other_leg_cost_center_20260912.run

Rollback: restore DB backup 20260912_233859-szl-database.sql.gz (taken
immediately before tonight's other post-9pm szl work).
"""

from __future__ import annotations

import frappe


def _count(sql, values=None):
    return int(frappe.db.sql(sql, values or {})[0][0])


def _preview():
    return {
        "gl_entry": _count(
            """
            select count(*) from `tabGL Entry` gle
            inner join `tabPayment Entry` pe on pe.name = gle.voucher_no
            where gle.voucher_type = 'Payment Entry' and gle.is_cancelled = 0
              and coalesce(gle.cost_center, '') = ''
              and coalesce(pe.cost_center, '') != ''
            """
        ),
    }


def run(dry_run=0):
    dry_run = int(dry_run or 0)
    before = _preview()
    if dry_run:
        return {"dry_run": 1, "before": before}

    frappe.db.sql(
        """
        update `tabGL Entry` gle
        inner join `tabPayment Entry` pe on pe.name = gle.voucher_no
        set gle.cost_center = pe.cost_center
        where gle.voucher_type = 'Payment Entry' and gle.is_cancelled = 0
          and coalesce(gle.cost_center, '') = ''
          and coalesce(pe.cost_center, '') != ''
        """
    )

    frappe.db.commit()
    after = _preview()
    return {
        "dry_run": 0,
        "backup": "20260912_233859-szl-database.sql.gz",
        "before": before,
        "after": after,
    }
