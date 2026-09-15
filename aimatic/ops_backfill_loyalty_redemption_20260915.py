"""One-off backfill: close the loyalty-redemption receivable gap left on
consolidated Sales Invoices, and debit the Loyalty Point Entry ledger for
every POS Invoice redemption that was actually honored (real cash/bank
collected less, not just claimed) but never got one.

Root cause and evidence: tasks/szl-loyalty-cash-redemption-outstanding-20260913/
findings.md. The two corrections applied here are exactly
aimatic.loyalty.events.on_submit_debit_redeemed_loyalty_points and
on_submit_close_consolidated_loyalty_gap -- the forward-fix hooks -- run
retroactively against already-submitted documents. Each correction guards on
its own existence check, so this is safe to re-run; it only touches invoices
the forward-fix (once deployed) hasn't already covered.

2026-09-15 correction: the first run of this script debited all 43 redemption
invoices unconditionally and was wrong to. Checked against every one of them:
0 of 43 ever actually collected less cash/bank for the claimed points -- the
discount was never applied at the register (see
_redemption_was_actually_honored's docstring). That took real point value
from all 43 customers for nothing; the 43 wrongful debits were deleted the
same day. _debit_redeemed_loyalty_points now only creates a debit when the
arithmetic proves the discount was genuinely taken off what was collected, so
re-running this script is safe -- it will not repeat that mistake.

2026-09-15, later same day: on_submit_close_consolidated_loyalty_gap now
splits into two outcomes instead of one. All 43 known invoices are unhonored
(no gap change here), but once the register-side fix lands and a real
redemption happens, that invoice's own outstanding_amount reads ~0 (core's
paid_amount/change_amount arithmetic cancels for an honored redemption) even
though Debtors still carries a genuine, uncleared loyalty_amount -- the gap
check alone would silently skip it forever. This script no longer
pre-filters on outstanding_amount before deciding whether there's anything to
do; it checks _redemption_was_actually_honored first and lets the hook itself
route to the correct GL treatment (Debtors credit / 5246 - Loyalty Points
Redemption Expense debit for honored, write-off for not).

Dry run first:
    bench --site szl execute aimatic.ops_backfill_loyalty_redemption_20260915.run --kwargs '{"dry_run": true}'

Then apply:
    bench --site szl execute aimatic.ops_backfill_loyalty_redemption_20260915.run --kwargs '{"dry_run": false}'
"""

import frappe
from frappe.utils import flt

from aimatic.loyalty.events import (
	_debit_redeemed_loyalty_points,
	_redemption_was_actually_honored,
	_sales_invoice_gl_already_closed,
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

	debited, already_debited, not_honored = [], [], []
	for name in pos_invoices:
		marker = f"Redemption debit for POS Invoice {name}"
		if frappe.db.exists("Loyalty Point Entry", {"discretionary_reason": marker}):
			already_debited.append(name)
			continue
		if dry_run:
			doc = frappe.get_doc("POS Invoice", name)
			(debited if _redemption_was_actually_honored(doc) else not_honored).append(name)
			continue
		doc = frappe.get_doc("POS Invoice", name)
		_debit_redeemed_loyalty_points(doc)
		if frappe.db.exists("Loyalty Point Entry", {"discretionary_reason": marker}):
			debited.append(name)
		else:
			not_honored.append(name)

	honored_gle, write_offs, no_action = [], [], []
	for name in sales_invoices:
		doc = frappe.get_doc("Sales Invoice", name)
		if _sales_invoice_gl_already_closed(name):
			no_action.append(name)
			continue
		honored = _redemption_was_actually_honored(doc)
		gap = round(flt(doc.outstanding_amount), 2)
		if not honored and gap <= 0.5:
			no_action.append(name)
			continue
		if not dry_run:
			on_submit_close_consolidated_loyalty_gap(doc)
			frappe.db.commit()
		if honored:
			honored_gle.append({"invoice": name, "loyalty_amount": round(flt(doc.loyalty_amount), 2)})
		else:
			write_offs.append({"invoice": name, "gap": gap})

	return {
		"dry_run": dry_run,
		"pos_invoices_checked": len(pos_invoices),
		"loyalty_debits_applied_or_pending": debited,
		"loyalty_debits_already_present": len(already_debited),
		"redemption_not_actually_honored_no_debit": len(not_honored),
		"sales_invoices_checked": len(sales_invoices),
		"honored_redemption_gle_applied_or_pending": honored_gle,
		"write_offs_applied_or_pending": write_offs,
		"invoices_with_no_action": len(no_action),
	}
