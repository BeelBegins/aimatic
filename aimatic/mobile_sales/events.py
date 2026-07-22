import frappe
from frappe import _
from frappe.utils import flt


def before_submit_sales_order(doc, method=None):
	"""Prevent a mobile-requested discount from bypassing its audit decision."""
	approval = frappe.db.get_value(
		"Mobile Sales Discount Approval",
		{"sales_order": doc.name},
		["status", "requested_percent"],
		as_dict=True,
	)
	if not approval:
		return
	if approval.status == "Pending":
		frappe.throw(_("Discount approval is still pending for Sales Order {0}").format(doc.name))
	if approval.status == "Rejected" and flt(doc.additional_discount_percentage) > 0:
		frappe.throw(_("The requested discount was rejected. Update the order before submitting it."))
	if approval.status == "Approved" and flt(doc.additional_discount_percentage) > flt(approval.requested_percent) + 0.0001:
		frappe.throw(_("The Sales Order discount exceeds the manager-approved percentage"))

