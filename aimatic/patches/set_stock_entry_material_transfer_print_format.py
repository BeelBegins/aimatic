"""Make the owned compact Material Transfer layout the Stock Entry default."""

import frappe

PRINT_FORMAT = "Stock Entry Material Transfer"
PROPERTY_SETTER = "Stock Entry-main-default_print_format"


def execute():
	if not frappe.db.exists("Print Format", PRINT_FORMAT):
		return
	if frappe.db.exists("Property Setter", PROPERTY_SETTER):
		frappe.db.set_value("Property Setter", PROPERTY_SETTER, "value", PRINT_FORMAT)
	else:
		frappe.get_doc({"doctype": "Property Setter", "doctype_or_field": "DocType", "doc_type": "Stock Entry", "property": "default_print_format", "property_type": "Data", "value": PRINT_FORMAT, "name": PROPERTY_SETTER}).insert(ignore_permissions=True)
	frappe.clear_cache(doctype="Stock Entry")
