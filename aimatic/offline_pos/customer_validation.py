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


def set_default_price_list_if_missing(doc):
	try:
		if not getattr(doc, "default_price_list", None):
			selling = frappe.get_single("Selling Settings")
			val = getattr(selling, "selling_price_list", None)
			if val:
				doc.default_price_list = val
	except Exception:
		pass


def validate_customer(doc, method=None):
	mobile = getattr(doc, "mobile_no", None)

	if not mobile:
		set_default_price_list_if_missing(doc)
		return

	normalized = normalize_pak_mobile(mobile)

	if not normalized:
		set_default_price_list_if_missing(doc)
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

	set_default_price_list_if_missing(doc)
