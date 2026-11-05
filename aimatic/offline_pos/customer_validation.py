import re

import frappe
from frappe import _


def normalize_pak_mobile(value):
	if not value:
		return None
	s = re.sub(r"[\s\-\(\)\+]", "", str(value))
	digits = re.sub(r"[^0-9]", "", s)

	# Strip leading international dialling zeros (e.g. 0092...)
	if digits.startswith("00"):
		digits = digits[2:]

	# 0XXXXXXXXXX (11 digits) → +92XXXXXXXXXX
	if digits.startswith("0") and len(digits) == 11:
		return "+92" + digits[1:]

	# 92XXXXXXXXXX (12 digits, no +) → +92XXXXXXXXXX
	if digits.startswith("92") and len(digits) == 12:
		return "+" + digits

	# 3XXXXXXXXX (10 digits, local without leading 0) → +923XXXXXXXXX
	if digits.startswith("3") and len(digits) == 10:
		return "+92" + digits

	return None


def get_branch_price_lists():
	"""Every price list owned by a branch counter: each Branch's Selling and
	Foodpanda list (by naming convention and Branch.default_selling_price_list)
	plus every POS Profile's selling_price_list.

	Retail price is a property of the counter, not of the customer. ERPNext's
	POS Invoice.set_pos_fields picks Customer.default_price_list before the
	POS Profile list (on build and again on validate), so a customer holding a
	branch list is charged that branch's prices at every other branch.
	"""
	from aimatic.shelf_pricing.utils import (
		get_branch_foodpanda_price_list_name,
		get_branch_price_list_name,
	)

	lists = set()
	for branch in frappe.get_all("Branch", fields=["name", "default_selling_price_list"]):
		lists.add(get_branch_price_list_name(branch.name))
		lists.add(get_branch_foodpanda_price_list_name(branch.name))
		if branch.default_selling_price_list:
			lists.add(branch.default_selling_price_list)
	lists.update(
		pl for pl in frappe.get_all("POS Profile", pluck="selling_price_list") if pl
	)
	return lists


def validate_no_branch_price_list(doc):
	"""Reject a branch counter price list on a Customer.

	Only checked when the value is newly set (new Customer or changed field), so
	legacy records awaiting cleanup can still be saved for unrelated edits.
	"""
	price_list = getattr(doc, "default_price_list", None)
	if not price_list:
		return
	if not doc.is_new() and not doc.has_value_changed("default_price_list"):
		return
	if price_list in get_branch_price_lists():
		frappe.throw(
			_(
				"Price List {0} belongs to a branch counter. Leave the Customer's "
				"Default Price List blank: POS sales are priced from the selling "
				"counter's POS Profile."
			).format(price_list)
		)


def validate_customer(doc, method=None):
	mobile = getattr(doc, "mobile_no", None)

	validate_no_branch_price_list(doc)

	if not mobile:
		return

	normalized = normalize_pak_mobile(mobile)

	if not normalized:
		return

	previous = None if doc.is_new() else doc.get_doc_before_save()
	mobile_changed = not previous or previous.get("mobile_no") != normalized
	doc.mobile_no = normalized

	# Skip the duplicate check entirely when mobile_no hasn't actually changed
	# (e.g. saving a customer for an unrelated field) — no need to re-query.
	if mobile_changed:
		# Query directly for the normalized number rather than a LIKE scan.
		# Because validate_customer normalizes numbers on every save, all persisted
		# mobile_no values are already in +92XXXXXXXXXX form, so a direct equality
		# filter is sufficient. This relies on a DB index on Customer.mobile_no
		# (see patches.create_customer_mobile_no_index) — without it, this becomes
		# a full table scan on every Customer save as the table grows.
		existing_name = frappe.db.get_value(
			"Customer",
			{"mobile_no": normalized},
			"name",
		)

		if existing_name and existing_name != doc.name:
			frappe.throw(_("Mobile number already belongs to Customer: {0}").format(existing_name))
