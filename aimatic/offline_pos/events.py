"""POS Invoice lifecycle hooks owned by offline_pos."""

from __future__ import annotations

import frappe


def restore_food_panda_credit_payment_marker(doc, method=None):
	"""Re-insert the zero-amount Food Panda Credit row after ERPNext clears it.

	``POS Invoice.on_submit`` calls ``clear_unallocated_mode_of_payments``, which
	deletes every payment child with ``amount == 0``. Food Panda credit sales
	intentionally use a zero-amount marker so the receivable stays outstanding
	for a later bank Payment Entry, while still giving consolidation a payment
	mode row. Without this restore, shift close builds a consolidated Sales
	Invoice with an empty payments table and throws
	"At least one mode of payment is required for POS invoice."
	"""
	from aimatic.offline_pos.api import (
		_get_food_panda_credit_mode,
		_is_food_panda_credit_sale,
	)

	if doc.doctype != "POS Invoice" or doc.docstatus != 1:
		return
	if not doc.pos_profile or not doc.customer:
		return
	pos = frappe.get_cached_doc("POS Profile", doc.pos_profile)
	if not _is_food_panda_credit_sale(pos, doc.customer):
		return
	credit_mode = _get_food_panda_credit_mode(pos)
	if not credit_mode:
		return

	if frappe.db.exists(
		"Sales Invoice Payment",
		{
			"parent": doc.name,
			"parenttype": "POS Invoice",
			"mode_of_payment": credit_mode,
		},
	):
		return

	# Do not invent a credit marker on top of real tender rows.
	if frappe.db.sql(
		"""
		select 1 from `tabSales Invoice Payment`
		where parent=%s and parenttype='POS Invoice' and ifnull(amount, 0) != 0
		limit 1
		""",
		(doc.name,),
	):
		return

	account = frappe.db.get_value(
		"Mode of Payment Account",
		{"parent": credit_mode, "company": doc.company},
		"default_account",
	)
	mop_type = frappe.db.get_value("Mode of Payment", credit_mode, "type") or "General"
	row = doc.append(
		"payments",
		{
			"mode_of_payment": credit_mode,
			"amount": 0,
			"base_amount": 0,
			"account": account,
			"type": mop_type,
		},
	)
	row.db_insert()
