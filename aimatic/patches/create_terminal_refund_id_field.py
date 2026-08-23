import frappe


def execute():
	"""Create the Terminal Refund ID custom field on POS Invoice.

	Used only on return POS Invoices to make refund submission idempotent
	(one return per terminal_refund_id). Mirrors the terminal_invoice_id field
	created in create_pos_terminal_custom_fields.
	"""
	custom_fields = [
		{
			"dt": "POS Invoice",
			"fieldname": "custom_terminal_refund_id",
			"label": "Terminal Refund ID",
			"fieldtype": "Data",
			"unique": 1,
			"read_only": 1,
			"no_copy": 1,
			"in_list_view": 0,
			"bold": 0,
			"insert_after": "custom_terminal_id",
			"description": "Unique ID assigned by the POS terminal for a refund. Used for idempotent return submission.",
		},
	]

	for field in custom_fields:
		if not frappe.db.exists("Custom Field", {"dt": field["dt"], "fieldname": field["fieldname"]}):
			cf = frappe.get_doc({"doctype": "Custom Field", **field})
			cf.insert(ignore_permissions=True)

	frappe.db.commit()
