import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""Add only the per-branch demand window; existing branches keep the 28-day behaviour."""
	from aimatic.retail_operations.setup import DEMAND_WINDOW_FIELD

	create_custom_fields({"Branch": [DEMAND_WINDOW_FIELD]}, update=True)
	frappe.db.sql(
		"""UPDATE `tabBranch` SET custom_demand_window_days=28
		WHERE IFNULL(custom_demand_window_days, 0)=0"""
	)
	frappe.clear_cache(doctype="Branch")
