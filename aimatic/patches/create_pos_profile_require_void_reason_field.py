import frappe


def execute():
	"""Add POS Profile.custom_require_void_reason (Check, default 0).

	Retail tills skip the reason step (it slows the cashier). Other businesses can
	turn it on per profile: the POS then asks for a reason on every removed line,
	Clear Cart and held-sale delete, and sends it with the void to POS Void Log.
	"""
	if not frappe.db.exists("Custom Field", {"dt": "POS Profile", "fieldname": "custom_require_void_reason"}):
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "POS Profile",
				"fieldname": "custom_require_void_reason",
				"label": "Require Void Reason",
				"fieldtype": "Check",
				"default": "0",
				"insert_after": "custom_allow_held_sales",
				"description": (
					"When checked, the POS asks the cashier for a reason before removing a "
					"cart line, clearing the cart or deleting a held sale, and records it in "
					"POS Void Log. Unchecked by default: retail tills void without the extra step."
				),
			}
		).insert(ignore_permissions=True)

	frappe.db.commit()
