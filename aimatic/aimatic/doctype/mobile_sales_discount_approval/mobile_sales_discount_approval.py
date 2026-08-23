import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class MobileSalesDiscountApproval(Document):
	def validate(self):
		requested = flt(self.requested_percent)
		if requested <= 0 or requested > 100:
			frappe.throw(_("Requested Discount Percent must be greater than 0 and at most 100"))
		if frappe.db.exists(
			"Mobile Sales Discount Approval",
			{"sales_order": self.sales_order, "name": ["!=", self.name]},
		):
			frappe.throw(_("A discount approval audit record already exists for this Sales Order"))
