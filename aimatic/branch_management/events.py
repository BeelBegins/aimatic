import frappe
from frappe import _

from aimatic.branch_management.utils import (
	REJECTED_WAREHOUSE_DOCTYPES,
	get_branch_defaults,
	get_user_default_branch,
	user_can_override,
)


def apply_branch_defaults(doc, method=None):
	"""
	Shared validate hook for Sales Order, Sales Invoice, Delivery Note,
	Purchase Order, Purchase Invoice, Purchase Receipt.

	Goal: a normal Sales/Purchase user never has to (and cannot) pick branch,
	cost center, or warehouse themselves - it's derived entirely from their
	assigned Branch. Branch/Sales/Purchase/Stock/Accounts managers and System
	Managers may still override on a case-by-case basis (e.g. inter-branch
	transfers) - for them this only fills in blanks, it never overwrites a
	value they deliberately chose.

	POS Invoice is out of scope: cashiers never see it directly (it's built
	server-side from POS Profile, which already carries its own cost_center/
	warehouse). Sales Invoices flagged `is_pos` (including the consolidated
	invoice POS Closing Entry creates) are skipped for the same reason.
	"""

	if doc.doctype == "Sales Invoice" and doc.get("is_pos"):
		return

	can_override = user_can_override()

	if not doc.branch:
		doc.branch = get_user_default_branch()

	if not doc.branch:
		if can_override:
			return
		frappe.throw(
			_(
				"No Branch is assigned to your user account, so this document cannot "
				"be created. Ask your administrator to grant you access to a Branch."
			)
		)

	branch_defaults = get_branch_defaults(doc.branch)

	if not can_override:
		if not branch_defaults["cost_center"] or not branch_defaults["finished_goods_warehouse"]:
			frappe.throw(
				_(
					"Branch {0} is not fully configured (missing Cost Center or Finished "
					"Goods Warehouse). Ask your administrator to complete its setup."
				).format(doc.branch)
			)

		doc.cost_center = branch_defaults["cost_center"]
		doc.set_warehouse = branch_defaults["finished_goods_warehouse"]
		if doc.doctype in REJECTED_WAREHOUSE_DOCTYPES:
			doc.rejected_warehouse = branch_defaults["rejected_warehouse"]
		return

	if not doc.cost_center:
		doc.cost_center = branch_defaults["cost_center"]
	if not doc.set_warehouse:
		doc.set_warehouse = branch_defaults["finished_goods_warehouse"]
	if (
		doc.doctype in REJECTED_WAREHOUSE_DOCTYPES
		and not doc.rejected_warehouse
		and branch_defaults["rejected_warehouse"]
	):
		doc.rejected_warehouse = branch_defaults["rejected_warehouse"]


_BRANCH_COMPANY_SCOPED_FIELDS = {
	"cost_center": ("Cost Center", "Cost Center"),
	"finished_goods_warehouse": ("Warehouse", "Finished Goods Warehouse"),
	"rejected_warehouse": ("Warehouse", "Rejected Warehouse"),
}


def validate_branch_company_consistency(doc, method=None):
	"""
	Branch's cost_center/finished_goods_warehouse/rejected_warehouse must
	belong to the Branch's own company. The link_filters on those fields only
	filter the dropdown *while picking a new value* - they never re-validate
	an already-set value when company itself changes. Without this check,
	editing Company alone silently leaves stale cross-company references,
	which then get force-injected into transactions via apply_branch_defaults
	above (a real incident: changing a Branch's Company left its Cost Center/
	Warehouse pointing at the old company).
	"""

	if not doc.company:
		return

	for fieldname, (linked_doctype, label) in _BRANCH_COMPANY_SCOPED_FIELDS.items():
		value = doc.get(fieldname)
		if not value:
			continue

		linked_company = frappe.db.get_value(linked_doctype, value, "company")
		if linked_company and linked_company != doc.company:
			frappe.throw(
				_(
					"{0} {1} belongs to {2}, not {3}. Update it (or this Branch's "
					"Company) so they match before saving."
				).format(label, value, linked_company, doc.company)
			)
