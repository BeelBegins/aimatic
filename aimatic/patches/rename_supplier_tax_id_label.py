import frappe


def execute():
	frappe.make_property_setter(
		{
			"doctype": "Supplier",
			"doctype_or_field": "DocField",
			"fieldname": "tax_id",
			"property": "label",
			"value": "Tax ID/NTN",
			"property_type": "Data",
		},
		is_system_generated=False,
	)
	frappe.make_property_setter(
		{
			"doctype": "Supplier",
			"doctype_or_field": "DocField",
			"fieldname": "tax_ntn",
			"property": "read_only",
			"value": "1",
			"property_type": "Check",
		},
		is_system_generated=False,
	)
	frappe.db.commit()
