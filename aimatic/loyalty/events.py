import frappe
from frappe.utils import add_days, cint, flt, getdate

from aimatic.loyalty.item_group_rate import get_item_group_loyalty_rate
from aimatic.pos_shared import returned_qty_by_row


def _compute_item_group_points(invoice, returned_qty=None):
	"""Item-group-weighted points for an invoice's remaining (non-returned) rows."""
	returned_qty = returned_qty or {}
	total_points = 0.0
	eligible_amount = 0.0

	for row in invoice.items:
		sold_qty = abs(flt(row.qty))
		if sold_qty <= 0:
			continue

		already_returned = flt(returned_qty.get(row.name, 0))
		remaining_ratio = max(0.0, (sold_qty - already_returned) / sold_qty)
		if remaining_ratio <= 0:
			continue

		row_eligible_amount = flt(row.amount, 2) * remaining_ratio
		eligible_amount += row_eligible_amount

		rate = get_item_group_loyalty_rate(row.item_group)
		if rate:
			total_points += row_eligible_amount * rate / 100.0

	return cint(total_points), flt(eligible_amount, 2)


def _correct_loyalty_point_entry(invoice, returned_qty=None):
	"""Overwrite the Loyalty Point Entry core's own on_submit already created/
	recalculated, replacing its flat collection_factor points with the
	item-group-weighted total. Loyalty Point Entry is not submittable, so this
	is a plain in-place update -- no cancel/amend needed.
	"""
	if not getattr(invoice, "loyalty_program", None):
		return

	entry_name = frappe.db.get_value(
		"Loyalty Point Entry",
		{"invoice_type": "POS Invoice", "invoice": invoice.name},
		"name",
	)
	if not entry_name:
		return

	points, eligible_amount = _compute_item_group_points(invoice, returned_qty)

	from erpnext.accounts.doctype.loyalty_program.loyalty_program import (
		get_loyalty_program_details_with_points,
	)

	lp_details = get_loyalty_program_details_with_points(
		invoice.customer,
		company=invoice.company,
		current_transaction_amount=eligible_amount,
		loyalty_program=invoice.loyalty_program,
		expiry_date=invoice.posting_date,
		include_expired_entry=True,
	)

	# Mirrors core's own eligibility guard in Sales Invoice.make_loyalty_point_entry.
	if not (
		lp_details
		and getdate(lp_details.from_date) <= getdate(invoice.posting_date)
		and (not lp_details.to_date or getdate(lp_details.to_date) >= getdate(invoice.posting_date))
	):
		return

	frappe.db.set_value(
		"Loyalty Point Entry",
		entry_name,
		{
			"loyalty_points": points,
			"purchase_amount": eligible_amount,
			"expiry_date": add_days(invoice.posting_date, lp_details.expiry_duration),
		},
	)


def on_submit_correct_loyalty_points(doc, method=None):
	"""Registered as a POS Invoice on_submit doc_event -- runs strictly after
	core's own on_submit (which creates/recalculates the Loyalty Point Entry).

	On a return, core recalculates the entry belonging to the ORIGINAL invoice
	(return_against), not the return itself, so we correct that one instead,
	recomputing points from the original's rows minus whatever has been
	returned so far (including this return, since it's already docstatus=1 by
	the time this hook runs).
	"""
	if cint(getattr(doc, "is_return", 0)):
		if not doc.return_against or not doc.loyalty_program:
			return
		original = frappe.get_doc("POS Invoice", doc.return_against)
		_correct_loyalty_point_entry(original, returned_qty_by_row(original.name))
	else:
		_correct_loyalty_point_entry(doc)


def _redemption_was_actually_honored(doc):
	"""True only if the discount a redeem-points claim implies was really
	collected less cash/bank, not just recorded on the invoice.

	get_pos_invoice_preview already computes and returns the correctly
	discounted `amount_due` to the terminal for display, so the backend side
	of this has never been wrong. But checked against every one of the 43
	redemption POS Invoices on szl (2026-09-15): every single one collected
	the FULL grand_total regardless -- payment.amount sums to grand_total (or
	more, for ordinary round-note change unrelated to loyalty, itself computed
	against grand_total rather than the discounted payable, e.g. `change_amount
	= tendered - grand_total` with no loyalty_amount in that subtraction at
	all). Zero of them ever reduced what the customer actually paid. That
	looks like a terminal/cashier-workflow gap (redemption is claimed but the
	discounted amount_due is never what gets collected), not something fixable
	from this backend -- see the offline_pos / Electron client, out of scope
	here.

	Debiting a customer's balance for a claim that was never honored took real
	point value from every one of those 43 customers for nothing (confirmed
	and reversed 2026-09-15). Only debit when the arithmetic proves the
	discount was genuinely taken off what was collected, so this can't repeat.
	"""
	# ERPNext's paid_amount already includes loyalty_amount, so measure what was
	# really tendered from the payment rows (POS Invoice and consolidated Sales
	# Invoice both carry them), less change handed back.
	tendered = sum(flt(p.amount) for p in (doc.get("payments") or []))
	real_kept = flt(tendered - flt(doc.change_amount, 2), 2)
	discounted = flt(flt(doc.grand_total, 2) - flt(doc.loyalty_amount, 2), 2)
	return abs(real_kept - discounted) <= 0.5


