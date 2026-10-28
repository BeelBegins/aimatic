from __future__ import annotations

import frappe
from frappe import _

LOYALTY_REPORT_ROLES = {"System Manager", "Sales Manager", "Accounts Manager", "POS Supervisor"}

# Keep this identical to the POS/dashboard rule: a selected points redemption
# counts only when the saved payment arithmetic proves the discount was taken.
HONORED_LOYALTY_CONDITION = """
    COALESCE(pi.is_return, 0) = 0
    AND COALESCE(pi.redeem_loyalty_points, 0) = 1
    AND COALESCE(pi.loyalty_points, 0) > 0
    AND COALESCE(pi.loyalty_amount, 0) > 0
    AND ABS(
        (COALESCE(pi.paid_amount, 0) - COALESCE(pi.loyalty_amount, 0) - COALESCE(pi.change_amount, 0))
        - (pi.grand_total - pi.loyalty_amount)
    ) <= 0.5
"""


def check_loyalty_report_access():
	if frappe.session.user == "Guest" or not (set(frappe.get_roles()) & LOYALTY_REPORT_ROLES):
		frappe.throw(_("Not permitted to view loyalty reports."), frappe.PermissionError)
	if not frappe.has_permission("POS Invoice", ptype="read"):
		frappe.throw(_("Not permitted to view POS sales data."), frappe.PermissionError)


def visible_branches(company: str, selected: str | None = None) -> list[str] | None:
	if selected:
		if not frappe.has_permission("Branch", ptype="read", doc=selected):
			frappe.throw(_("Not permitted to view branch {0}.").format(selected), frappe.PermissionError)
		return [selected]

	visible = frappe.get_all("Branch", filters={"company": company}, pluck="name")
	if len(visible) < frappe.db.count("Branch", {"company": company}):
		return visible
	return None
