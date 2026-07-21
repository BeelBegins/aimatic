import frappe
from frappe import _

_STOREFRONT_ROLE = "Storefront Integration"
MAX_PAGE_SIZE = 1000


def require_storefront_role():
	"""Authorization boundary for every storefront_api endpoint.

	Deliberately a role check in code, not core DocPerm — the Storefront
	Integration role carries zero DocPerm grants on purpose (see
	create_storefront_integration_role patch), so this allowlisted endpoint
	set is the only door in for that role, same shape as offline_pos's
	terminal-resource pattern.
	"""
	if frappe.session.user == "Guest" or _STOREFRONT_ROLE not in frappe.get_roles():
		frappe.throw(_("Storefront Integration role required"), frappe.PermissionError)


def paginate(limit_start, limit_page_length):
	try:
		start = int(limit_start or 0)
	except (TypeError, ValueError):
		start = 0
	try:
		page_length = min(int(limit_page_length or 500), MAX_PAGE_SIZE)
	except (TypeError, ValueError):
		page_length = 500
	return max(start, 0), max(page_length, 1)


def envelope(rows, start, page_length):
	has_more = len(rows) > page_length
	rows = rows[:page_length]
	next_start = start + len(rows) if has_more else None
	return {"rows": rows, "next_start": next_start, "has_more": has_more}


def resolve_branch_price_list(branch):
	"""Read-only branch -> Price List lookup.

	Does NOT call shelf_pricing.utils.get_or_create_branch_price_list — that
	function creates a Price List as a side effect, which a read endpoint
	must never trigger. A branch with no dedicated list yet simply falls
	back to the site's global default selling Price List.
	"""
	if not frappe.db.exists("Branch", branch):
		frappe.throw(_("Branch {0} not found").format(branch), frappe.DoesNotExistError)
	price_list = frappe.db.get_value("Branch", branch, "default_selling_price_list")
	return price_list or frappe.db.get_single_value("Selling Settings", "selling_price_list")


def get_branch_warehouses(branch):
	return frappe.get_all(
		"Warehouse",
		filters={"custom_branch": branch},
		fields=["name", "warehouse_name", "disabled"],
	)
