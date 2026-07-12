import frappe
from frappe import _
from frappe.utils import flt

from aimatic.branch_management.utils import (
	REJECTED_WAREHOUSE_DOCTYPES,
	get_branch_defaults,
	get_user_default_branch,
	get_warehouse_branch,
	user_can_override,
)


def _infer_stock_entry_branch(doc):
	"""Fallback for when the acting user has no default Branch (e.g.
	Administrator, or a Manager role testing/covering another branch): derive
	one from whichever item row's warehouse has a Branch mapped, so the
	document isn't left with a blank Branch - a mandatory Accounting
	Dimension for Profit and Loss accounts - which would otherwise only
	surface as a GL-entry error at submit time."""

	for row in doc.get("items") or []:
		branch = get_warehouse_branch(row.get("s_warehouse")) or get_warehouse_branch(
			row.get("t_warehouse")
		)
		if branch:
			return branch
	return None


def _apply_item_row_defaults(doc, branch_defaults, can_override, has_set_warehouse):
	"""
	Header-level cost_center/set_warehouse/rejected_warehouse (set by the
	caller) don't reliably cascade down to each item row - ERPNext's own
	cascades are inconsistent (e.g. a generic parent->child same-fieldname
	copy can stamp a row's rejected_warehouse with the accepted warehouse's
	value even when that row has no rejected_qty at all, tripping ERPNext's
	own "Accepted and Rejected Warehouse cannot be same" check on a row that
	was never supposed to have a rejected_warehouse in the first place).
	Force the row values directly instead of trusting cascade behavior.
	"""

	items = doc.get("items")
	if not items:
		return

	is_rejected_doctype = doc.doctype in REJECTED_WAREHOUSE_DOCTYPES

	for row in items:
		if row.meta.has_field("cost_center") and (not can_override or not row.get("cost_center")):
			row.cost_center = branch_defaults["cost_center"]

		if (
			has_set_warehouse
			and row.meta.has_field("warehouse")
			and (not can_override or not row.get("warehouse"))
		):
			row.warehouse = branch_defaults["finished_goods_warehouse"]

		if is_rejected_doctype and row.meta.has_field("rejected_warehouse"):
			if flt(row.get("rejected_qty")) > 0:
				if not can_override or not row.get("rejected_warehouse"):
					row.rejected_warehouse = branch_defaults["rejected_warehouse"]
			else:
				# No rejected qty on this row - it should never carry a
				# rejected_warehouse value at all, regardless of who's saving.
				row.rejected_warehouse = None

		if doc.doctype == "Stock Entry" and row.meta.has_field("branch") and not row.get("branch"):
			# Stock Entry Detail.branch has no fetch_from and nothing else
			# populates it - without this, every GL line from this row
			# inherits the single parent-level branch, which is wrong for an
			# inter-branch transfer and blank (throwing a mandatory-dimension
			# error at submit) for a user with no default Branch. Prefer the
			# row's own warehouse's branch over the document-level one.
			row.branch = (
				get_warehouse_branch(row.get("s_warehouse"))
				or get_warehouse_branch(row.get("t_warehouse"))
				or doc.branch
			)


def apply_branch_defaults(doc, method=None):
	"""
	Shared validate hook for Sales Order, Sales Invoice, Delivery Note,
	Purchase Order, Purchase Invoice, Purchase Receipt, Stock Entry.

	Stock Entry has no set_warehouse/rejected_warehouse fields, so those
	assignments below are harmless no-ops for it - only branch and
	cost_center actually apply. It's included so branch (a mandatory
	Accounting Dimension for Profit and Loss accounts) is populated before
	GL entries are made.

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

	if not doc.branch and can_override and doc.doctype == "Stock Entry":
		# Override users (e.g. Administrator, or a Manager) commonly have no
		# default Branch assigned - it's not needed for the rest of their
		# work. Without this, a Stock Entry submitted by such a user is left
		# with a blank Branch, which only surfaces as a GL-entry error at
		# submit time (Branch is a mandatory Accounting Dimension for Profit
		# and Loss accounts, e.g. the Stock Adjustment account).
		doc.branch = _infer_stock_entry_branch(doc)

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
	has_set_warehouse = doc.meta.has_field("set_warehouse")

	if not can_override:
		if not branch_defaults["cost_center"] or (
			has_set_warehouse and not branch_defaults["finished_goods_warehouse"]
		):
			frappe.throw(
				_(
					"Branch {0} is not fully configured (missing Cost Center or Finished "
					"Goods Warehouse). Ask your administrator to complete its setup."
				).format(doc.branch)
			)

		doc.cost_center = branch_defaults["cost_center"]
		if has_set_warehouse:
			doc.set_warehouse = branch_defaults["finished_goods_warehouse"]
		if doc.doctype in REJECTED_WAREHOUSE_DOCTYPES:
			doc.rejected_warehouse = branch_defaults["rejected_warehouse"]
		_apply_item_row_defaults(doc, branch_defaults, can_override, has_set_warehouse)
		return

	if not doc.cost_center:
		doc.cost_center = branch_defaults["cost_center"]
	if has_set_warehouse and not doc.set_warehouse:
		doc.set_warehouse = branch_defaults["finished_goods_warehouse"]
	if (
		doc.doctype in REJECTED_WAREHOUSE_DOCTYPES
		and not doc.rejected_warehouse
		and branch_defaults["rejected_warehouse"]
	):
		doc.rejected_warehouse = branch_defaults["rejected_warehouse"]
	_apply_item_row_defaults(doc, branch_defaults, can_override, has_set_warehouse)


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


def sync_branch_to_user(doc, method=None):
	"""
	Mirror a user's default Branch (assigned via User Permission, the actual
	source of truth apply_branch_defaults reads through
	frappe.defaults.get_user_default) onto User.custom_branch, a plain display
	field with no logic of its own - purely so "which branch is this user in"
	is visible as a column on the User list view instead of requiring a trip
	to User Permission. Runs on both create and delete of a Branch User
	Permission row.
	"""

	if doc.allow != "Branch":
		return

	rows = frappe.get_all(
		"User Permission",
		filters={"user": doc.user, "allow": "Branch"},
		fields=["for_value", "is_default"],
		order_by="is_default desc, creation asc",
	)
	branch = rows[0].for_value if rows else None
	frappe.db.set_value("User", doc.user, "custom_branch", branch)
