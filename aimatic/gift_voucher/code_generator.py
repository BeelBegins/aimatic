import secrets

import frappe
from frappe import _

# Excludes 0/O and 1/I to avoid ambiguity when a customer reads the code aloud
# or a cashier types it in at the register.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_LENGTH = 10
_MAX_ATTEMPTS = 5


def generate_voucher_code():
	for _attempt in range(_MAX_ATTEMPTS):
		code = "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))
		if not frappe.db.exists("Gift Voucher", {"voucher_code": code}):
			return code

	frappe.throw(_("Could not generate a unique gift voucher code, please retry"))
