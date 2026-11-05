import frappe


def execute():
	"""Add the per-branch demand window field; existing branches keep the 28-day behaviour."""
	from aimatic.retail_operations.setup import install

	install()
	if frappe.db.has_column("Branch", "custom_demand_window_days"):
		frappe.db.sql(
			"""UPDATE `tabBranch` SET custom_demand_window_days=28
			WHERE IFNULL(custom_demand_window_days, 0)=0"""
		)
