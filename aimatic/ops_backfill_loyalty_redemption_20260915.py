"""One-off backfill: close the loyalty-redemption receivable gap left on
consolidated Sales Invoices, and debit the Loyalty Point Entry ledger for
every POS Invoice redemption that never got one.

Root cause and evidence: tasks/szl-loyalty-cash-redemption-outstanding-20260913/
findings.md. The two corrections applied here are exactly
aimatic.loyalty.events.on_submit_debit_redeemed_loyalty_points and
on_submit_close_consolidated_loyalty_gap -- the forward-fix hooks -- run
retroactively against already-submitted documents. Each correction guards on
its own existence check, so this is safe to re-run; it only touches invoices
the forward-fix (once deployed) hasn't already covered.

Dry run first:
    bench --site szl execute aimatic.ops_backfill_loyalty_redemption_20260915.run --kwargs '{"dry_run": true}'

Then apply:
    bench --site szl execute aimatic.ops_backfill_loyalty_redemption_20260915.run --kwargs '{"dry_run": false}'
"""

import frappe
from frappe.utils import flt

from aimatic.loyalty.events import (
	_debit_redeemed_loyalty_points,
	on_submit_close_consolidated_loyalty_gap,
)


def run(dry_run=True):
	dry_run = bool(dry_run)

	pos_invoices = frappe.get_all(
		"POS Invoice",
		filters={"docstatus": 1, "redeem_loyalty_points": 1, "is_return": 0},
		pluck="name",
	)
	sales_invoices = frappe.get_all(
		"Sales Invoice",
		filters={
			"docstatus": 1,
			"is_consolidated": 1,
			"redeem_loyalty_points": 1,
			"is_return": 0,
		},
		pluck="name",
	)

	debited, already_debited = [], []
	for name in pos_invoices:
		marker = f"Redemption debit for POS Invoice {name}"
		if frappe.db.exists("Loyalty Point Entry", {"discretionary_reason": marker}):
			already_debited.append(name)
			continue
		if not dry_run:
			doc = frappe.get_doc("POS Invoice", name)
			_debit_redeemed_loyalty_points(doc)
		debited.append(name)

	closed, no_gap = [], []
	for name in sales_invoices:
		doc = frappe.get_doc("Sales Invoice", name)
		gap = round(flt(doc.outstanding_amount), 2)
		if gap <= 0.5:
			no_gap.append(name)
			continue
		if not dry_run:
			on_submit_close_consolidated_loyalty_gap(doc)
			frappe.db.commit()
		closed.append({"invoice": name, "gap": gap})

	return {
		"dry_run": dry_run,
		"pos_invoices_checked": len(pos_invoices),
		"loyalty_debits_applied_or_pending": debited,
		"loyalty_debits_already_present": len(already_debited),
		"sales_invoices_checked": len(sales_invoices),
		"gaps_closed_or_pending": closed,
		"invoices_with_no_gap": len(no_gap),
	}