def _debit_redeemed_loyalty_points(doc):
	"""Debit the customer's balance for points genuinely redeemed on this
	POS Invoice -- see _redemption_was_actually_honored for why "claimed"
	and "genuinely redeemed" are not the same thing here.

	Core never creates a redemption-side Loyalty Point Entry even for an
	honored redemption -- it only validates the redemption
	(loyalty_program.validate_loyalty_points) and folds loyalty_amount into
	paid_amount/change_amount arithmetic. get_loyalty_details sums every row
	for the customer with no docstatus filter and nothing else ever offsets a
	redemption, so without this an honored redemption's points would stay
	redeemable indefinitely.

	`invoice_type` is mandatory on this doctype. Leaves it as `"Journal
	Entry"` with `invoice` blank -- the same combination this bench's own
	2026-08-02 loyalty-opening migration already used for a manual/correction
	entry with no real originating document -- rather than `"POS Invoice"` +
	doc.name, which would collide with _correct_loyalty_point_entry's exact
	`{"invoice_type": "POS Invoice", "invoice": invoice.name}` lookup: with
	two rows matching, frappe.db.get_value could return this one and have its
	negative points silently overwritten by the earning recompute.
	`redeem_against` is a Link to another Loyalty Point Entry (which earning
	entry's points are being consumed) -- not usable here -- so
	`discretionary_reason` carries the marker this function checks for
	idempotency instead.
	"""
	if not (cint(doc.redeem_loyalty_points) and doc.loyalty_points and doc.loyalty_program):
		return
	if not _redemption_was_actually_honored(doc):
		return

	marker = f"Redemption debit for {doc.doctype} {doc.name}"
	if frappe.db.exists("Loyalty Point Entry", {"discretionary_reason": marker}):
		return

	frappe.get_doc(
		{
			"doctype": "Loyalty Point Entry",
			"company": doc.company,
			"loyalty_program": doc.loyalty_program,
			"customer": doc.customer,
			"invoice_type": "Journal Entry",
			"discretionary_reason": marker,
			"loyalty_points": -cint(doc.loyalty_points),
			"purchase_amount": 0,
			"posting_date": doc.posting_date,
			"expiry_date": add_days(doc.posting_date, 365),
		}
	).insert(ignore_permissions=True)


def on_submit_debit_redeemed_loyalty_points(doc, method=None):
	"""Registered as a POS Invoice on_submit doc_event."""
	if cint(getattr(doc, "is_return", 0)):
		return
	_debit_redeemed_loyalty_points(doc)


def _sales_invoice_gl_already_closed(sales_invoice_name):
	"""True if a submitted Journal Entry already references this Sales
	Invoice -- either correction JE on_submit_close_consolidated_loyalty_gap
	can create (honored-redemption GLE or unhonored write-off) checks this
	first, so re-running it or the one-off backfill never double-posts.
	"""
	return bool(
		frappe.db.sql(
			"""
			SELECT jea.name
			FROM `tabJournal Entry Account` jea
			JOIN `tabJournal Entry` je ON je.name = jea.parent
			WHERE jea.reference_type = 'Sales Invoice'
			  AND jea.reference_name = %s
			  AND je.docstatus = 1
			""",
			sales_invoice_name,
		)
	)


