"""Point PO default print at aimatic layout with barcode text (no images).

ERPNext's standard "Purchase Order with Item Image" reverts on sync, so do not
rely on editing it. Own format + Property Setter is the durable path.
"""

from __future__ import annotations

import frappe

STUB = (
	'{{ render_print_layout("purchase_order_with_item_image", doc, '
	"layout=layout, letter_head=letter_head, no_letterhead=no_letterhead, "
	"footer=footer, print_settings=print_settings, "
	"print_heading_template=print_heading_template) }}\n"
)

OWNED = "Purchase Order Updated Lyaout"
PS = "Purchase Order-main-default_print_format"


def execute():
	if frappe.db.exists("Print Format", OWNED):
		frappe.db.set_value(
			"Print Format",
			OWNED,
			{"html": STUB, "custom_format": 1, "print_format_type": "Jinja", "disabled": 0},
			update_modified=False,
		)
		frappe.db.set_value(
			"Print Format",
			OWNED,
			"modified",
			"2026-08-07 20:30:00",
			update_modified=False,
		)

	if frappe.db.exists("Property Setter", PS):
		frappe.db.set_value("Property Setter", PS, "value", OWNED)
	else:
		frappe.get_doc(
			{
				"doctype": "Property Setter",
				"doctype_or_field": "DocType",
				"doc_type": "Purchase Order",
				"property": "default_print_format",
				"property_type": "Data",
				"value": OWNED,
				"name": PS,
			}
		).insert(ignore_permissions=True)

	frappe.clear_cache()
