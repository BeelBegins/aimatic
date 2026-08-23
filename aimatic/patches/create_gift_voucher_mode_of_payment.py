import frappe


def execute():
	"""Create the 'Gift Voucher' Mode of Payment master.

	Used internally only, appended server-side by
	aimatic.offline_pos.api.submit_online_sale when a gift voucher code is
	redeemed on a sale. Deliberately never added to any POS Profile's payment
	list, so it can't be selected/sent by the terminal directly.

	MANUAL FOLLOW-UP REQUIRED: a Mode of Payment Account (GL account) must
	still be configured for this mode per Company before a voucher-redeeming
	sale can be submitted, same as Cash/Card already require. This patch
	deliberately does not guess/create that GL account mapping.
	"""
	if frappe.db.exists("Mode of Payment", "Gift Voucher"):
		return

	frappe.get_doc(
		{
			"doctype": "Mode of Payment",
			"mode_of_payment": "Gift Voucher",
			"type": "General",
			"enabled": 1,
		}
	).insert(ignore_permissions=True)

	frappe.db.commit()