def _post_honored_redemption_gle(doc):
	"""Post the Debtors-credit / loyalty-expense-debit entry ERPNext's own
	make_loyalty_point_redemption_gle would have posted for this invoice, had
	it not unconditionally skipped `is_consolidated`. Only ever called after
	_redemption_was_actually_honored confirms a real discount was collected
	-- this is a genuine loyalty-program cost, not a write-off.
	"""
	amount = flt(doc.loyalty_amount, 2)
	if amount <= 0:
		return

	expense_account = frappe.get_cached_value(
		"Loyalty Program", doc.loyalty_program, "expense_account"
	)
	if not expense_account:
		frappe.log_error(
			title="Loyalty redemption GL could not be posted",
			message=(
				f"Sales Invoice {doc.name}: a genuinely honored loyalty "
				f"redemption of {amount} has no GL entry because Loyalty "
				f"Program {doc.loyalty_program} has no expense_account "
				"configured. Close manually."
			),
		)
		return

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.posting_date = doc.posting_date
	je.company = doc.company
	je.user_remark = (
		f"Loyalty Points redeemed by the customer on {doc.name} -- posted "
		"here because ERPNext's own make_loyalty_point_redemption_gle "
		"unconditionally skips is_consolidated invoices."
	)
	je.append(
		"accounts",
		{
			"account": expense_account,
			"cost_center": doc.cost_center,
			"branch": doc.branch,
			"debit_in_account_currency": amount,
		},
	)
	je.append(
		"accounts",
		{
			"account": doc.debit_to,
			"party_type": "Customer",
			"party": doc.customer,
			"cost_center": doc.cost_center,
			"branch": doc.branch,
			"credit_in_account_currency": amount,
			"reference_type": "Sales Invoice",
			"reference_name": doc.name,
		},
	)
	je.insert(ignore_permissions=True)
	je.submit()


def on_submit_close_consolidated_loyalty_gap(doc, method=None):
	"""Registered as a Sales Invoice on_submit doc_event.

	ERPNext's Sales Invoice.make_loyalty_point_redemption_gle -- the GL entry
	that credits Debtors for a redeemed-points discount -- is unconditionally
	skipped `if ... not self.is_consolidated`, on the assumption the original
	POS Invoice already posted it. On this bench POS Invoice defers all GL to
	the consolidated Sales Invoice (POS Settings.post_change_gl_entries is
	off, verified: POS Invoice submission posts zero GL Entry rows), so that
	entry is never posted at all, for EITHER outcome below.

	Two different situations both land here, and need opposite treatment:

	- Honored (a real discount was actually collected,
	  _redemption_was_actually_honored): core's own paid_amount/change_amount
	  arithmetic happens to net doc.outstanding_amount to ~0 for this case,
	  which looks fully settled -- but the real GL entries only ever clear
	  Debtors by what was actually collected (grand_total - loyalty_amount),
	  leaving a genuine, hidden loyalty_amount sitting uncleared in the
	  Debtors control account that outstanding_amount never surfaces. This is
	  a real loyalty-program cost, not a write-off -- post the same
	  Debtors-credit / expense-debit entry core would have, against the
	  Loyalty Program's own expense_account.
	- Not honored (the customer paid full price and the "redemption" never
	  reduced anything): the invoice is left carrying a receivable equal to
	  the redeemed points that nobody is ever going to collect from a
	  customer who already paid in full. Close it the same way this business
	  already writes off a POS residual it won't chase -- a Journal Entry
	  against the invoice's own POS Profile write-off account.

	Either way, edit via a Journal Entry rather than an already-submitted
	document's stored totals.
	"""
	if not (
		cint(doc.is_consolidated)
		and cint(doc.redeem_loyalty_points)
		and flt(doc.loyalty_amount)
		and not cint(doc.is_return)
	):
		return

	if _sales_invoice_gl_already_closed(doc.name):
		return

	if _redemption_was_actually_honored(doc):
		_post_honored_redemption_gle(doc)
		return

	gap = flt(doc.outstanding_amount, 2)
	if gap <= 0.5:
		return

	write_off = (
		frappe.get_cached_value(
			"POS Profile",
			doc.pos_profile,
			["write_off_account", "write_off_cost_center"],
			as_dict=True,
		)
		if doc.pos_profile
		else None
	)
	if not write_off or not write_off.write_off_account:
		frappe.log_error(
			title="Loyalty redemption gap could not be closed",
			message=(
				f"Sales Invoice {doc.name}: consolidated loyalty redemption left "
				f"{gap} outstanding but POS Profile {doc.pos_profile} has no "
				"write_off_account configured. Close manually."
			),
		)
		return

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.posting_date = doc.posting_date
	je.company = doc.company
	je.user_remark = (
		f"Close loyalty-redemption receivable gap left by consolidated invoice "
		f"{doc.name} (ERPNext skips make_loyalty_point_redemption_gle when "
		"is_consolidated is set)."
	)
	je.append(
		"accounts",
		{
			"account": write_off.write_off_account,
			"cost_center": write_off.write_off_cost_center or doc.cost_center,
			"branch": doc.branch,
			"debit_in_account_currency": gap,
		},
	)
	je.append(
		"accounts",
		{
			"account": doc.debit_to,
			"party_type": "Customer",
			"party": doc.customer,
			"cost_center": doc.cost_center,
			"branch": doc.branch,
			"credit_in_account_currency": gap,
			"reference_type": "Sales Invoice",
			"reference_name": doc.name,
		},
	)
	je.insert(ignore_permissions=True)
	je.submit()
