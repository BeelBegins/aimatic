"""Expose existing per-account remarks in Journal Entry forms and standard prints."""

import frappe

REMARK_PROPERTIES = (
	("label", "Remarks", "Data"),
	("hidden", "0", "Check"),
	("in_list_view", "1", "Check"),
	("columns", "2", "Int"),
	("print_hide", "0", "Check"),
	("print_width", "180px", "Data"),
)


def setup_journal_entry_remarks():
	if not frappe.db.exists("DocType", "Journal Entry Account"):
		return

	for property_name, value, property_type in REMARK_PROPERTIES:
		filters = {
			"doc_type": "Journal Entry Account",
			"field_name": "user_remark",
			"property": property_name,
		}
		existing = frappe.db.get_value("Property Setter", filters, "value")
		if existing is not None and str(existing) == value:
			continue
		frappe.make_property_setter(
			{
				"doctype": "Journal Entry Account",
				"fieldname": "user_remark",
				"property": property_name,
				"value": value,
				"property_type": property_type,
			},
			is_system_generated=False,
			module="Aimatic",
		)
