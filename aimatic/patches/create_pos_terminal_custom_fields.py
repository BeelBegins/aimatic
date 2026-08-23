import frappe

# NOTE: POS Invoice.custom_terminal_id below is populated from the client's (now server-derived,
# read-only) logical terminal_id — a label that may be shared across every physical terminal
# assigned to the same POS Profile. Its "Hardware terminal identifier" description predates that;
# the real per-machine identifier is POS Invoice.custom_hardware_id, added by
# create_pos_profile_terminal_id_field.py.


def execute():
	custom_fields = [
		{
			"dt": "POS Invoice",
			"fieldname": "custom_terminal_invoice_id",
			"label": "Terminal Invoice ID",
			"fieldtype": "Data",
			"unique": 1,
			"no_copy": 1,
			"in_list_view": 0,
			"bold": 0,
			"insert_after": "custom_fbr_usin",
			"description": "Unique ID assigned by the POS terminal. Used for idempotent submission.",
		},
		{
			"dt": "POS Invoice",
			"fieldname": "custom_terminal_id",
			"label": "Terminal ID",
			"fieldtype": "Data",
			"unique": 0,
			"no_copy": 1,
			"in_list_view": 0,
			"bold": 0,
			"insert_after": "custom_terminal_invoice_id",
			"description": "Hardware terminal identifier.",
		},
	]

	for field in custom_fields:
		if not frappe.db.exists("Custom Field", {"dt": field["dt"], "fieldname": field["fieldname"]}):
			cf = frappe.get_doc({"doctype": "Custom Field", **field})
			cf.insert(ignore_permissions=True)

	frappe.db.commit()
