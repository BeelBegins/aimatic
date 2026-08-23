import frappe


def execute():
	"""Add Warehouse.custom_branch (Link -> Branch) and backfill it from each
	Branch's own finished_goods_warehouse/rejected_warehouse.

	Warehouse has no field pointing back to its owning Branch today - Branch
	only points forward to its 2 warehouses. vendor_performance's optional
	Branch/Warehouse filters need the reverse lookup (given a Branch, find all
	its warehouses) without hardcoding "exactly 2" - see
	aimatic.vendor_performance.api._get_branch_warehouses. Named custom_branch
	(not bare branch) to match the mirror-field convention already used for
	User.custom_branch.
	"""
	if not frappe.db.exists("Custom Field", {"dt": "Warehouse", "fieldname": "custom_branch"}):
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "Warehouse",
				"fieldname": "custom_branch",
				"label": "Branch",
				"fieldtype": "Link",
				"options": "Branch",
				"insert_after": "company",
				"in_standard_filter": 1,
				"description": (
					"Branch this warehouse belongs to. Not auto-derived - set directly "
					"(backfilled from Branch.finished_goods_warehouse/rejected_warehouse) "
					"since a Branch may have more than these 2 warehouses in the future."
				),
			}
		).insert(ignore_permissions=True)

	# Idempotent backfill via direct db.set_value (no doc.save - avoid Warehouse's
	# own controller/tree side effects for a plain data stamp). Only fills blanks,
	# so re-running never clobbers a deliberate manual re-tag.
	branches = frappe.get_all("Branch", fields=["name", "finished_goods_warehouse", "rejected_warehouse"])
	for branch in branches:
		for warehouse in (branch.finished_goods_warehouse, branch.rejected_warehouse):
			if warehouse and not frappe.db.get_value("Warehouse", warehouse, "custom_branch"):
				frappe.db.set_value(
					"Warehouse", warehouse, "custom_branch", branch.name, update_modified=False
				)

	frappe.db.commit()
