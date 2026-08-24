import frappe

from aimatic.relationship_manager.flow import SUPPORTED_DOCTYPES, build_relationship_tree


@frappe.whitelist()
def get_relationship_tree(doctype: str, name: str):
	"""Return the SAP-style document flow tree for a purchase/sales document."""
	doctype = (doctype or "").strip()
	name = (name or "").strip()
	if not doctype or not name:
		frappe.throw("doctype and name are required")
	if doctype not in SUPPORTED_DOCTYPES:
		frappe.throw(f"Relationship Manager does not support {doctype}")
	if not frappe.db.exists(doctype, name):
		frappe.throw(f"{doctype} {name} not found")

	return build_relationship_tree(doctype, name)
