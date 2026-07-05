import frappe

# Roles that may pick a branch/cost center/warehouse different from what the
# Branch record specifies. Everyone else gets these fields silently forced to
# match their branch so they never have to (or get a chance to) pick wrong.
BRANCH_OVERRIDE_ROLES = {
	"Sales Manager",
	"Purchase Manager",
	"Stock Manager",
	"Accounts Manager",
	"System Manager",
}

# Doctypes carrying a header-level `rejected_warehouse` field (purchase side only).
REJECTED_WAREHOUSE_DOCTYPES = {"Purchase Receipt", "Purchase Invoice"}


def user_can_override(user=None):
	user = user or frappe.session.user
	return bool(BRANCH_OVERRIDE_ROLES.intersection(frappe.get_roles(user)))


def get_user_default_branch(user=None):
	return frappe.defaults.get_user_default("Branch", user)


def get_branch_defaults(branch):
	"""Cached Branch -> (cost_center, finished_goods_warehouse, rejected_warehouse)."""

	branch_doc = frappe.get_cached_doc("Branch", branch)
	return {
		"cost_center": branch_doc.get("cost_center"),
		"finished_goods_warehouse": branch_doc.get("finished_goods_warehouse"),
		"rejected_warehouse": branch_doc.get("rejected_warehouse"),
	}
