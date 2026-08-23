"""One-off data correction for szl POS-CLO-2026-00005 (S1 Food Panda).

Context: aimatic.fbr_pos.accounting.adjust_cash_payment_to_grand_total had a bug
(fixed 2026-08-03) that force-filled the Food Panda Credit marker payment row to
the invoice's full grand_total instead of leaving it at 0, before the code fix
existed. This corrupted the 37 POS Invoices already submitted in this window --
their Food Panda Credit payment row shows the full amount instead of 0, and
paid_amount/outstanding_amount/status were bumped to look "Paid" instead of
fully outstanding. This script corrects those specific submitted documents back
to the intended shape (payment row amount 0, outstanding_amount = grand_total,
status recomputed) so POS-CLO-2026-00005 can be retried and consolidated.

Run on szl only, via `bench --site szl console` (paste the body) or
`bench --site szl execute <path>.run` after adjusting for your invocation style.
Read-only verification is printed before and after; no other invoices are
touched (only S1 Food Panda profile, only within this specific closing's
posting-date window, only rows currently mismatched).
"""

import frappe
from frappe.utils import flt

TARGET_SITE = "szl"
CLOSING_ENTRY = "POS-CLO-2026-00005"
POS_PROFILE = "S1 Food Panda"
MODE_OF_PAYMENT = "Food Panda Credit"


def run():
	if frappe.local.site != TARGET_SITE:
		frappe.throw(
			f"This script is locked to site '{TARGET_SITE}', but current site is '{frappe.local.site}'."
		)

	ce = frappe.get_doc("POS Closing Entry", CLOSING_ENTRY)
	invs = frappe.get_all(
		"POS Invoice",
		filters={
			"pos_profile": POS_PROFILE,
			"docstatus": 1,
			"posting_date": ["between", [ce.period_start_date.date(), ce.period_end_date.date()]],
		},
		fields=["name", "grand_total"],
		order_by="creation asc",
	)
	print(f"Found {len(invs)} POS Invoices in window")

	fixed = 0
	skipped = 0
	for inv in invs:
		pay_rows = frappe.get_all(
			"Sales Invoice Payment",
			filters={"parent": inv.name, "parenttype": "POS Invoice", "mode_of_payment": MODE_OF_PAYMENT},
			fields=["name", "amount"],
		)
		if not pay_rows:
			print("SKIP (no Food Panda Credit row):", inv.name)
			skipped += 1
			continue

		row = pay_rows[0]
		if flt(row.amount) == 0:
			print("already correct (amount=0), skipping:", inv.name)
			skipped += 1
			continue

		frappe.db.set_value(
			"Sales Invoice Payment", row.name, {"amount": 0, "base_amount": 0}, update_modified=False
		)
		frappe.db.set_value(
			"POS Invoice",
			inv.name,
			{"paid_amount": 0, "base_paid_amount": 0, "outstanding_amount": flt(inv.grand_total, 2)},
			update_modified=False,
		)
		doc = frappe.get_doc("POS Invoice", inv.name)
		doc.set_status(update=True)
		fixed += 1

	frappe.db.commit()
	print(f"Fixed: {fixed}, Skipped: {skipped}")

	check = frappe.get_all(
		"POS Invoice",
		filters={"name": ["in", [i.name for i in invs]]},
		fields=["name", "paid_amount", "outstanding_amount", "status"],
		order_by="creation asc",
	)
	for c in check:
		print(c)

	print(
		"\nNext step: open POS Closing Entry POS-CLO-2026-00005 in Desk and click "
		"'Retry' (or run frappe.get_doc('POS Closing Entry', 'POS-CLO-2026-00005').retry() "
		"in console)."
	)


run()
