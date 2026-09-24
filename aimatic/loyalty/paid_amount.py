from frappe.utils import cint, flt


def include_loyalty_in_paid_amount(doc, method=None):
	"""Registered as a POS Invoice before_save doc_event. Kept in its own module
	so a hook-cache refresh can never point live workers at a function that
	their already-imported events module lacks.

	Core's calculate_paid_amount counts a redeemed-points discount as paid
	(payments + loyalty_amount), but core's before_save -> set_paid_amount then
	overwrites paid_amount with the payment rows alone. Submit
	(docstatus 1) does not recalculate totals, so validate_full_payment sees that
	stored figure and rejects a correctly discounted sale with 417 "Partial
	Payment in POS Transactions are not allowed". Restore core's own convention.
	"""
	if cint(getattr(doc, "is_return", 0)) or not cint(getattr(doc, "redeem_loyalty_points", 0)):
		return
	loyalty_amount = flt(getattr(doc, "loyalty_amount", 0) or 0, 2)
	if loyalty_amount <= 0:
		return
	tendered = sum(flt(p.amount) for p in (doc.get("payments") or []))
	doc.paid_amount = flt(tendered + loyalty_amount, 2)
	doc.base_paid_amount = flt(doc.paid_amount * flt(doc.get("conversion_rate") or 1), 2)
