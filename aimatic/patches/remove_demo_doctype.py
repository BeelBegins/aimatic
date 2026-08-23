import frappe


def execute():
	if not frappe.db.exists("DocType", "Demo"):
		return

	if not frappe.db.get_value("DocType", "Demo", "custom"):
		frappe.throw("Refusing to remove non-custom DocType Demo")

	frappe.delete_doc("DocType", "Demo", force=True, ignore_permissions=True)
