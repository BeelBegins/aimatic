import frappe


def execute():
	if frappe.db.exists("Custom Field", {"dt": "Sales Order", "fieldname": "custom_mobile_sales_notes"}):
		return
	frappe.get_doc(
		{
			"doctype": "Custom Field",
			"dt": "Sales Order",
			"fieldname": "custom_mobile_sales_notes",
			"label": "Mobile Sales Notes",
			"fieldtype": "Small Text",
			"insert_after": "po_date",
			"no_copy": 0,
			"description": "Delivery or sales notes captured by the Ai Matic Sales app.",
		}
	).insert(ignore_permissions=True)
